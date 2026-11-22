"""Micro y macro de las partidas de los OTPs (para aprender de jugadores de mucho elo).

De cada partida de un OTP con uno de los campeones elegidos (en su rol) se baja el **timeline** (1 llamada más) y se
guarda en ``otp_games``:

* la partida y el timeline **en bruto** (comprimidos): así se pueden volver a analizar cuando mejore el análisis de
  «Mi progreso», sin pedir nada más a Riot;
* el **dossier**: el mismo análisis que se hace a tus partidas (muertes y su contexto, oro, build, jungla, objetivos,
  macro, fase de líneas, series minuto a minuto…) visto desde el OTP, más lo que ese análisis no guarda: orden de
  habilidades y runas completas, compras/ventas, wards, su posición y zona minuto a minuto y la de su rival.

Además, **cada partida se escribe sola en disco** (``<DATA_DIR>/otps``), una base de datos por OTP con su rol claro::

    otps/<Campeón>/resumen.json                      (se rehace solo: por rol y, dentro, por elo)
    otps/<Campeón>/<ROL>/<servidor>_<RiotID>_<id>/perfil.json         (su elo actual y sus partidas por elo)
    otps/<Campeón>/<ROL>/<servidor>_<RiotID>_<id>/partidas.jsonl.gz   (una línea por partida: el dossier entero)

Cada partida lleva el elo que tenía el OTP al bajarla (tier, división y LP), para comparar Aspirante, Gran Maestro,
Maestro… La base de datos es la fuente: si la carpeta falta o cambia su formato, se regenera sola desde ella.
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
from typing import ClassVar

from app.coach import geometry as geo
from app.coach.analyzer import ANALYSIS_VERSION, NotInMatch, analyze_game
from app.coach.store import pack, unpack
from app.data.db import now_s
from app.logbus import get_logger
from app.riot.routing import PLATFORMS, ROLES, TEAM_POSITION_TO_ROLE

log = get_logger("otp-details")

DOSSIER_VERSION = 2  # 2: contexto de draft (las 5 líneas, presencia de los junglas y objetivos)
LANE_MINUTES = (5, 10, 15, 20)
EARLY_S = 15 * 60
FILES_DIR = "otps"
LAYOUT_VERSION = 1  # formato de la carpeta: si cambia, se regenera desde la base de datos
SUMMARY_EVERY_S = 300  # el resumen de un campeón se rehace como mucho cada 5 min mientras entran partidas
TIER_ORDER = ("CHALLENGER", "GRANDMASTER", "MASTER", "DIAMOND", "EMERALD", "PLATINUM", "GOLD", "SILVER", "BRONZE", "IRON")


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


def _game_context(match: dict, timeline: dict | None, team: int) -> dict:
    """Lo que hace falta para planes de partida: cómo fue cada línea (quién contra quién y ventaja de oro, XP y CS a los
    5/10/15/20 min), por dónde anduvo cada jungla los primeros 15 min (y en qué líneas mató), la primera torre y todos
    los objetivos, desde el punto de vista del equipo ``team`` (``ally`` = el equipo del OTP)."""
    info = match.get("info") or {}
    slot: dict[tuple[int, str], int] = {}
    champ_of: dict[int, int] = {}
    team_of: dict[int, int] = {}
    for p in info.get("participants") or []:
        pid = int(p.get("participantId") or 0)
        champ_of[pid] = int(p.get("championId") or 0)
        team_of[pid] = int(p.get("teamId") or 0)
        role = TEAM_POSITION_TO_ROLE.get((p.get("teamPosition") or "").upper())
        if role:
            slot[(team_of[pid], role)] = pid
    enemy = 300 - team
    frames = (timeline or {}).get("info", {}).get("frames") or []

    def fval(pid: int, minute: int, key: str):
        if minute >= len(frames):
            return None
        pf = (frames[minute].get("participantFrames") or {}).get(str(pid)) or {}
        if key == "cs":
            return int(pf.get("minionsKilled") or 0) + int(pf.get("jungleMinionsKilled") or 0) if pf else None
        return pf.get(key)

    events = [ev for fr in frames for ev in fr.get("events") or []]
    kills = [ev for ev in events if ev.get("type") == "CHAMPION_KILL"]
    lanes = {}
    for role in ROLES:
        a, e = slot.get((team, role)), slot.get((enemy, role))
        if not a or not e:
            continue
        diff = {}
        for m in LANE_MINUTES:
            vals = {"gold": (fval(a, m, "totalGold"), fval(e, m, "totalGold")), "xp": (fval(a, m, "xp"), fval(e, m, "xp")), "cs": (fval(a, m, "cs"), fval(e, m, "cs"))}
            if all(x is not None and y is not None for x, y in vals.values()):
                diff[str(m)] = {k: x - y for k, (x, y) in vals.items()}
        early = [k for k in kills if int(k.get("timestamp") or 0) < EARLY_S * 1000]
        lanes[role] = {
            "ally": champ_of.get(a),
            "enemy": champ_of.get(e),
            "diff": diff,  # aliado − rival en esa línea
            "kills_pre15": sum(1 for k in early if k.get("killerId") == a),
            "deaths_pre15": sum(1 for k in early if k.get("victimId") == a),
        }

    def presence(pid: int | None) -> dict | None:
        """Minutos 2-14: dónde estaba al final de cada minuto (líneas, río, su jungla, la rival, base) y en qué zona
        consiguió kills o asistencias antes del 15 (ganks)."""
        if not pid or not frames:
            return None
        own = team_of.get(pid)
        where = Counter()
        for m in range(2, min(15, len(frames))):
            pos = ((frames[m].get("participantFrames") or {}).get(str(pid)) or {}).get("position") or {}
            if pos.get("x") is None:
                continue
            z = geo.zone((pos["x"], pos["y"]))
            if z.startswith("jungle_"):
                z = "own_jungle" if z.startswith(f"jungle_{own}_") else "enemy_jungle"
            elif z.startswith("base_"):
                z = "base"
            where[z] += 1
        ganks = Counter()
        first = None
        for k in kills:
            t = int(k.get("timestamp") or 0)
            if t >= EARLY_S * 1000 or team_of.get(k.get("victimId")) == own:
                continue
            if k.get("killerId") == pid or pid in (k.get("assistingParticipantIds") or []):
                pos = k.get("position") or {}
                z = geo.zone((pos.get("x", 0), pos.get("y", 0))) if pos else "?"
                lane = z if geo.is_lane(z) else ("river" if z == "river" else "jungle")
                ganks[lane] += 1
                if first is None:
                    first = {"t": round(t / 1000), "where": lane}
        return {"positions": dict(where), "takedowns_pre15": dict(ganks), "first_takedown": first}

    objectives = []
    first_tower = None
    for ev in events:
        kind = ev.get("type")
        t = round(int(ev.get("timestamp") or 0) / 1000)
        if kind == "ELITE_MONSTER_KILL":
            objectives.append(
                {"t": t, "type": (ev.get("monsterType") or "").lower(), "sub": ev.get("monsterSubType"), "ally": ev.get("killerTeamId") == team}
            )
        elif kind == "BUILDING_KILL" and ev.get("buildingType") == "TOWER_BUILDING" and first_tower is None:
            # ``teamId`` es el dueño de la torre: si es del rival, la tiró el equipo del OTP.
            first_tower = {"t": t, "lane": (ev.get("laneType") or "").replace("_LANE", "").lower(), "ally": ev.get("teamId") == enemy}
    first_of = {}
    for o in objectives:
        first_of.setdefault(o["type"], o)
    return {
        "lanes": lanes,
        "jungle": {"ally": presence(slot.get((team, "JUNGLE"))), "enemy": presence(slot.get((enemy, "JUNGLE")))},
        "objectives": objectives,
        "first": {k: {"t": v["t"], "ally": v["ally"]} for k, v in first_of.items()} | ({"tower": first_tower} if first_tower else {}),
    }


def build_dossier(match: dict, timeline: dict | None, puuid: str, items=None, *, elo: dict | None = None) -> dict:
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
        # Elo del OTP cuando se bajó la partida (el de la liga en ese momento).
        "tier": (elo or {}).get("tier"),
        "division": (elo or {}).get("division"),
        "lp": (elo or {}).get("lp"),
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
        "context": _game_context(match, timeline, analysis["team"]),
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


def player_info(db, puuid: str) -> dict:
    """Riot ID, servidor y elo actual del OTP (de la tabla de jugadores)."""
    row = db.one("SELECT puuid, platform, tier, division, lp, game_name, tag_line FROM players WHERE puuid = ?", (puuid,))
    return dict(row) if row else {"puuid": puuid}


def save(db, svc, match: dict, timeline: dict | None, puuid: str, elo: dict | None = None, *, write_files: bool = True) -> bool:
    """Analiza y guarda la partida (con su bruto) y la escribe en la carpeta del OTP. ``False`` si no está en ella."""
    try:
        dossier = build_dossier(match, timeline, puuid, catalog_for(svc, match), elo=elo)
    except NotInMatch:
        return False
    is_new = not db.scalar("SELECT 1 FROM otp_games WHERE match_id = ? AND puuid = ?", (dossier["match_id"], puuid))
    with db.write() as conn:
        conn.execute(
            "INSERT OR REPLACE INTO otp_games (match_id, puuid, champ, role, win, game_start, patch, platform, tier, "
            "division, lp, has_timeline, version, match_raw, timeline_raw, dossier, fetched_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                dossier["match_id"], puuid, dossier["champ"], dossier["role"], int(bool(dossier["win"])),
                dossier["game_start"], dossier["patch"], dossier["platform"], dossier["tier"], dossier["division"],
                dossier["lp"], int(timeline is not None), DOSSIER_VERSION * 1000 + ANALYSIS_VERSION, pack(match),
                pack(timeline), pack(dossier), now_s(),
            ),
        )
    if is_new and write_files:
        files = Files.of(svc)
        if files is not None:
            try:
                files.append(dossier, player_info(db, puuid))
            except OSError:
                log.exception("No se pudo escribir la partida %s en la carpeta de OTPs", dossier["match_id"])
    return True


def counts(db) -> dict:
    row = db.one("SELECT COUNT(*) AS games, COUNT(DISTINCT puuid) AS players, SUM(has_timeline) AS timelines FROM otp_games")
    tiers = {
        r["tier"] or "?": int(r["n"])
        for r in db.query("SELECT tier, COUNT(*) AS n FROM otp_games GROUP BY tier")
    }
    return {
        "games": int(row["games"] or 0),
        "players": int(row["players"] or 0),
        "timelines": int(row["timelines"] or 0),
        "tiers": {t: tiers[t] for t in sorted(tiers, key=lambda t: TIER_ORDER.index(t) if t in TIER_ORDER else 99)},
    }


def reanalyze(db, svc, limit: int | None = None) -> int:
    """Rehace los dossiers con el análisis actual a partir del bruto guardado (sin llamadas a Riot) y la carpeta."""
    current = DOSSIER_VERSION * 1000 + ANALYSIS_VERSION
    rows = db.query(
        "SELECT match_id, puuid, tier, division, lp FROM otp_games WHERE version != ? " + ("LIMIT ?" if limit else ""),
        (current, limit) if limit else (current,),
    )
    done = 0
    for r in rows:
        raw = db.one("SELECT match_raw, timeline_raw FROM otp_games WHERE match_id = ? AND puuid = ?", (r["match_id"], r["puuid"]))
        match, timeline = unpack(raw["match_raw"]), unpack(raw["timeline_raw"])
        elo = {"tier": r["tier"], "division": r["division"], "lp": r["lp"]}
        if match and save(db, svc, match, timeline, r["puuid"], elo, write_files=False):
            done += 1
    if done:
        files = Files.of(svc)
        if files is not None:
            files.rebuild()
    return done


# ------------------------------------------------------------------ resumen
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


def _tier_key(tier: str) -> int:
    return TIER_ORDER.index(tier) if tier in TIER_ORDER else 99


def _role_block(role: str, rows: list[dict], name_champ=None, name_item=None) -> dict:
    """Lo agregado de unas partidas de un rol: medias, ganadas contra perdidas, builds, runas, rivales y avisos."""

    def metric_block(rows: list[dict]) -> dict:
        keys = sorted({k for d in rows for k, v in (d["analysis"].get("metrics") or {}).items() if isinstance(v, (int, float))})
        return {k: s for k in keys if (s := _stats([(d["analysis"].get("metrics") or {}).get(k) for d in rows]))}

    def jungle_block(rows: list[dict]) -> dict:
        keys = sorted({k for d in rows for k, v in (d["analysis"].get("jungle") or {}).items() if isinstance(v, (int, float))})
        return {k: s for k in keys if (s := _stats([(d["analysis"].get("jungle") or {}).get(k) for d in rows]))}

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
    return {
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
        **_draft_block(role, rows, name_champ),
    }


AHEAD_GOLD = 500  # ventaja de oro de una línea a los 15 que cuenta como «va ganando» (o perdiendo)


def _wr(rows: list[dict]) -> dict:
    return {"games": len(rows), "wr": round(100 * sum(1 for d in rows if d["win"]) / len(rows), 1) if rows else None}


def _draft_block(role: str, rows: list[dict], name_champ=None) -> dict:
    """Para planes de partida: cómo le va contra cada rival de línea, qué pasa con cada línea por delante o por
    detrás, a qué objetivos juega su equipo primero y (si es jungla) por dónde anda según qué línea va ganando."""
    ctx = [d for d in rows if d.get("context")]
    if not ctx:
        return {}
    name = (lambda c: name_champ(c) or c) if name_champ else (lambda c: c)

    def lane_diff(d: dict, r: str, minute: str, key: str = "gold"):
        return (((d["context"]["lanes"].get(r) or {}).get("diff") or {}).get(minute) or {}).get(key)

    # Contra cada rival de su línea: winrate y ventaja media a los 10 y 15.
    by_opp: dict[int, list[dict]] = defaultdict(list)
    for d in ctx:
        opp = (d["context"]["lanes"].get(role) or {}).get("enemy")
        if opp:
            by_opp[opp].append(d)
    matchups = []
    for opp, ds in sorted(by_opp.items(), key=lambda kv: -len(kv[1]))[:25]:
        entry = {"enemy": name(opp), "enemy_id": opp, **_wr(ds)}
        for m in ("10", "15"):
            for key in ("gold", "xp", "cs"):
                st = _stats([lane_diff(d, role, m, key) for d in ds])
                entry[f"{key}_diff{m}"] = st["mean"] if st else None
        matchups.append(entry)

    # Cada línea por delante / igualada / por detrás a los 15: winrate (qué línea decide más sus partidas).
    lanes_impact = {}
    for r in ROLES:
        groups = {"ahead": [], "even": [], "behind": []}
        for d in ctx:
            g = lane_diff(d, r, "15")
            if g is None:
                continue
            groups["ahead" if g > AHEAD_GOLD else "behind" if g < -AHEAD_GOLD else "even"].append(d)
        if any(groups.values()):
            lanes_impact[r] = {k: _wr(v) for k, v in groups.items()}

    # Objetivos: quién coge el primero de cada uno, cuándo y cómo acaba la partida.
    objectives = {}
    for kind in ("dragon", "horde", "riftherald", "baron_nashor", "atakhan", "tower"):
        got = [d for d in ctx if kind in d["context"]["first"]]
        if not got:
            continue
        ally = [d for d in got if d["context"]["first"][kind]["ally"]]
        t = _stats([d["context"]["first"][kind]["t"] / 60 for d in ally])
        objectives[kind] = {
            "ally_first_pct": round(100 * len(ally) / len(got), 1),
            "ally_first_min": t["mean"] if t else None,
            "wr_ally_first": _wr(ally)["wr"],
            "wr_enemy_first": _wr([d for d in got if not d["context"]["first"][kind]["ally"]])["wr"],
            "games": len(got),
        }
    first_tower_lane = Counter(d["context"]["first"]["tower"]["lane"] for d in ctx if (d["context"]["first"].get("tower") or {}).get("ally"))

    out = {"matchups": matchups, "lanes_impact": lanes_impact, "objectives_first": objectives, "first_tower_lane": dict(first_tower_lane)}
    if role == "JUNGLE":
        # Por dónde anda (minutos 2-14) y dónde consigue kills antes del 15; también según qué línea va ganando a los 10.
        def presence(ds: list[dict]) -> dict:
            keys = ("top", "mid", "bot", "river", "own_jungle", "enemy_jungle", "base")
            pos = {k: _stats([((d["context"]["jungle"]["ally"] or {}).get("positions") or {}).get(k, 0) for d in ds]) for k in keys}
            take = {k: _stats([((d["context"]["jungle"]["ally"] or {}).get("takedowns_pre15") or {}).get(k, 0) for d in ds]) for k in ("top", "mid", "bot", "river", "jungle")}
            return {
                "minutes_at": {k: v["mean"] for k, v in pos.items() if v},
                "takedowns_pre15": {k: v["mean"] for k, v in take.items() if v},
                **_wr(ds),
            }

        jungle = [d for d in ctx if d["context"]["jungle"].get("ally")]
        out["jungle_presence"] = presence(jungle) if jungle else None
        out["jungle_presence_when_lane_ahead10"] = {
            r: presence(ds)
            for r in ("TOP", "MID", "ADC")
            if (ds := [d for d in jungle if (lane_diff(d, r, "10") or 0) > AHEAD_GOLD / 2])
        }
        out["first_takedown_where"] = dict(
            Counter(((d["context"]["jungle"]["ally"] or {}).get("first_takedown") or {}).get("where") or "ninguna" for d in jungle)
        )
    return out


def summarize(dossiers: list[dict], name_champ=None, name_item=None) -> dict:
    """Lo agregado de un campeón por rol y, dentro de cada rol, por elo (lo que hacen sus OTPs y en qué se diferencian
    sus victorias)."""
    games = len(dossiers)
    wins = sum(1 for d in dossiers if d["win"])
    by_role: dict[str, list[dict]] = defaultdict(list)
    for d in dossiers:
        by_role[d["role"] or "SIN_ROL"].append(d)
    roles = {}
    for role, rows in sorted(by_role.items(), key=lambda kv: -len(kv[1])):
        by_tier: dict[str, list[dict]] = defaultdict(list)
        for d in rows:
            by_tier[d.get("tier") or "?"].append(d)
        roles[role] = {
            "all": _role_block(role, rows, name_champ, name_item) | {"players": len({d.get("puuid") for d in rows})},
            "by_tier": {
                tier: _role_block(role, tier_rows, name_champ, name_item) | {"players": len({d.get("puuid") for d in tier_rows})}
                for tier, tier_rows in sorted(by_tier.items(), key=lambda kv: _tier_key(kv[0]))
            },
        }
    tiers = Counter(d.get("tier") or "?" for d in dossiers)
    patches = Counter(d.get("patch") or "?" for d in dossiers)
    return {
        "games": games,
        "wins": wins,
        "wr": round(100 * wins / games, 1) if games else None,
        "players": len({d.get("puuid") for d in dossiers}),
        "tiers": {t: tiers[t] for t in sorted(tiers, key=_tier_key)},
        "patches": dict(patches.most_common()),
        "with_timeline": sum(1 for d in dossiers if d["analysis"].get("has_timeline")),
        "roles": roles,
    }


README = """# Partidas de OTPs (micro y macro)

Se escribe solo mientras el recolector baja partidas de OTPs (Tempu Drafter). Estructura:

* `<Campeón>/resumen.json`: lo agregado de sus OTPs, **por rol** (`roles.<ROL>.all`) y, dentro, **por elo**
  (`roles.<ROL>.by_tier.CHALLENGER`, `GRANDMASTER`, `MASTER`…): medias de todas las métricas del análisis de
  «Mi progreso», las mismas en victorias y en derrotas, primer objeto y builds más jugadas (con winrate), orden de
  habilidades, runas, hechizos, primer campamento (jungla), rivales y los avisos que el análisis les marca. Se rehace
  solo cada pocos minutos mientras entran partidas.
* `<Campeón>/<ROL>/<servidor>_<Riot ID>_<id>/perfil.json`: el OTP (Riot ID, servidor, elo actual) y sus partidas
  con ese campeón en ese rol, por elo.
* `<Campeón>/<ROL>/<servidor>_<Riot ID>_<id>/partidas.jsonl.gz`: una línea JSON por partida con el dossier entero:
  elo del OTP en ese momento (`tier`, `division`, `lp`), análisis (muertes con contexto, oro, build, jungla,
  objetivos, macro, fase de líneas, series por minuto), build completa (cada compra/venta con su segundo, orden de
  objetos, vueltas a base, objetos finales, habilidades), runas, hechizos, estadísticas de Riot, posición/zona/oro/CS
  minuto a minuto suyo y de su rival, wards y eventos del mapa.

Ids de campeones y objetos de Data Dragon; tiempos en segundos salvo `min` (minutos). La partida y el timeline en bruto
están en la base de datos (`otp_games`): si esta carpeta se borra o cambia de formato, se regenera sola desde ahí.
"""


class Files:
    """La carpeta ``otps``: escribe cada partida al guardarla y mantiene los resúmenes al día (en segundo plano)."""

    _instances: ClassVar[dict[str, Files]] = {}
    _lock: ClassVar[threading.Lock] = threading.Lock()

    def __init__(self, svc, base: Path) -> None:
        self.svc = svc
        self.base = base
        self.lock = threading.RLock()  # escrituras de varios hilos (una región cada uno) y regeneración
        self.dirty: set[int] = set()
        self.last_summary: dict[int, float] = {}
        self.thread: threading.Thread | None = None
        self.force_again = False
        self.state: dict = {"status": "idle"}

    @classmethod
    def of(cls, svc) -> Files | None:
        settings = getattr(svc, "settings", None)
        if settings is None or getattr(settings, "data_dir", None) is None:
            return None
        base = Path(settings.data_dir) / FILES_DIR
        with cls._lock:
            inst = cls._instances.get(str(base))
            if inst is None or inst.svc is not svc:
                inst = cls._instances[str(base)] = cls(svc, base)
            return inst

    # ---------------------------------------------------------- nombres
    def _names(self):
        dd = getattr(self.svc, "dd", None)
        name_champ = (lambda c: dd.name(c)) if dd is not None and hasattr(dd, "name") else None
        catalog = getattr(dd, "item_catalog", None)
        name_item = (lambda i: (catalog.items.get(int(i)) or {}).get("name")) if catalog else None
        return name_champ, name_item

    def _champ_name(self, champ: int) -> str:
        name_champ, _ = self._names()
        return (name_champ(champ) if name_champ else None) or str(champ)

    def _champ_dir(self, root: Path, champ: int) -> Path:
        """Carpeta del campeón por su nombre. Si se creó con el id (Data Dragon aún sin cargar), se renombra."""
        folder = root / _slug(self._champ_name(champ))
        by_id = root / str(champ)
        if folder != by_id and by_id.is_dir() and not folder.exists():
            try:
                by_id.rename(folder)
            except OSError:
                return by_id
        return folder

    def _player_dir(self, root: Path, champ: int, role: str | None, info: dict) -> Path:
        """Carpeta del OTP para ese campeón y rol. Si cambia de nombre se sigue usando la suya (acaba en su id)."""
        role_dir = self._champ_dir(root, champ) / (role or "SIN_ROL")
        puuid = info.get("puuid") or ""
        if role_dir.is_dir():
            for existing in role_dir.iterdir():
                if existing.is_dir() and existing.name.endswith(f"_{puuid[:8]}"):
                    return existing
        riot_id = f"{info.get('game_name') or 'sin_nombre'}#{info.get('tag_line') or ''}"
        return role_dir / f"{_slug((info.get('platform') or '') + '_' + riot_id)}_{puuid[:8]}"

    def _profile(self, folder: Path, champ: int, role: str | None, info: dict) -> None:
        db = self.svc.db
        rows = db.query(
            "SELECT tier, COUNT(*) AS games, SUM(win) AS wins, MIN(game_start) AS first, MAX(game_start) AS last "
            "FROM otp_games WHERE puuid = ? AND champ = ? AND role IS ? GROUP BY tier",
            (info.get("puuid"), champ, role),
        )
        by_tier = {
            r["tier"] or "?": {"games": int(r["games"]), "wins": int(r["wins"] or 0), "wr": round(100 * int(r["wins"] or 0) / int(r["games"]), 1)}
            for r in sorted(rows, key=lambda r: _tier_key(r["tier"] or "?"))
        }
        games = sum(t["games"] for t in by_tier.values())
        wins = sum(t["wins"] for t in by_tier.values())
        platform = info.get("platform")
        profile = {
            "riot_id": f"{info.get('game_name') or 'sin_nombre'}#{info.get('tag_line') or ''}",
            "platform": platform,
            "server": PLATFORMS[platform][1] if platform in PLATFORMS else platform,
            "puuid": info.get("puuid"),
            "champ": champ,
            "champ_name": self._champ_name(champ),
            "role": role,
            "elo_now": {"tier": info.get("tier"), "division": info.get("division"), "lp": info.get("lp")},
            "games": games,
            "wins": wins,
            "wr": round(100 * wins / games, 1) if games else None,
            "by_tier": by_tier,
            "first_game": min((r["first"] or 0 for r in rows), default=None),
            "last_game": max((r["last"] or 0 for r in rows), default=None),
            "updated_at": now_s(),
        }
        tmp = folder / "perfil.json.tmp"
        tmp.write_text(json.dumps(profile, ensure_ascii=False, indent=1), encoding="utf-8")
        tmp.replace(folder / "perfil.json")

    # ---------------------------------------------------------- escritura de cada partida
    def append(self, dossier: dict, info: dict) -> None:
        with self.lock:
            self._ensure_layout_locked()
            folder = self._player_dir(self.base, dossier["champ"], dossier["role"], info)
            folder.mkdir(parents=True, exist_ok=True)
            # Un «miembro» gzip por partida: el fichero se lee entero con cualquier lector de gzip.
            with gzip.open(folder / "partidas.jsonl.gz", "at", encoding="utf-8") as fh:
                fh.write(json.dumps(dossier, ensure_ascii=False, separators=(",", ":")) + "\n")
            self._profile(folder, dossier["champ"], dossier["role"], info)
            self.dirty.add(dossier["champ"])
        self.maybe_summarize()

    def _ensure_layout_locked(self) -> None:
        marker = self.base / ".formato"
        if not self.base.is_dir():
            self.base.mkdir(parents=True, exist_ok=True)
            (self.base / "LEEME.md").write_text(README, encoding="utf-8")
            marker.write_text(str(LAYOUT_VERSION), encoding="utf-8")

    def needs_rebuild(self) -> bool:
        """La carpeta falta, es de otro formato o tiene menos partidas que la base de datos."""
        if not self.svc.db.scalar("SELECT 1 FROM otp_games LIMIT 1"):
            return False
        marker = self.base / ".formato"
        try:
            return marker.read_text(encoding="utf-8").strip() != str(LAYOUT_VERSION)
        except OSError:
            return True

    # ---------------------------------------------------------- resúmenes
    def maybe_summarize(self, force: bool = False) -> None:
        """Rehace en segundo plano el resumen de los campeones con partidas nuevas (como mucho cada 5 min cada uno)."""
        with Files._lock:
            if self.thread is not None and self.thread.is_alive():
                self.force_again = self.force_again or force  # al terminar la tanda en curso, otra con lo pendiente
                return
            now = time.time()
            due = [c for c in list(self.dirty) if force or now - self.last_summary.get(c, 0) >= SUMMARY_EVERY_S]
            if not due:
                return
            self.thread = threading.Thread(target=self._summaries, args=(due,), name="otp-summaries", daemon=True)
            self.thread.start()

    def _summaries(self, champs: list[int]) -> None:
        while champs:
            self._summary_batch(champs)
            with Files._lock:
                champs = list(self.dirty) if self.force_again else []
                self.force_again = False

    def _summary_batch(self, champs: list[int]) -> None:
        for champ in champs:
            try:
                self.write_summary(champ)
            except Exception:
                log.exception("No se pudo rehacer el resumen de OTPs del campeón %s", champ)

    def write_summary(self, champ: int, root: Path | None = None) -> dict:
        self.dirty.discard(champ)
        self.last_summary[champ] = time.time()
        dossiers = []
        for r in self.svc.db.query("SELECT puuid, dossier FROM otp_games WHERE champ = ?", (champ,)):
            d = unpack(r["dossier"])
            if d:
                d["puuid"] = r["puuid"]
                dossiers.append(d)
        name_champ, name_item = self._names()
        summary = {
            "champ": champ,
            "champ_name": self._champ_name(champ),
            "updated_at": now_s(),
            **summarize(dossiers, name_champ, name_item),
        }
        with self.lock:  # no mientras se regenera la carpeta
            folder = self._champ_dir(root or self.base, champ)
            folder.mkdir(parents=True, exist_ok=True)
            tmp = folder / "resumen.json.tmp"
            tmp.write_text(json.dumps(summary, ensure_ascii=False, indent=1), encoding="utf-8")
            tmp.replace(folder / "resumen.json")
        return summary

    # ---------------------------------------------------------- regenerar desde la base de datos
    def rebuild_in_background(self) -> dict:
        if self.state.get("status") == "running":
            return self.state
        self.state = {"status": "running", "started_at": time.time(), "done": 0, "total": 0}
        threading.Thread(target=self._rebuild_thread, name="otp-files", daemon=True).start()
        return self.state

    def _rebuild_thread(self) -> None:
        try:
            self.state = {"status": "done", "finished_at": time.time(), **self.rebuild()}
        except Exception as exc:
            log.exception("Error regenerando la carpeta de OTPs")
            self.state = {"status": "error", "error": str(exc)}

    def rebuild(self) -> dict:
        """Reescribe la carpeta entera desde ``otp_games`` (las escrituras nuevas esperan a que termine)."""
        db = self.svc.db
        with self.lock:
            tmp = self.base.with_name(self.base.name + ".tmp")
            shutil.rmtree(tmp, ignore_errors=True)
            tmp.mkdir(parents=True)
            infos = {
                r["puuid"]: dict(r)
                for r in db.query(
                    "SELECT puuid, platform, tier, division, lp, game_name, tag_line FROM players "
                    "WHERE puuid IN (SELECT DISTINCT puuid FROM otp_games)"
                )
            }
            groups = db.query("SELECT champ, role, puuid, COUNT(*) AS n FROM otp_games GROUP BY champ, role, puuid")
            total = sum(int(g["n"]) for g in groups)
            done = 0
            for g in groups:
                info = infos.get(g["puuid"]) or {"puuid": g["puuid"]}
                folder = self._player_dir(tmp, int(g["champ"]), g["role"], info)
                folder.mkdir(parents=True, exist_ok=True)
                with gzip.open(folder / "partidas.jsonl.gz", "wt", encoding="utf-8") as fh:
                    for r in db.query(
                        "SELECT dossier FROM otp_games WHERE champ = ? AND role IS ? AND puuid = ? ORDER BY game_start",
                        (g["champ"], g["role"], g["puuid"]),
                    ):
                        d = unpack(r["dossier"])
                        if d:
                            fh.write(json.dumps(d, ensure_ascii=False, separators=(",", ":")) + "\n")
                        done += 1
                        if done % 50 == 0:
                            self.state.update(done=done, total=total)
                self._profile(folder, int(g["champ"]), g["role"], info)
            champs = sorted({int(g["champ"]) for g in groups})
            for champ in champs:
                self.write_summary(champ, tmp)
            (tmp / "LEEME.md").write_text(README, encoding="utf-8")
            (tmp / ".formato").write_text(str(LAYOUT_VERSION), encoding="utf-8")
            old = self.base.with_name(self.base.name + ".old")
            shutil.rmtree(old, ignore_errors=True)
            if self.base.exists():
                self.base.rename(old)
            tmp.rename(self.base)
            shutil.rmtree(old, ignore_errors=True)
            self.dirty.clear()
        log.info("Carpeta de OTPs regenerada (%s): %d partidas de %d campeones.", self.base, total, len(champs))
        return {"path": str(self.base), "games": total, "folders": len(groups), "champions": len(champs)}


def ensure_files(svc) -> None:
    """Al arrancar el recolector: si la carpeta falta o es de otro formato y hay partidas, se regenera sola."""
    files = Files.of(svc)
    if files is not None and files.needs_rebuild():
        files.rebuild_in_background()


def flush(svc) -> None:
    """Al parar el recolector: los resúmenes pendientes se rehacen ya."""
    files = Files.of(svc)
    if files is not None:
        files.maybe_summarize(force=True)
