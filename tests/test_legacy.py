"""app/ingest/legacy.py::import_legacy con un matchups_stats.json sintético."""

from __future__ import annotations

import json

import pytest

from app.analytics.aggregates import Aggregates
from app.analytics.filters import Filters
from app.data.repository import known_match_ids, store_match
from app.ddragon import DDragon
from app.ingest.extractor import parse_match
from app.ingest.legacy import import_legacy, legacy_status
from tests.factories import AATROX, AHRI, DARIUS, JINX, KAISA, LEE_SIN, NAUTILUS, THRESH, VIEGO, WUKONG, ZED, make_match

SIMPLE = {
    "processed_matches": [{"id": "EUW1_1"}, {"id": "EUW1_2"}, "KR_3", {"id": None}, 42, "nounderscore"],
    "stats": {
        "TOP": {
            "aatrox_vs_darius": {"wins": 3, "losses": 1, "elos": {"GOLD": {"wins": 3, "losses": 1}}},
            "ahri_vs_ahri": {"wins": 2, "losses": 2, "elos": {"PLATINUM": {"wins": 2, "losses": 2}}},
        }
    },
}

# 10 partidas "completas": cada rol suma 10 partidas en DIAMOND (con alias de rol antiguos).
FULL = {
    "processed_matches": [{"id": f"EUW1_{i}"} for i in range(10)],
    "stats": {
        "TOP": {"aatrox_vs_darius": {"wins": 6, "losses": 4, "elos": {"DIAMOND": {"wins": 6, "losses": 4}}}},
        "JUNGLE": {"leesin_vs_viego": {"wins": 5, "losses": 5, "elos": {"DIAMOND": {"wins": 5, "losses": 5}}}},
        "MIDDLE": {"ahri_vs_zed": {"wins": 7, "losses": 3, "elos": {"DIAMOND": {"wins": 7, "losses": 3}}}},
        "BOTTOM": {"jinx_vs_kaisa": {"wins": 4, "losses": 6, "elos": {"DIAMOND": {"wins": 4, "losses": 6}}}},
        "UTILITY": {"thresh_vs_nautilus": {"wins": 5, "losses": 5, "elos": {"DIAMOND": {"wins": 5, "losses": 5}}}},
    },
}


def write(tmp_path, data) -> object:
    path = tmp_path / "matchups_stats.json"
    path.write_text(json.dumps(data), encoding="utf-8")
    return path


def legacy_matchups(db) -> dict:
    return {
        (r["role"], r["champ"], r["opp"], r["tier"]): (r["games"], r["wins"])
        for r in db.query("SELECT * FROM agg_matchup WHERE patch = 'legacy' AND platform = 'legacy'")
    }


def legacy_champs(db) -> dict:
    return {
        (r["role"], r["champ"], r["tier"]): (r["games"], r["wins"])
        for r in db.query("SELECT * FROM agg_champ WHERE patch = 'legacy' AND platform = 'legacy'")
    }


def test_import_creates_directed_rows_mirrors_and_legacy_ids(db, dd, tmp_path):
    report = import_legacy(db, dd, write(tmp_path, SIMPLE))
    assert report["status"] == "done"
    assert legacy_matchups(db) == {
        ("TOP", AATROX, DARIUS, "GOLD"): (4, 3),
        ("TOP", DARIUS, AATROX, "GOLD"): (4, 1),
        # Mirror: 2 filas dirigidas por partida y exactamente 50 % de victorias.
        ("TOP", AHRI, AHRI, "PLATINUM"): (8, 4),
    }
    assert legacy_champs(db) == {
        ("TOP", AATROX, "GOLD"): (4, 3),
        ("TOP", DARIUS, "GOLD"): (4, 1),
        ("TOP", AHRI, "PLATINUM"): (8, 4),
    }
    assert {r[0] for r in db.query("SELECT match_id FROM legacy_matches")} == {"EUW1_1", "EUW1_2", "KR_3"}
    assert report["processed_ids"] == 3
    assert report["matchup_rows"] == 3
    assert report["unmapped"] == {}
    assert legacy_status(db) == report


def test_role_aliases_and_match_totals(db, dd, tmp_path):
    report = import_legacy(db, dd, write(tmp_path, FULL))
    rows = legacy_matchups(db)
    assert rows[("MID", AHRI, ZED, "DIAMOND")] == (10, 7)
    assert rows[("MID", ZED, AHRI, "DIAMOND")] == (10, 3)
    assert rows[("ADC", JINX, KAISA, "DIAMOND")] == (10, 4)
    assert rows[("SUPPORT", NAUTILUS, THRESH, "DIAMOND")] == (10, 5)
    assert rows[("JUNGLE", LEE_SIN, VIEGO, "DIAMOND")] == (10, 5)
    assert {role for role, *_ in rows} == {"TOP", "JUNGLE", "MID", "ADC", "SUPPORT"}
    totals = {r["tier"]: r["matches"] for r in db.query("SELECT * FROM agg_totals WHERE patch = 'legacy'")}
    assert totals == {"DIAMOND": 10}
    assert report["matches"] == 10


def test_unknown_names_roles_and_empty_elos(db, dd, tmp_path):
    data = {
        "stats": {
            "TOP": {
                "fakechamp_vs_aatrox": {"wins": 2, "losses": 3},
                "wukong_vs_darius": {"wins": 1, "losses": 1},  # alias de MonkeyKing
                "no-es-un-matchup": {"wins": 9, "losses": 9},
            },
            "MIDDLE": {"ahri_vs_zed": {"wins": 5, "losses": 7}},  # sin elos -> UNRANKED
            "BOTTOM": {
                "jinx_vs_kaisa": {
                    "wins": 6,
                    "losses": 4,
                    "elos": {"diamond": {"wins": 4, "losses": 2}, "EMERALD": {"wins": 2, "losses": 2}, "IRON": {"wins": 0, "losses": 0}},
                }
            },
            "ARAM": {"ahri_vs_zed": {"wins": 1, "losses": 0}},  # rol desconocido
        }
    }
    report = import_legacy(db, dd, write(tmp_path, data))
    rows = legacy_matchups(db)
    assert rows[("TOP", WUKONG, DARIUS, "UNRANKED")] == (2, 1)
    assert rows[("MID", AHRI, ZED, "UNRANKED")] == (12, 5)
    assert rows[("MID", ZED, AHRI, "UNRANKED")] == (12, 7)
    assert rows[("ADC", JINX, KAISA, "DIAMOND")] == (6, 4)
    assert rows[("ADC", KAISA, JINX, "EMERALD")] == (4, 2)
    assert not any(tier == "IRON" for *_, tier in rows)  # elos sin partidas no crean filas
    assert not any(role == "ARAM" for role, *_ in rows)
    assert report["unmapped"] == {"fakechamp": 5}


def test_imported_history_is_visible_to_queries(db, dd, tmp_path):
    import_legacy(db, dd, write(tmp_path, SIMPLE))
    agg = Aggregates(db)
    assert agg.matchups_of(Filters(), "TOP", AATROX)[DARIUS]["games"] == 4
    assert agg.matchups_of(Filters(), "TOP", AATROX)[DARIUS]["wins"] == 3
    recent = Filters.parse(db, patch="recent")
    assert agg.matchups_of(recent, "TOP", AATROX) == {}


def test_reimport_is_idempotent(db, dd, tmp_path):
    path = write(tmp_path, SIMPLE)
    first = import_legacy(db, dd, path)
    before = (legacy_matchups(db), legacy_champs(db))
    assert import_legacy(db, dd, path) == first  # ya hecho: no se repite
    forced = import_legacy(db, dd, path, force=True)
    assert forced["status"] == "done"
    assert (legacy_matchups(db), legacy_champs(db)) == before  # no se duplican filas
    assert db.scalar("SELECT COUNT(*) FROM legacy_matches") == 3


def test_new_crawl_does_not_recount_legacy_matches(db, dd, tmp_path):
    import_legacy(db, dd, write(tmp_path, SIMPLE))
    assert known_match_ids(db, ["EUW1_1", "EUW1_99"]) == {"EUW1_1"}
    assert store_match(db, parse_match(make_match("EUW1_1")), seed_tier="GOLD", stats_queues=(420,)) == "stored"
    assert db.scalar("SELECT aggregated FROM matches WHERE match_id = 'EUW1_1'") == 0
    assert db.scalar("SELECT COUNT(*) FROM agg_matchup WHERE patch <> 'legacy'") == 0


def test_missing_file(db, dd, tmp_path):
    report = import_legacy(db, dd, tmp_path / "nope.json")
    assert report["status"] == "missing"
    assert legacy_status(db)["status"] == "missing"


def test_waits_for_ddragon(db, tmp_path):
    report = import_legacy(db, DDragon(tmp_path / "empty"), write(tmp_path, SIMPLE))
    assert report == {"status": "waiting_ddragon"}
    assert legacy_status(db) == {"status": "pending"}
    assert db.scalar("SELECT COUNT(*) FROM agg_matchup") == 0


@pytest.mark.parametrize("bad_stats", [None, [], {"TOP": []}, {"TOP": {"aatrox_vs_darius": "3-1"}}])
def test_malformed_sections_are_skipped(db, dd, tmp_path, bad_stats):
    report = import_legacy(db, dd, write(tmp_path, {"stats": bad_stats, "processed_matches": []}))
    assert report["status"] == "done"
    assert report["matchup_rows"] == 0
