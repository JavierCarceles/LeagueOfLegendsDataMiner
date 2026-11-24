"""Búsqueda por partidas: ventanas tipo minidraft con matchups o composiciones.

Cada **ventana** es una partida a buscar: campeones del equipo A y del equipo B (el lado, azul o rojo, da igual).

- **Matchup** (``kind="matchup"``): cada campeón en su rol. La partida cuenta si están todos los enfrentamientos de la
  ventana a la vez: con Lee Sin contra Hecarim en jungla y Ahri contra Viktor en mid, solo las partidas con los dos.
  Varias ventanas se buscan por separado (una partida cuenta en cada ventana con la que encaje).
- **Composición** (``kind="comp"``): campeones del mismo equipo, con rol o sin él, y cuántos tienen que estar como
  mínimo (``min_a``/``min_b``; todos = la misma composición, menos = composiciones parecidas). Se ve quién gana.

El elo no filtra: se busca en todos los elos y en los servidores elegidos. Los jugadores que se visitan son los de esos
campeones (OTPs, mucha maestría o cualquiera que los juegue); cada partida que se baja se guarda en la base de datos
(sirve para todo lo demás) y, si encaja con una ventana, se baja su timeline y se guarda **entera** en
``data/busquedas/<ventana>/partidas.jsonl.gz``: los 10 jugadores (campeón, rol, equipo, objetos, runas, estadísticas de
Riot) con el análisis minuto a minuto de cada uno, en el mismo formato que las carpetas de OTPs. Así los demás matchups
y composiciones de esas partidas sirven para comprobar después el análisis general (``iter_dossiers``).

Al arrancar también se buscan en las partidas ya guardadas (``scan_stored``): las que encajan se completan con su
timeline sin tener que dar con ellas otra vez.
"""

from __future__ import annotations

import gzip
import hashlib
import json
import re
import threading
import time
import unicodedata
from pathlib import Path

from app.logbus import get_logger

log = get_logger("busquedas")

ROLES = ("TOP", "JUNGLE", "MID", "ADC", "SUPPORT")
KINDS = ("matchup", "comp")
MAX_WINDOWS = 20
MAX_TEAM = 5
DIR = "busquedas"
FILE = "partidas.jsonl.gz"
SUMMARY = "resumen.json"
VERSION = 1
# De cada jugador no se guarda lo que es igual para los 10 (va una vez por partida) ni lo que solo usan las páginas de
# OTPs (el oro y la experiencia de cada minuto suyo y de su rival).
DROP_FROM_PLAYER = ("frames", "opponent_frames", "map_events", "context", "teams_by_pid")
SCAN_CHUNK = 400


# ------------------------------------------------------------------ ventanas
def _slot(raw, need_role: bool) -> dict | None:
    if not isinstance(raw, dict):
        return None
    try:
        champ = int(raw.get("champ") or 0)
    except (TypeError, ValueError):
        return None
    role = str(raw.get("role") or "").upper() or None
    if role not in ROLES:
        role = None
    if champ <= 0 or (need_role and role is None):
        return None
    return {"champ": champ, "role": role}


def window_id(w: dict) -> str:
    """Identificador estable: el mismo contenido da el mismo id (el nombre no cuenta)."""
    canon = {
        "kind": w["kind"],
        "a": sorted((s["champ"], s["role"] or "") for s in w["a"]),
        "b": sorted((s["champ"], s["role"] or "") for s in w["b"]),
        "min_a": w["min_a"],
        "min_b": w["min_b"],
    }
    return hashlib.sha1(json.dumps(canon, sort_keys=True).encode()).hexdigest()[:10]


def _team(raw_slots, need_role: bool, used: set[int]) -> list[dict]:
    """Los campeones de un equipo de la ventana (como mucho 5, sin repetir campeón ni, con rol, rol)."""
    slots: list[dict] = []
    roles: set[str] = set()
    for raw in (raw_slots or [])[: MAX_TEAM * 2]:
        slot = _slot(raw, need_role)
        if slot is None or slot["champ"] in used or (slot["role"] and slot["role"] in roles):
            continue
        if len(slots) >= MAX_TEAM:
            break
        used.add(slot["champ"])
        if slot["role"]:
            roles.add(slot["role"])
        slots.append(slot)
    return sorted(slots, key=lambda s: (ROLES.index(s["role"]) if s["role"] else 9, s["champ"]))


def _minimum(item: dict, side: str, n: int) -> int:
    """Cuántos campeones de ese equipo tienen que estar (composiciones): entre 1 y todos; 0 si el equipo está vacío."""
    if not n:
        return 0
    try:
        want = int(item.get(f"min_{side}") or n)
    except (TypeError, ValueError):
        want = n
    return max(1, min(n, want))


def _window(item) -> dict | None:
    """Una ventana limpia (o ``None`` si no tiene ningún campeón válido)."""
    if not isinstance(item, dict):
        return None
    kind = item.get("kind") if item.get("kind") in KINDS else "matchup"
    used: set[int] = set()
    a = _team(item.get("a"), kind == "matchup", used)
    b = _team(item.get("b"), kind == "matchup", used)
    if not a and not b:
        return None
    comp = kind == "comp"
    w = {
        "kind": kind,
        "name": str(item.get("name") or "").strip()[:80],
        "a": a,
        "b": b,
        "min_a": _minimum(item, "a", len(a)) if comp else len(a),
        "min_b": _minimum(item, "b", len(b)) if comp else len(b),
    }
    w["id"] = window_id(w)
    return w


def clean(raw) -> list[dict]:
    """Ventanas válidas (como mucho ``MAX_WINDOWS``, sin repetidas). Un campeón no puede salir dos veces en la misma
    ventana (en una partida clasificatoria solo lo juega uno) y en un matchup cada rol de cada equipo, una vez."""
    out: list[dict] = []
    seen: set[str] = set()
    for item in raw or []:
        w = _window(item)
        if w is None or w["id"] in seen:
            continue
        seen.add(w["id"])
        out.append(w)
        if len(out) >= MAX_WINDOWS:
            break
    return out


def champions(windows: list[dict]) -> list[int]:
    """Todos los campeones de las ventanas (los jugadores que se visitan son de estos campeones)."""
    return list(dict.fromkeys(s["champ"] for w in windows for side in ("a", "b") for s in w[side]))


def champion_roles(windows: list[dict]) -> dict[int, list[str]]:
    """Rol de cada campeón en las ventanas (si en alguna va sin rol, cualquiera: sin entrada)."""
    roles: dict[int, set[str] | None] = {}
    for w in windows:
        for side in ("a", "b"):
            for s in w[side]:
                if s["role"] is None:
                    roles[s["champ"]] = None
                elif roles.get(s["champ"], set()) is not None:
                    roles.setdefault(s["champ"], set()).add(s["role"])
    return {c: [r for r in ROLES if r in rs] for c, rs in roles.items() if rs is not None and len(rs) < len(ROLES)}


def _matchup_label(w: dict, name) -> str:
    by_role: dict[str, list] = {}
    for side, k in (("a", 0), ("b", 1)):
        for s in w[side]:
            by_role.setdefault(s["role"], [None, None])[k] = s["champ"]
    parts = []
    for role in ROLES:
        if role not in by_role:
            continue
        a, b = (name(c) if c else "cualquiera" for c in by_role[role])
        parts.append(f"{a} vs {b} ({ROLE_NAMES[role]})")
    return " · ".join(parts)


def _comp_label(w: dict, name) -> str:
    def team(side: str) -> str:
        return " + ".join(name(s["champ"]) for s in w[side]) or "cualquiera"

    return f"{team('a')} vs {team('b')}"


def label(w: dict, name_of=None) -> str:
    """«Lee Sin vs Hecarim (jungla) · Ahri vs Viktor (mid)» o «Yasuo + Malphite vs Diana»."""
    name = name_of or str
    return _matchup_label(w, name) if w["kind"] == "matchup" else _comp_label(w, name)


ROLE_NAMES = {"TOP": "top", "JUNGLE": "jungla", "MID": "mid", "ADC": "ADC", "SUPPORT": "support"}


def _slug(text: str) -> str:
    text = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode()
    return re.sub(r"[^a-zA-Z0-9]+", "-", text).strip("-").lower()


def folder_name(w: dict, name_of=None) -> str:
    base = _slug(w.get("name") or label(w, name_of))[:70].strip("-") or w["kind"]
    return f"{base}_{w['id']}"


# ------------------------------------------------------------------ ¿encaja?
def lineups(rows) -> dict[int, dict]:
    """{equipo: {"roles": {rol: campeón}, "champs": {campeones}}} de filas (equipo, rol, campeón)."""
    teams: dict[int, dict] = {}
    for team, role, champ in rows:
        t = teams.setdefault(int(team), {"roles": {}, "champs": set()})
        t["champs"].add(int(champ))
        if role:
            t["roles"][role] = int(champ)
    return teams


def _hits(slots: list[dict], team: dict) -> int:
    return sum(1 for s in slots if (team["roles"].get(s["role"]) == s["champ"] if s["role"] else s["champ"] in team["champs"]))


def match_window(w: dict, teams: dict[int, dict]) -> dict | None:
    """¿La partida encaja con la ventana? El equipo A puede ser el azul o el rojo (el que más coincide)."""
    best = None
    for side_a in (100, 200):
        ta, tb = teams.get(side_a), teams.get(300 - side_a)
        if ta is None or tb is None:
            continue
        ha, hb = _hits(w["a"], ta), _hits(w["b"], tb)
        if ha >= w["min_a"] and hb >= w["min_b"] and (best is None or ha + hb > best["hits"]):
            best = {"side_a": side_a, "hits": ha + hb, "exact": ha == len(w["a"]) and hb == len(w["b"])}
    return best


def match_record(windows: list[dict], record) -> list[tuple[dict, dict]]:
    """Ventanas con las que encaja una partida ya leída (``parse_match``): [(ventana, cómo encaja)]."""
    if not windows or record is None or not record.is_rift or record.is_remake:
        return []
    teams = lineups((p.team, p.role, p.champ) for p in record.participants)
    out = []
    for w in windows:
        res = match_window(w, teams)
        if res is not None:
            out.append((w, res))
    return out


# ------------------------------------------------------------------ guardar
def done(db, match_id: str, window: str) -> bool:
    return bool(db.scalar("SELECT analyzed FROM pattern_games WHERE match_id = ? AND window_id = ?", (match_id, window)))


def _team_info(match: dict) -> dict:
    info = match.get("info") or {}
    out = {}
    for t in info.get("teams") or []:
        objectives = {k: (v or {}).get("kills") for k, v in (t.get("objectives") or {}).items()}
        first = [k for k, v in (t.get("objectives") or {}).items() if (v or {}).get("first")]
        out[str(t.get("teamId"))] = {
            "win": bool(t.get("win")),
            "bans": [b.get("championId") for b in t.get("bans") or []],
            "objectives": objectives,
            "first": first,
        }
    return out


class Results:
    """Ficheros de resultados (``data/busquedas``) y su resumen por ventana. Uno por carpeta de datos."""

    _instances: dict[str, Results] = {}
    _lock = threading.Lock()

    def __init__(self, base: Path) -> None:
        self.base = base
        self.lock = threading.Lock()

    @classmethod
    def of(cls, svc) -> Results | None:
        data_dir = getattr(getattr(svc, "settings", None), "data_dir", None)
        if data_dir is None:
            return None
        key = str(Path(data_dir).resolve())
        with cls._lock:
            inst = cls._instances.get(key)
            if inst is None:
                inst = cls._instances[key] = Results(Path(data_dir) / DIR)
            return inst

    def _readme(self) -> None:
        path = self.base / "LEEME.md"
        if path.exists():
            return
        self.base.mkdir(parents=True, exist_ok=True)
        path.write_text(
            "# Búsquedas por partida (matchups y composiciones)\n\n"
            "Una carpeta por ventana de búsqueda. `resumen.json`: la ventana (campeones del equipo A y del B, con su rol), "
            "cuántas partidas encajan, cuántas gana el equipo A (por lado y por elo) y, en composiciones, las iguales "
            "(todos los campeones) frente a las parecidas.\n\n"
            "`partidas.jsonl.gz`: una partida por línea con los 10 jugadores (`players`), cada uno con el mismo formato que "
            "las carpetas de OTPs (análisis minuto a minuto, recorrido, estadísticas y retos de Riot, objetos y runas). "
            "`side_a`: el equipo (100 azul, 200 rojo) que hace de equipo A; `win_a`: si ganó. El elo (`tier`) es el del "
            "jugador por el que se encontró la partida (el emparejamiento junta elos parecidos).\n",
            encoding="utf-8",
        )

    def folder(self, w: dict, name_of=None) -> Path:
        for existing in self.base.glob(f"*_{w['id']}"):
            if existing.is_dir():
                return existing
        return self.base / folder_name(w, name_of)

    def append(self, w: dict, game: dict, name_of=None) -> None:
        line = (json.dumps(game, ensure_ascii=False, separators=(",", ":")) + "\n").encode("utf-8")
        with self.lock:
            self._readme()
            folder = self.folder(w, name_of)
            folder.mkdir(parents=True, exist_ok=True)
            with gzip.open(folder / FILE, "ab") as fh:
                fh.write(line)
            self._update_summary(folder, w, game, name_of)

    def _update_summary(self, folder: Path, w: dict, game: dict, name_of=None) -> None:
        path = folder / SUMMARY
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            data = {}
        if data.get("window", {}).get("id") != w["id"]:
            data = {"version": VERSION, "games": 0, "wins_a": 0, "exact": 0, "exact_wins_a": 0, "by_side": {}, "by_tier": {}}
        data["window"] = w
        data["label"] = label(w, name_of)
        win = bool(game.get("win_a"))
        data["games"] += 1
        data["wins_a"] += win
        if game.get("exact"):
            data["exact"] += 1
            data["exact_wins_a"] += win
        side = "azul" if game.get("side_a") == 100 else "rojo"
        s = data["by_side"].setdefault(side, [0, 0])
        s[0] += 1
        s[1] += win
        t = data["by_tier"].setdefault(game.get("tier") or "?", [0, 0])
        t[0] += 1
        t[1] += win
        data["winrate_a"] = round(100 * data["wins_a"] / data["games"], 1)
        data["updated_at"] = int(time.time())
        tmp = path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
        tmp.replace(path)


def game_record(match: dict, timeline: dict | None, tier: str | None, catalog=None) -> dict | None:
    """La partida entera: los 10 jugadores con su análisis (mismo formato que las carpetas de OTPs, sin lo repetido)."""
    from app.ingest import otp_details

    info = match.get("info") or {}
    players = []
    shared: dict = {}
    for p in info.get("participants") or []:
        puuid = p.get("puuid")
        if not puuid:
            continue
        try:
            dossier = otp_details.build_dossier(match, timeline, puuid, catalog, elo={"tier": tier})
        except Exception:  # noqa: BLE001  (un jugador raro no tira la partida entera)
            log.debug("No se pudo analizar a un jugador de %s", (match.get("metadata") or {}).get("matchId"), exc_info=True)
            continue
        team = str(dossier.get("team"))
        if "map_events" not in shared:
            shared["map_events"] = dossier.get("map_events")
        shared.setdefault("context", {}).setdefault(team, dossier.get("context"))
        for k in DROP_FROM_PLAYER:
            dossier.pop(k, None)
        dossier["puuid"] = puuid
        players.append(dossier)
    if not players:
        return None
    first = players[0]
    return {
        "version": VERSION,
        "match_id": first["match_id"],
        "platform": first["platform"],
        "patch": first["patch"],
        "game_start": first["game_start"],
        "duration": first["duration"],
        "queue": info.get("queueId"),
        "tier": tier,
        "has_timeline": timeline is not None,
        "teams": _team_info(match),
        **shared,
        "players": players,
    }


def save_game(svc, match: dict, timeline: dict | None, found: list[tuple[dict, dict]], tier: str | None) -> int:
    """Guarda la partida en la carpeta de cada ventana con la que encaja y en ``pattern_games``. Devuelve en cuántas."""
    from app.ingest import otp_details

    if not found:
        return 0
    results = Results.of(svc)
    dd = getattr(svc, "dd", None)
    name_of = getattr(dd, "name", None)
    game = game_record(match, timeline, tier, otp_details.catalog_for(svc, match))
    if game is None:
        return 0
    try:
        otp_details.save_raw(svc, game["match_id"], match, timeline)  # en local, para re-analizar sin volver a bajarla
    except Exception:  # noqa: BLE001
        log.debug("No se pudo guardar el bruto de %s", game["match_id"], exc_info=True)
    saved = 0
    winner = next((int(t) for t, v in game["teams"].items() if v.get("win")), None)
    for w, res in found:
        record = {**game, "window": w["id"], "side_a": res["side_a"], "win_a": winner == res["side_a"], "hits": res["hits"], "exact": res["exact"]}
        if results is not None:
            try:
                results.append(w, record, name_of)
            except OSError:
                log.exception("No se pudo escribir la partida %s de la búsqueda", game["match_id"])
                continue
        with svc.db.write() as conn:
            conn.execute(
                "INSERT OR REPLACE INTO pattern_games (match_id, window_id, side_a, win_a, hits, exact, platform, tier, "
                "game_start, analyzed, found_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 1, ?)",
                (game["match_id"], w["id"], res["side_a"], int(record["win_a"]), res["hits"], int(res["exact"]),
                 game["platform"], tier, game["game_start"], int(time.time())),
            )
        saved += 1
    return saved


# ------------------------------------------------------------------ partidas ya guardadas
def _anchor_matches(db, w: dict, counts: dict[int, int], cond: list[str], params: list) -> list:
    """Partidas guardadas con el campeón menos jugado de la ventana (en su rol, si lo tiene) que aún no están en sus
    resultados."""
    slots = [s for side in ("a", "b") for s in w[side]]
    anchor = min(slots, key=lambda s: (counts.get(s["champ"], 0), s["role"] is None))
    role_sql = " AND p.role = ?" if anchor["role"] else ""
    return db.query(
        f"SELECT p.match_id, m.platform, m.tier, m.game_start FROM participants p JOIN matches m ON m.match_id = p.match_id "
        f"WHERE p.champ = ?{role_sql} AND {' AND '.join(cond)} "
        f"AND p.match_id NOT IN (SELECT match_id FROM pattern_games WHERE window_id = ? AND analyzed = 1)",
        (anchor["champ"], *([anchor["role"]] if anchor["role"] else []), *params, w["id"]),
    )


def _lineups_of(db, ids: list[str]) -> dict[str, list]:
    rows: dict[str, list] = {}
    for r in db.query(f"SELECT match_id, team, role, champ FROM participants WHERE match_id IN ({','.join('?' * len(ids))})", ids):
        rows.setdefault(r["match_id"], []).append((r["team"], r["role"], r["champ"]))
    return rows


def scan_stored(db, windows: list[dict], platforms: list[str] | None = None, queues: tuple[int, ...] = (420,)) -> list[dict]:
    """Partidas de la base de datos que encajan con alguna ventana y aún no están en sus resultados (las más recientes
    primero): [{"match_id", "platform", "tier", "found": [(ventana, cómo encaja)]}]. Por cada ventana se miran solo
    las partidas de su campeón menos jugado (el ancla); la base de datos ya las tiene, solo falta su timeline."""
    if not windows:
        return []
    champs = champions(windows)
    marks = ",".join("?" * len(champs))
    counts = {int(r[0]): int(r[1]) for r in db.query(f"SELECT champ, COUNT(*) FROM participants WHERE champ IN ({marks}) GROUP BY champ", champs)}
    cond, params = ["m.is_remake = 0", f"m.queue IN ({','.join('?' * len(queues))})"], list(queues)
    if platforms:
        cond.append(f"m.platform IN ({','.join('?' * len(platforms))})")
        params.extend(platforms)
    candidates: dict[str, dict] = {}
    for w in windows:
        for r in _anchor_matches(db, w, counts, cond, params):
            c = candidates.setdefault(r["match_id"], {"match_id": r["match_id"], "platform": r["platform"], "tier": r["tier"],
                                                      "game_start": int(r["game_start"] or 0), "windows": set()})
            c["windows"].add(w["id"])
    by_id = {w["id"]: w for w in windows}
    ids = list(candidates)
    out = []
    for i in range(0, len(ids), SCAN_CHUNK):
        lineups_by_match = _lineups_of(db, ids[i : i + SCAN_CHUNK])
        for mid, rows in lineups_by_match.items():
            teams = lineups(rows)
            c = candidates[mid]
            found = [(by_id[wid], res) for wid in c["windows"] if (res := match_window(by_id[wid], teams)) is not None]
            if found:
                out.append({"match_id": mid, "platform": c["platform"], "tier": c["tier"], "game_start": c["game_start"], "found": found})
    out.sort(key=lambda c: -c["game_start"])
    return out


# ------------------------------------------------------------------ leer
def iter_games(data_dir: Path):
    """Cada partida guardada por las búsquedas (una vez, aunque encaje con varias ventanas)."""
    seen: set[str] = set()
    for f in sorted(Path(data_dir, DIR).glob(f"*/{FILE}")):
        try:
            with gzip.open(f, "rt", encoding="utf-8") as fh:
                for line in fh:
                    try:
                        game = json.loads(line)
                    except ValueError:
                        continue  # una línea a medio escribir
                    if game.get("match_id") in seen:
                        continue
                    seen.add(game.get("match_id"))
                    yield game
        except (OSError, EOFError):
            continue


def iter_dossiers(data_dir: Path):
    """Los 10 jugadores de cada partida de las búsquedas, como las partidas de las carpetas de OTPs (para el perfil por
    elo y la auditoría de la calibración): (jugador, dossier)."""
    for game in iter_games(data_dir):
        shared = {k: game.get(k) for k in ("map_events",)}
        for p in game.get("players") or []:
            ctx = (game.get("context") or {}).get(str(p.get("team")))
            yield p.get("puuid") or "", {**shared, "context": ctx, **p}


def files(data_dir: Path) -> list[Path]:
    return sorted(Path(data_dir, DIR).glob(f"*/{FILE}"))
