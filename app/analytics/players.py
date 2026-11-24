"""Perfil profesional de un jugador a partir de sus últimas partidas.

Coste en llamadas (key gratuita): ~4 fijas (cuenta, región, invocador,
liga) + 1 por cada partida que aún no esté en la base de datos. Las partidas
ya descargadas (por el recolector o por búsquedas anteriores) salen gratis.
"""

from __future__ import annotations

import math
from collections import defaultdict

from app.analytics.filters import Filters
from app.analytics.stats import pct, round_or_none, safe_div
from app.data.db import now_s
from app.data.repository import known_match_ids, mark_skipped, store_match, upsert_player_profile
from app.ingest.extractor import parse_match
from app.logbus import get_logger
from app.riot.client import RiotAPIError
from app.riot.routing import PLATFORMS, QUEUES, ROLE_INDEX, TIER_INDEX, region_of

log = get_logger("players")

QUEUE_FILTERS = {
    "solo": {"queue": 420, "ranked_only": False},
    "flex": {"queue": 440, "ranked_only": False},
    "ranked": {"queue": None, "ranked_only": True},
    "all": {"queue": None, "ranked_only": False},
}


class NotFound(Exception):
    pass


def _rank(entry: dict | None) -> dict | None:
    if not entry:
        return None
    wins, losses = entry.get("wins") or 0, entry.get("losses") or 0
    return {
        "tier": entry.get("tier"),
        "division": entry.get("rank"),
        "lp": entry.get("leaguePoints"),
        "wins": wins,
        "losses": losses,
        "wr": pct(safe_div(wins, wins + losses)),
        "hot_streak": bool(entry.get("hotStreak")),
    }


def _split_ints(text: str | None) -> list[int]:
    return [int(x) for x in (text or "").split(",") if x.strip().lstrip("-").isdigit()]


def fetch_matches(svc, ids: list[str], seed_tier: str | None) -> int:
    """Descarga y guarda las partidas que aún no tengan filas de participantes."""
    if not ids:
        return 0
    marks = ",".join("?" * len(ids))
    with_rows = {r[0] for r in svc.db.query(f"SELECT DISTINCT match_id FROM participants WHERE match_id IN ({marks})", ids)}
    dead = {
        r[0]
        for r in svc.db.query(
            f"SELECT match_id FROM skipped_matches WHERE match_id IN ({marks}) "
            f"UNION SELECT match_id FROM matches WHERE is_remake = 1 AND match_id IN ({marks})",
            ids * 2,
        )
    }
    fetched = 0
    for match_id in ids:
        if match_id in with_rows or match_id in dead:
            continue
        payload = svc.riot.match(match_id)
        fetched += 1
        if payload is None:
            mark_skipped(svc.db, match_id, "404")
            continue
        record = parse_match(payload)
        if record is None:
            mark_skipped(svc.db, match_id, "formato desconocido")
            continue
        store_match(svc.db, record, seed_tier=seed_tier, stats_queues=svc.settings.stats_queues)
    if fetched:
        svc.agg.bump()
    return fetched


def load_match_rows(svc, ids: list[str]) -> tuple[dict, dict]:
    """(partidas por id, participantes por id) con nombres de jugador."""
    if not ids:
        return {}, {}
    marks = ",".join("?" * len(ids))
    matches = {r["match_id"]: dict(r) for r in svc.db.query(f"SELECT * FROM matches WHERE match_id IN ({marks})", ids)}
    parts: dict[str, list[dict]] = defaultdict(list)
    for r in svc.db.query(
        f"SELECT pa.*, pl.puuid, pl.game_name, pl.tag_line FROM participants pa "
        f"LEFT JOIN players pl ON pl.id = pa.player_id WHERE pa.match_id IN ({marks}) ORDER BY pa.match_id, pa.pid",
        ids,
    ):
        parts[r["match_id"]].append(dict(r))
    return matches, parts


def _player_match(match: dict, rows: list[dict], puuid: str) -> dict | None:
    me = next((r for r in rows if r.get("puuid") == puuid), None)
    if me is None:
        return None
    opp = next((r for r in rows if r["team"] != me["team"] and r["role"] and r["role"] == me["role"]), None)
    minutes = max(match["duration"], 60) / 60
    def side(team):
        members = [r for r in rows if r["team"] == team]
        members.sort(key=lambda r: ROLE_INDEX.get(r["role"] or "", 9))
        return [
            {"champ": r["champ"], "role": r["role"], "name": r.get("game_name"), "tag": r.get("tag_line"), "puuid": r.get("puuid"),
             "kills": r["kills"], "deaths": r["deaths"], "assists": r["assists"]}
            for r in members
        ]
    cs10_diff = (me["cs10"] - opp["cs10"]) if opp and me["cs10"] is not None and opp["cs10"] is not None else None
    return {
        "match_id": match["match_id"],
        "queue": match["queue"],
        "queue_label": QUEUES.get(match["queue"], f"Cola {match['queue']}"),
        "patch": match["patch"],
        "platform": match["platform"],
        "tier": match["tier"],
        "game_start": match["game_start"],
        "duration": match["duration"],
        "remake": bool(match["is_remake"]),
        "win": bool(me["win"]),
        "champ": me["champ"],
        "role": me["role"],
        "level": me["level"],
        "kills": me["kills"],
        "deaths": me["deaths"],
        "assists": me["assists"],
        "kda": round(safe_div(me["kills"] + me["assists"], max(1, me["deaths"])), 2),
        "cs": me["cs"],
        "cs_min": round(me["cs"] / minutes, 1),
        "cs10": me["cs10"],
        "cs10_diff": round_or_none(cs10_diff),
        "gold": me["gold"],
        "gold_min": round(me["gold"] / minutes),
        "gold_diff": (me["gold"] - opp["gold"]) if opp else None,
        "dmg": me["dmg"],
        "dmg_min": round(me["dmg"] / minutes),
        "dmg_share": pct(me["dmg_share"]),
        "kp": pct(me["kp"]),
        "vision": me["vision"],
        "vision_min": round(me["vision"] / minutes, 2),
        "wards": me["wards"],
        "wards_killed": me["wards_killed"],
        "control_wards": me["control_wards"],
        "solo_kills": me["solo_kills"],
        "plates": me["plates"],
        "first_blood": bool(me["first_blood"]),
        "lane_adv": me["lane_adv"],
        "opp_lane_adv": opp["lane_adv"] if opp else None,
        "items": _split_ints(me["items"]),
        "spells": _split_ints(me["spells"]),
        "runes": _split_ints(me["runes"]),
        "opponent": {"champ": opp["champ"], "name": opp.get("game_name"), "kills": opp["kills"], "deaths": opp["deaths"], "assists": opp["assists"]} if opp else None,
        "team": side(me["team"]),
        "enemies": side(300 - me["team"]),
        "_minutes": minutes,
        "_raw": me,
        "_opp": opp,
    }


def _summary(games: list[dict]) -> dict:
    n = len(games)
    if not n:
        return {"games": 0}
    wins = sum(g["win"] for g in games)
    minutes = sum(g["_minutes"] for g in games)
    k = sum(g["kills"] for g in games)
    d = sum(g["deaths"] for g in games)
    a = sum(g["assists"] for g in games)
    lane_known = [g for g in games if g["lane_adv"] is not None and g["opp_lane_adv"] is not None]
    cs10_diffs = [g["cs10_diff"] for g in games if g["cs10_diff"] is not None]
    kps = [g["kp"] for g in games if g["kp"] is not None]
    shares = [g["dmg_share"] for g in games if g["dmg_share"] is not None]
    cs10s = [g["cs10"] for g in games if g["cs10"] is not None]
    return {
        "games": n,
        "wins": wins,
        "losses": n - wins,
        "wr": pct(wins / n),
        "kda": round(safe_div(k + a, max(1, d)), 2),
        "kills": round(k / n, 1),
        "deaths": round(d / n, 1),
        "assists": round(a / n, 1),
        "cs_min": round(sum(g["cs"] for g in games) / minutes, 2),
        "gold_min": round(sum(g["gold"] for g in games) / minutes),
        "dmg_min": round(sum(g["dmg"] for g in games) / minutes),
        "vision_min": round(sum(g["vision"] for g in games) / minutes, 2),
        "kp": round(sum(kps) / len(kps), 1) if kps else None,
        "dmg_share": round(sum(shares) / len(shares), 1) if shares else None,
        "cs10": round(sum(cs10s) / len(cs10s), 1) if cs10s else None,
        "cs10_diff": round(sum(cs10_diffs) / len(cs10_diffs), 1) if cs10_diffs else None,
        "lane_win": pct(safe_div(sum(1 for g in lane_known if (g["lane_adv"] or 0) > 0), len(lane_known))),
        "lane_loss": pct(safe_div(sum(1 for g in lane_known if (g["opp_lane_adv"] or 0) > 0), len(lane_known))),
        "solo_kills": round(sum(g["solo_kills"] or 0 for g in games) / n, 2),
        "plates": round(sum(g["plates"] or 0 for g in games) / n, 2),
        "first_blood": pct(sum(g["first_blood"] for g in games) / n),
        "avg_minutes": round(minutes / n, 1),
        "control_wards": round(sum(g["control_wards"] or 0 for g in games) / n, 1),
    }


BENCH_METRICS = [
    ("cs_min", "CS/min", 1),
    ("gold_min", "Oro/min", 1),
    ("dmg_min", "Daño/min", 1),
    ("vision_min", "Visión/min", 1),
    ("kp", "Part. en kills", 1),
    ("dmg_share", "% daño del equipo", 1),
    ("kda", "KDA", 1),
    ("cs10", "CS a los 10", 1),
]


def _benchmark(svc, role: str, champ: int, tier: str | None, mine: dict) -> dict | None:
    """Compara al jugador con la media del dataset (mismo campeón y rol)."""
    from app.analytics.metrics import champ_metrics

    k = svc.priors.get()["champ"]
    scope = "tier"
    f = Filters.parse(svc.db, patch="recent", tier=tier) if tier and tier in TIER_INDEX else None
    row = svc.agg.role_champs(f, role).get(champ) if f else None
    if not row or (row.get("dgames") or 0) < 150:
        f = Filters.parse(svc.db, patch="recent")
        row = svc.agg.role_champs(f, role).get(champ)
        scope = "all"
    if not row or (row.get("dgames") or 0) < 30:
        return None
    ref = champ_metrics(row, k)
    items = []
    for key, label, _ in BENCH_METRICS:
        value, avg = mine.get(key), ref.get(key)
        if value is None or avg in (None, 0):
            continue
        items.append({"key": key, "label": label, "player": value, "avg": avg, "diff_pct": round((value - avg) / abs(avg) * 100, 1)})
    return {"role": role, "champ": champ, "scope": scope, "tier": tier if scope == "tier" else None, "sample": row.get("dgames"), "metrics": items}


def _active_platform(svc, puuid: str, platform: str) -> tuple[str, str | None]:
    """La región en la que juega de verdad la cuenta y, si no es la pedida, la nota que lo explica."""
    note = None
    try:
        actual = svc.riot.active_platform(puuid, platform)
        if actual in PLATFORMS and actual != platform:
            note = f"La cuenta juega en {PLATFORMS[actual][1]}; mostrando datos de esa región."
            platform = actual
    except RiotAPIError as exc:
        log.debug("No se pudo resolver la región activa: %s", exc.message)
    return platform, note


def _save_profile(svc, puuid: str, game_name: str, tag_line: str, platform: str, solo: dict | None, summoner: dict) -> None:
    """Guarda el Riot ID, la región, el rango de SoloQ y el icono y nivel del jugador."""
    ts = now_s()
    rank = solo or {}
    upsert_player_profile(
        svc.db,
        puuid,
        game_name=game_name,
        tag_line=tag_line,
        platform=platform,
        tier=rank.get("tier"),
        division=rank.get("rank"),
        lp=rank.get("leaguePoints"),
        ranked_wins=rank.get("wins"),
        ranked_losses=rank.get("losses"),
        rank_checked_at=ts if solo else None,
        profile_icon=summoner.get("profileIconId"),
        summoner_level=summoner.get("summonerLevel"),
        profile_checked_at=ts,
    )


def _player_games(ids: list[str], matches: dict, parts: dict, puuid: str) -> list[dict]:
    """Las partidas del jugador (en el orden de ``ids``) que están en la base de datos con sus participantes."""
    games = []
    for match_id in ids:
        if match_id in matches and parts.get(match_id):
            item = _player_match(matches[match_id], parts[match_id], puuid)
            if item:
                games.append(item)
    return games


def _main_role(gs: list[dict]) -> str | None:
    """El rol más jugado en esas partidas."""
    return max({g["role"] for g in gs}, key=lambda r: sum(1 for g in gs if g["role"] == r))


def _champion_list(champs: dict[int, list[dict]]) -> list[dict]:
    """Resumen de cada campeón con su rol principal, del más jugado al que menos."""
    champ_list = []
    for champ, gs in champs.items():
        summary = _summary(gs)
        champ_list.append({"champ": champ, "main_role": _main_role(gs), **summary})
    champ_list.sort(key=lambda c: (c["games"], c["wins"]), reverse=True)
    return champ_list


def _champion_benchmarks(svc, champ_list: list[dict], champs: dict[int, list[dict]], tier: str | None) -> list[dict]:
    """Sus 3 campeones más jugados (en su rol principal) frente a la media del dataset."""
    benchmarks = []
    for champ_item in champ_list[:3]:
        if not champ_item["main_role"]:
            continue
        mine_games = [g for g in champs[champ_item["champ"]] if g["role"] == champ_item["main_role"]]
        bench = _benchmark(svc, champ_item["main_role"], champ_item["champ"], tier, _summary(mine_games))
        if bench:
            benchmarks.append(bench)
    return benchmarks


def _streak(valid: list[dict]) -> int:
    """Racha de la partida más reciente hacia atrás: victorias seguidas (positiva) o derrotas (negativa)."""
    streak = 0
    for g in valid:
        if not streak:
            streak = 1 if g["win"] else -1
        elif (streak > 0) == g["win"]:
            streak += 1 if streak > 0 else -1
        else:
            break
    return streak


def get_profile(svc, platform: str, game_name: str, tag_line: str, count: int = 20, queue: str = "solo") -> dict:
    platform = platform.lower()
    if platform not in PLATFORMS:
        raise ValueError("Plataforma no válida")
    count = max(5, min(int(count), 60))
    account = svc.riot.account_by_riot_id(game_name, tag_line, platform)
    if not account or not account.get("puuid"):
        raise NotFound(f"No existe el Riot ID {game_name}#{tag_line}")
    puuid = account["puuid"]
    game_name, tag_line = account.get("gameName") or game_name, account.get("tagLine") or tag_line
    platform, note = _active_platform(svc, puuid, platform)

    summoner = svc.riot.summoner_by_puuid(platform, puuid) or {}
    entries = svc.riot.league_entries_by_puuid(platform, puuid)
    solo = next((e for e in entries if e.get("queueType") == "RANKED_SOLO_5x5"), None)
    flex = next((e for e in entries if e.get("queueType") == "RANKED_FLEX_SR"), None)
    _save_profile(svc, puuid, game_name, tag_line, platform, solo, summoner)
    tier = (solo or {}).get("tier")

    qf = QUEUE_FILTERS.get(queue, QUEUE_FILTERS["solo"])
    ids = svc.riot.match_ids(platform, puuid, queue=qf["queue"], ranked_only=qf["ranked_only"], count=count)
    already = len(known_match_ids(svc.db, ids))
    fetched = fetch_matches(svc, ids, tier)
    matches, parts = load_match_rows(svc, ids)

    games = _player_games(ids, matches, parts, puuid)
    valid = [g for g in games if not g["remake"]]

    roles: dict[str, list[dict]] = defaultdict(list)
    champs: dict[int, list[dict]] = defaultdict(list)
    for g in valid:
        roles[g["role"] or "?"].append(g)
        champs[g["champ"]].append(g)
    role_list = sorted(
        ({"role": r, "games": len(gs), "wins": sum(g["win"] for g in gs), "wr": pct(sum(g["win"] for g in gs) / len(gs))} for r, gs in roles.items()),
        key=lambda x: x["games"],
        reverse=True,
    )
    champ_list = _champion_list(champs)
    benchmarks = _champion_benchmarks(svc, champ_list, champs, tier)
    streak = _streak(valid)

    public_games = [{k: v for k, v in g.items() if not k.startswith("_")} for g in games]
    return {
        "account": {"puuid": puuid, "game_name": game_name, "tag_line": tag_line},
        "platform": platform,
        "region": region_of(platform),
        "note": note,
        "summoner": {"icon": summoner.get("profileIconId"), "level": summoner.get("summonerLevel")},
        "ranks": {"solo": _rank(solo), "flex": _rank(flex)},
        "queue": queue,
        "summary": _summary(valid),
        "roles": role_list,
        "champions": champ_list,
        "benchmarks": benchmarks,
        "form": [g["win"] for g in valid[:10]],
        "streak": streak,
        "matches": public_games,
        "fetch": {"requested": len(ids), "from_cache": already, "downloaded": fetched},
    }


def search_known(svc, query: str, limit: int = 8) -> list[dict]:
    query = (query or "").strip()
    if len(query) < 2:
        return []
    name, _, tag = query.partition("#")
    params: list = [f"{name}%"]
    sql = "SELECT game_name, tag_line, platform, tier, division FROM players WHERE game_name LIKE ? COLLATE NOCASE"
    if tag:
        sql += " AND tag_line LIKE ? COLLATE NOCASE"
        params.append(f"{tag}%")
    sql += " AND tag_line IS NOT NULL ORDER BY (tier IS NULL), updated_at DESC LIMIT ?"
    params.append(limit)
    return [dict(r) for r in svc.db.query(sql, params)]


def _role_reference(role_ref: dict) -> dict:
    """Las medias del rol en el dataset: KDA, daño, oro, visión y CS por minuto y participación en kills."""
    ref_minutes = role_ref["minutes"]
    return {
        "kda": safe_div(role_ref["kills"] + role_ref["assists"], max(1, role_ref["deaths"])),
        "dmg_min": role_ref["dmg"] / ref_minutes,
        "gold_min": role_ref["gold"] / ref_minutes,
        "vision_min": role_ref["vision"] / ref_minutes,
        "cs_min": role_ref["cs"] / ref_minutes,
        "kp": safe_div(role_ref["kp_sum"], role_ref["kp_n"]),
    }


def _player_values(r: dict, minutes: float) -> dict:
    """Los mismos valores de un jugador en la partida."""
    return {
        "kda": safe_div((r["kills"] or 0) + (r["assists"] or 0), max(1, r["deaths"] or 0)),
        "dmg_min": (r["dmg"] or 0) / minutes,
        "gold_min": (r["gold"] or 0) / minutes,
        "vision_min": (r["vision"] or 0) / minutes,
        "cs_min": (r["cs"] or 0) / minutes,
        "kp": r["kp"],
    }


def _score(role: str | None, avg: dict, mine: dict, weights: dict) -> float | None:
    """Nota 0-10 de un jugador: media ponderada de cuánto se aleja de la media de su rol (``None`` si no hay con qué)."""
    total = weight_sum = 0.0
    for key, weight in weights.items():
        value, average = mine.get(key), avg.get(key)
        if value is None or not average:
            continue
        if key == "kp":
            term = max(-1.0, min(1.0, (value - average) / 0.2))
        else:
            term = max(-1.0, min(1.0, math.log2(max(value, 1e-3) / average)))
        if role == "SUPPORT" and key == "cs_min":
            continue
        total += weight * term
        weight_sum += weight
    if weight_sum:
        return round(max(0.0, min(10.0, 5 + 4 * total / weight_sum)), 1)
    return None


def performance_scores(svc, rows: list[dict], duration: int) -> dict[int, float]:
    """Nota 0-10 por jugador frente a la media de su rol (5 = media)."""
    ref = svc.agg.role_totals(Filters.parse(svc.db, patch="recent"))
    minutes = max(duration, 60) / 60
    weights = {"kda": 0.25, "dmg_min": 0.2, "gold_min": 0.15, "kp": 0.15, "vision_min": 0.1, "cs_min": 0.15}
    scores = {}
    team_kills = defaultdict(int)
    for r in rows:
        team_kills[r["team"]] += r["kills"] or 0
    for r in rows:
        role_ref = ref.get(r["role"] or "")
        if not role_ref or not role_ref.get("dgames") or not role_ref.get("minutes"):
            continue
        score = _score(r["role"], _role_reference(role_ref), _player_values(r, minutes), weights)
        if score is not None:
            scores[r["pid"]] = score
    return scores
