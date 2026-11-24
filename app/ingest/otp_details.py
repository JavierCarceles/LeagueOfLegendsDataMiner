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
import io
import json
import os
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
FILES_DIR = "otps"  # solo OTPs: lo que usan «Tú frente a los OTPs», sus resúmenes, la página de OTPs y el draft
PLAYERS_DIR = "partidas_campeon"  # cualquier otro jugador («Cualquiera que lo juegue»): no son datos de OTPs
KIND_DIRS = {"otp": FILES_DIR, "player": PLAYERS_DIR}
# Quién cuenta como OTP de un campeón al clasificar una partida: su campeón principal, con al menos estos puntos y
# 1,5 veces los del segundo; o estar en la tabla de los mejores de ese campeón (OP.GG).
OTP_RATIO = 1.5
OTP_MIN_POINTS = 100_000
# Con partidas suyas revisadas, lo que manda es lo que juega: OTP si al menos la mitad de ellas son con ese campeón.
# La maestría es de toda la vida de la cuenta y engaña (300 K con un campeón y 130 K con el siguiente no es ser OTP).
OTP_MIN_SHARE = 0.5
OTP_SHARE_MIN_SEEN = 20
LAYOUT_VERSION = 4  # formato de la carpeta: si cambia, se regenera desde la base de datos (2: objetos según el rival; 3: resúmenes sin partidas con AFK; 4: los que no son OTP, a otra carpeta)
# Campeones que se curan mucho (los que justifican Heridas graves): para separar los objetos de los OTPs contra ellos.
HEAVY_HEAL = {
    "Aatrox", "Soraka", "Yuumi", "Vladimir", "Sylas", "Warwick", "Dr. Mundo", "Swain", "Briar", "Illaoi", "Nami",
    "Sona", "Seraphine", "Olaf", "Trundle", "Volibear", "Gwen", "Nasus", "Maokai", "Zac", "Fiddlesticks", "Kayn",
    "Senna", "Milio", "Renata Glasc", "Viego", "Darius", "Sett", "Irelia", "Aphelios", "Samira", "Kindred",
}
# El resumen de un campeón se rehace como mucho cada 30 min mientras entran partidas (al parar el recolector, ya). Cada
# uno relee todas sus partidas analizadas: con 173 campeones y ~30.000 partidas, cada 5 min se comía la CPU que
# necesitan las descargas y el análisis.
# Resumen de cada campeón como mucho cada hora: con los 173 campeones elegidos, cada 30 min eran ~6 por minuto, cada uno
# descomprimiendo todas sus partidas; esa CPU la perdían los hilos que guardan (con el candado cogido más rato, 24-11-2026).
SUMMARY_EVERY_S = 3600
SUMMARY_YIELD_EVERY = 50  # al leer las partidas de un campeón, cede el procesador cada 50 (el recolector va antes)
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
    # Solo su recorrido rodea muros: de los otros 9 no se guarda ni se usa nada que dependa de ello (y era la mayor parte
    # del tiempo de análisis de cada partida del recolector).
    analysis = analyze_game(match, timeline, puuid=puuid, items=items, route_all=False)
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


def play_share(db, puuid: str, champ: int) -> tuple[int, float] | None:
    """(partidas revisadas, parte con ese campeón) de un jugador del recolector, o ``None`` sin muestra suficiente."""
    try:
        row = db.one("SELECT seen FROM otp_progress WHERE puuid = ?", (puuid,))
    except Exception:  # noqa: BLE001  (BD sin la tabla: tests o versión vieja)
        return None
    seen = int((row["seen"] if row else 0) or 0)
    if seen < OTP_SHARE_MIN_SEEN:
        return None
    stored = int(
        db.scalar(
            "SELECT COUNT(DISTINCT pa.match_id) FROM participants pa JOIN players pl ON pl.id = pa.player_id "
            "WHERE pl.puuid = ? AND pa.champ = ?",
            (puuid, champ),
        )
        or 0
    )
    analysed = int(db.scalar("SELECT COUNT(DISTINCT match_id) FROM otp_games WHERE puuid = ? AND champ = ?", (puuid, champ)) or 0)
    return seen, min(1.0, max(stored, analysed) / seen)


def classify(db, puuid: str, champ: int) -> str:
    """``otp`` si ese jugador es OTP de ese campeón; si no, ``player``.

    Con al menos ``OTP_SHARE_MIN_SEEN`` partidas suyas revisadas, por lo que juega: la mitad o más con ese campeón.
    Sin esa muestra, por su maestría guardada (principal con 1,5 veces los puntos del segundo) o la tabla de los mejores
    de OP.GG. Sin nada, ``player``: ante la duda, no ensucia los datos de OTPs."""
    share = play_share(db, puuid, champ)
    if share is not None:
        return "otp" if share[1] >= OTP_MIN_SHARE else "player"
    if db.scalar("SELECT 1 FROM otp_sources WHERE puuid = ? AND champ = ? LIMIT 1", (puuid, champ)):
        return "otp"
    raw = db.scalar("SELECT top FROM mastery_checks WHERE puuid = ?", (puuid,))
    try:
        top = [e for e in json.loads(raw or "[]") if len(e) >= 2]
    except (TypeError, ValueError):
        top = []
    if not top or int(top[0][0]) != int(champ):
        return "player"
    points, second = int(top[0][1]), int(top[1][1]) if len(top) > 1 else 0
    return "otp" if points >= OTP_MIN_POINTS and points >= OTP_RATIO * second else "player"


def reclassify(db) -> list[tuple[str, int]]:
    """Vuelve a clasificar (OTP o jugador) a quien ya tiene partidas revisadas suficientes: con lo que juega de verdad.
    Devuelve las parejas (jugador, campeón) que cambiaron de carpeta. Sin llamadas a Riot."""
    try:
        pairs = db.query(
            "SELECT DISTINCT g.puuid, g.champ, g.kind FROM otp_games g JOIN otp_progress o ON o.puuid = g.puuid "
            "WHERE o.seen >= ? AND g.kind IS NOT NULL",
            (OTP_SHARE_MIN_SEEN,),
        )
    except Exception:  # noqa: BLE001  (BD vieja sin otp_progress)
        return []
    changes = []
    for r in pairs:
        kind = classify(db, r["puuid"], int(r["champ"]))
        if kind != r["kind"]:
            changes.append((kind, r["puuid"], int(r["champ"])))
    if changes:
        with db.write() as conn:
            conn.executemany("UPDATE otp_games SET kind = ? WHERE puuid = ? AND champ = ?", changes)
        log.info(
            "Reclasificados por lo que juegan de verdad: %d jugador-campeón (%d dejan de contar como OTP).",
            len(changes), sum(1 for k, _p, _c in changes if k == "player"),
        )
    return [(p, c) for _k, p, c in changes]


def classify_pending(db) -> int:
    """Marca como OTP o jugador las partidas guardadas sin marca (de antes de separarlas). Sin llamadas a Riot."""
    pairs = db.query("SELECT DISTINCT puuid, champ FROM otp_games WHERE kind IS NULL")
    if not pairs:
        return 0
    kinds = [(classify(db, r["puuid"], int(r["champ"])), r["puuid"], int(r["champ"])) for r in pairs]
    with db.write() as conn:
        conn.executemany("UPDATE otp_games SET kind = ? WHERE puuid = ? AND champ = ? AND kind IS NULL", kinds)
    moved = sum(1 for k, _p, _c in kinds if k == "player")
    log.info("Partidas analizadas clasificadas: %d jugadores (%d OTPs, %d jugadores normales).", len(kinds), len(kinds) - moved, moved)
    return len(kinds)


def save(
    db, svc, match: dict, timeline: dict | None, puuid: str, elo: dict | None = None, *, write_files: bool = True, kind: str | None = None
) -> bool:
    """Analiza y guarda la partida (con su bruto) y la escribe en su carpeta: ``otps`` si el jugador es OTP de ese
    campeón (``kind="otp"``, o lo dice ``classify``), ``partidas_campeon`` si no. ``False`` si no está en ella."""
    try:
        dossier = build_dossier(match, timeline, puuid, catalog_for(svc, match), elo=elo)
    except NotInMatch:
        return False
    kind = kind if kind in KIND_DIRS else classify(db, puuid, dossier["champ"])
    is_new = not db.scalar("SELECT 1 FROM otp_games WHERE match_id = ? AND puuid = ?", (dossier["match_id"], puuid))
    # La partida y el timeline en bruto (~400 KB por partida) van a un fichero (``raw_path``), no a la base de datos:
    # en ella llenaban el registro de escrituras (WAL: 1,25 GB en 7 min) y cada escritura tardaba ~1 s. Sin ``svc`` (o
    # sin carpeta), como antes. Se comprime antes de coger el candado de escritura.
    if save_raw(svc, dossier["match_id"], match, timeline):
        packed = None, None, pack(dossier)
    else:
        packed = pack(match), pack(timeline), pack(dossier)
    with db.write() as conn:
        conn.execute(
            "INSERT OR REPLACE INTO otp_games (match_id, puuid, champ, role, win, game_start, patch, platform, tier, "
            "division, lp, has_timeline, version, match_raw, timeline_raw, dossier, fetched_at, kind) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                dossier["match_id"], puuid, dossier["champ"], dossier["role"], int(bool(dossier["win"])),
                dossier["game_start"], dossier["patch"], dossier["platform"], dossier["tier"], dossier["division"],
                dossier["lp"], int(timeline is not None), DOSSIER_VERSION * 1000 + ANALYSIS_VERSION, *packed,
                now_s(), kind,
            ),
        )
    if is_new:
        _note_live(db, dossier["tier"], kind, timeline is not None)
    if is_new and write_files:
        files = Files.of(svc, kind)
        if files is not None:
            try:
                files.append(dossier, player_info(db, puuid))
            except OSError:
                log.exception("No se pudo escribir la partida %s en su carpeta", dossier["match_id"])
    return True


RAW_DIR = "raw_otp"  # data/raw_otp/<plataforma>/<id>.json.gz (no se sube a GitHub: .gitignore)


def raw_path(svc, match_id: str) -> Path | None:
    data_dir = getattr(getattr(svc, "settings", None), "data_dir", None)
    if not data_dir or not match_id:
        return None
    platform = match_id.split("_", 1)[0].lower() or "otros"
    return Path(data_dir) / RAW_DIR / platform / f"{match_id}.json.gz"


def save_raw(svc, match_id: str, match: dict, timeline: dict | None) -> bool:
    """Guarda la partida y su timeline en bruto en su fichero (una vez por partida aunque la jueguen varios OTPs)."""
    path = raw_path(svc, match_id)
    if path is None:
        return False
    try:
        if path.exists():
            return True
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(f".{threading.get_ident()}.tmp")
        with gzip.open(tmp, "wt", encoding="utf-8", compresslevel=5) as fh:
            json.dump({"match": match, "timeline": timeline}, fh, ensure_ascii=False, separators=(",", ":"))
        os.replace(tmp, path)
        return True
    except OSError:
        log.debug("No se pudo guardar la partida en bruto %s", match_id, exc_info=True)
        return False


def load_raw(db, svc, match_id: str, puuid: str) -> tuple[dict | None, dict | None]:
    """La partida y el timeline en bruto de una partida guardada: de la base de datos (las de antes) o de su fichero."""
    row = db.one("SELECT match_raw, timeline_raw FROM otp_games WHERE match_id = ? AND puuid = ?", (match_id, puuid))
    if row is not None and row["match_raw"] is not None:
        return unpack(row["match_raw"]), unpack(row["timeline_raw"])
    path = raw_path(svc, match_id)
    if path is None or not path.exists():
        return None, None
    try:
        with gzip.open(path, "rt", encoding="utf-8") as fh:
            data = json.load(fh)
        return data.get("match"), data.get("timeline")
    except (OSError, ValueError):
        return None, None


# Contadores en vivo para «Datos y API» (se pintan cada 2,5 s): el recuento completo recorre toda la tabla (con el
# recolector a tope, segundos en Windows) y antes iba en la petición de la lista de OTPs, que tarda más: el número
# saltaba de 20 en 20 o de 80 en 80. Ahora se cuenta una vez, se suma cada partida nueva al guardarla y se recuenta
# de verdad cada ``LIVE_RECOUNT_S`` (en un hilo aparte).
LIVE_RECOUNT_S = 300
_live_lock = threading.Lock()  # el estado va en la propia base de datos (``db._live_counts``)


def _live_state(db) -> dict:
    st = getattr(db, "_live_counts", None)
    if st is None:
        st = {"data": None, "at": 0.0, "counting": False}
        try:
            db._live_counts = st
        except AttributeError:
            pass
    return st


def _note_live(db, tier: str | None, kind: str | None, has_timeline: bool) -> None:
    with _live_lock:
        d = _live_state(db)["data"]
        if d is None:
            return
        d["games"] += 1
        d["timelines"] += int(has_timeline)
        d["tiers"][tier or "?"] = d["tiers"].get(tier or "?", 0) + 1
        if kind == "otp":
            d["otp_games"] += 1


def live_counts(db) -> dict:
    """``counts`` al momento sin recorrer la tabla en cada petición (ver ``_note_live``)."""
    with _live_lock:
        st = _live_state(db)
        data, stale, busy = st["data"], time.time() - st["at"] >= LIVE_RECOUNT_S, st["counting"]
        if data is not None and stale and not busy:
            st["counting"] = True

    def recount() -> None:
        try:
            fresh = counts(db)
            with _live_lock:
                st.update(data=fresh, at=time.time())
        finally:
            with _live_lock:
                st["counting"] = False

    if data is None:
        recount()  # la primera vez, ya
    elif stale and not busy:
        threading.Thread(target=recount, name="otp-counts", daemon=True).start()
    with _live_lock:
        d = st["data"] or {}
        return {**d, "tiers": dict(d.get("tiers") or {})}


def counts(db) -> dict:
    row = db.one("SELECT COUNT(*) AS games, COUNT(DISTINCT puuid) AS players, SUM(has_timeline) AS timelines FROM otp_games")
    tiers = {
        r["tier"] or "?": int(r["n"])
        for r in db.query("SELECT tier, COUNT(*) AS n FROM otp_games GROUP BY tier")
    }
    otp = db.one("SELECT COUNT(*) AS games, COUNT(DISTINCT puuid) AS players FROM otp_games WHERE kind = 'otp'")
    return {
        "games": int(row["games"] or 0),
        "players": int(row["players"] or 0),
        "timelines": int(row["timelines"] or 0),
        "tiers": {t: tiers[t] for t in sorted(tiers, key=lambda t: TIER_ORDER.index(t) if t in TIER_ORDER else 99)},
        # De ellas, de OTPs (data/otps); el resto, de otros jugadores (data/partidas_campeon).
        "otp_games": int(otp["games"] or 0),
        "otp_players": int(otp["players"] or 0),
    }


def outdated(db) -> int:
    """Partidas de OTPs analizadas con otra versión del análisis (o del dossier)."""
    return int(db.scalar("SELECT COUNT(*) FROM otp_games WHERE version != ?", (DOSSIER_VERSION * 1000 + ANALYSIS_VERSION,)) or 0)


def reanalyze(db, svc, limit: int | None = None, *, stop: threading.Event | None = None, pause=None) -> int:
    """Rehace los dossiers con el análisis actual a partir del bruto guardado (sin llamadas a Riot) y la carpeta.

    ``stop``: se para a mitad (al cerrar la app); ``pause()``: mientras devuelva ``True`` (estás en partida) espera.
    Lo que falla una partida se registra y se sigue con la siguiente (nunca se queda en bucle con la misma).
    """
    current = DOSSIER_VERSION * 1000 + ANALYSIS_VERSION
    rows = db.query(
        "SELECT match_id, puuid, tier, division, lp, kind FROM otp_games WHERE version != ? ORDER BY match_id " + ("LIMIT ?" if limit else ""),
        (current, limit) if limit else (current,),
    )
    done = 0
    for r in rows:
        if stop is not None and stop.is_set():
            break
        while pause is not None and pause():
            if stop is None:
                time.sleep(5)
            elif stop.wait(5):
                break
        if stop is not None and stop.is_set():
            break
        match, timeline = load_raw(db, svc, r["match_id"], r["puuid"])
        if match is None:
            continue
        elo = {"tier": r["tier"], "division": r["division"], "lp": r["lp"]}
        try:
            if match and save(db, svc, match, timeline, r["puuid"], elo, write_files=False, kind=r["kind"]):
                done += 1
        except Exception:
            log.exception("No se pudo re-analizar la partida %s del OTP", r["match_id"])
        if stop is not None:
            stop.wait(0.01)  # suelta el GIL: la web sigue respondiendo mientras tanto
    if done:
        rebuild_all(svc)
    return done


def relocate(svc, pairs: list[tuple[str, int]]) -> int:
    """Mueve de carpeta solo a los jugadores reclasificados (OTP ↔ otros jugadores): reescribe su carpeta en la que le
    toca y la borra de la otra. Antes se regeneraban las dos carpetas enteras (~29.000 partidas) en cada arranque, con
    la escritura bloqueada: el recolector se quedaba esperando minutos para guardar cada partida."""
    db = svc.db
    files = {kind: Files.of(svc, kind) for kind in KIND_DIRS}
    if not pairs or any(f is None for f in files.values()):
        return 0
    moved = 0
    for puuid, champ in pairs:
        info = player_info(db, puuid)
        for r in db.query("SELECT DISTINCT role, kind FROM otp_games WHERE puuid = ? AND champ = ?", (puuid, champ)):
            for kind, f in files.items():
                with f.lock:  # un jugador cada vez: el recolector solo espera milisegundos
                    folder = f._player_dir(f.base, champ, r["role"], info)
                    if kind != r["kind"]:
                        shutil.rmtree(folder, ignore_errors=True)
                        f.dirty.add(champ)
                        continue
                    folder.mkdir(parents=True, exist_ok=True)
                    with gzip.GzipFile(folder / "partidas.jsonl.gz", "wb", mtime=0) as raw, io.TextIOWrapper(raw, encoding="utf-8") as fh:
                        for g in db.query(
                            "SELECT dossier FROM otp_games WHERE champ = ? AND role IS ? AND puuid = ? AND kind = ? ORDER BY game_start",
                            (champ, r["role"], puuid, kind),
                        ):
                            d = unpack(g["dossier"])
                            if d:
                                fh.write(json.dumps(d, ensure_ascii=False, separators=(",", ":")) + "\n")
                    f._profile(folder, champ, r["role"], info)
                    f.dirty.add(champ)
            moved += 1
    for f in files.values():
        f.maybe_summarize(force=True)  # los resúmenes de esos campeones, en segundo plano
    log.info("Jugadores cambiados de carpeta (OTP u otros jugadores): %d.", moved)
    return moved


def rebuild_all(svc) -> None:
    """Regenera las dos carpetas (OTPs y otros jugadores) desde la base de datos (si una falla, la otra sigue)."""
    for kind in KIND_DIRS:
        files = Files.of(svc, kind)
        if files is None:
            continue
        try:
            files.rebuild()
        except OSError:
            log.exception("No se pudo regenerar la carpeta %s: se reintentará al arrancar", files.base)


def start_reanalysis(svc) -> threading.Thread | None:
    """Al arrancar: si hay partidas de OTPs analizadas con una versión anterior del análisis, se rehacen solas en
    segundo plano (sin Riot; en pausa mientras juegas) y se regenera ``data/otps``. Así «Tú frente a los OTPs», sus
    resúmenes y las builds del draft comparan con las mismas reglas que tus partidas."""
    db = svc.db
    pending = outdated(db)
    unclassified = int(db.scalar("SELECT COUNT(*) FROM otp_games WHERE kind IS NULL") or 0)
    stale = any(f is not None and f.needs_rebuild() for f in (Files.of(svc, k) for k in KIND_DIRS))
    # OTP = lo que juega (sus partidas revisadas), no solo su maestría: quien cambia de carpeta se mueve él solo (en
    # segundo plano; regenerar las carpetas enteras bloqueaba al recolector durante minutos en cada arranque).
    moved = reclassify(db)
    if not pending and not unclassified and not stale:
        if moved:
            thread = threading.Thread(target=relocate, args=(svc, moved), name="otp-relocate", daemon=True)
            thread.start()
            return thread
        return None
    stop = getattr(svc, "shutdown_event", None)

    def in_game() -> bool:
        tracker = getattr(svc, "tracker", None)
        return bool(tracker is not None and tracker.state.get("in_game"))

    def run() -> None:
        try:
            # Primero, separar OTPs de otros jugadores en lo guardado antes de la marca (solo SQL).
            classified = classify_pending(db) if unclassified else 0
            if (classified or stale) and not pending:
                rebuild_all(svc)  # carpetas al día (también si la última vez quedaron a medias)
            elif moved and not pending:
                relocate(svc, moved)
            if pending:
                log.info("Re-analizando %d partidas guardadas con la versión %d del análisis…", pending, ANALYSIS_VERSION)
                done = reanalyze(db, svc, stop=stop, pause=in_game)
                log.info("Partidas re-analizadas: %d (versión %d).", done, ANALYSIS_VERSION)
        except Exception:
            log.exception("Fallo re-analizando o clasificando las partidas guardadas")

    thread = threading.Thread(target=run, name="otp-reanalyze", daemon=True)
    thread.start()
    return thread


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


def enemy_profile(d: dict, champ_info) -> list[str]:
    """Grupos del equipo rival de una partida: ``ap``/``ad``/``mixed`` por su daño, y ``healers``/``tanks``."""
    enemies = [int(c) for c in (d.get("enemies") or {}).values() if c]
    infos = [champ_info(c) for c in enemies]
    infos = [i for i in infos if i]
    if not infos:
        return []
    magic = sum(i["magic"] for i in infos) / len(infos)
    out = ["ap" if magic >= 0.5 else "ad" if magic <= 0.3 else "mixed"]
    if any(i["name"] in HEAVY_HEAL for i in infos):
        out.append("healers")
    if sum(1 for i in infos if "Tank" in i["tags"]) >= 2:
        out.append("tanks")
    if sum(1 for i in infos if "Assassin" in i["tags"]) >= 2:
        out.append("assassins")
    return out


def _items_block(rows: list[dict], champ_info) -> dict:
    """Objetos completos y botas que compran (partidas y victorias), en total y según cómo era el equipo rival."""
    if champ_info is None:
        return {}
    groups: dict[str, dict] = {}

    def add(group: str, kind: str, item: int, win: bool) -> None:
        g = groups.setdefault(group, {"games": 0, "items": {}, "boots": {}})
        cur = g[kind].setdefault(str(item), [0, 0])
        cur[0] += 1
        cur[1] += int(win)

    for d in rows:
        build = (d.get("analysis") or {}).get("build") or {}
        order = build.get("order") or []
        if not order:
            continue
        legend = {o["item"] for o in order if o.get("kind") == "legendary"}
        boots = {o["item"] for o in order if o.get("kind") == "boots"}
        for group in ["all", *enemy_profile(d, champ_info)]:
            groups.setdefault(group, {"games": 0, "items": {}, "boots": {}})["games"] += 1
            for i in legend:
                add(group, "items", i, d["win"])
            for i in boots:
                add(group, "boots", i, d["win"])
    for g in groups.values():
        for kind in ("items", "boots"):
            g[kind] = {k: v for k, v in sorted(g[kind].items(), key=lambda kv: -kv[1][0]) if v[0] >= 3}
    return {"items_by_enemy": groups} if groups else {}


def _role_block(role: str, rows: list[dict], name_champ=None, name_item=None, champ_info=None) -> dict:
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
        **_items_block(rows, champ_info),
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


def summarize(dossiers: list[dict], name_champ=None, name_item=None, champ_info=None) -> dict:
    """Lo agregado de un campeón por rol y, dentro de cada rol, por elo (lo que hacen sus OTPs y en qué se diferencian
    sus victorias).

    Como en tus medias (``insights.reliable``), no cuentan las partidas con alguien AFK 3 minutos o más (o marcado AFK
    por Riot): sus cifras no reflejan cómo juega (CS/min de 20 minutos, objetivos regalados…). Se dice cuántas son.
    """
    from app.coach.insights import reliable  # aquí: evita un import circular

    total = len(dossiers)
    dossiers = [d for d in dossiers if reliable(d.get("analysis") or {})]
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
            "all": _role_block(role, rows, name_champ, name_item, champ_info) | {"players": len({d.get("puuid") for d in rows})},
            "by_tier": {
                tier: _role_block(role, tier_rows, name_champ, name_item, champ_info) | {"players": len({d.get("puuid") for d in tier_rows})}
                for tier, tier_rows in sorted(by_tier.items(), key=lambda kv: _tier_key(kv[0]))
            },
        }
    tiers = Counter(d.get("tier") or "?" for d in dossiers)
    patches = Counter(d.get("patch") or "?" for d in dossiers)
    return {
        "games": games,
        "excluded_afk": total - games,
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


# Informes de la búsqueda y medias del ranking (``ranking_stats``): no son partidas, no se borran al regenerar.
KEEP_ON_REBUILD = frozenset({"_busqueda.json", "_busqueda.json.tmp", "_medias_ranking.json", "_medias_ranking.json.tmp", "_registro.json"})
REPLACE_RETRIES = 4


def _retry(action, path: Path) -> bool:
    """Ejecuta ``action`` con reintentos cortos (un antivirus o el indexador sueltan el fichero en un momento)."""
    for attempt in range(REPLACE_RETRIES):
        try:
            action()
            return True
        except FileNotFoundError:
            return True
        except OSError:
            if attempt == REPLACE_RETRIES - 1:
                return False
            time.sleep(0.25 * (attempt + 1))
    return False


def replace_tree(src: Path, dst: Path, keep: frozenset[str] = KEEP_ON_REBUILD) -> list[str]:
    """Pone el contenido de ``src`` en ``dst`` fichero a fichero y borra de ``dst`` lo que ya no está (salvo ``keep``).

    No renombra ``dst``: en Windows renombrar una carpeta falla (WinError 5, acceso denegado) si cualquier programa
    tiene algo abierto dentro (el explorador, el IDE indexando, el antivirus, OneDrive). Un fichero bloqueado se
    reintenta, luego se copia encima y, si tampoco se puede, se devuelve en la lista (no tumba nada). ``src`` se borra.
    """
    failed: list[str] = []
    wanted = {path.relative_to(src) for path in src.rglob("*")}
    dst.mkdir(parents=True, exist_ok=True)
    for rel in sorted(wanted, key=lambda r: len(r.parts)):
        source, target = src / rel, dst / rel
        if source.is_dir():
            if target.exists() and not target.is_dir() and not _retry(target.unlink, target):
                failed.append(str(rel))
                continue
            target.mkdir(parents=True, exist_ok=True)
            continue
        if target.is_dir():
            _retry(lambda t=target: shutil.rmtree(t), target)
        if not _retry(lambda s=source, d=target: os.replace(s, d), target) and not _retry(lambda s=source, d=target: shutil.copyfile(s, d), target):
            failed.append(str(rel))
    # Lo que sobra (de más profundo a menos): ficheros y carpetas que ya no existen en la versión nueva.
    for path in sorted(dst.rglob("*"), key=lambda p: len(p.parts), reverse=True):
        rel = path.relative_to(dst)
        if rel.parts[0] in keep or rel in wanted:
            continue
        if path.is_dir():
            if not any(path.iterdir()) and not _retry(path.rmdir, path):
                failed.append(str(rel))
        elif not _retry(path.unlink, path):
            failed.append(str(rel))
    shutil.rmtree(src, ignore_errors=True)
    return failed


class Files:
    """Una carpeta de partidas analizadas (``otps`` para OTPs, ``partidas_campeon`` para cualquier otro jugador):
    escribe cada partida al guardarla y mantiene los resúmenes al día (en segundo plano)."""

    _instances: ClassVar[dict[str, Files]] = {}
    _lock: ClassVar[threading.Lock] = threading.Lock()

    def __init__(self, svc, base: Path, kind: str = "otp") -> None:
        self.svc = svc
        self.base = base
        self.kind = kind
        self.lock = threading.RLock()  # escrituras de varios hilos (una región cada uno) y regeneración
        self.dirty: set[int] = set()
        self.last_summary: dict[int, float] = {}
        self.thread: threading.Thread | None = None
        self.force_again = False
        self.state: dict = {"status": "idle"}

    @classmethod
    def of(cls, svc, kind: str = "otp") -> Files | None:
        settings = getattr(svc, "settings", None)
        if settings is None or getattr(settings, "data_dir", None) is None or kind not in KIND_DIRS:
            return None
        base = Path(settings.data_dir) / KIND_DIRS[kind]
        with cls._lock:
            inst = cls._instances.get(str(base))
            if inst is None or inst.svc is not svc:
                inst = cls._instances[str(base)] = cls(svc, base, kind)
            return inst

    def _readme(self) -> str:
        if self.kind == "otp":
            return README
        return README.replace(
            "# Partidas de OTPs", "# Partidas de otros jugadores (no OTPs)", 1
        ) + (
            "\n**Aquí no hay OTPs**: son jugadores que tienen el campeón entre sus 3 más jugados (búsqueda «Cualquiera que "
            "lo juegue»). Sirven para builds y matchups de ese campeón; las comparaciones con OTPs usan solo la carpeta "
            "`otps`.\n"
        )

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
            "FROM otp_games WHERE puuid = ? AND champ = ? AND role IS ? AND kind = ? GROUP BY tier",
            (info.get("puuid"), champ, role, self.kind),
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
        # Por la marca, no por la carpeta: el informe de la búsqueda (_busqueda.json) puede haberla creado antes.
        if not marker.exists():
            self.base.mkdir(parents=True, exist_ok=True)
            (self.base / "LEEME.md").write_text(self._readme(), encoding="utf-8")
            marker.write_text(str(LAYOUT_VERSION), encoding="utf-8")

    def needs_rebuild(self) -> bool:
        """La carpeta falta, es de otro formato o tiene menos partidas que la base de datos."""
        if not self.svc.db.scalar("SELECT 1 FROM otp_games WHERE kind = ? LIMIT 1", (self.kind,)):
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
                log.exception("No se pudo rehacer el resumen del campeón %s (%s)", champ, self.base.name)

    def write_summary(self, champ: int, root: Path | None = None) -> dict:
        self.dirty.discard(champ)
        self.last_summary[champ] = time.time()
        dossiers = []
        for i, r in enumerate(self.svc.db.query("SELECT puuid, dossier FROM otp_games WHERE champ = ? AND kind = ?", (champ, self.kind))):
            if i % SUMMARY_YIELD_EVERY == SUMMARY_YIELD_EVERY - 1:
                time.sleep(0.005)
            d = unpack(r["dossier"])
            if d:
                d["puuid"] = r["puuid"]
                dossiers.append(d)
        name_champ, name_item = self._names()
        summary = {
            "champ": champ,
            "champ_name": self._champ_name(champ),
            "updated_at": now_s(),
            **summarize(dossiers, name_champ, name_item, champ_info_from(getattr(self.svc, "dd", None))),
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
        if not db.scalar("SELECT 1 FROM otp_games WHERE kind = ? LIMIT 1", (self.kind,)) and not self.base.exists():
            return {"path": str(self.base), "games": 0, "folders": 0, "champions": 0}  # nada que escribir
        with self.lock:
            tmp = self.base.with_name(self.base.name + ".tmp")
            shutil.rmtree(tmp, ignore_errors=True)
            tmp.mkdir(parents=True)
            infos = {
                r["puuid"]: dict(r)
                for r in db.query(
                    "SELECT puuid, platform, tier, division, lp, game_name, tag_line FROM players "
                    "WHERE puuid IN (SELECT DISTINCT puuid FROM otp_games WHERE kind = ?)",
                    (self.kind,),
                )
            }
            groups = db.query("SELECT champ, role, puuid, COUNT(*) AS n FROM otp_games WHERE kind = ? GROUP BY champ, role, puuid", (self.kind,))
            total = sum(int(g["n"]) for g in groups)
            done = 0
            for g in groups:
                info = infos.get(g["puuid"]) or {"puuid": g["puuid"]}
                folder = self._player_dir(tmp, int(g["champ"]), g["role"], info)
                folder.mkdir(parents=True, exist_ok=True)
                # mtime=0: el mismo contenido da los mismos bytes (al regenerar, git solo ve lo que cambió de verdad).
                with gzip.GzipFile(folder / "partidas.jsonl.gz", "wb", mtime=0) as raw, io.TextIOWrapper(raw, encoding="utf-8") as fh:
                    for r in db.query(
                        "SELECT dossier FROM otp_games WHERE champ = ? AND role IS ? AND puuid = ? AND kind = ? ORDER BY game_start",
                        (g["champ"], g["role"], g["puuid"], self.kind),
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
            (tmp / "LEEME.md").write_text(self._readme(), encoding="utf-8")
            (tmp / ".formato").write_text(str(LAYOUT_VERSION), encoding="utf-8")
            # Restos de versiones anteriores (que sí renombraban la carpeta y en Windows podían quedarse a medias).
            shutil.rmtree(self.base.with_name(self.base.name + ".old"), ignore_errors=True)
            failed = replace_tree(tmp, self.base)
            if failed:
                # Algo bloqueado por otro programa: sin la marca de formato, se vuelve a intentar al arrancar.
                (self.base / ".formato").unlink(missing_ok=True)
                log.warning(
                    "Carpeta %s regenerada a medias: %d ficheros los tenía abiertos otro programa (%s…). Se reintentará al arrancar.",
                    self.base, len(failed), ", ".join(failed[:3]),
                )
            self.dirty.clear()
        log.info("Carpeta regenerada (%s): %d partidas de %d campeones.", self.base, total, len(champs))
        return {"path": str(self.base), "games": total, "folders": len(groups), "champions": len(champs), "failed": len(failed)}


def champ_info_from(dd):
    """``champ_info(c)`` -> {magic: 0-1, tags, name} con Data Dragon (cuánto daño mágico hace, clases y nombre)."""
    if dd is None or not getattr(dd, "champions", None):
        return None

    def info(champ: int) -> dict | None:
        c = dd.champions.get(int(champ)) or {}
        stats = c.get("info") or {}
        if not c:
            return None
        attack, magic = stats.get("attack", 5), stats.get("magic", 5)
        return {"magic": magic / max(1, attack + magic), "tags": list(c.get("tags") or []), "name": dd.name(int(champ))}

    return info


def ensure_files(svc) -> None:
    """Al arrancar el recolector: si una carpeta falta o es de otro formato y hay partidas, se regenera sola."""
    if svc.db.scalar("SELECT 1 FROM otp_games WHERE kind IS NULL LIMIT 1"):
        classify_pending(svc.db)  # antes de escribir nada: cada partida a su carpeta
    for kind in KIND_DIRS:
        files = Files.of(svc, kind)
        if files is not None and files.needs_rebuild():
            files.rebuild_in_background()


SUMMARIES_WAIT_MAX_S = 900


def wait_summaries(svc, timeout: float = SUMMARIES_WAIT_MAX_S) -> bool:
    """Espera a que terminen los resúmenes por campeón en curso o pendientes (los de ``flush``). ``True`` si acabaron."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        busy = False
        for kind in KIND_DIRS:
            files = Files.of(svc, kind)
            if files is None:
                continue
            thread = files.thread
            if thread is not None and thread.is_alive():
                busy = True
                thread.join(min(5.0, max(0.0, deadline - time.time())))
        if not busy:
            return True
    return False


def flush(svc) -> None:
    """Al parar el recolector: los resúmenes pendientes se rehacen ya."""
    for kind in KIND_DIRS:
        files = Files.of(svc, kind)
        if files is not None:
            files.maybe_summarize(force=True)
