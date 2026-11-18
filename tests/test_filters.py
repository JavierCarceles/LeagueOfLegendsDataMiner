"""app/analytics/filters.py: traducción de filtros a SQL."""

from __future__ import annotations

from app.analytics.filters import Filters, available_patches, patch_sort_key
from app.data.repository import store_match
from app.ingest.extractor import parse_match
from tests.factories import make_match


def store_patches(db, versions):
    for i, version in enumerate(versions):
        store_match(db, parse_match(make_match(f"EUW1_{900 + i}", game_version=version)), seed_tier="GOLD", stats_queues=(420,))


def test_defaults_mean_everything(db):
    f = Filters.parse(db)
    assert (f.patches, f.tiers, f.platforms) == (None, None, None)
    assert f.includes_legacy
    assert f.clause() == ("1=1", [])


def test_recent_excludes_legacy(db):
    f = Filters.parse(db, patch="recent")
    assert f.exclude_legacy and not f.includes_legacy
    sql, params = f.clause()
    assert "patch <> 'legacy'" in sql and params == []


def test_explicit_patch_list(db):
    f = Filters.parse(db, patch="16.22, 16.21")
    assert f.patches == ("16.22", "16.21")
    assert not f.includes_legacy
    assert Filters.parse(db, patch="legacy,16.22").includes_legacy


def test_available_patches_use_numeric_order(db):
    store_patches(db, ["16.9.1.1", "16.10.2.3", "16.22.1.2", "15.24.734.7485"])
    assert available_patches(db) == ["16.22", "16.10", "16.9", "15.24"]
    assert Filters.parse(db, patch="latest").patches == ("16.22",)
    assert Filters.parse(db, patch="last2").patches == ("16.22", "16.10")
    assert Filters.parse(db, patch="last").patches == ("16.22", "16.10", "16.9")


def test_latest_without_data_matches_nothing(db):
    assert Filters.parse(db, patch="latest").patches == ("__none__",)


def test_patch_sort_key():
    assert sorted(["16.9", "16.10", "15.24", "legacy"], key=patch_sort_key) == ["legacy", "15.24", "16.9", "16.10"]


def test_tier_presets_and_lists(db):
    assert Filters.parse(db, tier="diamond_plus").tiers == ("DIAMOND", "MASTER", "GRANDMASTER", "CHALLENGER")
    assert Filters.parse(db, tier="gold,platinum").tiers == ("GOLD", "PLATINUM")
    assert Filters.parse(db, tier="UNRANKED").tiers == ("UNRANKED",)


def test_region_expands_to_its_platforms(db):
    f = Filters.parse(db, platform="europe")
    assert f.platforms == ("euw1", "eun1", "tr1", "ru", "me1")
    assert not f.includes_legacy
    assert Filters.parse(db, platform="EUW1,kr,euw1").platforms == ("euw1", "kr")


def test_clause_and_match_clause(db):
    f = Filters.parse(db, patch="16.22", tier="GOLD", platform="euw1")
    assert f.clause("a") == ("a.patch IN (?) AND a.tier IN (?) AND a.platform IN (?)", ["16.22", "GOLD", "euw1"])
    sql, params = f.match_clause("m")
    assert sql.startswith("m.aggregated = 1 AND ") and params == ["16.22", "GOLD", "euw1"]
    assert f.public()["patches"] == ["16.22"]
