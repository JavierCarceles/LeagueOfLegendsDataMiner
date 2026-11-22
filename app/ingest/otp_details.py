"""Micro y macro de las partidas de los OTPs (para aprender de jugadores de mucho elo).

De cada partida de un OTP con uno de los campeones elegidos (en su rol) se baja el **timeline** (1 llamada más) y se
guarda en ``otp_games``:

* la partida y el timeline **en bruto** (comprimidos): así se pueden volver a analizar cuando mejore el análisis de
  «Mi progreso», sin pedir nada más a Riot;
* el **dossier**: el mismo análisis que se hace a tus partidas (muertes y su contexto, oro, build, jungla, objetivos,
  macro, fase de líneas, series minuto a minuto…) visto desde el OTP, más lo que ese análisis no guarda: orden de
  habilidades y runas completas, compras/ventas, wards, su posición y zona minuto a minuto y la de su rival.

``export`` escribe una carpeta por campeón con un fichero por OTP (``.json.gz``) y un ``resumen.json`` por campeón con
lo agregado (medias por rol, ganadas contra perdidas, builds, habilidades, runas, jungla y avisos del análisis), que es
lo que hay que leer primero.
"""

from __future__ import annotations

import gzip
import json
import re
import shutil
import statistics
import threading
import time
from collections import Counter, defaultdict
from pathlib import Path

from app.coach import geometry as geo
from app.coach.analyzer import ANALYSIS_VERSION, NotInMatch, analyze_game
from app.coach.store import pack, unpack
from app.data.db import now_s
from app.logbus import get_logger
from app.riot.routing import PLATFORMS, TEAM_POSITION_TO_ROLE

log = get_logger("otp-details")

DOSSIER_VERSION = 1
EXPORT_DIR = "otp_export"


# ------------------------------------------------------------------ dossier de una partida
def catalog_for(svc, match: dict):
    """Catálogo de objetos del parche de la partida (o el actual); ``None`` si Data Dragon no ha cargado."""
    dd = getattr(svc, "dd", None)
    finder = getattr(dd, "catalog_for", None)
    if finder is not None:
        try:
            catalog = finder((match.get("info") or {}).get("gameVersion"))
        except Exception:  # noqa: BLE001  (sin catálogo de ese parche se usa el actual)
            catalog = None
        if catalog:
            return catalog
    catalog = getattr(dd, "item_catalog", None)
    return catalog or None


def _pid_of(match: dict, puuid: str) -> int | None:
    for p in (match.get("info") or {}).get("participants") or []:
        if p.get("puuid") == puuid:
            return int(p.get("participantId") or 0) or None
    return None


def _runes(p: dict) -> dict:
    perks = p.get("perks") or {}
    styles = perks.get("styles") or []
    primary = next((s for s in styles if s.get("description") == "primaryStyle"), styles[0] if styles else {})
    sub = next((s for s in styles if s.get("description") == "subStyle"), styles[1] if len(styles) > 1 else {})

    def picks(style: dict) -> list[int]:
        return [int(x.get("perk") or 0) for x in style.get("selections") or []]

    return {
        "primary_style": primary.get("style"),
        "sub_style": sub.get("style"),
        "keystone": (picks(primary) or [None])[0],
        "primary": picks(primary),
        "secondary": picks(sub),
        "shards": perks.get("statPerks") or {},
    }


def _frames(timeline: dict | None, pid: int | None) -> list[dict]:
    """Posición, zona, oro, nivel, CS y daño de un jugador al final de cada minuto."""
    if not timeline or not pid:
        return []
    out = []
    for fr in (timeline.get("info") or {}).get("frames") or []:
        pf = (fr.get("participantFrames") or {}).get(str(pid)) or {}
        pos = pf.get("position") or {}
        x, y = pos.get("x"), pos.get("y")
        dmg = pf.get("damageStats") or {}
        out.append(
            {
                "min": round(int(fr.get("timestamp") or 0) / 60_000, 1),
                "x": x,
                "y": y,
                "zone": geo.zone((x, y)) if x is not None and y is not None else None,
                "gold": pf.get("totalGold"),
                "current_gold": pf.get("currentGold"),
                "xp": pf.get("xp"),
                "level": pf.get("level"),
                "cs": int(pf.get("minionsKilled") or 0) + int(pf.get("jungleMinionsKilled") or 0),
                "jungle_cs": pf.get("jungleMinionsKilled"),
                "dmg_champs": dmg.get("totalDamageDoneToChampions"),
                "dmg_taken": dmg.get("totalDamageTaken"),
            }
        )
    return out


def _events(timeline: dict | None, pid: int | None) -> dict:
    """Eventos propios que el análisis no guarda tal cual: compras/ventas, habilidades y wards."""
    out = {"items": [], "skills": [], "wards_placed": [], "wards_killed": [], "level_ups": []}
    if not timeline or not pid:
        return out
    for fr in (timeline.get("info") or {}).get("frames") or []:
        for ev in fr.get("events") or []:
            kind = ev.get("type")
            t = round(int(ev.get("timestamp") or 0) / 1000, 1)
            if kind in ("ITEM_PURCHASED", "ITEM_SOLD", "ITEM_UNDO", "ITEM_DESTROYED") and ev.get("participantId") == pid:
                out["items"].append([t, kind.removeprefix("ITEM_").lower(), ev.get("itemId") or ev.get("beforeId") or 0])
            elif kind == "SKILL_LEVEL_UP" and ev.get("participantId") == pid:
                out["skills"].append([t, ev.get("skillSlot"), ev.get("levelUpType")])
            elif kind == "WARD_PLACED" and ev.get("creatorId") == pid:
                out["wards_placed"].append([t, ev.get("wardType")])
            elif kind == "WARD_KILL" and ev.get("killerId") == pid:
                out["wards_killed"].append([t, ev.get("wardType")])
            elif kind == "LEVEL_UP" and ev.get("participantId") == pid:
                out["level_ups"].append([t, ev.get("level")])
    return out


def build_dossier(match: dict, timeline: dict | None, puuid: str, items=None, *, tier: str | None = None) -> dict:
    """Todo lo que se puede sacar de una partida desde el punto de vista de ese jugador (función pura)."""
    analysis = analyze_game(match, timeline, puuid=puuid, items=items)
    info = match.get("info") or {}
    pid = analysis["me"]
    raw = next((p for p in info.get("participants") or [] if int(p.get("participantId") or 0) == pid), {})
    opp = analysis.get("opponent")
    replay = analysis.pop("replay", None) or {}
    me_track = (replay.get("tracks") or {}).get(str(pid))
    team_of = {int(p.get("participantId") or 0): int(p.get("teamId") or 0) for p in info.get("participants") or []}

    def lineup(team: int) -> dict:
        return {
            TEAM_POSITION_TO_ROLE.get((p.get("teamPosition") or "").upper()) or f"?{p.get('participantId')}": int(p.get("championId") or 0)
            for p in info.get("participants") or []
            if int(p.get("teamId") or 0) == team
        }

    challenges = raw.get("challenges") or {}
    return {
        "dossier_version": DOSSIER_VERSION,
        "analysis_version": ANALYSIS_VERSION,
        "match_id": analysis["match_id"],
        "platform": str(analysis["match_id"] or "").split("_")[0].lower(),
        "tier": tier,
        "patch": analysis["patch"],
        "game_start": analysis["game_start"],
        "duration": analysis["duration"],
        "champ": analysis["champ"],
        "role": analysis["role"],
        "win": analysis["win"],
        "team": analysis["team"],
        "side": "blue" if analysis["team"] == 100 else "red",
        "opponent_champ": next((p["champ"] for p in analysis["players"] if p["pid"] == opp), None) if opp else None,
        "allies": lineup(analysis["team"]),
        "enemies": lineup(300 - analysis["team"]),
        "spells": [raw.get("summoner1Id"), raw.get("summoner2Id")],
        "runes": _runes(raw),
        # Estadísticas de Riot que el análisis no usa (todas las de ``challenges`` son del propio jugador).
        "riot_stats": {
            k: raw.get(k)
            for k in (
                "goldEarned", "goldSpent", "totalDamageDealtToChampions", "physicalDamageDealtToChampions",
                "magicDamageDealtToChampions", "trueDamageDealtToChampions", "totalDamageTaken", "damageSelfMitigated",
                "totalHeal", "totalHealsOnTeammates", "timeCCingOthers", "damageDealtToObjectives", "damageDealtToBuildings",
                "damageDealtToTurrets", "visionScore", "wardsPlaced", "wardsKilled", "detectorWardsPlaced",
                "visionWardsBoughtInGame", "totalMinionsKilled", "neutralMinionsKilled", "totalAllyJungleMinionsKilled",
                "totalEnemyJungleMinionsKilled", "longestTimeSpentLiving", "totalTimeSpentDead", "largestKillingSpree",
                "largestMultiKill", "firstBloodKill", "firstBloodAssist", "firstTowerKill", "firstTowerAssist",
                "turretKills", "inhibitorKills", "dragonKills", "baronKills", "objectivesStolen", "spell1Casts",
                "spell2Casts", "spell3Casts", "spell4Casts", "summoner1Casts", "summoner2Casts",
            )
            if k in raw
        },
        "challenges": challenges,
        "analysis": analysis,
        "track": me_track,
        "frames": _frames(timeline, pid),
        "opponent_frames": _frames(timeline, opp),
        "events": _events(timeline, pid),
        "map_events": {k: replay.get(k) for k in ("kills", "objectives", "buildings")},
        "teams_by_pid": team_of,
    }


# ------------------------------------------------------------------ guardar
def missing(db, puuid: str, player_id: int, cond: str, params: list, limit: int) -> list[str]:
    """Partidas de ese OTP con sus campeones (en su rol) aún sin micro/macro, de la más reciente a la más antigua."""
    rows = db.query(
        f"SELECT p.match_id FROM participants p JOIN matches m ON m.match_id = p.match_id "
        f"LEFT JOIN otp_games o ON o.match_id = p.match_id AND o.puuid = ? "
        f"WHERE p.player_id = ? AND {cond} AND o.match_id IS NULL ORDER BY m.game_start DESC LIMIT ?",
        (puuid, player_id, *params, limit),
    )
    return [r[0] for r in rows]


def save(db, svc, match: dict, timeline: dict | None, puuid: str, tier: str | None) -> bool:
    """Analiza y guarda la partida (con su bruto). ``False`` si el jugador no está en ella."""
    try:
        dossier = build_dossier(match, timeline, puuid, catalog_for(svc, match), tier=tier)
    except NotInMatch:
        return False
    with db.write() as conn:
        conn.execute(
            "INSERT OR REPLACE INTO otp_games (match_id, puuid, champ, role, win, game_start, patch, platform, tier, "
            "has_timeline, version, match_raw, timeline_raw, dossier, fetched_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                dossier["match_id"], puuid, dossier["champ"], dossier["role"], int(bool(dossier["win"])),
                dossier["game_start"], dossier["patch"], dossier["platform"], tier, int(timeline is not None),
                DOSSIER_VERSION * 1000 + ANALYSIS_VERSION, pack(match), pack(timeline), pack(dossier), now_s(),
            ),
        )
    return True


def counts(db) -> dict:
    row = db.one("SELECT COUNT(*) AS games, COUNT(DISTINCT puuid) AS players, SUM(has_timeline) AS timelines FROM otp_games")
    return {"games": int(row["games"] or 0), "players": int(row["players"] or 0), "timelines": int(row["timelines"] or 0)}


def reanalyze(db, svc, limit: int | None = None) -> int:
    """Rehace los dossiers con el análisis actual a partir del bruto guardado (sin llamadas a Riot)."""
    current = DOSSIER_VERSION * 1000 + ANALYSIS_VERSION
    rows = db.query(
        "SELECT match_id, puuid, tier FROM otp_games WHERE version != ? " + ("LIMIT ?" if limit else ""),
        (current, limit) if limit else (current,),
    )
    done = 0
    for r in rows:
        raw = db.one("SELECT match_raw, timeline_raw FROM otp_games WHERE match_id = ? AND puuid = ?", (r["match_id"], r["puuid"]))
        match, timeline = unpack(raw["match_raw"]), unpack(raw["timeline_raw"])
        if match and save(db, svc, match, timeline, r["puuid"], r["tier"]):
            done += 1
    return done


# ------------------------------------------------------------------ exportar
def _slug(text: str) -> str:
    return re.sub(r"[^\w.#-]+", "_", text, flags=re.UNICODE).strip("_") or "sin_nombre"


def _stats(values: list[float]) -> dict | None:
    vals = [float(v) for v in values if isinstance(v, (int, float)) and not isinstance(v, bool)]
    if not vals:
        return None
    return {"mean": round(statistics.fmean(vals), 2), "median": round(statistics.median(vals), 2), "n": len(vals)}


def _top(counter: Counter, wins: Counter, n: int = 8, name=None) -> list[dict]:
    total = sum(counter.values()) or 1
    return [
        {"value": name(k) if name else k, "key": k, "games": c, "pct": round(100 * c / total, 1), "wr": round(100 * wins[k] / c, 1)}
        for k, c in counter.most_common(n)
    ]


def summarize(dossiers: list[dict], name_champ=None, name_item=None) -> dict:
    """Lo agregado de un campeón: lo que hacen sus OTPs de forma habitual y en qué se diferencian sus victorias."""
    games = len(dossiers)
    wins = sum(1 for d in dossiers if d["win"])
    by_role: dict[str, list[dict]] = defaultdict(list)
    for d in dossiers:
        by_role[d["role"] or "?"].append(d)

    def metric_block(rows: list[dict]) -> dict:
        keys = sorted({k for d in rows for k, v in (d["analysis"].get("metrics") or {}).items() if isinstance(v, (int, float))})
        return {k: s for k in keys if (s := _stats([(d["analysis"].get("metrics") or {}).get(k) for d in rows]))}

    def jungle_block(rows: list[dict]) -> dict:
        keys = sorted({k for d in rows for k, v in (d["analysis"].get("jungle") or {}).items() if isinstance(v, (int, float))})
        return {k: s for k in keys if (s := _stats([(d["analysis"].get("jungle") or {}).get(k) for d in rows]))}

    roles = {}
    for role, rows in sorted(by_role.items(), key=lambda kv: -len(kv[1])):
        w = [d for d in rows if d["win"]]
        l_ = [d for d in rows if not d["win"]]
        first_items, paths, skill_max, keystones, spells, first_camp, opponents = (Counter() for _ in range(7))
        first_items_w, paths_w, skill_max_w, keystones_w, spells_w, first_camp_w, opponents_w = (Counter() for _ in range(7))
        moments, moments_games = Counter(), Counter()
        for d in rows:
            build = d["analysis"].get("build") or {}
            legend = [o["item"] for o in build.get("order") or [] if o.get("kind") == "legendary"]
            win = 1 if d["win"] else 0
            if legend:
                first_items[legend[0]] += 1
                first_items_w[legend[0]] += win
                path = " > ".join(str(i) for i in legend[:3])
                paths[path] += 1
                paths_w[path] += win
            if build.get("skill_max"):
                key = ">".join(build["skill_max"])
                skill_max[key] += 1
                skill_max_w[key] += win
            ks = (d.get("runes") or {}).get("keystone")
            if ks:
                keystones[ks] += 1
                keystones_w[ks] += win
            sp = "+".join(str(s) for s in sorted(x for x in d.get("spells") or [] if x))
            spells[sp] += 1
            spells_w[sp] += win
            path_j = (d["analysis"].get("jungle") or {}).get("path") or []
            first = next((p.get("camp") for p in path_j if p.get("camp")), None)
            if first:
                first_camp[first] += 1
                first_camp_w[first] += win
            if d.get("opponent_champ"):
                opponents[d["opponent_champ"]] += 1
                opponents_w[d["opponent_champ"]] += win
            seen = set()
            for m in d["analysis"].get("moments") or []:
                key = f"{m.get('severity')} · {m.get('kind')} · {re.sub(r'[0-9]+', 'N', m.get('title') or '')}"
                moments[key] += 1
                seen.add(key)
            for key in seen:
                moments_games[key] += 1
        item_name = (lambda i: name_item(i) or i) if name_item else None
        roles[role] = {
            "games": len(rows),
            "wins": len(w),
            "wr": round(100 * len(w) / len(rows), 1) if rows else None,
            "metrics": metric_block(rows),
            "metrics_wins": metric_block(w),
            "metrics_losses": metric_block(l_),
            "jungle": jungle_block(rows) if role == "JUNGLE" else None,
            "first_legendary": _top(first_items, first_items_w, name=item_name),
            "build_paths": _top(paths, paths_w, name=(lambda p: " > ".join(str(name_item(int(i)) or i) for i in p.split(" > "))) if name_item else None),
            "skill_max": _top(skill_max, skill_max_w, 5),
            "keystones": _top(keystones, keystones_w, 5),
            "summoner_spells": _top(spells, spells_w, 5),
            "first_camp": _top(first_camp, first_camp_w, 6) if role == "JUNGLE" else None,
            "opponents": _top(opponents, opponents_w, 15, name=(lambda c: name_champ(c) or c) if name_champ else None),
            # Avisos que el análisis de «Mi progreso» les marca: en jugadores de este nivel, lo muy repetido apunta
            # a umbrales a revisar (posibles falsos positivos).
            "analysis_flags": [
                {"flag": k, "times": c, "games_pct": round(100 * moments_games[k] / len(rows), 1)} for k, c in moments.most_common(40)
            ],
        }
    tiers = Counter(d.get("tier") or "?" for d in dossiers)
    patches = Counter(d.get("patch") or "?" for d in dossiers)
    return {
        "games": games,
        "wins": wins,
        "wr": round(100 * wins / games, 1) if games else None,
        "players": len({d.get("puuid") for d in dossiers}),
        "tiers": dict(tiers.most_common()),
        "patches": dict(patches.most_common()),
        "with_timeline": sum(1 for d in dossiers if d["analysis"].get("has_timeline")),
        "roles": roles,
    }


README = """# Datos de OTPs (micro y macro)

Generado por Tempu Drafter ({when}). Una carpeta por campeón:

* `resumen.json`: lo agregado de sus OTPs (léelo primero): medias por rol de todas las métricas del análisis de
  «Mi progreso», las mismas en victorias y en derrotas, primer objeto y builds más jugadas (con winrate), orden de
  habilidades, runas, hechizos, primer campamento (jungla), rivales y los avisos que el análisis les marca.
* `<servidor>_<Riot ID>_<id>.json.gz`: un fichero por OTP con sus datos y la lista de partidas. Cada partida es un dossier
  completo: análisis (muertes con contexto, oro, build, jungla, objetivos, macro, fase de líneas, series por minuto),
  runas, hechizos, estadísticas de Riot, posición/zona/oro/CS minuto a minuto suyo y de su rival, compras, habilidades,
  wards y los eventos del mapa (kills, objetivos y estructuras).

Ids: campeones y objetos de Data Dragon; tiempos en segundos salvo `min` (minutos). Roles: TOP, JUNGLE, MID, ADC,
SUPPORT. La partida y el timeline en bruto quedan en la base de datos (`otp_games`) para volver a analizarlos.
"""


class Exporter:
    """Exportación en segundo plano (puede tardar con miles de partidas)."""

    def __init__(self, svc) -> None:
        self.svc = svc
        self.lock = threading.Lock()
        self.state: dict = {"status": "idle"}

    def start(self) -> dict:
        with self.lock:
            if self.state.get("status") == "running":
                return self.state
            self.state = {"status": "running", "started_at": time.time(), "done": 0, "total": 0}
        threading.Thread(target=self._run, name="otp-export", daemon=True).start()
        return self.state

    def _run(self) -> None:
        try:
            result = export(self.svc, progress=lambda done, total: self.state.update(done=done, total=total))
            self.state = {"status": "done", "finished_at": time.time(), **result}
        except Exception as exc:  # que la web lo muestre en vez de quedarse «exportando»
            log.exception("Error exportando los datos de OTPs")
            self.state = {"status": "error", "error": str(exc)}


def export(svc, out_dir: Path | None = None, progress=None) -> dict:
    """Escribe ``data/otp_export``: una carpeta por campeón con ``resumen.json`` y un ``.json.gz`` por OTP."""
    db = svc.db
    out = Path(out_dir or Path(svc.settings.data_dir) / EXPORT_DIR)
    tmp = out.with_name(out.name + ".tmp")
    shutil.rmtree(tmp, ignore_errors=True)
    tmp.mkdir(parents=True)
    dd = getattr(svc, "dd", None)
    name_champ = (lambda c: dd.name(c)) if dd is not None and hasattr(dd, "name") else None
    catalog = getattr(dd, "item_catalog", None)
    name_item = (lambda i: (catalog.items.get(int(i)) or {}).get("name")) if catalog else None

    players = {
        r["puuid"]: dict(r)
        for r in db.query(
            "SELECT puuid, platform, tier, division, lp, game_name, tag_line FROM players WHERE puuid IN (SELECT DISTINCT puuid FROM otp_games)"
        )
    }
    groups = db.query("SELECT champ, puuid, COUNT(*) AS n FROM otp_games GROUP BY champ, puuid ORDER BY champ, n DESC")
    total = sum(int(g["n"]) for g in groups)
    done = 0
    files = 0
    by_champ: dict[int, list[dict]] = defaultdict(list)
    for g in groups:
        champ, puuid = int(g["champ"]), g["puuid"]
        dossiers = []
        for r in db.query(
            "SELECT dossier FROM otp_games WHERE champ = ? AND puuid = ? ORDER BY game_start DESC", (champ, puuid)
        ):
            d = unpack(r["dossier"])
            if d:
                d["puuid"] = puuid
                dossiers.append(d)
            done += 1
            if progress and done % 50 == 0:
                progress(done, total)
        if not dossiers:
            continue
        info = players.get(puuid) or {"puuid": puuid}
        champ_name = (name_champ(champ) if name_champ else None) or str(champ)
        folder = tmp / _slug(champ_name)
        folder.mkdir(exist_ok=True)
        riot_id = f"{info.get('game_name') or 'sin_nombre'}#{info.get('tag_line') or ''}"
        platform = info.get("platform") or dossiers[0]["platform"]
        payload = {
            "player": {
                "riot_id": riot_id,
                "platform": platform,
                "server": PLATFORMS[platform][1] if platform in PLATFORMS else platform,
                "tier": info.get("tier"),
                "division": info.get("division"),
                "lp": info.get("lp"),
                "puuid": puuid,
            },
            "champ": champ,
            "champ_name": champ_name,
            "games": dossiers,
        }
        # El trozo del puuid evita que dos jugadores con el mismo nombre (o sin nombre aún) se pisen el fichero.
        with gzip.open(folder / f"{_slug(platform + '_' + riot_id)}_{puuid[:8]}.json.gz", "wt", encoding="utf-8") as fh:
            json.dump(payload, fh, ensure_ascii=False, separators=(",", ":"))
        files += 1
        by_champ[champ].extend(dossiers)

    champions = []
    for champ, dossiers in by_champ.items():
        champ_name = (name_champ(champ) if name_champ else None) or str(champ)
        summary = {"champ": champ, "champ_name": champ_name, **summarize(dossiers, name_champ, name_item)}
        (tmp / _slug(champ_name) / "resumen.json").write_text(json.dumps(summary, ensure_ascii=False, indent=1), encoding="utf-8")
        champions.append({"champ": champ, "name": champ_name, "games": summary["games"], "players": summary["players"]})
    (tmp / "LEEME.md").write_text(README.format(when=time.strftime("%Y-%m-%d %H:%M")), encoding="utf-8")

    shutil.rmtree(out, ignore_errors=True)
    tmp.rename(out)
    size = sum(f.stat().st_size for f in out.rglob("*") if f.is_file())
    log.info("Datos de OTPs exportados a %s: %d partidas, %d ficheros, %.1f MB.", out, total, files, size / 1e6)
    return {"path": str(out), "games": total, "files": files, "bytes": size, "champions": champions}
