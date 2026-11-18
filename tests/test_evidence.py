"""app/analytics/evidence.py: combinación de datos de OP.GG y de la BD propia.

Se usa ``FakeOpgg`` (en memoria) en lugar de activar ``opgg_enabled``: así se
prueba la mezcla de fuentes sin tocar la red.
"""

from __future__ import annotations

from app.analytics import champions, regions
from app.analytics.evidence import Evidence
from app.analytics.filters import Filters
from tests.factories import AATROX, DARIUS, GAREN, FakeOpgg, install_opgg, make_match, opgg_row, store_payloads


def own_top_matches(svc, n: int = 20):
    """Aatrox (azul) contra Darius (rojo) en TOP, mitad y mitad."""
    store_payloads(svc, [make_match(f"EUW1_{6000 + i}", blue_win=i % 2 == 0) for i in range(n)])


def test_without_opgg_only_own_data_is_used(svc):
    own_top_matches(svc)
    ev = Evidence(svc, Filters())
    assert ev.scope is None
    p = ev.pair("TOP", AATROX, DARIUS)
    assert (p["own"], p["ext"]) == ((20, 10), (0, 0))


def test_pair_adds_opgg_counters_to_own_counts(svc):
    own_top_matches(svc)
    install_opgg(svc, FakeOpgg(counters={("TOP", AATROX): {DARIUS: (1000, 520)}}))
    p = Evidence(svc, Filters()).pair("TOP", AATROX, DARIUS)
    assert p["own"] == (20, 10) and p["ext"] == (1000, 520)
    assert (p["games"], p["wins"]) == (1020, 530)


def test_pair_falls_back_to_the_opponents_counter_page(svc):
    install_opgg(svc, FakeOpgg(counters={("TOP", DARIUS): {AATROX: (800, 500)}}))
    p = Evidence(svc, Filters()).pair("TOP", AATROX, DARIUS)
    assert p["ext"] == (800, 300)  # victorias de Aatrox = partidas - victorias de Darius


def test_sources_parameter_selects_the_evidence(svc):
    own_top_matches(svc)
    install_opgg(svc, FakeOpgg(counters={("TOP", AATROX): {DARIUS: (1000, 520)}}))
    own = Evidence(svc, Filters(), "own").pair("TOP", AATROX, DARIUS)
    ext = Evidence(svc, Filters(), "opgg").pair("TOP", AATROX, DARIUS)
    assert (own["own"], own["ext"]) == ((20, 10), (0, 0))
    assert (ext["own"], ext["ext"]) == ((0, 0), (1000, 520))


def test_tier_list_mixing_own_and_opgg_only_champions_does_not_crash(svc):
    # Aatrox y Darius: solo BD propia (10/20 -> 50.0 %, games=20).
    # Garen: solo tier list de OP.GG con 50 % -> mismo wr_adj redondeado y games=None.
    own_top_matches(svc)
    install_opgg(svc, FakeOpgg(tier_lists={"TOP": {GAREN: opgg_row(50.0)}}))
    res = champions.tier_list(svc, Filters(), "TOP", min_games=0)
    by_champ = {c["champ"]: c for c in res["champions"]}
    assert set(by_champ) == {AATROX, DARIUS, GAREN}
    assert by_champ[GAREN]["source"] == "opgg" and by_champ[DARIUS]["source"] == "own"


def test_region_detail_with_opgg_only_champions_does_not_crash(svc):
    own_top_matches(svc)
    install_opgg(svc, FakeOpgg(tier_lists={"TOP": {GAREN: opgg_row(51.3)}}))
    res = regions.region_detail(svc, Filters(), "all")
    popular = [c["champ"] for c in res["meta"]["TOP"]["popular"]]
    assert set(popular) == {AATROX, DARIUS, GAREN}
