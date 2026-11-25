"""app/ingest/crawler.py con un cliente de Riot falso (sin red).

Mundo simulado (una plataforma, ``euw1``):

* 3 semillas de la liga Challenger;
* 9 partidas válidas (EUW1_1004 la comparten dos semillas), 1 remake, 1 ARAM
  y 1 id que devuelve 404.
"""

from __future__ import annotations

import json
import time
from collections import Counter

import pytest

from app.ingest.crawler import CrawlerConfig
from tests.factories import TEST_KEY, FakeRiotClient, make_match, make_timeline

SEEDS = ["seed-0", "seed-1", "seed-2"]


def build_world() -> tuple[dict, dict]:
    ladder = {
        "seed-0": ["EUW1_1004", "EUW1_1003", "EUW1_1002", "EUW1_1001"],
        "seed-1": ["EUW1_2003", "EUW1_2002", "EUW1_2001", "EUW1_1004"],  # A4 compartida
        "seed-2": ["EUW1_3404", "EUW1_3303", "EUW1_3202", "EUW1_3101", "EUW1_3001"],
    }
    payloads: dict = {}
    start = 1_763_000_000_000
    normal = ["EUW1_1001", "EUW1_1002", "EUW1_1003", "EUW1_1004", "EUW1_2001", "EUW1_2002", "EUW1_2003", "EUW1_3001", "EUW1_3101"]
    for i, match_id in enumerate(normal):
        seed = next(s for s, ids in ladder.items() if match_id in ids)
        puuids = {1: seed}
        if match_id == "EUW1_1004":
            puuids = {1: "seed-0", 6: "seed-1"}
        payloads[match_id] = make_match(match_id, puuids=puuids, blue_win=i % 2 == 0, game_start=start + i * 3_600_000)
    payloads["EUW1_3202"] = make_match("EUW1_3202", puuids={1: "seed-2"}, duration=190, game_start=start + 20 * 3_600_000)
    payloads["EUW1_3303"] = make_match("EUW1_3303", puuids={1: "seed-2"}, map_id=12, queue=450, game_start=start + 21 * 3_600_000)
    payloads["EUW1_3404"] = None  # 404
    return ladder, payloads


def wait_until(predicate, timeout: float = 10.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.02)
    return predicate()


def run_crawler(svc, fake, tiers):
    svc.riot = fake
    status = svc.crawler.start(CrawlerConfig(platforms=["euw1"], tiers=tiers))
    assert status["running"] is True
    worker = svc.crawler.workers["europe"]
    # Cuando ya no quedan semillas por recorrer el hilo se queda "idle".
    assert wait_until(lambda: worker.state["status"] == "idle"), worker.state
    svc.crawler.stop()
    worker.join(timeout=5)
    assert not worker.is_alive()
    return worker


def test_crawler_stores_matches_and_seeds(keyed_svc):
    svc = keyed_svc
    assert svc.keys.get() == TEST_KEY and svc.keys.status != "invalid"
    ladder, payloads = build_world()
    fake = FakeRiotClient(ladder, payloads)
    since = CrawlerConfig(season_version=svc.dd.version).validated().active_since()  # el periodo por defecto: la temporada
    worker = run_crawler(svc, fake, ["CHALLENGER"])
    db = svc.db

    # Semillas de la liga apex.
    seeds = db.query("SELECT puuid, platform, tier, division, lp, last_crawled_at, newest_match_at FROM players WHERE is_seed = 1")
    assert sorted(r["puuid"] for r in seeds) == SEEDS
    assert all(r["platform"] == "euw1" and r["tier"] == "CHALLENGER" and r["division"] == "I" for r in seeds)
    assert all(r["last_crawled_at"] and r["newest_match_at"] for r in seeds)
    assert db.scalar("SELECT COUNT(*) FROM seed_pages WHERE platform = 'euw1' AND tier = 'CHALLENGER'") == 1
    assert fake.calls_to("apex_league")[0][1:3] == ("euw1", "CHALLENGER")

    # Partidas guardadas (A4 una sola vez) y agregadas.
    assert db.scalar("SELECT COUNT(*) FROM matches WHERE is_remake = 0") == 9
    assert db.scalar("SELECT COUNT(*) FROM matches WHERE aggregated = 1") == 9
    assert db.scalar("SELECT COUNT(*) FROM matches WHERE is_remake = 1") == 1
    assert db.scalar("SELECT COUNT(*) FROM participants") == 90
    assert db.scalar("SELECT matches FROM agg_totals") == 9
    assert {r[0] for r in db.query("SELECT DISTINCT tier FROM matches")} == {"CHALLENGER"}
    skipped = {r["match_id"]: r["reason"] for r in db.query("SELECT match_id, reason FROM skipped_matches")}
    assert set(skipped) == {"EUW1_3303", "EUW1_3404"}
    assert skipped["EUW1_3404"] == "404"

    # Cada partida se pide como mucho una vez (deduplicación antes de descargar).
    fetched = Counter(call[1] for call in fake.calls_to("match"))
    assert max(fetched.values()) == 1
    assert set(fetched) == set(payloads)

    # Solo ranked SoloQ y, la primera vez, desde el inicio del periodo.
    for _, platform, puuid, queue, ranked_only, start_time, count in fake.calls_to("match_ids"):
        assert platform == "euw1" and puuid in SEEDS
        assert queue == 420 and ranked_only is False and count == 20
        assert abs(start_time - since) <= 60

    assert worker.state["stored"] == 9 and svc.crawler.total_stored == 9
    assert worker.state["remakes"] == 1 and worker.state["ignored"] == 1
    assert worker.state["seeds_added"] == 3 and worker.state["players"] == 3
    status = svc.crawler.status()
    assert status["running"] is False and status["stored"] == 9


def test_crawler_uses_league_pages_below_master(keyed_svc):
    svc = keyed_svc
    ladder, payloads = build_world()
    fake = FakeRiotClient(ladder, payloads)
    run_crawler(svc, fake, ["DIAMOND"])
    first = fake.calls_to("league_entries")[0]
    assert first[1:] == ("euw1", "DIAMOND", "I", 1)
    rows = svc.db.query("SELECT tier, division FROM players WHERE is_seed = 1")
    assert len(rows) == 3 and all((r["tier"], r["division"]) == ("DIAMOND", "I") for r in rows)
    pages = {r["division"]: dict(r) for r in svc.db.query("SELECT * FROM seed_pages WHERE tier = 'DIAMOND'")}
    assert pages["I"]["next_page"] == 2 and pages["I"]["exhausted_at"] is None
    assert svc.db.scalar("SELECT COUNT(*) FROM matches WHERE aggregated = 1") == 9


def test_crawler_resumes_incrementally(keyed_svc):
    svc = keyed_svc
    ladder, payloads = build_world()
    run_crawler(svc, FakeRiotClient(ladder, payloads), ["CHALLENGER"])
    newest = {r["puuid"]: r["newest_match_at"] for r in svc.db.query("SELECT puuid, newest_match_at FROM players WHERE is_seed = 1")}
    # Segunda pasada: los jugadores ya recorridos piden solo partidas posteriores a la última vista.
    with svc.db.write() as conn:
        conn.execute("UPDATE players SET last_crawled_at = NULL WHERE is_seed = 1")
    svc.crawler.workers.clear()
    second = FakeRiotClient(ladder, payloads)
    run_crawler(svc, second, ["CHALLENGER"])
    calls = {call[2]: call[5] for call in second.calls_to("match_ids")}
    assert set(calls) == set(SEEDS)
    for puuid, start_time in calls.items():
        assert start_time == newest[puuid] // 1000 + 1
    assert second.calls_to("match") == []  # todo era ya conocido
    assert svc.db.scalar("SELECT COUNT(*) FROM matches WHERE aggregated = 1") == 9


def test_crawler_refuses_to_start_without_key(svc):
    assert svc.keys.get() == ""
    with pytest.raises(PermissionError):
        svc.crawler.start(CrawlerConfig(platforms=["euw1"], tiers=["CHALLENGER"]))
    assert not svc.crawler.running


def test_crawler_config_validation():
    cfg = CrawlerConfig(platforms=["EUW1", "kr", "xx1", "euw1"], tiers=["challenger", "gold", "bogus"], matches_per_player=1, recrawl_hours=0).validated()
    assert cfg.platforms == ["euw1", "kr"]
    assert cfg.tiers == ["GOLD", "CHALLENGER"]
    assert (cfg.matches_per_player, cfg.recrawl_hours) == (5, 1.0)
    with pytest.raises(ValueError):
        CrawlerConfig(platforms=["xx1"]).validated()
    with pytest.raises(ValueError):
        CrawlerConfig(tiers=["bogus"]).validated()


def test_todo_el_ranking_reparte_por_las_partidas_analizadas_de_cada_elo(keyed_svc):
    """25-11-2026: con todo el ranking se contaba la tabla ``matches`` (Hierro y Bronce eran los que menos tenían) en vez
    de las partidas analizadas (Bronce y Oro). Ahora cuentan las analizadas, como con campeones: más parte al que menos
    tiene, ninguno sin partidas nuevas, y las visitas en curso de otras regiones cuentan."""
    from collections import Counter

    from app.ingest.crawler import RegionWorker

    crawler = keyed_svc.crawler
    with keyed_svc.db.write() as conn:
        rows = [(f"M{i}", tier) for i, tier in enumerate(["IRON"] * 300 + ["GOLD"] * 100 + ["CHALLENGER"] * 1000)]
        conn.executemany("INSERT INTO otp_games (match_id, puuid, champ, win, tier, kind, version, fetched_at) VALUES (?, 'p', 1, 1, ?, 'otp', 1, 1)", rows)
    config = CrawlerConfig(platforms=["euw1", "eun1"], tiers=["IRON", "GOLD", "CHALLENGER"], matches_per_player=20).validated()
    worker = RegionWorker(crawler, "europe", ["euw1", "eun1"], config)
    combos = [(p, t) for t in config.tiers for p in worker.platforms]
    assert worker._ladder_pick(combos) == ("euw1", "GOLD")  # el que menos analizadas tiene
    assert worker._ladder_pick(combos) == ("eun1", "GOLD")  # dentro del elo, los servidores por turnos
    crawler.note_session("americas", "GOLD", 100)  # otra región ya trajo 100 de Oro: le toca a Hierro
    assert worker._ladder_pick(combos)[1] == "IRON"
    crawler.visit_started("asia", "IRON", expected=200)  # y una visita en curso a Hierro cuenta lo que dará
    assert worker._ladder_pick(combos)[1] != "IRON"
    crawler.visit_ended("asia")
    picks: Counter = Counter()
    for _ in range(60):
        tier = worker._ladder_pick(combos)[1]
        picks[tier] += 1
        crawler.note_session("europe", tier, 20)
    assert picks["GOLD"] > picks["IRON"] > picks["CHALLENGER"] > 0  # Aspirante, con 1.000, también recibe
    assert worker._ladder_pick([("euw1", "CHALLENGER")]) == ("euw1", "CHALLENGER")  # sin otros elos disponibles


def test_la_grafica_del_recolector_acumula_desde_que_arranca_y_no_vuelve_a_0_al_subir(keyed_svc):
    """La gráfica por elo del recolector contaba desde la última subida a GitHub y volvía a 0 cada 2.000 partidas: ahora
    acumula desde que arrancó el recolector (/api/crawler/otps/live → ``session_tiers``)."""
    from fastapi.testclient import TestClient

    from app.main import create_app

    crawler = keyed_svc.crawler
    crawler.note_session("europe", "GOLD", 30)
    crawler.note_analyzed("IRON", 5)
    crawler.visit_started("asia", "BRONZE")  # una visita en curso no cuenta hasta que trae partidas
    app = create_app(keyed_svc.settings, run_startup=False)
    app.state.svc = keyed_svc
    with TestClient(app) as client:
        assert client.get("/api/crawler/otps/live").json()["session_tiers"] == {"GOLD": 30, "IRON": 5}
    crawler._reset_session(CrawlerConfig().validated())  # al arrancar otra vez el recolector, de cero
    assert crawler.session_tiers() == {}


def test_el_numero_junto_al_boton_cuenta_las_nuevas_desde_la_ultima_subida(keyed_svc, monkeypatch):
    """Junto a «Detener», las partidas nuevas desde la última subida a GitHub (vuelve a 0 cada 2.000): el estado del
    recolector trae ``pending`` y ``every`` de la subida automática."""
    from types import SimpleNamespace

    from app import autopush

    fake = SimpleNamespace(public=lambda: {"enabled": True, "every": 2000, "pending": 137, "running": False})
    monkeypatch.setattr(autopush.AutoPush, "of", staticmethod(lambda svc: fake))
    push = keyed_svc.crawler.status()["push"]
    assert push["pending"] == 137 and push["every"] == 2000 and push["enabled"] is True


def test_todo_el_ranking_analiza_los_10_jugadores_solo_en_la_base_de_datos(keyed_svc):
    """25-11-2026: con todo el ranking toda partida nueva se analiza, los 10 jugadores (un timeline por partida). Van solo
    a la base de datos (``kind='ranking'``): cuentan en la gráfica del total pero no se escriben en data/ (ni se suben
    a GitHub: serían ~900 MB/hora)."""
    from app.ingest import otp_details

    ladder, payloads = build_world()
    fake = FakeRiotClient(ladder, payloads)
    run_crawler(keyed_svc, fake, ["CHALLENGER"])
    db = keyed_svc.db
    valid = db.scalar("SELECT COUNT(*) FROM matches WHERE is_remake = 0")
    assert valid == 9
    timelines = Counter(call[1] for call in fake.calls_to("timeline"))
    assert len(timelines) == valid and max(timelines.values()) == 1  # uno por partida nueva, nunca repetido
    rows = db.query("SELECT match_id, kind, COUNT(*) AS n FROM otp_games GROUP BY match_id, kind")
    assert {r["kind"] for r in rows} == {"ranking"} and len(rows) == valid and all(r["n"] == 10 for r in rows)
    live = otp_details.live_counts(db)
    assert live["games"] == 10 * valid and live["tiers_player"].get("CHALLENGER") == live["tiers"]["CHALLENGER"]  # «resto»
    data_dir = keyed_svc.settings.data_dir
    for folder in ("otps", "partidas_campeon"):
        assert not list((data_dir / folder).rglob("partidas.jsonl.gz"))
    assert otp_details.outdated(db) == 0  # sin bruto: nunca se intentan rehacer


def test_los_10_analisis_de_cada_partida_del_ranking_van_juntos_en_xz_y_se_recuperan_exactos(keyed_svc):
    """Los 10 análisis de una partida de todo el ranking van en un bloque xz con lo común aparte: sin pérdidas y mucho
    más pequeño que un análisis comprimido por jugador. Sus filas de otp_games no llevan dossier."""
    from app.coach.store import pack
    from app.ingest import otp_details

    ladder, payloads = build_world()
    run_crawler(keyed_svc, FakeRiotClient(ladder, payloads), ["CHALLENGER"])
    db = keyed_svc.db
    assert db.scalar("SELECT COUNT(*) FROM otp_games WHERE kind = 'ranking' AND dossier IS NOT NULL") == 0
    match_id = "EUW1_1001"
    saved = otp_details.ranking_dossiers(db, match_id)
    payload = payloads[match_id]
    shared: dict = {}  # como al guardarla: la reconstrucción de los 10, una vez
    expected = [otp_details.build_dossier(payload, make_timeline(payload), p["puuid"], otp_details.catalog_for(keyed_svc, payload),
                                          elo={"tier": "CHALLENGER"}, shared=shared) for p in payload["info"]["participants"]]
    by_puuid = {d.pop("puuid"): d for d in saved}
    assert len(by_puuid) == 10
    for p, exp in zip(payload["info"]["participants"], expected):
        got = by_puuid[p["puuid"]]
        exp = json.loads(json.dumps(exp))  # como queda al guardarlo en JSON (igual que con pack): claves numéricas en texto
        assert {k: v for k, v in got.items() if k not in ("tier", "division", "lp")} == {k: v for k, v in exp.items() if k not in ("tier", "division", "lp")}
    blob = db.scalar("SELECT blob FROM ranking_games WHERE match_id = ?", (match_id,))
    assert len(blob) < sum(len(pack(d)) for d in expected) / 2  # al menos la mitad que uno por jugador
