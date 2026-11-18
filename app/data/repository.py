"""Escrituras: guardar partidas, jugadores y mantener los agregados.

Cada partida se guarda en **una sola transacción** (fila de partida,
participantes y todos los contadores agregados). Si la partida ya existía no
se vuelve a contar, así que la ingesta es idempotente aunque el recolector y
una búsqueda interactiva descarguen la misma partida a la vez.
"""

from __future__ import annotations

import statistics
from collections.abc import Iterable

from app.data.db import Database, now_s
from app.ingest.extractor import MatchRecord, ParticipantRow, duration_bucket
from app.riot.routing import TIER_INDEX, TIERS, UNRANKED

CHAMP_COLS = [
    "games", "wins", "dgames", "kills", "deaths", "assists", "cs", "gold", "minutes",
    "dmg", "dmg_phys", "dmg_magic", "dmg_true", "taken", "mitigated", "cc", "vision",
    "cs10_sum", "cs10_n", "kp_sum", "kp_n", "dshare_sum", "dshare_n",
    "lane_w", "lane_l", "lane_n", "solo_kills",
    "g0", "w0", "g1", "w1", "g2", "w2", "g3", "w3",
]
MATCHUP_COLS = [
    "games", "wins", "dgames", "cs10_diff", "cs10_n", "gpm_diff", "dpm_diff",
    "lane_w", "lane_l", "lane_n", "solo_k", "solo_d", "plates_for", "plates_against",
]
SYNERGY_COLS = ["games", "wins"]
TOTALS_COLS = ["matches", "dmatches", "blue_wins", "minutes"]


def upsert_sql(table: str, keys: list[str], cols: list[str]) -> str:
    names = keys + cols
    updates = ", ".join(f"{c} = {c} + excluded.{c}" for c in cols)
    return (
        f"INSERT INTO {table} ({', '.join(names)}) VALUES ({', '.join('?' * len(names))}) "
        f"ON CONFLICT ({', '.join(keys)}) DO UPDATE SET {updates}"
    )


SQL_CHAMP = upsert_sql("agg_champ", ["role", "champ", "patch", "tier", "platform"], CHAMP_COLS)
SQL_MATCHUP = upsert_sql("agg_matchup", ["role", "champ", "opp", "patch", "tier", "platform"], MATCHUP_COLS)
SQL_SYNERGY = upsert_sql("agg_synergy", ["role1", "champ1", "role2", "champ2", "patch", "tier", "platform"], SYNERGY_COLS)
SQL_BANS = upsert_sql("agg_bans", ["champ", "patch", "tier", "platform"], ["bans"])
SQL_TOTALS = upsert_sql("agg_totals", ["patch", "tier", "platform"], TOTALS_COLS)

MATCH_COLUMNS = [
    "match_id", "platform", "region", "queue", "map_id", "patch", "game_start", "duration", "tier",
    "winner", "is_remake", "surrender", "full_roles", "aggregated",
    "first_blood", "first_tower", "first_dragon", "first_herald", "first_grubs", "first_baron", "first_inhib",
    "blue_kills", "red_kills", "blue_gold", "red_gold", "blue_towers", "red_towers",
    "blue_dragons", "red_dragons", "blue_barons", "red_barons", "blue_grubs", "red_grubs",
    "blue_heralds", "red_heralds", "ingested_at",
]
PARTICIPANT_COLUMNS = [
    "match_id", "pid", "player_id", "team", "role", "champ", "win", "kills", "deaths", "assists", "level",
    "cs", "cs10", "gold", "dmg", "dmg_phys", "dmg_magic", "dmg_true", "taken", "mitigated", "cc",
    "vision", "wards", "wards_killed", "control_wards", "kp", "dmg_share", "solo_kills", "plates",
    "lane_adv", "early_lane_adv", "max_cs_adv", "first_blood", "items", "spells", "runes",
]
SQL_INSERT_MATCH = f"INSERT OR IGNORE INTO matches ({', '.join(MATCH_COLUMNS)}) VALUES ({', '.join('?' * len(MATCH_COLUMNS))})"
SQL_INSERT_PARTICIPANT = (
    f"INSERT OR REPLACE INTO participants ({', '.join(PARTICIPANT_COLUMNS)}) "
    f"VALUES ({', '.join('?' * len(PARTICIPANT_COLUMNS))})"
)


def _csv(values: Iterable[int]) -> str:
    return ",".join(str(int(v)) for v in values)


# ---------------------------------------------------------------------- tiers
def median_tier(tiers: Iterable[str]) -> str | None:
    ranked = sorted(TIER_INDEX[t] for t in tiers if t in TIER_INDEX)
    if not ranked:
        return None
    return TIERS[int(statistics.median_low(ranked))]


def estimate_match_tier(conn, puuids: list[str], fallback: str | None) -> str:
    """Tier de la partida = mediana de los tiers conocidos de sus 10 jugadores.

    Los tiers vienen de las ligas descargadas por el recolector, así que no
    cuesta ninguna llamada extra. Si no se conoce ninguno se usa el del
    jugador semilla (o UNRANKED).
    """
    known: list[str] = []
    if puuids:
        marks = ",".join("?" * len(puuids))
        rows = conn.execute(f"SELECT tier FROM players WHERE puuid IN ({marks}) AND tier IS NOT NULL", puuids).fetchall()
        known = [r[0] for r in rows]
    if fallback and fallback in TIER_INDEX:
        known.append(fallback)
    return median_tier(known) or (fallback if fallback else UNRANKED)


# -------------------------------------------------------------------- players
def upsert_match_players(conn, rec: MatchRecord) -> dict[str, int]:
    ts = now_s()
    rows = [
        (p.puuid, p.game_name or None, p.tag_line or None, rec.platform, p.profile_icon, p.summoner_level, ts)
        for p in rec.participants
        if p.puuid and p.puuid != "BOT"
    ]
    conn.executemany(
        """
        INSERT INTO players (puuid, game_name, tag_line, platform, profile_icon, summoner_level, updated_at)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(puuid) DO UPDATE SET
            game_name = COALESCE(excluded.game_name, players.game_name),
            tag_line = COALESCE(excluded.tag_line, players.tag_line),
            platform = excluded.platform,
            profile_icon = COALESCE(excluded.profile_icon, players.profile_icon),
            summoner_level = COALESCE(excluded.summoner_level, players.summoner_level),
            updated_at = excluded.updated_at
        """,
        rows,
    )
    puuids = [r[0] for r in rows]
    if not puuids:
        return {}
    marks = ",".join("?" * len(puuids))
    return {r[1]: r[0] for r in conn.execute(f"SELECT id, puuid FROM players WHERE puuid IN ({marks})", puuids)}


def upsert_player_profile(db: Database, puuid: str, **fields) -> int:
    """Crea/actualiza un jugador con los campos indicados y devuelve su id."""
    fields = {k: v for k, v in fields.items() if v is not None}
    fields["updated_at"] = now_s()
    cols = ["puuid", *fields.keys()]
    updates = ", ".join(f"{k} = excluded.{k}" for k in fields)
    with db.write() as conn:
        conn.execute(
            f"INSERT INTO players ({', '.join(cols)}) VALUES ({', '.join('?' * len(cols))}) "
            f"ON CONFLICT(puuid) DO UPDATE SET {updates}",
            [puuid, *fields.values()],
        )
        return conn.execute("SELECT id FROM players WHERE puuid = ?", (puuid,)).fetchone()[0]


def upsert_seed_players(db: Database, platform: str, entries: list[dict], tier: str, division: str | None = None) -> int:
    ts = now_s()
    rows = []
    for e in entries:
        puuid = e.get("puuid")
        if not puuid:
            continue
        rows.append(
            (
                puuid,
                platform,
                (e.get("tier") or tier).upper(),
                e.get("rank") or division or "I",
                int(e.get("leaguePoints") or 0),
                int(e.get("wins") or 0),
                int(e.get("losses") or 0),
                ts,
                ts,
            )
        )
    if not rows:
        return 0
    with db.write() as conn:
        conn.executemany(
            """
            INSERT INTO players (puuid, platform, tier, division, lp, ranked_wins, ranked_losses,
                                 rank_checked_at, is_seed, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, 1, ?)
            ON CONFLICT(puuid) DO UPDATE SET
                platform = excluded.platform, tier = excluded.tier, division = excluded.division,
                lp = excluded.lp, ranked_wins = excluded.ranked_wins, ranked_losses = excluded.ranked_losses,
                rank_checked_at = excluded.rank_checked_at, is_seed = 1, updated_at = excluded.updated_at
            """,
            rows,
        )
    return len(rows)


# -------------------------------------------------------------------- matches
def known_match_ids(db: Database, match_ids: list[str]) -> set[str]:
    if not match_ids:
        return set()
    marks = ",".join("?" * len(match_ids))
    sql = (
        f"SELECT match_id FROM matches WHERE match_id IN ({marks}) "
        f"UNION SELECT match_id FROM legacy_matches WHERE match_id IN ({marks}) "
        f"UNION SELECT match_id FROM skipped_matches WHERE match_id IN ({marks})"
    )
    return {r[0] for r in db.query(sql, match_ids * 3)}


def mark_skipped(db: Database, match_id: str, reason: str) -> None:
    with db.write() as conn:
        conn.execute(
            "INSERT OR REPLACE INTO skipped_matches(match_id, reason, at) VALUES (?, ?, ?)",
            (match_id, reason, now_s()),
        )


def _participant_tuple(rec: MatchRecord, p: ParticipantRow, player_id: int | None) -> tuple:
    return (
        rec.match_id, p.pid, player_id, p.team, p.role, p.champ, int(p.win), p.kills, p.deaths, p.assists,
        p.level, p.cs, p.cs10, p.gold, p.dmg, p.dmg_phys, p.dmg_magic, p.dmg_true, p.taken, p.mitigated,
        p.cc, p.vision, p.wards, p.wards_killed, p.control_wards, p.kp, p.dmg_share, p.solo_kills,
        p.plates, p.lane_adv, p.early_lane_adv, p.max_cs_adv, int(p.first_blood), _csv(p.items),
        _csv(p.spells), _csv(p.runes),
    )


def _flag(value: int | None) -> int:
    return 1 if value and value > 0 else 0


def _champ_row(rec: MatchRecord, p: ParticipantRow, opp: ParticipantRow | None, tier: str) -> tuple:
    minutes = rec.minutes
    bucket = duration_bucket(minutes)
    lane_known = opp is not None and p.lane_adv is not None and opp.lane_adv is not None
    buckets = [0] * 8
    buckets[bucket * 2] = 1
    buckets[bucket * 2 + 1] = int(p.win)
    return (
        p.role, p.champ, rec.patch, tier, rec.platform,
        1, int(p.win), 1, p.kills, p.deaths, p.assists, p.cs, p.gold, minutes,
        p.dmg, p.dmg_phys, p.dmg_magic, p.dmg_true, p.taken, p.mitigated, p.cc, p.vision,
        p.cs10 or 0.0, int(p.cs10 is not None),
        p.kp or 0.0, int(p.kp is not None),
        p.dmg_share or 0.0, int(p.dmg_share is not None),
        _flag(p.lane_adv) if lane_known else 0,
        _flag(opp.lane_adv) if lane_known else 0,
        int(lane_known),
        p.solo_kills or 0,
        *buckets,
    )


def _matchup_row(rec: MatchRecord, role: str, a: ParticipantRow, b: ParticipantRow, tier: str) -> tuple:
    minutes = rec.minutes
    cs_known = a.cs10 is not None and b.cs10 is not None
    lane_known = a.lane_adv is not None and b.lane_adv is not None
    return (
        role, a.champ, b.champ, rec.patch, tier, rec.platform,
        1, int(a.win), 1,
        (a.cs10 - b.cs10) if cs_known else 0.0, int(cs_known),
        (a.gold - b.gold) / minutes, (a.dmg - b.dmg) / minutes,
        _flag(a.lane_adv) if lane_known else 0,
        _flag(b.lane_adv) if lane_known else 0,
        int(lane_known),
        a.solo_kills or 0, b.solo_kills or 0,
        a.plates or 0, b.plates or 0,
    )


def store_match(db: Database, rec: MatchRecord, *, seed_tier: str | None, stats_queues: tuple[int, ...]) -> str:
    """Guarda la partida. Devuelve ``stored``, ``duplicate``, ``remake`` o ``ignored``.

    * ``stored``: guardada (y agregada si es una cola de estadísticas).
    * ``remake``: se registra para no volver a pedirla, pero no cuenta.
    * ``ignored``: otro mapa (ARAM, Arena…): solo se marca como vista.
    """
    if not rec.is_rift:
        with db.write() as conn:
            conn.execute(
                "INSERT OR IGNORE INTO skipped_matches(match_id, reason, at) VALUES (?, ?, ?)",
                (rec.match_id, f"map {rec.map_id} / queue {rec.queue}", now_s()),
            )
        return "ignored"

    aggregate = (not rec.is_remake) and rec.queue in stats_queues and rec.patch != "unknown"
    with db.write() as conn:
        # Las partidas del histórico ya están contadas en los agregados 'legacy'.
        if aggregate and conn.execute("SELECT 1 FROM legacy_matches WHERE match_id = ?", (rec.match_id,)).fetchone():
            aggregate = False
        tier = estimate_match_tier(conn, [p.puuid for p in rec.participants if p.puuid], seed_tier)
        o, t = rec.objectives, rec.team_totals
        cursor = conn.execute(
            SQL_INSERT_MATCH,
            (
                rec.match_id, rec.platform, rec.region, rec.queue, rec.map_id, rec.patch, rec.game_start,
                rec.duration, tier, rec.winner, int(rec.is_remake), int(rec.surrender), int(rec.full_roles),
                int(aggregate),
                o["first_blood"], o["first_tower"], o["first_dragon"], o["first_herald"], o["first_grubs"],
                o["first_baron"], o["first_inhib"],
                t["blue_kills"], t["red_kills"], t["blue_gold"], t["red_gold"], t["blue_towers"], t["red_towers"],
                t["blue_dragons"], t["red_dragons"], t["blue_barons"], t["red_barons"], t["blue_grubs"],
                t["red_grubs"], t["blue_heralds"], t["red_heralds"], now_s(),
            ),
        )
        if cursor.rowcount == 0:
            return "duplicate"
        if rec.is_remake:
            return "remake"

        player_ids = upsert_match_players(conn, rec)
        conn.executemany(
            SQL_INSERT_PARTICIPANT,
            [_participant_tuple(rec, p, player_ids.get(p.puuid)) for p in rec.participants],
        )
        if aggregate:
            _aggregate(conn, rec, tier)
    return "stored"


def _aggregate(conn, rec: MatchRecord, tier: str) -> None:
    opponents: dict[int, ParticipantRow] = {}
    matchup_rows = []
    for role, blue, red in rec.lane_pairs():
        opponents[blue.pid] = red
        opponents[red.pid] = blue
        matchup_rows.append(_matchup_row(rec, role, blue, red, tier))
        matchup_rows.append(_matchup_row(rec, role, red, blue, tier))

    champ_rows = [_champ_row(rec, p, opponents.get(p.pid), tier) for p in rec.participants if p.role]
    synergy_rows = [
        (a.role, a.champ, b.role, b.champ, rec.patch, tier, rec.platform, 1, int(a.win))
        for _, a, b in rec.team_pairs()
    ]
    conn.executemany(SQL_CHAMP, champ_rows)
    conn.executemany(SQL_MATCHUP, matchup_rows)
    conn.executemany(SQL_SYNERGY, synergy_rows)
    conn.executemany(SQL_BANS, [(champ, rec.patch, tier, rec.platform, 1) for champ in rec.bans])
    conn.execute(
        SQL_TOTALS,
        (rec.patch, tier, rec.platform, 1, 1, int(rec.winner == 100), rec.minutes),
    )
