"""app/ddragon.py sin red: caché local sintética."""

from __future__ import annotations

import pytest

from app.ddragon import DDragon
from tests.factories import AATROX, CHAMPIONS, DD_VERSION, KAISA, LEE_SIN, WUKONG, write_ddragon_cache


def test_offline_load(dd):
    assert dd.version == DD_VERSION
    assert dd.ready.is_set()
    assert len(dd.champions) == len(CHAMPIONS)
    assert dd.champions[AATROX]["key"] == "Aatrox"
    assert dd.champions[AATROX]["tags"] == ["Fighter"]
    assert dd.champions[AATROX]["info"]["attack"] == 8


@pytest.mark.parametrize(
    "value, expected",
    [
        (AATROX, AATROX),
        ("266", AATROX),
        ("Aatrox", AATROX),
        ("aatrox", AATROX),
        ("MonkeyKing", WUKONG),
        ("Wukong", WUKONG),
        ("wukong", WUKONG),
        ("Kai'Sa", KAISA),
        ("kaisa", KAISA),
        ("Kaisa", KAISA),
        ("Lee Sin", LEE_SIN),
        ("leesin", LEE_SIN),
        (" LeeSin ", LEE_SIN),
        (99999, None),
        ("99999", None),
        ("NotAChampion", None),
        ("", None),
        (None, None),
    ],
)
def test_resolve(dd, value, expected):
    assert dd.resolve(value) == expected


def test_name_and_key(dd):
    assert dd.name(WUKONG) == "Wukong"
    assert dd.key(WUKONG) == "MonkeyKing"
    assert dd.name(1) == "#1"
    assert dd.key(None) == ""


def test_public_lists_champions_sorted_by_name(dd):
    public = dd.public()
    names = [c["name"] for c in public["champions"]]
    assert names == sorted(names)
    assert public["version"] == DD_VERSION


def test_latest_cached_version_is_the_numeric_maximum(tmp_path):
    write_ddragon_cache(tmp_path, "16.9.1")
    write_ddragon_cache(tmp_path, "16.22.1")
    (tmp_path / "17.1.1").mkdir()  # sin champion.json: se ignora
    dd = DDragon(tmp_path)
    assert dd.load(allow_network=False)
    assert dd.version == "16.22.1"


def test_load_falls_back_to_cache_when_cdn_is_unreachable(tmp_path, monkeypatch):
    import requests

    def offline(*args, **kwargs):
        raise requests.ConnectionError("sin red")

    monkeypatch.setattr("app.ddragon.requests.get", offline)
    write_ddragon_cache(tmp_path)
    dd = DDragon(tmp_path)
    assert dd.load() is True  # allow_network=True por defecto
    assert dd.version == DD_VERSION and dd.resolve("Wukong") == WUKONG


def test_load_without_cache_fails_cleanly(tmp_path):
    dd = DDragon(tmp_path / "nothing")
    assert dd.load(allow_network=False) is False
    assert dd.champions == {} and not dd.ready.is_set()
    assert dd.resolve("Aatrox") is None
