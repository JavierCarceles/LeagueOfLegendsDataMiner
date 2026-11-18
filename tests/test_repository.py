"""app/data/repository.py::store_match sobre una BD temporal."""

from __future__ import annotations

import pytest

from app.analytics.aggregates import Aggregates
from app.analytics.filters import Filters
from app.data.repository import known_match_ids, median_tier, store_match, upsert_seed_players
from app.ingest.extractor import parse_match
from app.riot.routing import ROLE_INDEX
from tests.factories import (
    AATROX,
    AHRI,
    DARIUS,
    DEFAULT_BLUE,
    DEFAULT_RED,
    ROLES,
    make_match,
    participant,
)

QUEUES = (420, 440)
AGG_TABLES = ("agg_champ", "agg_matchup", "agg_synergy", "agg_bans", "agg_totals")
LANES = [(role, DEFAULT_BLUE[role], DEFAULT_RED[role]) for role in ROLES]


def store(db, payload, seed_tier="DIAMOND", queues=QUEUES):
    return store_match(db, parse_match(payload), seed_tier=seed_tier, stats_queues=queues)


def dump_aggregates(db) -> dict:
    return {table: sorted(tuple(r) for r in db.query(f"SELECT * FROM {table}")) for table in AGG_TABLES}


def assert_no_aggregates(db):
    for table in AGG_TABLES:
        assert db.scalar(f"SELECT COUNT(*) FROM {table}") == 0, table


def cs10(raw: dict) -> float:
    return raw["challenges"]["laneMinionsFirst10Minutes"] + raw["challenges"]["jungleCsBefore10Minutes"]


# ------------------------------------------------------------- matchups
def test_matchups_are_stored_in_both_directions(db):
    payload = make_match("EUW1_1", blue_win=True)
    assert store(db, payload) == "stored"
    rows = {(r["role"], r["champ"], r["opp"]): dict(r) for r in db.query("SELECT * FROM agg_matchup")}
    assert len(rows) == 10
    for role, blue, red in LANES:
        assert (rows[(role, blue, red)]["games"], rows[(role, blue, red)]["wins"]) == (1, 1)
        assert (rows[(role, red, blue)]["games"], rows[(role, red, blue)]["wins"]) == (1, 0)
    row = rows[("TOP", AATROX, DARIUS)]
    assert (row["patch"], row["tier"], row["platform"]) == ("16.22", "DIAMOND", "euw1")
    assert row["dgames"] == 1


def test_matchup_wins_follow_the_winner(db):
    store(db, make_match("EUW1_1", blue_win=False))
    store(db, make_match("EUW1_2", blue_win=False))
    store(db, make_match("EUW1_3", blue_win=True))
    agg = Aggregates(db)
    assert agg.matchup(Filters(), "TOP", AATROX, DARIUS)["games"] == 3
    assert agg.matchup(Filters(), "TOP", AATROX, DARIUS)["wins"] == 1
    assert agg.matchup(Filters(), "TOP", DARIUS, AATROX)["wins"] == 2


def test_matchup_lane_metrics_are_antisymmetric(db):
    payload = make_match("EUW1_1", blue_win=True)
    store(db, payload)
    blue, red = participant(payload, 1), participant(payload, 6)
    fwd = dict(db.one("SELECT * FROM agg_matchup WHERE role='TOP' AND champ=? AND opp=?", (AATROX, DARIUS)))
    rev = dict(db.one("SELECT * FROM agg_matchup WHERE role='TOP' AND champ=? AND opp=?", (DARIUS, AATROX)))
    assert fwd["cs10_diff"] == pytest.approx(cs10(blue) - cs10(red)) and fwd["cs10_n"] == 1
    assert rev["cs10_diff"] == pytest.approx(-fwd["cs10_diff"])
    assert fwd["gpm_diff"] == pytest.approx((blue["goldEarned"] - red["goldEarned"]) / 30)
    assert rev["gpm_diff"] == pytest.approx(-fwd["gpm_diff"])
    assert fwd["dpm_diff"] == pytest.approx((blue["totalDamageDealtToChampions"] - red["totalDamageDealtToChampions"]) / 30)
    assert (fwd["lane_w"], fwd["lane_l"], fwd["lane_n"]) == (1, 0, 1)
    assert (rev["lane_w"], rev["lane_l"], rev["lane_n"]) == (0, 1, 1)
    assert (fwd["solo_k"], fwd["solo_d"]) == (blue["challenges"]["soloKills"], red["challenges"]["soloKills"])
    assert (fwd["plates_for"], fwd["plates_against"]) == (
        blue["challenges"]["turretPlatesTaken"],
        red["challenges"]["turretPlatesTaken"],
    )


# ---------------------------------------------------------------- campeón
def test_agg_champ_has_one_row_per_participant(db):
    payload = make_match("EUW1_1", blue_win=True)
    store(db, payload)
    rows = {(r["role"], r["champ"]): dict(r) for r in db.query("SELECT * FROM agg_champ")}
    assert len(rows) == 10
    for pid in range(1, 11):
        raw = participant(payload, pid)
        role = ROLES[(pid - 1) % 5]
        r = rows[(role, raw["championId"])]
        assert (r["games"], r["wins"], r["dgames"]) == (1, int(raw["win"]), 1)
        assert (r["kills"], r["deaths"], r["assists"]) == (raw["kills"], raw["deaths"], raw["assists"])
        assert r["cs"] == raw["totalMinionsKilled"] + raw["neutralMinionsKilled"]
        assert r["gold"] == raw["goldEarned"]
        assert r["dmg"] == raw["totalDamageDealtToChampions"]
        assert r["minutes"] == pytest.approx(30.0)
        assert (r["cs10_sum"], r["cs10_n"]) == (pytest.approx(cs10(raw)), 1)
        assert (r["kp_sum"], r["kp_n"]) == (pytest.approx(raw["challenges"]["killParticipation"]), 1)
        assert (r["lane_w"], r["lane_l"], r["lane_n"]) == ((1, 0, 1) if raw["win"] else (0, 1, 1))
        assert r["solo_kills"] == raw["challenges"]["soloKills"]
        # 30 min -> tramo 2 (30-35 min)
        assert (r["g0"], r["g1"], r["g2"], r["g3"]) == (0, 0, 1, 0)
        assert r["w2"] == int(raw["win"])


# --------------------------------------------------------------- sinergia
def test_synergy_rows_use_canonical_role_order(db):
    payload = make_match("EUW1_1", blue_win=True)
    payload["info"]["participants"].reverse()  # el orden del payload no debe importar
    store(db, payload)
    rows = db.query("SELECT * FROM agg_synergy")
    assert len(rows) == 20
    blue = set(DEFAULT_BLUE.values())
    for r in rows:
        assert ROLE_INDEX[r["role1"]] < ROLE_INDEX[r["role2"]]
        same_team = blue if r["champ1"] in blue else set(DEFAULT_RED.values())
        assert r["champ2"] in same_team
        assert r["games"] == 1
        assert r["wins"] == (1 if same_team is blue else 0)
    # Las consultas no dependen del orden en que se pregunte.
    agg = Aggregates(db)
    assert agg.synergy(Filters(), "MID", AHRI, "TOP", AATROX) == (1, 1)
    assert agg.synergy(Filters(), "TOP", AATROX, "MID", AHRI) == (1, 1)
    assert agg.partners(Filters(), "MID", AHRI, "TOP") == {AATROX: (1, 1)}
    assert agg.partners(Filters(), "TOP", AATROX, "MID") == {AHRI: (1, 1)}


# ------------------------------------------------------- totales y bans
def test_totals_and_bans(db):
    rec = parse_match(make_match("EUW1_1", blue_win=True))
    store(db, make_match("EUW1_1", blue_win=True))
    t = dict(db.one("SELECT * FROM agg_totals"))
    assert (t["patch"], t["tier"], t["platform"]) == ("16.22", "DIAMOND", "euw1")
    assert (t["matches"], t["dmatches"], t["blue_wins"]) == (1, 1, 1)
    assert t["minutes"] == pytest.approx(30.0)
    bans = {r["champ"]: r["bans"] for r in db.query("SELECT champ, bans FROM agg_bans")}
    assert bans == {champ: 1 for champ in rec.bans}

    store(db, make_match("EUW1_2", blue_win=False, duration=2400))
    t = dict(db.one("SELECT * FROM agg_totals"))
    assert (t["matches"], t["dmatches"], t["blue_wins"]) == (2, 2, 1)
    assert t["minutes"] == pytest.approx(70.0)
    assert {r["champ"]: r["bans"] for r in db.query("SELECT champ, bans FROM agg_bans")} == {c: 2 for c in rec.bans}


# --------------------------------------------------------- idempotencia
def test_storing_twice_is_a_duplicate_and_does_not_double_count(db):
    payload = make_match("EUW1_1")
    assert store(db, payload) == "stored"
    snapshot = dump_aggregates(db)
    assert store(db, payload) == "duplicate"
    assert dump_aggregates(db) == snapshot
    assert db.scalar("SELECT COUNT(*) FROM matches") == 1
    assert db.scalar("SELECT COUNT(*) FROM participants") == 10
    assert db.scalar("SELECT matches FROM agg_totals") == 1


# ------------------------------------------------------- casos que no cuentan
def test_remake_is_recorded_but_not_aggregated(db):
    payload = make_match("EUW1_3", duration=200)
    assert store(db, payload) == "remake"
    m = db.one("SELECT * FROM matches WHERE match_id = 'EUW1_3'")
    assert m["is_remake"] == 1 and m["aggregated"] == 0
    assert db.scalar("SELECT COUNT(*) FROM participants") == 0
    assert_no_aggregates(db)
    assert known_match_ids(db, ["EUW1_3"]) == {"EUW1_3"}
    assert store(db, payload) == "duplicate"


def test_early_surrender_is_a_remake(db):
    assert store(db, make_match("EUW1_4", duration=1000, early_surrender=True)) == "remake"
    assert_no_aggregates(db)


def test_other_maps_are_ignored(db):
    assert store(db, make_match("EUW1_5", map_id=12, queue=450)) == "ignored"
    assert db.scalar("SELECT COUNT(*) FROM matches") == 0
    reason = db.scalar("SELECT reason FROM skipped_matches WHERE match_id = 'EUW1_5'")
    assert reason and "12" in reason
    assert_no_aggregates(db)
    assert known_match_ids(db, ["EUW1_5", "EUW1_6"]) == {"EUW1_5"}


def test_match_already_in_legacy_history_is_stored_but_not_aggregated(db):
    with db.write() as conn:
        conn.execute("INSERT INTO legacy_matches(match_id) VALUES ('EUW1_7')")
    assert store(db, make_match("EUW1_7")) == "stored"
    m = db.one("SELECT * FROM matches WHERE match_id = 'EUW1_7'")
    assert m["aggregated"] == 0 and m["is_remake"] == 0
    assert db.scalar("SELECT COUNT(*) FROM participants WHERE match_id = 'EUW1_7'") == 10
    assert_no_aggregates(db)


def test_queue_outside_stats_queues_is_stored_but_not_aggregated(db):
    assert store(db, make_match("EUW1_8", queue=400)) == "stored"
    assert db.scalar("SELECT aggregated FROM matches WHERE match_id = 'EUW1_8'") == 0
    assert db.scalar("SELECT COUNT(*) FROM participants") == 10
    assert_no_aggregates(db)
    # La misma cola sí cuenta si se configura como cola de estadísticas.
    assert store(db, make_match("EUW1_9", queue=400), queues=(400,)) == "stored"
    assert db.scalar("SELECT aggregated FROM matches WHERE match_id = 'EUW1_9'") == 1
    assert db.scalar("SELECT matches FROM agg_totals") == 1


def test_unknown_patch_is_not_aggregated(db):
    assert store(db, make_match("EUW1_10", game_version="")) == "stored"
    assert db.scalar("SELECT aggregated FROM matches WHERE match_id = 'EUW1_10'") == 0
    assert_no_aggregates(db)


def test_partial_roles_are_aggregated_without_inventing_matchups(db):
    assert store(db, make_match("EUW1_11", positions={2: "TOP"})) == "stored"
    assert db.scalar("SELECT full_roles FROM matches") == 0
    assert {r[0] for r in db.query("SELECT DISTINCT role FROM agg_matchup")} == {"MID", "ADC", "SUPPORT"}
    assert db.scalar("SELECT COUNT(*) FROM agg_champ") == 8
    assert db.scalar("SELECT COUNT(*) FROM agg_synergy") == 3 + 10
    assert db.scalar("SELECT COUNT(*) FROM participants WHERE role IS NULL") == 2


# ------------------------------------------------------------- jugadores
def test_players_and_participants_are_linked(db):
    payload = make_match("EUW1_12")
    store(db, payload)
    assert db.scalar("SELECT COUNT(*) FROM players") == 10
    row = db.one(
        "SELECT pl.puuid, pl.game_name, pl.tag_line, pl.platform, pa.role, pa.champ, pa.items, pa.spells, pa.runes "
        "FROM participants pa JOIN players pl ON pl.id = pa.player_id WHERE pa.match_id = 'EUW1_12' AND pa.pid = 1"
    )
    assert row["puuid"] == participant(payload, 1)["puuid"]
    assert (row["game_name"], row["tag_line"], row["platform"]) == ("Player1", "EUW", "euw1")
    assert (row["role"], row["champ"]) == ("TOP", AATROX)
    assert row["items"] == "3071,3047,6333,3053,3742,0,3364"
    assert row["spells"] == "4,12"
    assert row["runes"] == "8010,8000,8400"


def test_match_flags_and_objectives_are_stored(db):
    store(db, make_match("EUW1_13", blue_win=False, duration=1500, surrender=True))
    m = dict(db.one("SELECT * FROM matches WHERE match_id = 'EUW1_13'"))
    assert (m["platform"], m["region"], m["queue"], m["map_id"], m["patch"]) == ("euw1", "europe", 420, 11, "16.22")
    assert (m["winner"], m["duration"], m["surrender"], m["full_roles"], m["aggregated"]) == (200, 1500, 1, 1, 1)
    assert (m["first_blood"], m["first_dragon"], m["first_baron"]) == (100, 200, 200)
    assert (m["blue_towers"], m["red_towers"]) == (8, 4)


# ------------------------------------------------------------------ tiers
def test_match_tier_is_the_median_of_known_players(db):
    payload = make_match("EUW1_14")
    entries = [
        {"puuid": participant(payload, 1)["puuid"], "tier": "GOLD"},
        {"puuid": participant(payload, 2)["puuid"], "tier": "PLATINUM"},
        {"puuid": participant(payload, 7)["puuid"], "tier": "EMERALD"},
    ]
    upsert_seed_players(db, "euw1", entries, "GOLD")
    store(db, payload, seed_tier="DIAMOND")
    # median_low([GOLD, PLATINUM, EMERALD, DIAMOND]) = PLATINUM
    assert db.scalar("SELECT tier FROM matches WHERE match_id = 'EUW1_14'") == "PLATINUM"
    assert db.scalar("SELECT DISTINCT tier FROM agg_matchup") == "PLATINUM"


def test_match_tier_falls_back_to_seed_tier_then_unranked(db):
    store(db, make_match("EUW1_15"), seed_tier="MASTER")
    store(db, make_match("EUW1_16"), seed_tier=None)
    tiers = {r[0]: r[1] for r in db.query("SELECT match_id, tier FROM matches")}
    assert tiers == {"EUW1_15": "MASTER", "EUW1_16": "UNRANKED"}


@pytest.mark.parametrize(
    "tiers, expected",
    [([], None), (["GOLD"], "GOLD"), (["IRON", "CHALLENGER"], "IRON"), (["GOLD", "SILVER", "DIAMOND"], "GOLD"), (["BOGUS", "EMERALD"], "EMERALD")],
)
def test_median_tier(tiers, expected):
    assert median_tier(tiers) == expected
