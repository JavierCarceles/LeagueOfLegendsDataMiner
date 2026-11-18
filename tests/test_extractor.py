"""app/ingest/extractor.py: de match-v5 a MatchRecord."""

from __future__ import annotations

import pytest

from app.ingest.extractor import REMAKE_SECONDS, duration_bucket, parse_match, parse_patch
from app.riot.routing import ROLE_INDEX, ROLES
from tests.factories import (
    AATROX,
    AHRI,
    DARIUS,
    EZREAL,
    GAME_START_MS,
    GAREN,
    JINX,
    KAISA,
    LEE_SIN,
    LEONA,
    NAUTILUS,
    ORIANNA,
    SYNDRA,
    THRESH,
    VIEGO,
    WUKONG,
    ZED,
    make_match,
    participant,
)

ROLE_PAIRS = {(ROLES[i], ROLES[j]) for i in range(5) for j in range(i + 1, 5)}


def roles_by_pid(rec):
    return {p.pid: p.role for p in rec.participants}


# ------------------------------------------------------------------ básicos
def test_basic_fields():
    rec = parse_match(make_match("EUW1_123"))
    assert rec.match_id == "EUW1_123"
    assert rec.platform == "euw1" and rec.region == "europe"
    assert rec.queue == 420 and rec.map_id == 11 and rec.is_rift
    assert rec.patch == "16.22"
    assert rec.duration == 1800 and rec.minutes == 30.0
    assert rec.game_start == GAME_START_MS
    assert rec.winner == 100
    assert not rec.is_remake and not rec.surrender
    assert len(rec.participants) == 10
    assert rec.full_roles


def test_red_win_and_participant_win_flags():
    rec = parse_match(make_match(blue_win=False))
    assert rec.winner == 200
    assert all(p.win == (p.team == 200) for p in rec.participants)


def test_platform_falls_back_to_match_id_prefix():
    payload = make_match("KR_7001")
    del payload["info"]["platformId"]
    rec = parse_match(payload)
    assert rec.platform == "kr" and rec.region == "asia"


@pytest.mark.parametrize("prefix, region", [("NA1", "americas"), ("OC1", "sea"), ("SG2", "sea"), ("EUN1", "europe")])
def test_region_routing(prefix, region):
    assert parse_match(make_match(f"{prefix}_99")).region == region


def test_unknown_platform_returns_none():
    assert parse_match(make_match("XX9_1")) is None


@pytest.mark.parametrize(
    "payload",
    [None, [], "EUW1_1", {}, {"metadata": {"matchId": "EUW1_1"}, "info": {"participants": []}}, {"metadata": {}, "info": {"participants": [{}]}}],
)
def test_malformed_payloads_return_none(payload):
    assert parse_match(payload) is None


# -------------------------------------------------------------------- roles
def test_team_position_mapping():
    rec = parse_match(make_match())
    expected = ["TOP", "JUNGLE", "MID", "ADC", "SUPPORT"]
    by_pid = roles_by_pid(rec)
    assert [by_pid[i] for i in range(1, 6)] == expected  # MIDDLE->MID, BOTTOM->ADC, UTILITY->SUPPORT
    assert [by_pid[i] for i in range(6, 11)] == expected


def test_lowercase_team_position_is_accepted():
    rec = parse_match(make_match(positions={3: "middle", 4: "bottom", 5: "utility"}))
    assert [roles_by_pid(rec)[i] for i in (3, 4, 5)] == ["MID", "ADC", "SUPPORT"]


def test_duplicate_team_position_drops_both_roles():
    # El azul tiene dos TOP y ningún JUNGLE.
    rec = parse_match(make_match(positions={2: "TOP"}))
    by_pid = roles_by_pid(rec)
    assert by_pid[1] is None and by_pid[2] is None
    assert [by_pid[i] for i in (3, 4, 5)] == ["MID", "ADC", "SUPPORT"]
    assert all(by_pid[i] is not None for i in range(6, 11))  # el rojo no se toca
    assert rec.full_roles is False
    assert [role for role, _, _ in rec.lane_pairs()] == ["MID", "ADC", "SUPPORT"]
    pairs = list(rec.team_pairs())
    assert sum(1 for team, _, _ in pairs if team == 100) == 3  # C(3, 2)
    assert sum(1 for team, _, _ in pairs if team == 200) == 10


def test_same_position_in_different_teams_is_fine():
    rec = parse_match(make_match())
    assert len({(p.team, p.role) for p in rec.participants}) == 10
    assert rec.full_roles


@pytest.mark.parametrize("position", ["", "Invalid", "NONE"])
def test_empty_or_unknown_team_position_means_no_role(position):
    rec = parse_match(make_match(positions={7: position}))
    assert roles_by_pid(rec)[7] is None
    assert not rec.full_roles
    assert "JUNGLE" not in [role for role, _, _ in rec.lane_pairs()]


# ------------------------------------------------------------------ remakes
@pytest.mark.parametrize(
    "duration, expected",
    [(120, True), (REMAKE_SECONDS - 1, True), (REMAKE_SECONDS, False), (301, False), (1800, False)],
)
def test_remake_by_duration(duration, expected):
    assert parse_match(make_match(duration=duration)).is_remake is expected


def test_remake_by_early_surrender_even_if_long():
    rec = parse_match(make_match(duration=900, early_surrender=True))
    assert rec.is_remake


def test_remake_if_any_player_flags_team_early_surrender():
    rec = parse_match(make_match(duration=900, overrides={8: {"teamEarlySurrendered": True}}))
    assert rec.is_remake


def test_normal_surrender_is_not_a_remake():
    rec = parse_match(make_match(duration=1300, surrender=True))
    assert rec.surrender and not rec.is_remake


def test_pre_11_20_duration_in_milliseconds():
    rec = parse_match(make_match(duration=1_754_321, end_timestamp=False))
    assert rec.duration == 1754


def test_minutes_has_a_floor_of_one_minute():
    assert parse_match(make_match(duration=30)).minutes == 1.0


# -------------------------------------------------------------------- parche
@pytest.mark.parametrize(
    "version, patch",
    [
        ("16.22.1.2", "16.22"),
        ("14.3.567.8910", "14.3"),
        ("15.24.734.7485", "15.24"),
        ("016.01.1", "16.1"),
        ("16", "unknown"),
        ("", "unknown"),
        (None, "unknown"),
        ("abc.def", "unknown"),
        ("16.x.1", "unknown"),
    ],
)
def test_parse_patch(version, patch):
    assert parse_patch(version) == patch


def test_patch_from_payload():
    assert parse_match(make_match(game_version="15.24.734.7485")).patch == "15.24"


# --------------------------------------------------------- bans y objetivos
def test_bans_are_deduplicated_and_skip_empty_slots():
    rec = parse_match(make_match(bans=([GAREN, SYNDRA, -1, EZREAL, LEONA], [WUKONG, GAREN, -1, 0, ORIANNA])))
    assert rec.bans == [GAREN, SYNDRA, EZREAL, LEONA, WUKONG, ORIANNA]


def test_objectives_first_team_ids():
    rec = parse_match(make_match())
    assert rec.objectives == {
        "first_blood": 100,
        "first_tower": 100,
        "first_dragon": 200,
        "first_herald": 100,
        "first_grubs": 200,
        "first_baron": 200,
        "first_inhib": 100,
    }


def test_objective_nobody_took_is_none():
    rec = parse_match(make_match(objectives={200: {"baron": {"first": False, "kills": 0}}}))
    assert rec.objectives["first_baron"] is None
    assert rec.team_totals["red_barons"] == 0


def test_team_totals():
    payload = make_match()
    rec = parse_match(payload)
    raw = payload["info"]["participants"]
    t = rec.team_totals
    assert t["blue_kills"] == sum(p["kills"] for p in raw if p["teamId"] == 100)
    assert t["red_kills"] == sum(p["kills"] for p in raw if p["teamId"] == 200)
    assert t["blue_gold"] == sum(p["goldEarned"] for p in raw if p["teamId"] == 100)
    assert t["red_gold"] == sum(p["goldEarned"] for p in raw if p["teamId"] == 200)
    assert (t["blue_towers"], t["red_towers"]) == (8, 4)
    assert (t["blue_dragons"], t["red_dragons"]) == (1, 3)
    assert (t["blue_barons"], t["red_barons"]) == (0, 1)
    assert (t["blue_grubs"], t["red_grubs"]) == (2, 4)
    assert (t["blue_heralds"], t["red_heralds"]) == (1, 0)


def test_team_kills_fall_back_to_participants_without_objectives():
    payload = make_match()
    for team in payload["info"]["teams"]:
        team["objectives"] = {}
    rec = parse_match(payload)
    raw = payload["info"]["participants"]
    assert rec.team_totals["blue_kills"] == sum(p["kills"] for p in raw if p["teamId"] == 100)
    assert rec.objectives["first_blood"] is None


# ------------------------------------------------------------- participantes
def test_participant_fields_are_mapped():
    payload = make_match()
    rec = parse_match(payload)
    p = next(x for x in rec.participants if x.pid == 1)
    raw = participant(payload, 1)
    assert (p.puuid, p.game_name, p.tag_line) == (raw["puuid"], "Player1", "EUW")
    assert (p.team, p.champ, p.win) == (100, AATROX, True)
    assert (p.kills, p.deaths, p.assists) == (raw["kills"], raw["deaths"], raw["assists"])
    assert p.level == raw["champLevel"]
    assert p.cs == raw["totalMinionsKilled"] + raw["neutralMinionsKilled"]
    assert p.gold == raw["goldEarned"]
    assert p.dmg == raw["totalDamageDealtToChampions"]
    assert (p.dmg_phys, p.dmg_magic, p.dmg_true) == (
        raw["physicalDamageDealtToChampions"],
        raw["magicDamageDealtToChampions"],
        raw["trueDamageDealtToChampions"],
    )
    assert (p.taken, p.mitigated) == (raw["totalDamageTaken"], raw["damageSelfMitigated"])
    assert p.cc == float(raw["timeCCingOthers"])
    assert (p.vision, p.wards, p.wards_killed, p.control_wards) == (28, 11, 3, 3)
    ch = raw["challenges"]
    assert p.kp == ch["killParticipation"]
    assert p.dmg_share == ch["teamDamagePercentage"]
    assert p.solo_kills == ch["soloKills"]
    assert p.plates == ch["turretPlatesTaken"]
    assert p.lane_adv == ch["laningPhaseGoldExpAdvantage"]
    assert p.early_lane_adv == ch["earlyLaningPhaseGoldExpAdvantage"]
    assert p.max_cs_adv == ch["maxCsAdvantageOnLaneOpponent"]
    assert p.first_blood is True
    assert p.items == [raw[f"item{i}"] for i in range(7)]
    assert p.spells == [raw["summoner1Id"], raw["summoner2Id"]]
    assert p.runes == [8010, 8000, 8400]  # piedra angular, rama principal, rama secundaria
    assert (p.profile_icon, p.summoner_level) == (5001, 301)


def test_champions_per_participant():
    rec = parse_match(make_match())
    assert [p.champ for p in rec.participants] == [AATROX, LEE_SIN, AHRI, JINX, THRESH, DARIUS, VIEGO, ZED, KAISA, NAUTILUS]


def test_cs10_is_lane_plus_jungle_cs():
    payload = make_match(
        overrides={
            1: {"challenges": {"laneMinionsFirst10Minutes": 71, "jungleCsBefore10Minutes": 6}},
            2: {"challenges": {"laneMinionsFirst10Minutes": 4, "jungleCsBefore10Minutes": 56.0}},
        }
    )
    participant(payload, 3)["challenges"].pop("laneMinionsFirst10Minutes")
    participant(payload, 3)["challenges"].pop("jungleCsBefore10Minutes")
    participant(payload, 4)["challenges"].pop("laneMinionsFirst10Minutes")
    participant(payload, 4)["challenges"]["jungleCsBefore10Minutes"] = 9
    cs10 = {p.pid: p.cs10 for p in parse_match(payload).participants}
    assert cs10[1] == 77.0
    assert cs10[2] == 60.0
    assert cs10[3] is None  # sin datos de challenges: desconocido, no 0
    assert cs10[4] == 9.0


def test_missing_challenges_give_unknown_lane_metrics():
    payload = make_match()
    participant(payload, 6)["challenges"] = {}
    p = next(x for x in parse_match(payload).participants if x.pid == 6)
    assert p.cs10 is None and p.lane_adv is None and p.solo_kills is None and p.plates is None


def test_kill_participation_and_damage_share_fallbacks():
    payload = make_match()
    for raw in payload["info"]["participants"]:
        raw["challenges"].pop("killParticipation")
        raw["challenges"].pop("teamDamagePercentage")
    rec = parse_match(payload)
    raw_all = payload["info"]["participants"]
    blue_kills = sum(r["kills"] for r in raw_all if r["teamId"] == 100)
    blue_dmg = sum(r["totalDamageDealtToChampions"] for r in raw_all if r["teamId"] == 100)
    raw = participant(payload, 2)
    p = next(x for x in rec.participants if x.pid == 2)
    assert p.kp == pytest.approx((raw["kills"] + raw["assists"]) / blue_kills)
    assert p.dmg_share == pytest.approx(raw["totalDamageDealtToChampions"] / blue_dmg)


# ------------------------------------------------------------ parejas
def test_lane_pairs():
    rec = parse_match(make_match())
    pairs = list(rec.lane_pairs())
    assert [(role, b.champ, r.champ) for role, b, r in pairs] == [
        ("TOP", AATROX, DARIUS),
        ("JUNGLE", LEE_SIN, VIEGO),
        ("MID", AHRI, ZED),
        ("ADC", JINX, KAISA),
        ("SUPPORT", THRESH, NAUTILUS),
    ]
    assert all(b.team == 100 and r.team == 200 for _, b, r in pairs)


def test_team_pairs_are_role_ordered_even_if_payload_order_is_not():
    payload = make_match()
    payload["info"]["participants"].reverse()
    rec = parse_match(payload)
    pairs = list(rec.team_pairs())
    assert len(pairs) == 20
    for team, a, b in pairs:
        assert a.team == b.team == team
        assert ROLE_INDEX[a.role] < ROLE_INDEX[b.role]
    for team in (100, 200):
        assert {(a.role, b.role) for t, a, b in pairs if t == team} == ROLE_PAIRS


@pytest.mark.parametrize("minutes, bucket", [(0, 0), (24.99, 0), (25, 1), (29.99, 1), (30, 2), (34.99, 2), (35, 3), (60, 3)])
def test_duration_bucket(minutes, bucket):
    assert duration_bucket(minutes) == bucket
