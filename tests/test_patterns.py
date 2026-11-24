"""Búsqueda por partidas: ventanas tipo minidraft con matchups y composiciones (sin red)."""

from __future__ import annotations

import gzip
import json

from app.ingest import patterns
from app.ingest.crawler import CrawlerConfig
from tests.factories import AATROX, AHRI, DARIUS, LEE_SIN, SYNDRA, VIEGO, WUKONG, ZED, make_match, store_payloads
from tests.test_crawler import wait_until
from tests.test_crawler_otp import HistoryRiot, _now_s

# Lee Sin contra Viego en jungla y Ahri contra Zed en mid, en la misma partida (la alineación por defecto de las pruebas).
DOUBLE = {
    "kind": "matchup",
    "a": [{"champ": LEE_SIN, "role": "JUNGLE"}, {"champ": AHRI, "role": "MID"}],
    "b": [{"champ": VIEGO, "role": "JUNGLE"}, {"champ": ZED, "role": "MID"}],
}


def teams_of(match: dict) -> dict:
    return patterns.lineups(
        (p["teamId"], {"TOP": "TOP", "JUNGLE": "JUNGLE", "MIDDLE": "MID", "BOTTOM": "ADC", "UTILITY": "SUPPORT"}.get(p["teamPosition"]), p["championId"])
        for p in match["info"]["participants"]
    )


def test_las_ventanas_se_limpian_y_su_id_no_depende_del_orden_ni_del_nombre():
    raw = [
        {**DOUBLE, "name": "Jungla y mid"},
        {"kind": "matchup", "name": "otra", "a": list(reversed(DOUBLE["a"])), "b": DOUBLE["b"]},  # la misma: fuera
        {"kind": "matchup", "a": [{"champ": AATROX}], "b": []},  # un matchup necesita el rol de cada campeón
        {"kind": "matchup", "a": [{"champ": AATROX, "role": "TOP"}, {"champ": AATROX, "role": "MID"}], "b": [{"champ": DARIUS, "role": "TOP"}]},
        {"kind": "comp", "a": [{"champ": AATROX}, {"champ": LEE_SIN, "role": "JUNGLE"}, {"champ": AHRI}], "min_a": 9},
    ]
    windows = patterns.clean(raw)
    assert len(windows) == 3
    double, solo, comp = windows
    assert double["name"] == "Jungla y mid" and double["min_a"] == 2 and double["min_b"] == 2
    assert solo["a"] == [{"champ": AATROX, "role": "TOP"}]  # un campeón no sale dos veces en la misma partida
    assert comp["kind"] == "comp" and comp["min_a"] == 3 and comp["min_b"] == 0  # el mínimo, como mucho todos
    assert patterns.window_id(dict(double, name="otro nombre")) == double["id"]
    assert patterns.champions(windows) == [LEE_SIN, AHRI, VIEGO, ZED, AATROX, DARIUS]
    roles = patterns.champion_roles(windows)
    assert roles[LEE_SIN] == ["JUNGLE"] and AATROX not in roles  # en la composición Aatrox va sin rol: cualquiera


def test_un_matchup_doble_solo_cuenta_con_los_dos_enfrentamientos_y_da_igual_el_lado():
    (w,) = patterns.clean([DOUBLE])
    both = make_match("EUW1_1")
    assert patterns.match_window(w, teams_of(both)) == {"side_a": 100, "hits": 4, "exact": True}
    swapped = make_match("EUW1_2", blue={"JUNGLE": VIEGO, "MID": ZED}, red={"JUNGLE": LEE_SIN, "MID": AHRI})
    assert patterns.match_window(w, teams_of(swapped))["side_a"] == 200  # Lee Sin y Ahri en el rojo
    only_jungle = make_match("EUW1_3", blue={"MID": SYNDRA})
    assert patterns.match_window(w, teams_of(only_jungle)) is None
    split = make_match("EUW1_4", red={"MID": AHRI}, blue={"MID": ZED})  # Ahri contra Zed, pero Ahri con Viego
    assert patterns.match_window(w, teams_of(split)) is None


def test_una_composicion_cuenta_las_iguales_y_las_parecidas():
    (exact, partial) = patterns.clean([
        {"kind": "comp", "a": [{"champ": LEE_SIN}, {"champ": AHRI}, {"champ": AATROX}]},
        {"kind": "comp", "a": [{"champ": LEE_SIN}, {"champ": AHRI}, {"champ": WUKONG}], "min_a": 2},
    ])
    game = make_match("EUW1_5")  # azul: Aatrox, Lee Sin, Ahri, Jinx, Thresh
    assert patterns.match_window(exact, teams_of(game)) == {"side_a": 100, "hits": 3, "exact": True}
    assert patterns.match_window(partial, teams_of(game)) == {"side_a": 100, "hits": 2, "exact": False}
    assert patterns.match_window(partial, teams_of(make_match("EUW1_6", blue={"MID": SYNDRA}))) is None


def _world():
    """Un OTP de Lee Sin con 4 partidas: 2 encajan con la ventana doble (una con él en el lado rojo), 2 no."""
    now = _now_s() - 3 * 3600
    ms = lambda i: (now - i * 8 * 3600) * 1000  # noqa: E731
    payloads = {
        "EUW1_9000": make_match("EUW1_9000", puuids={2: "otp-lee"}, game_start=ms(0)),
        "EUW1_9001": make_match("EUW1_9001", puuids={2: "otp-lee"}, red={"JUNGLE": WUKONG}, game_start=ms(1)),
        "EUW1_9002": make_match("EUW1_9002", puuids={2: "otp-lee"}, blue={"MID": SYNDRA}, game_start=ms(2)),
        "EUW1_9003": make_match(
            "EUW1_9003", puuids={7: "otp-lee"}, blue={"JUNGLE": VIEGO, "MID": ZED}, red={"JUNGLE": LEE_SIN, "MID": AHRI}, game_start=ms(3)
        ),
    }
    return {"otp-lee": list(payloads)}, payloads


def _run(svc, fake, **cfg):
    svc.riot = fake
    svc.crawler.workers.clear()
    svc.crawler.start(CrawlerConfig(platforms=["euw1"], tiers=["CHALLENGER"], focus="mastery", min_mastery=10_000, patterns=[DOUBLE], **cfg))
    worker = svc.crawler.workers["europe"]
    return worker


def test_la_busqueda_por_partidas_guarda_enteras_solo_las_que_encajan(keyed_svc):
    ladder, payloads = _world()
    fake = HistoryRiot(ladder, payloads, {"otp-lee": [(LEE_SIN, 900_000)]})
    worker = _run(keyed_svc, fake)
    db = keyed_svc.db
    assert wait_until(lambda: worker.state["status"] == "idle", 30), worker.state
    keyed_svc.crawler.stop()
    worker.join(timeout=5)
    keyed_svc.crawler.wait_stopped()
    cfg = keyed_svc.crawler.config
    assert cfg.tiers[0] == "IRON" and cfg.tiers[-1] == "CHALLENGER"  # el elo no filtra
    # Las 4 se bajan y se guardan en la base de datos (sirven para todo lo demás)…
    assert {r[0] for r in db.query("SELECT match_id FROM matches")} == set(payloads)
    # …pero el timeline, solo de las 2 que encajan.
    assert sorted(c[1] for c in fake.calls_to("timeline")) == ["EUW1_9000", "EUW1_9003"]
    rows = {r["match_id"]: dict(r) for r in db.query("SELECT * FROM pattern_games")}
    assert set(rows) == {"EUW1_9000", "EUW1_9003"}
    assert rows["EUW1_9000"]["side_a"] == 100 and rows["EUW1_9000"]["win_a"] == 1  # gana el azul (por defecto)
    assert rows["EUW1_9003"]["side_a"] == 200 and rows["EUW1_9003"]["win_a"] == 0
    # El fichero: cada partida entera, con los 10 jugadores analizados.
    (folder,) = list((keyed_svc.settings.data_dir / "busquedas").glob("*_" + cfg.patterns[0]["id"]))
    with gzip.open(folder / "partidas.jsonl.gz", "rt", encoding="utf-8") as fh:
        games = [json.loads(line) for line in fh]
    assert [g["match_id"] for g in games] == ["EUW1_9000", "EUW1_9003"]
    assert all(len(g["players"]) == 10 and g["has_timeline"] for g in games)
    roles = {(p["team"], p["role"]) for p in games[0]["players"]}
    assert len(roles) == 10 and all(p["analysis"]["has_timeline"] for p in games[0]["players"])
    assert "frames" not in games[0]["players"][0] and games[0]["map_events"]
    summary = json.loads((folder / "resumen.json").read_text(encoding="utf-8"))
    assert summary["games"] == 2 and summary["wins_a"] == 1 and summary["winrate_a"] == 50.0
    assert summary["by_side"] == {"azul": [1, 1], "rojo": [1, 0]}
    report = keyed_svc.crawler.pattern_report()
    window = report["ventanas"][cfg.patterns[0]["id"]]
    assert report["revisadas"] == 4 and window["encontradas"] == 2 and window["guardadas"] == 2


def test_las_partidas_ya_guardadas_que_encajan_se_completan_con_su_timeline(keyed_svc):
    """Al arrancar se miran las partidas de la base de datos: las que encajan solo necesitan partida y timeline."""
    _ladder, payloads = _world()
    store_payloads(keyed_svc, list(payloads.values()), seed_tier="GOLD")
    (w,) = patterns.clean([DOUBLE])
    found = patterns.scan_stored(keyed_svc.db, [w], ["euw1"])
    assert [f["match_id"] for f in found] == ["EUW1_9000", "EUW1_9003"] and found[0]["tier"] == "GOLD"
    fake = HistoryRiot({}, payloads, {})  # nadie a quien visitar: solo lo ya guardado
    worker = _run(keyed_svc, fake)
    db = keyed_svc.db
    assert wait_until(lambda: db.scalar("SELECT COUNT(*) FROM pattern_games WHERE analyzed = 1") == 2, 30)
    keyed_svc.crawler.stop()
    worker.join(timeout=5)
    keyed_svc.crawler.wait_stopped()
    assert sorted(c[1] for c in fake.calls_to("timeline")) == ["EUW1_9000", "EUW1_9003"]
    assert not [c for c in fake.calls_to("match") if c[1] in ("EUW1_9001", "EUW1_9002")]
    assert db.scalar("SELECT tier FROM pattern_games WHERE match_id = 'EUW1_9000'") == "GOLD"
    assert keyed_svc.crawler.pattern_report()["ventanas"][w["id"]]["de_bd"] == 2
    assert patterns.scan_stored(db, [w], ["euw1"]) == []  # ya están en sus resultados


def test_la_api_da_los_resultados_y_el_perfil_por_elo_lee_a_los_10(keyed_svc):
    from fastapi.testclient import TestClient

    from app.coach import elo_profile
    from app.main import create_app

    ladder, payloads = _world()
    fake = HistoryRiot(ladder, payloads, {"otp-lee": [(LEE_SIN, 900_000)]})
    worker = _run(keyed_svc, fake)
    assert wait_until(lambda: worker.state["status"] == "idle", 30), worker.state
    keyed_svc.crawler.stop()
    worker.join(timeout=5)
    keyed_svc.crawler.wait_stopped()
    app = create_app(keyed_svc.settings, run_startup=False)
    app.state.svc = keyed_svc
    with TestClient(app) as client:
        data = client.get("/api/crawler/patterns").json()
    (win,) = data["windows"]
    assert win["games"] == 2 and win["winrate_a"] == 50.0 and win["exact"] == 2
    assert "Lee Sin vs Viego (jungla)" in win["label"] and "Ahri vs Zed (mid)" in win["label"]
    latest = win["recent"][0]
    assert latest["match_id"] == "EUW1_9000" and len(latest["teams"]["100"]) == 5 and len(latest["teams"]["200"]) == 5
    # Los 10 jugadores de cada partida entran en el análisis general (una vez cada uno).
    players = list(patterns.iter_dossiers(keyed_svc.settings.data_dir))
    assert len(players) == 20 and len({(d["match_id"], d["team"], d["champ"]) for _p, d in players}) == 20
    recs = list(elo_profile._records(keyed_svc.settings.data_dir))
    assert len(recs) >= 20 and {r["role"] for r in recs} == {"TOP", "JUNGLE", "MID", "ADC", "SUPPORT"}


def test_el_arranque_acepta_ventanas_con_nombres_de_campeon(keyed_svc, monkeypatch):
    from fastapi.testclient import TestClient

    from app.main import create_app

    started = {}
    monkeypatch.setattr(keyed_svc.crawler, "start", lambda config, **kw: started.setdefault("cfg", config.validated()) and {"running": True})
    app = create_app(keyed_svc.settings, run_startup=False)
    app.state.svc = keyed_svc
    body = {"platforms": ["euw1"], "tiers": ["GOLD"], "focus": "otp",
            "patterns": [{"kind": "matchup", "a": [{"champ": "Lee Sin", "role": "JUNGLE"}], "b": [{"champ": "Viego", "role": "JUNGLE"}]}]}
    with TestClient(app) as client:
        res = client.post("/api/crawler/start", json=body)
        assert res.status_code == 200, res.text
        bad = client.post("/api/crawler/start", json={**body, "patterns": [{"kind": "matchup", "a": [{"champ": "Lee Sin"}]}]})
        assert bad.status_code == 400  # un matchup sin rol no es nada
    cfg = started["cfg"]
    assert cfg.champions == [LEE_SIN, VIEGO] and cfg.champion_roles == {LEE_SIN: ["JUNGLE"], VIEGO: ["JUNGLE"]}
    assert len(cfg.tiers) == 10


def test_en_lo_ya_guardado_una_composicion_parecida_no_necesita_al_campeon_menos_jugado(keyed_svc):
    """Con «al menos 2 de 3», una partida encaja sin el campeón menos jugado de la ventana. Antes se miraban solo las
    partidas de ese campeón y se perdían (8 de 20 ventanas, comprobado con partidas reales el 24-11-2026)."""
    payloads = [
        make_match("EUW1_8001"),  # azul: Lee Sin y Ahri (2 de 3, sin Wukong)
        make_match("EUW1_8002", red={"JUNGLE": WUKONG}),  # azul: Lee Sin y Ahri; Wukong en el otro equipo
        make_match("EUW1_8003", blue={"MID": SYNDRA}),  # azul: solo Lee Sin (1 de 3)
    ]
    store_payloads(keyed_svc, payloads)
    (w,) = patterns.clean([{"kind": "comp", "a": [{"champ": LEE_SIN}, {"champ": AHRI}, {"champ": WUKONG}], "min_a": 2}])
    found = {f["match_id"]: f["found"][0][1] for f in patterns.scan_stored(keyed_svc.db, [w])}
    assert set(found) == {"EUW1_8001", "EUW1_8002"}
    assert all(res["side_a"] == 100 and not res["exact"] for res in found.values())
