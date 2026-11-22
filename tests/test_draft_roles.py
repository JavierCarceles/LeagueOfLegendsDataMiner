"""Draft: solo se recomiendan (y se ofrecen en el desplegable) campeones que se juegan de verdad en esa línea."""

from __future__ import annotations

import pytest

from app.analytics import draft
from app.analytics.evidence import Evidence
from app.analytics.filters import Filters
from tests.factories import (
    AATROX,
    AHRI,
    DARIUS,
    GAREN,
    FakeOpgg,
    build_services,
    generate_draft_matches,
    install_opgg,
    make_match,
    store_payloads,
)

F = Filters()


@pytest.fixture(scope="module")
def offrole_svc(tmp_path_factory):
    """360 partidas normales + 8 de una Ahri top que las gana todas (un pick raro con winrate altísimo)."""
    base = tmp_path_factory.mktemp("draft-roles")
    with pytest.MonkeyPatch.context() as mp:
        svc = build_services(base, mp)
        payloads = generate_draft_matches(360)
        payloads += [make_match(f"EUW1_79000000{i:02d}", blue={"TOP": AHRI, "MID": 238}, blue_win=True) for i in range(8)]
        store_payloads(svc, payloads)
        yield svc
        svc.shutdown()
        svc.db.close()


def test_an_offrole_pick_with_a_huge_winrate_is_not_recommended(offrole_svc):
    """Ahri top gana el 100 % de sus 8 partidas, pero solo ~4 % de sus partidas son en top: no es un pick de top."""
    ev = Evidence(offrole_svc, F)
    assert ev.presence("TOP", AHRI)["share"] < 0.08 and not ev.plays_role("TOP", AHRI, 0)
    assert ev.plays_role("MID", AHRI, 0) and ev.plays_role("TOP", DARIUS, 0)
    res = draft.suggest(offrole_svc, F, {}, {}, "TOP", min_games=0)
    assert AHRI not in res["scores"] and {AATROX, DARIUS, GAREN} <= set(res["scores"])


def test_recommendations_need_300_real_games_in_the_role_by_default(offrole_svc):
    """Cada campeón de top tiene ~240 partidas en esta BD: con el mínimo por defecto (300) no hay muestra fiable."""
    assert draft.suggest(offrole_svc, F, {}, {}, "TOP")["evaluated"] == 0
    assert draft.suggest(offrole_svc, F, {}, {}, "TOP", min_games=200)["evaluated"] == 3


def test_web_role_rate_and_pick_decide_who_plays_a_role(tmp_path, monkeypatch):
    """Esmeralda+ reales: Sylas top (7,6 % de sus partidas, 1,2 % de pick) y Maestro Yi top (8,3 %, 0,55 %) sí;
    Fiddlesticks top (9,5 %, 0,25 %) y Zilean top (6 %, 0,2 %, aunque gane un 56 %) no; Ivern jungla (su línea
    principal) sí aunque casi nadie lo coja."""
    from tests.factories import EZREAL, JINX, LEE_SIN, ORIANNA, SYNDRA

    svc = build_services(tmp_path, monkeypatch)
    SYLAS, ZILEAN, YI, FIDDLE, IVERN = AHRI, GAREN, SYNDRA, ORIANNA, JINX  # ids cualesquiera del catálogo de pruebas
    install_opgg(svc, FakeOpgg(tier_lists={
        "TOP": {
            DARIUS: {"wr": 50.0, "pick": 6.0, "role_rate": 0.95},
            SYLAS: {"wr": 49.0, "pick": 1.17, "role_rate": 0.076},
            YI: {"wr": 50.0, "pick": 0.55, "role_rate": 0.083},
            FIDDLE: {"wr": 52.0, "pick": 0.25, "role_rate": 0.095},
            ZILEAN: {"wr": 56.6, "pick": 0.2, "role_rate": 0.06},
        },
        "JUNGLE": {LEE_SIN: {"wr": 50.0, "pick": 9.0, "role_rate": 0.9}, IVERN: {"wr": 51.0, "pick": 0.3, "role_rate": 0.97}, EZREAL: {"wr": 50.0, "pick": 0.3, "role_rate": 0.2}},
    }))
    ev = Evidence(svc, F, fetch=False)
    if ev.scope is None:
        pytest.skip("sin parches conocidos en el catálogo de pruebas")
    assert set(ev.champions_in_role("TOP")) == {DARIUS, SYLAS, YI}  # sin recuento de partidas: no se descartan por eso
    assert set(ev.champions_in_role("JUNGLE")) == {LEE_SIN, IVERN}
    svc.shutdown()
    svc.db.close()


def test_role_pools_endpoint(offrole_svc):
    from fastapi.testclient import TestClient

    from app.main import create_app

    app = create_app(offrole_svc.settings, run_startup=False)
    app.state.svc = offrole_svc
    with TestClient(app) as client:
        data = client.get("/api/draft/roles").json()
    assert data["min_games"] == 300 and set(data["roles"]) == {"TOP", "JUNGLE", "MID", "ADC", "SUPPORT"}
    assert data["roles"]["TOP"] == []  # ~240 partidas cada uno en esta BD: ninguno llega a 300


def test_secondary_role_picks_get_a_score_but_are_not_recommended(tmp_path, monkeypatch):
    """Vel'Koz ADC (16 % de sus partidas, 0,85 % de pick, 54 % WR): se puede elegir y puntúa, pero los
    «Recomendados» son los habituales de la línea (su línea principal o ≥ 2 % de pick)."""
    from tests.factories import EZREAL, JINX, KAISA, SYNDRA

    svc = build_services(tmp_path, monkeypatch)
    VELKOZ = SYNDRA
    install_opgg(svc, FakeOpgg(tier_lists={"ADC": {
        JINX: {"wr": 52.3, "pick": 10.6, "role_rate": 1.0},
        KAISA: {"wr": 50.6, "pick": 23.5, "role_rate": 0.98},
        EZREAL: {"wr": 47.0, "pick": 15.4, "role_rate": 0.95},
        VELKOZ: {"wr": 54.2, "pick": 0.85, "role_rate": 0.16},
    }}))
    if Evidence(svc, F, fetch=False).scope is None:
        pytest.skip("sin parches conocidos en el catálogo de pruebas")
    res = draft.suggest(svc, F, {}, {}, "ADC")
    assert VELKOZ in res["scores"] and max(res["scores"], key=res["scores"].get) == VELKOZ  # el que más puntúa…
    assert VELKOZ not in [b["champ"] for b in res["best"]] and res["best"][0]["champ"] == JINX  # …pero no se recomienda
    svc.shutdown()
    svc.db.close()
