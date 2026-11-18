"""API HTTP con ``TestClient(create_app(settings, run_startup=False))``.

Datos: 30 partidas con la alineación por defecto (Aatrox azul vs Darius rojo
en TOP); el rojo gana 20 de 30. Sin key de Riot y con OP.GG desactivado.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

import app.services
from app.main import create_app
from app.riot.routing import ROLES
from tests.factories import (
    AATROX,
    DARIUS,
    DD_VERSION,
    GAME_START_MS,
    JINX,
    KAISA,
    make_match,
    make_settings,
    store_payloads,
    write_ddragon_cache,
    write_env,
)

N = 30


@pytest.fixture
def api(tmp_path, monkeypatch):
    settings = make_settings(tmp_path)
    monkeypatch.setattr(app.services, "ENV_PATH", write_env(tmp_path))
    application = create_app(settings, run_startup=False)
    svc = application.state.svc
    write_ddragon_cache(settings.ddragon_dir)
    assert svc.dd.load(allow_network=False)
    payloads = [make_match(f"EUW1_{8000 + i}", blue_win=i % 3 == 0, game_start=GAME_START_MS + i * 3_600_000) for i in range(N)]
    assert store_payloads(svc, payloads) == ["stored"] * N
    with TestClient(application) as client:
        yield client, svc
    svc.db.close()


def ok(response):
    assert response.status_code == 200, response.text
    return response.json()


# --------------------------------------------------------------- metadatos
def test_meta(api):
    client, _ = api
    body = ok(client.get("/api/meta"))
    assert body["ddragon"]["version"] == DD_VERSION
    assert {"Aatrox", "Darius", "Wukong", "Kai'Sa"} <= {c["name"] for c in body["ddragon"]["champions"]}
    assert [r["id"] for r in body["roles"]] == list(ROLES)
    assert body["patches"] == ["16.22"]
    assert body["has_legacy"] is False
    assert "euw1" in {p["id"] for p in body["platforms"]}
    assert body["queues"]["420"]


def test_system_status(api):
    client, _ = api
    body = ok(client.get("/api/system/status"))
    assert body["ready"] is True
    assert body["key"]["configured"] is False and body["key"]["status"] == "missing"
    assert body["db"]["matches"] == N and body["db"]["stored_matches"] == N
    assert body["db"]["players"] == 10 * N and body["db"]["seeds"] == 0
    assert body["crawler"]["running"] is False
    assert body["legacy"]["status"] == "pending"


# -------------------------------------------------------------- campeones
def test_champions_tier_list(api):
    client, _ = api
    body = ok(client.get("/api/champions", params={"role": "TOP"}))
    assert body["role"] == "TOP"
    assert body["totals"]["matches"] == N
    rows = {c["champ"]: c for c in body["champions"]}
    assert set(rows) == {AATROX, DARIUS}
    assert rows[DARIUS]["games"] == N and rows[DARIUS]["own"]["wins"] == 20
    assert rows[AATROX]["games"] == N and rows[AATROX]["own"]["wins"] == 10
    assert body["champions"][0]["champ"] == DARIUS  # ordenado por WR ajustado


def test_champions_role_alias(api):
    client, _ = api
    body = ok(client.get("/api/champions", params={"role": "bot"}))
    assert body["role"] == "ADC"
    assert {c["champ"] for c in body["champions"]} == {JINX, KAISA}


def test_champion_detail(api):
    client, _ = api
    body = ok(client.get("/api/champions/darius"))
    assert body["champ"] == DARIUS and body["role"] == "TOP"
    assert body["matchups"]["count"] == 1


# --------------------------------------------------------------- matchups
def test_matchup_list(api):
    client, _ = api
    body = ok(client.get("/api/matchups/TOP/Aatrox"))
    assert body["role"] == "TOP" and body["champ"] == AATROX
    assert [m["opp"] for m in body["matchups"]] == [DARIUS]
    m = body["matchups"][0]
    assert (m["games"], m["wins"]) == (N, 10)
    assert m["wr"] == pytest.approx(33.3, abs=0.05)
    assert m["sources"]["opgg"] is None and m["sources"]["own"]["games"] == N


def test_matchup_detail(api):
    client, _ = api
    body = ok(client.get("/api/matchups/TOP/Aatrox/Darius"))
    assert (body["champ"], body["opp"]) == (AATROX, DARIUS)
    assert (body["pair"]["games"], body["pair"]["wins"]) == (N, 10)
    assert body["a"]["games"] == N and body["b"]["games"] == N
    assert [(r["key"], r["games"]) for r in body["breakdown"]["patch"]] == [("16.22", N)]
    assert [(r["key"], r["games"]) for r in body["breakdown"]["platform"]] == [("euw1", N)]
    assert [(r["key"], r["games"]) for r in body["breakdown"]["tier"]] == [("DIAMOND", N)]
    assert body["guide"] is None
    reverse = ok(client.get("/api/matchups/top/darius/aatrox"))
    assert (reverse["pair"]["games"], reverse["pair"]["wins"]) == (N, 20)


# ------------------------------------------------------------------ draft
def test_draft_analyze(api):
    client, _ = api
    body = ok(client.post("/api/draft/analyze", json={"ally": {"TOP": "Aatrox"}, "enemy": {"TOP": "Darius"}}))
    assert 0 <= body["win_probability"] <= 100
    assert body["win_probability"] < 50  # Darius ganó 20 de 30
    top = body["lanes"][0]
    assert (top["kind"], top["ally"], top["enemy"], top["games"], top["wins"]) == ("matchup", AATROX, DARIUS, N, 10)
    # Mismo draft con nombres en minúsculas, ids numéricos y huecos vacíos.
    again = ok(client.post("/api/draft/analyze", json={"ally": {"top": "aatrox", "MID": None}, "enemy": {"TOP": 122, "JUNGLE": ""}}))
    assert again["win_probability"] == body["win_probability"]
    swapped = ok(client.post("/api/draft/analyze", json={"ally": {"TOP": "Darius"}, "enemy": {"TOP": "Aatrox"}}))
    assert swapped["win_probability"] + body["win_probability"] == pytest.approx(100, abs=0.11)


def test_draft_analyze_rejects_duplicated_champions(api):
    client, _ = api
    r = client.post("/api/draft/analyze", json={"ally": {"TOP": "Aatrox"}, "enemy": {"TOP": "Aatrox"}})
    assert r.status_code == 400
    r = client.post("/api/draft/analyze", json={"ally": {"TOP": "Aatrox", "MID": "aatrox"}, "enemy": {}})
    assert r.status_code == 400


def test_draft_suggest(api):
    client, _ = api
    body = ok(client.post("/api/draft/suggest", params={"role": "TOP", "min_games": 0}, json={"ally": {}, "enemy": {}}))
    assert [r["champ"] for r in body["best"]] == [DARIUS, AATROX]
    body = ok(client.post("/api/draft/suggest", params={"role": "TOP", "min_games": 0}, json={"ally": {"TOP": "Darius"}}))
    assert DARIUS not in [r["champ"] for r in body["best"]]


def test_draft_backtest_endpoints(api):
    client, _ = api
    assert ok(client.get("/api/draft/backtest")) == {"alpha": 1.0, "report": None}
    body = ok(client.post("/api/draft/backtest"))
    assert body == {"status": "insufficient", "matches": N, "needed": 200}


# ---------------------------------------------------------------- regiones
def test_regions(api):
    client, _ = api
    body = ok(client.get("/api/regions"))
    euw = next(p for p in body["platforms"] if p["platform"] == "euw1")
    assert euw["matches"] == N
    assert euw["blue_wr"] == pytest.approx(33.3, abs=0.05)
    assert euw["tiers"] == {"DIAMOND": N}
    assert body["legacy_matches"] == 0


def test_region_detail(api):
    client, _ = api
    body = ok(client.get("/api/regions/euw1"))
    assert body["matches"] == N and body["remakes"] == 0
    assert body["duration"] == [{"label": "30-35 min", "matches": N, "share": 100.0, "blue_wr": 33.3}]
    assert body["meta"]["TOP"]["best"][0]["champ"] == DARIUS
    assert client.get("/api/regions/xx9").status_code == 404


def test_region_detail_ignores_the_global_platform_filter(api):
    # Docstring de /api/regions/{target}: "El filtro global de región se ignora aquí".
    client, _ = api
    assert ok(client.get("/api/regions/euw1", params={"platform": "kr"}))["matches"] == N
    assert ok(client.get("/api/regions/all", params={"platform": "kr"}))["matches"] == N


# -------------------------------------------------------------- recolector
def test_crawler_start_without_key_is_412(api):
    client, svc = api
    r = client.post("/api/crawler/start", json={"platforms": ["euw1"], "tiers": ["CHALLENGER"]})
    assert r.status_code == 412
    assert svc.crawler.running is False
    assert client.post("/api/crawler/start", json={"platforms": ["xx9"]}).status_code == 400
    assert ok(client.get("/api/crawler/status"))["running"] is False


# ------------------------------------------------------------------ OP.GG
def test_opgg_disabled_endpoints_stay_offline(api):
    client, _ = api
    body = ok(client.get("/api/opgg/status"))
    assert body["enabled"] is False and body["versions"] == []
    assert client.post("/api/opgg/sync", json={}).status_code == 412
    assert client.get("/api/opgg/guide/TOP/Aatrox/Darius").status_code == 404


# ------------------------------------------------------------------ errores
@pytest.mark.parametrize(
    "path",
    ["/api/matchups/TOP/NotAChampion", "/api/matchups/TOP/Aatrox/NotAChampion", "/api/champions/NotAChampion", "/api/matchups/TOP/99999"],
)
def test_unknown_champion_is_404(api, path):
    client, _ = api
    assert client.get(path).status_code == 404


def test_unknown_champion_in_draft_is_404(api):
    client, _ = api
    assert client.post("/api/draft/analyze", json={"ally": {"TOP": "NotAChampion"}}).status_code == 404


@pytest.mark.parametrize(
    "method, path, kwargs",
    [
        ("get", "/api/matchups/FOO/Aatrox", {}),
        ("get", "/api/matchups/FOO/Aatrox/Darius", {}),
        ("get", "/api/champions", {"params": {"role": "FOO"}}),
        ("get", "/api/champions/Aatrox", {"params": {"role": "FOO"}}),
        ("post", "/api/draft/analyze", {"json": {"ally": {"FOO": "Aatrox"}}}),
        ("post", "/api/draft/suggest", {"params": {"role": "FOO"}, "json": {}}),
    ],
)
def test_bad_role_is_400(api, method, path, kwargs):
    client, _ = api
    assert getattr(client, method)(path, **kwargs).status_code == 400


def test_invalid_sources_param_is_rejected(api):
    client, _ = api
    assert client.get("/api/champions", params={"role": "TOP", "sources": "bogus"}).status_code == 422
