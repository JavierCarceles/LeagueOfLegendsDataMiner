"""Análisis de una partida concreta (y su timeline bajo demanda)."""

from __future__ import annotations

import json

from app.analytics import draft
from app.analytics.filters import Filters
from app.analytics.players import _split_ints, fetch_matches, load_match_rows, performance_scores
from app.analytics.stats import pct, round_or_none, safe_div
from app.data.db import now_s
from app.riot.routing import QUEUES, ROLE_INDEX, ROLES, TIER_INDEX

OBJECTIVES = [
    ("first_blood", "Primera sangre"),
    ("first_tower", "Primera torre"),
    ("first_dragon", "Primer dragón"),
    ("first_grubs", "Primeras larvas"),
    ("first_herald", "Primer heraldo"),
    ("first_baron", "Primer barón"),
    ("first_inhib", "Primer inhibidor"),
]


class MatchNotFound(Exception):
    pass


def _team_totals(rows: list[dict]) -> dict:
    """Kills, oro, daño, visión y CS de cada equipo."""
    team_totals = {100: {"kills": 0, "gold": 0, "dmg": 0, "vision": 0, "cs": 0}, 200: {"kills": 0, "gold": 0, "dmg": 0, "vision": 0, "cs": 0}}
    for r in rows:
        t = team_totals[r["team"]]
        t["kills"] += r["kills"] or 0
        t["gold"] += r["gold"] or 0
        t["dmg"] += r["dmg"] or 0
        t["vision"] += r["vision"] or 0
        t["cs"] += r["cs"] or 0
    return team_totals


def _player_view(r: dict, minutes: float, scores: dict) -> dict:
    """Vista pública de un jugador de la partida (con su nota)."""
    return {
        "pid": r["pid"],
        "team": r["team"],
        "role": r["role"],
        "champ": r["champ"],
        "name": r.get("game_name"),
        "tag": r.get("tag_line"),
        "puuid": r.get("puuid"),
        "win": bool(r["win"]),
        "level": r["level"],
        "kills": r["kills"],
        "deaths": r["deaths"],
        "assists": r["assists"],
        "kda": round(safe_div((r["kills"] or 0) + (r["assists"] or 0), max(1, r["deaths"] or 0)), 2),
        "cs": r["cs"],
        "cs_min": round((r["cs"] or 0) / minutes, 1),
        "cs10": r["cs10"],
        "gold": r["gold"],
        "dmg": r["dmg"],
        "dmg_share": pct(r["dmg_share"]),
        "kp": pct(r["kp"]),
        "vision": r["vision"],
        "wards": r["wards"],
        "control_wards": r["control_wards"],
        "taken": r["taken"],
        "mitigated": r["mitigated"],
        "dmg_profile": {"phys": r["dmg_phys"], "magic": r["dmg_magic"], "true": r["dmg_true"]},
        "solo_kills": r["solo_kills"],
        "plates": r["plates"],
        "lane_adv": r["lane_adv"],
        "items": _split_ints(r["items"]),
        "spells": _split_ints(r["spells"]),
        "runes": _split_ints(r["runes"]),
        "score": scores.get(r["pid"]),
    }


def _lane_winner(blue: dict, red: dict) -> int | None:
    """El equipo que ganó la línea (100 o 200), o ``None`` si ninguno."""
    if (blue["lane_adv"] or 0) > 0:
        return 100
    return 200 if (red["lane_adv"] or 0) > 0 else None


def _lane_view(role: str, blue: dict, red: dict) -> dict:
    """Una línea, azul contra rojo: diferencias de CS a los 10, oro, daño y kills, y quién la ganó."""
    return {
        "role": role,
        "blue": blue["pid"],
        "red": red["pid"],
        "cs10_diff": round_or_none((blue["cs10"] - red["cs10"]) if blue["cs10"] is not None and red["cs10"] is not None else None),
        "gold_diff": (blue["gold"] or 0) - (red["gold"] or 0),
        "dmg_diff": (blue["dmg"] or 0) - (red["dmg"] or 0),
        "kills_diff": (blue["kills"] or 0) - (red["kills"] or 0),
        "lane_winner": _lane_winner(blue, red),
    }


def _lanes(by_slot: dict) -> list[dict]:
    """Las líneas con jugador en los dos equipos, en el orden de los roles."""
    lanes = []
    for role in ROLES:
        blue, red = by_slot.get((100, role)), by_slot.get((200, role))
        if not blue or not red:
            continue
        lanes.append(_lane_view(role, blue, red))
    return lanes


def _team_view(m: dict, team: int, totals: dict) -> dict:
    """Vista pública de un equipo: resultado, totales, objetivos y qué objetivos consiguió primero."""
    side = "blue" if team == 100 else "red"
    return {
        "team": team,
        "side": side,
        "win": m["winner"] == team,
        **totals,
        "towers": m[f"{side}_towers"],
        "dragons": m[f"{side}_dragons"],
        "barons": m[f"{side}_barons"],
        "grubs": m[f"{side}_grubs"],
        "heralds": m[f"{side}_heralds"],
        "firsts": [label for key, label in OBJECTIVES if m.get(key) == team],
    }


def match_detail(svc, match_id: str) -> dict:
    match_id = match_id.strip().upper()
    matches, parts = load_match_rows(svc, [match_id])
    if match_id not in matches or not parts.get(match_id):
        fetch_matches(svc, [match_id], None)
        matches, parts = load_match_rows(svc, [match_id])
    if match_id not in matches:
        raise MatchNotFound(f"No se encontró la partida {match_id}")
    m = matches[match_id]
    rows = parts.get(match_id) or []
    if not rows:
        raise MatchNotFound("La partida es una remake o no es de la Grieta del Invocador")

    minutes = max(m["duration"], 60) / 60
    scores = performance_scores(svc, rows, m["duration"])
    team_totals = _team_totals(rows)
    players = sorted((_player_view(r, minutes, scores) for r in rows), key=lambda p: (p["team"], ROLE_INDEX.get(p["role"] or "", 9)))
    by_slot = {(p["team"], p["role"]): p for p in players if p["role"]}
    lanes = _lanes(by_slot)
    teams = [_team_view(m, team, team_totals[team]) for team in (100, 200)]

    blue_team = {p["role"]: p["champ"] for p in players if p["team"] == 100 and p["role"]}
    red_team = {p["role"]: p["champ"] for p in players if p["team"] == 200 and p["role"]}

    has_timeline = (
        svc.db.one("SELECT 1 FROM timelines WHERE match_id = ? UNION SELECT 1 FROM my_games WHERE match_id = ? AND timeline_raw IS NOT NULL", (match_id, match_id))
        is not None
    )
    return {
        "match_id": match_id,
        "platform": m["platform"],
        "queue": m["queue"],
        "queue_label": QUEUES.get(m["queue"], f"Cola {m['queue']}"),
        "patch": m["patch"],
        "tier": m["tier"],
        "game_start": m["game_start"],
        "duration": m["duration"],
        "winner": m["winner"],
        "surrender": bool(m["surrender"]),
        "teams": teams,
        "players": players,
        "lanes": lanes,
        "can_predict": len(blue_team) == 5 and len(red_team) == 5,
        "has_timeline": has_timeline,
    }


def match_prediction(svc, match_id: str) -> dict | None:
    """Lo que predecía el modelo de draft para esta partida (equipo azul)."""
    match_id = match_id.strip().upper()
    matches, parts = load_match_rows(svc, [match_id])
    if match_id not in matches:
        raise MatchNotFound(f"No se encontró la partida {match_id}")
    m, rows = matches[match_id], parts.get(match_id) or []
    blue = {r["role"]: r["champ"] for r in rows if r["team"] == 100 and r["role"]}
    red = {r["role"]: r["champ"] for r in rows if r["team"] == 200 and r["role"]}
    if len(blue) != 5 or len(red) != 5:
        return None
    tier = m["tier"] if m["tier"] in TIER_INDEX else "emerald_plus"
    result = draft.analyze(svc, Filters.parse(svc.db, tier=tier), blue, red)
    return {
        "blue_win_probability": result["win_probability"],
        "interval": result["interval"],
        "grade": result["grade"],
        "correct": (result["win_probability"] >= 50) == (m["winner"] == 100),
        "lanes": [{"role": lane["role"], "wr_adj": lane.get("wr_adj"), "impact_pp": lane.get("impact_pp")} for lane in result["lanes"]],
        "note": "La partida ya cuenta en tus datos; para una validación honesta usa el backtest de «Datos y API».",
    }


def _snapshot(pf: dict) -> dict:
    """Oro, experiencia y CS de un jugador en un frame del timeline."""
    return {
        "gold": pf.get("totalGold") or 0,
        "xp": pf.get("xp") or 0,
        "cs": (pf.get("minionsKilled") or 0) + (pf.get("jungleMinionsKilled") or 0),
    }


def _frame_point(frame: dict, team_of: dict, at: dict) -> dict:
    """Diferencia de oro y experiencia de un minuto; a los 10 y a los 15 guarda además lo de cada jugador en ``at``."""
    minute = round((frame.get("timestamp") or 0) / 60000)
    gold = {100: 0, 200: 0}
    xp = {100: 0, 200: 0}
    for pid_str, pf in (frame.get("participantFrames") or {}).items():
        pid = int(pid_str)
        team = team_of.get(pid, 100 if pid <= 5 else 200)
        gold[team] += pf.get("totalGold") or 0
        xp[team] += pf.get("xp") or 0
        if minute in (10, 15):
            at.setdefault(pid, {})[minute] = _snapshot(pf)
    return {"minute": minute, "gold_diff": gold[100] - gold[200], "xp_diff": xp[100] - xp[200]}


def _kill_event(ev: dict, t: float, team_of: dict) -> dict:
    """Una kill, con el equipo que la hizo (el contrario de la víctima si no la hizo un jugador)."""
    killer = ev.get("killerId") or 0
    victim = ev.get("victimId") or 0
    team = team_of.get(killer) or (300 - team_of.get(victim, 100) if victim else None)
    return {"t": t, "type": "kill", "team": team, "killer": killer, "victim": victim}


def _building_event(ev: dict, t: float) -> dict:
    """Una torre o un inhibidor destruido, con el equipo que lo destruyó."""
    owner = ev.get("teamId")
    return {
        "t": t,
        "type": "tower" if ev.get("buildingType") == "TOWER_BUILDING" else "inhibitor",
        "team": 300 - owner if owner else None,
        "lane": ev.get("laneType"),
    }


def _event(ev: dict, team_of: dict) -> dict | None:
    """Un evento del timeline que se enseña (kill, monstruo épico o edificio); ``None`` si es de otro tipo."""
    kind = ev.get("type")
    t = round((ev.get("timestamp") or 0) / 60000, 2)
    if kind == "CHAMPION_KILL":
        return _kill_event(ev, t, team_of)
    if kind == "ELITE_MONSTER_KILL":
        return {"t": t, "type": (ev.get("monsterType") or "").lower(), "sub": ev.get("monsterSubType"), "team": ev.get("killerTeamId")}
    if kind == "BUILDING_KILL":
        return _building_event(ev, t)
    return None


def _events(frames: list[dict], team_of: dict) -> list[dict]:
    """Kills, monstruos épicos y edificios de la partida, en orden."""
    events = []
    for frame in frames:
        for ev in frame.get("events") or []:
            item = _event(ev, team_of)
            if item is not None:
                events.append(item)
    return events


def _lanes_at(by_slot: dict, at: dict) -> list[dict]:
    """Diferencia de oro, experiencia y CS de cada línea a los 10 y a los 15 minutos."""
    lanes_at = []
    for role in ROLES:
        blue, red = by_slot.get((100, role)), by_slot.get((200, role))
        if not blue or not red:
            continue
        item = {"role": role}
        for minute in (10, 15):
            b, r = at.get(blue, {}).get(minute), at.get(red, {}).get(minute)
            if b and r:
                item[f"gold{minute}"] = b["gold"] - r["gold"]
                item[f"xp{minute}"] = b["xp"] - r["xp"]
                item[f"cs{minute}"] = b["cs"] - r["cs"]
        lanes_at.append(item)
    return lanes_at


def match_timeline(svc, match_id: str) -> dict:
    match_id = match_id.strip().upper()
    cached = svc.db.one("SELECT data FROM timelines WHERE match_id = ?", (match_id,))
    if cached:
        return json.loads(cached["data"])
    detail = match_detail(svc, match_id)
    team_of = {p["pid"]: p["team"] for p in detail["players"]}
    from app.coach.store import raw_timeline

    payload = raw_timeline(svc.db, match_id) or svc.riot.timeline(match_id)
    if not payload:
        raise MatchNotFound("Riot no tiene timeline para esta partida")
    frames = (payload.get("info") or {}).get("frames") or []
    series = []
    at: dict[int, dict] = {}
    for frame in frames:
        series.append(_frame_point(frame, team_of, at))
    events = _events(frames, team_of)
    by_slot = {(p["team"], p["role"]): p["pid"] for p in detail["players"] if p["role"]}
    lanes_at = _lanes_at(by_slot, at)

    data = {"match_id": match_id, "series": series, "events": events, "lanes": lanes_at}
    with svc.db.write() as conn:
        conn.execute(
            "INSERT OR REPLACE INTO timelines(match_id, data, fetched_at) VALUES (?, ?, ?)",
            (match_id, json.dumps(data, separators=(",", ":")), now_s()),
        )
    return data
