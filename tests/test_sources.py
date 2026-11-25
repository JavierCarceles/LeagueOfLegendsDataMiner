"""Fuentes externas, precarga en segundo plano y repeticiones en el cliente (sin red)."""

from __future__ import annotations

import threading
import time

import pytest

from app import budget, lcu
from app.analytics.evidence import combine_web
from app.coach import replay_client
from app.external import base as base_mod
from app.external.base import CachedSource
from app.external.lolalytics import Lolalytics, game_version
from app.external.patchnotes import change_direction, parse_notes, verdict
from app.warmer import Warmer


# ------------------------------------------------------------------ caché común
class Dummy(CachedSource):
    name = "dummy"


def test_fresh_hit_does_not_fetch(db):
    src = Dummy(db)
    calls = []
    assert src._cached("k", "a", 3600, lambda: calls.append(1) or {"v": 1}) == {"v": 1}
    assert src._cached("k", "a", 3600, lambda: calls.append(1) or {"v": 2}) == {"v": 1}
    assert len(calls) == 1


def test_stale_is_served_at_once_and_refreshed_in_background(db):
    queued = []

    class FakeWarmer:
        def submit(self, lane, key, fn, priority, label=None):
            queued.append((lane, key, fn))
            return True

    src = Dummy(db, warmer=FakeWarmer())
    src._cached("k", "a", 3600, lambda: {"v": 1})
    with db.write() as conn:  # lo envejecemos
        conn.execute("UPDATE ext_cache SET fetched_at = fetched_at - 10000")
    src._mem.clear()
    value = src._cached("k", "a", 3600, lambda: {"v": 2})
    assert value == {"v": 1}  # lo viejo, al instante
    assert len(queued) == 1
    queued[0][2]()  # el precargador lo refresca
    assert src._cached("k", "a", 3600, lambda: {"v": 3}) == {"v": 2}


def test_single_flight_fetches_once_for_concurrent_callers(db):
    src = Dummy(db)
    calls = []
    gate = threading.Event()

    def fetch():
        calls.append(1)
        gate.wait(2)
        return {"v": 1}

    results = []
    threads = [threading.Thread(target=lambda: results.append(src._cached("k", "same", 3600, fetch))) for _ in range(5)]
    for t in threads:
        t.start()
    time.sleep(0.2)
    gate.set()
    for t in threads:
        t.join(3)
    assert len(calls) == 1
    assert results == [{"v": 1}] * 5


def test_failures_back_off_and_never_raise(db):
    src = Dummy(db)
    calls = []

    def boom():
        calls.append(1)
        raise RuntimeError("caída")

    assert src._cached("k", "x", 3600, boom) is None
    assert src._cached("k", "x", 3600, boom) is None  # en backoff: no se reintenta
    assert len(calls) == 1
    assert src.stats["errors"] == 1
    src.clear_failures()
    assert src._cached("k", "x", 3600, boom) is None
    assert len(calls) == 2


def test_interactive_fetch_is_bounded_by_the_request_budget(db):
    src = Dummy(db)
    release = threading.Event()

    def slow():
        release.wait(5)
        return {"v": 1}

    b = budget.Budget(0.3)
    token = budget._current.set(b)
    try:
        start = time.monotonic()
        assert src._cached("k", "slow", 3600, slow) is None
        assert time.monotonic() - start < 2
        assert b.pending
    finally:
        budget._current.reset(token)
        release.set()
    # la descarga siguió en segundo plano y queda en caché
    deadline = time.monotonic() + 3
    while time.monotonic() < deadline and src.peek("k", "slow") is None:
        time.sleep(0.05)
    assert src.peek("k", "slow")[1] == {"v": 1}


def test_disabled_source_only_reads_cache(db):
    src = Dummy(db, enabled=False)
    assert src._cached("k", "a", 3600, lambda: {"v": 1}) is None


def test_inline_refresh_inside_warmer_threads_refreshes_stale(db):
    src = Dummy(db)
    src._cached("k", "a", 3600, lambda: {"v": 1})
    with db.write() as conn:
        conn.execute("UPDATE ext_cache SET fetched_at = fetched_at - 10000")
    src._mem.clear()
    token = base_mod.inline_refresh.set(True)
    try:
        assert src._cached("k", "a", 3600, lambda: {"v": 2}) == {"v": 2}
    finally:
        base_mod.inline_refresh.reset(token)


# ------------------------------------------------------------- presupuesto
def test_budget_header_marks_pending_responses(svc):
    from fastapi.testclient import TestClient

    from app.main import create_app

    app = create_app(svc.settings, run_startup=False)
    with TestClient(app) as client:
        res = client.get("/api/system/status")
        assert res.status_code == 200
        assert res.headers["x-data-pending"] == "0"
        assert "warmer" in res.json()


# ------------------------------------------------------------- precargador
class _Svc:
    def __init__(self, db, in_game=False):
        self.db = db
        self.settings = type("S", (), {"pause_in_game": True})()
        self.tracker = type("T", (), {"state": {"in_game": in_game}})()
        self.startup_done = threading.Event()


def test_warmer_dedups_and_keeps_best_priority(db):
    w = Warmer(_Svc(db))
    ran = []
    assert w.submit("opgg-web", ("k", 1), lambda: ran.append("low"), 40)
    assert w.submit("opgg-web", ("k", 1), lambda: ran.append("high"), 10)
    lane = w.lanes["opgg-web"]
    assert lane.pending() == 1
    prio, seq, key = lane.queue.get_nowait()
    # la entrada vieja (prioridad 40) queda obsoleta; la buena es la de prioridad 10
    while lane.best[key][2] != seq:
        prio, seq, key = lane.queue.get_nowait()
    assert prio == 10
    lane.best[key][1]()
    assert ran == ["high"]


def test_warmer_pauses_in_game_and_manual(db):
    assert Warmer(_Svc(db, in_game=True)).paused_reason() == "in_game"
    w = Warmer(_Svc(db))
    assert w.paused_reason() is None
    w.set_paused(True)
    assert w.paused_reason() == "manual"


def test_warmer_remembers_scopes_and_ignores_legacy(db):
    w = Warmer(_Svc(db))
    w.note_scope("diamond_plus", "euw1", "latest")
    w.note_scope("gold_plus", "all", "legacy")
    scopes = w.scopes()
    assert scopes[0] == {"tier": "emerald_plus", "platform": "all", "patch": "all"}
    assert {"tier": "diamond_plus", "platform": "euw1", "patch": "latest"} in scopes
    assert all(s["patch"] != "legacy" for s in scopes)
    w2 = Warmer(_Svc(db))  # persiste en la BD
    assert {"tier": "diamond_plus", "platform": "euw1", "patch": "latest"} in w2.scopes()


def test_disabled_warmer_accepts_nothing(db):
    w = Warmer(_Svc(db), enabled=False)
    assert w.submit("opgg-web", "k", lambda: None, 1) is False


# ------------------------------------------------------------- webs combinadas
def test_combine_web_takes_max_sample_and_pooled_rate():
    out = combine_web({1: (1000, 550)}, {1: (400, 200), 2: (100, 60)})
    games, wins = out[1]
    assert games == 1000  # no se suman: se solapan
    assert abs(wins / games - 750 / 1400) < 0.002
    assert out[2] == (100, 60)
    assert combine_web(None, None) is None
    assert combine_web({}, None) == {}


# --------------------------------------------------------------- Lolalytics
def test_game_version_maps_year_patches():
    assert game_version("26.23") == "16.23"
    assert game_version("16.23") == "16.23"
    assert game_version("x") == "x"


def test_lolalytics_parses_tier_list_and_counters(db, dd, monkeypatch):
    lola = Lolalytics(db, dd)
    champ = next(iter(dd.champions))
    other = next(c for c in dd.champions if c != champ)

    def fake_get(params):
        if params["ep"] == "list":
            return {"cid": {str(champ): {"wr": 52.5, "pr": 3.1, "br": 1.0, "games": 5000, "pctLane": 80.0, "tier": 2, "rank": 4}, "999999": {"games": 0}}}
        return {"stats": {"cid": champ}, "counters": [{"cid": other, "vsWr": 55.0, "n": 200}, {"cid": champ, "vsWr": 50, "n": 9}, {"cid": "bad"}]}

    monkeypatch.setattr(lola, "_get", fake_get)
    tl = lola.tier_list("TOP", "emerald_plus", "global", "26.23")
    assert tl[champ]["games"] == 5000 and tl[champ]["role_rate"] == 0.8
    counters = lola.counters(champ, "TOP", "emerald_plus", "global", "26.23")
    assert counters == {other: (200, 110)}


def test_lolalytics_parses_team_synergies(db, dd, monkeypatch):
    """``ep=build-team``: el WR del campeón con cada compañero de cada línea (una petición para todas)."""
    lola = Lolalytics(db, dd)
    champ = next(iter(dd.champions))
    other = next(c for c in dd.champions if c != champ)
    calls = []

    def fake_get(params):
        calls.append(params["ep"])
        return {
            "team_h": ["id", "wr", "d1", "d2", "pr", "n"],
            "team": {"jungle": [[other, 54.0, 1.2, 0.4, 3.1, 1000], ["roto"], [champ, 50.0, 0, 0, 1, 0]], "middle": [], "otra": [[other, 60, 0, 0, 1, 5]]},
        }

    monkeypatch.setattr(lola, "_get", fake_get)
    assert lola.synergies(champ, "TOP", "JUNGLE", "emerald_plus", "global", "26.23") == {other: (1000, 540)}
    assert lola.synergies(champ, "TOP", "MID", "emerald_plus", "global", "26.23") == {}
    assert lola.synergies(champ, "TOP", "NADA", "emerald_plus", "global", "26.23") is None
    assert calls == ["build-team"]  # la segunda línea sale de la misma descarga (caché)


def test_lolalytics_rejects_unknown_scope(db, dd):
    lola = Lolalytics(db, dd)
    assert lola.tier_list("TOP", "nope", "global", "26.23") is None
    assert lola.counters(next(iter(dd.champions)), "TOP", "emerald_plus", "atlantis", "26.23") is None


# ---------------------------------------------------------- notas de parche
NOTES_HTML = """
<html><head><title>Notas de la versión 26.23 | League of Legends</title></head><body>
<h2>Campeones</h2>
<div><p><a><img src="https://ddragon.leagueoflegends.com/cdn/16.22.1/img/champion/Annie.png"></a></p>
<h3 class="change-title" id="patch-annie"><a>Annie</a></h3>
<blockquote class="blockquote context"><p>Más potencia.</p></blockquote>
<h4 class="change-detail-title ability-title">R - Tibbers</h4>
<ul><li><strong>Daño</strong>: 80/120/160 ⇒ <strong>80/125/170</strong>.</li>
<li><strong>Enfriamiento</strong>: 120/100/80 ⇒ <strong>110/95/80</strong>.</li></ul></div>
<h2>ARAM</h2>
<div><p><img src="https://ddragon.leagueoflegends.com/cdn/16.22.1/img/champion/Annie.png"></p>
<h3 class="change-title"><a>Annie</a></h3><ul><li>Daño infligido: 100 % ⇒ 90 %.</li></ul></div>
</body></html>
"""


class _DD:
    def resolve(self, value):
        return 1 if str(value).lower() == "annie" else None


def test_parse_notes_keeps_only_rift_sections():
    data = parse_notes(NOTES_HTML, _DD())
    assert data["title"] == "Notas de la versión 26.23 de League of Legends" or data["title"].startswith("Notas de la versión 26.23")
    annie = data["champions"]["1"]
    assert annie["verdict"] == "buff"  # el bloque de ARAM (-10 %) no pisa al de la Grieta
    assert annie["context"] == "Más potencia."
    assert annie["groups"][0]["title"] == "R - Tibbers"


def test_change_direction_compares_number_by_number():
    assert change_direction("Daño: 80/120/160 ⇒ 80/125/170.") == 1
    assert change_direction("Enfriamiento: 12/11/10 ⇒ 10/9/8.") == 1
    assert change_direction("Coste de maná: 50 ⇒ 60.") == -1
    assert change_direction("Vida: 1150-3500 ⇒ 1275-4000.") == 1  # rango, no negativos
    assert change_direction("Nuevo efecto: ahora ralentiza.") == 0
    assert verdict(["Daño: 10 ⇒ 20.", "Coste: 50 ⇒ 60."]) == "adjust"


# ------------------------------------------------- repeticiones en el cliente
def test_game_id_from_match_id():
    assert replay_client.game_id("EUW1_7412345678") == 7412345678
    with pytest.raises(ValueError):
        replay_client.game_id("sin-numero")


def test_status_without_client(monkeypatch):
    def no_client(gid):
        raise lcu.LCUError("No se encontró el cliente de League of Legends abierto")

    monkeypatch.setattr(lcu, "replay_metadata", no_client)
    out = replay_client.ClientReplays().status("EUW1_1")
    assert out["state"] == "no_client" and out["client_open"] is False and "Abre el cliente" in out["detail"]


def test_watch_downloads_opens_and_seeks(monkeypatch):
    states = iter(["download", "download", "downloading", "watch", "watch"])
    calls = []
    monkeypatch.setattr(lcu, "replay_metadata", lambda gid: {"state": next(states, "watch"), "downloadProgress": 50})
    monkeypatch.setattr(lcu, "replay_download", lambda gid: calls.append(("download", gid)))
    monkeypatch.setattr(lcu, "replay_watch", lambda gid: calls.append(("watch", gid)))
    monkeypatch.setattr(lcu, "replay_api_enabled", lambda: True)
    monkeypatch.setattr(lcu, "replay_playback", lambda: {"length": 1800})
    monkeypatch.setattr(lcu, "replay_seek", lambda t: calls.append(("seek", t)))
    monkeypatch.setattr(replay_client.time, "sleep", lambda s: None)
    client = replay_client.ClientReplays()
    res = client.watch("EUW1_42", 600)
    assert "Abriendo" in res["message"]
    deadline = time.monotonic() + 3
    while time.monotonic() < deadline and client.job["status"] == "running":
        time.sleep(0.02)
    assert client.job["status"] == "done", client.job
    assert calls == [("download", 42), ("watch", 42), ("seek", 600.0)]


def test_watch_refuses_expired_replays(monkeypatch):
    monkeypatch.setattr(lcu, "replay_metadata", lambda gid: {"state": "incompatible"})
    with pytest.raises(lcu.LCUError, match="otro parche"):
        replay_client.ClientReplays().watch("EUW1_42", 10)


def test_watch_without_replay_api_tells_the_minute(monkeypatch):
    monkeypatch.setattr(lcu, "replay_metadata", lambda gid: {"state": "watch"})
    monkeypatch.setattr(lcu, "replay_watch", lambda gid: None)
    monkeypatch.setattr(lcu, "replay_api_enabled", lambda: False)
    client = replay_client.ClientReplays()
    client.watch("EUW1_42", 125)
    deadline = time.monotonic() + 3
    while time.monotonic() < deadline and client.job["status"] == "running":
        time.sleep(0.02)
    assert client.job["status"] == "done" and "2:05" in client.job["message"]


def test_enable_replay_api_edits_game_cfg_with_backup(tmp_path, monkeypatch):
    cfg = tmp_path / "Config" / "game.cfg"
    cfg.parent.mkdir()
    cfg.write_bytes(b"[General]\r\nWindowMode=2\r\n[HUD]\r\nScale=1\r\n")
    monkeypatch.setattr(lcu, "game_cfg", lambda: cfg)
    assert lcu.replay_api_enabled() is False
    lcu.enable_replay_api()
    text = cfg.read_bytes().decode("utf-8")
    assert "[General]\r\nEnableReplayApi=1\r\nWindowMode=2" in text
    assert (cfg.parent / "game.cfg.tempu-backup").exists()
    assert lcu.replay_api_enabled() is True
    lcu.enable_replay_api()  # idempotente
    assert cfg.read_bytes().decode("utf-8").count("EnableReplayApi") == 1


def test_enable_replay_api_keeps_read_only_and_leaves_no_temp_file(tmp_path, monkeypatch):
    import os
    import stat

    cfg = tmp_path / "Config" / "game.cfg"
    cfg.parent.mkdir()
    cfg.write_bytes(b"[General]\r\nWindowMode=2\r\n")
    os.chmod(cfg, stat.S_IREAD)  # el usuario lo tiene en solo lectura
    monkeypatch.setattr(lcu, "game_cfg", lambda: cfg)
    try:
        lcu.enable_replay_api()
        assert lcu.replay_api_enabled() is True
        assert not os.access(cfg, os.W_OK)  # sigue en solo lectura
        assert sorted(p.name for p in cfg.parent.iterdir()) == ["game.cfg", "game.cfg.tempu-backup"]  # sin temporales
    finally:
        os.chmod(cfg, stat.S_IREAD | stat.S_IWRITE)
        os.chmod(cfg.parent / "game.cfg.tempu-backup", stat.S_IREAD | stat.S_IWRITE)


def test_install_dir_prefers_the_running_client_then_live_over_pbe(tmp_path, monkeypatch):
    pbe = tmp_path / "League of Legends (PBE)"
    live = tmp_path / "League of Legends"
    for folder in (pbe, live):
        (folder / "Config").mkdir(parents=True)
    monkeypatch.setattr(lcu, "INSTALL_DIRS", [pbe, live])  # RiotClientInstalls.json puede listar la PBE primero
    assert lcu.install_dir() == live
    (pbe / "lockfile").write_text("x")  # pero si la que está abierta es la PBE, esa
    assert lcu.install_dir() == pbe


def test_enable_replay_api_without_install_is_a_clear_error(monkeypatch):
    monkeypatch.setattr(lcu, "game_cfg", lambda: None)
    with pytest.raises(lcu.LCUError, match="game.cfg"):
        lcu.enable_replay_api()


def test_replay_endpoints_without_client(svc, monkeypatch):
    from fastapi.testclient import TestClient

    from app.main import create_app

    def no_client(gid):
        raise lcu.LCUError("No se encontró el cliente de League of Legends abierto")

    monkeypatch.setattr(lcu, "replay_metadata", no_client)
    app = create_app(svc.settings, run_startup=False)
    with TestClient(app) as client:
        assert client.get("/api/replay/EUW1_5/client").json()["state"] == "no_client"
        res = client.post("/api/replay/EUW1_5/client/watch", json={"t": 30})
        assert res.status_code == 409 and "cliente" in res.json()["detail"]


# ------------------------------------------------------------- recolector
def test_crawler_autostart_only_when_left_running(svc, monkeypatch):
    from app.ingest.crawler import CrawlerConfig

    started = []
    monkeypatch.setattr(svc.crawler, "start", lambda config, require_key=True, remember=True: started.append((config, require_key, remember)))
    svc.crawler.config = CrawlerConfig().validated()
    assert svc.crawler.autostart() is False  # nunca lo arrancaste
    svc.db.meta_set("crawler_autostart", "1")
    assert svc.crawler.autostart() is True
    assert started and started[0][1] is False and started[0][2] is False


# ------------------------------------------------------------- planificador
class _FakeLola:
    enabled = True

    def __init__(self, tier=None):
        self._tier = tier or {}
        self.calls = []

    def tier_list(self, role, tier, region, patch, allow_fetch=True):
        self.calls.append(("tier", role, patch))
        return self._tier.get(role)

    def counters(self, champ, role, tier, region, patch, allow_fetch=True):
        self.calls.append(("counters", champ, role, patch))
        return {}


def _plan(svc):
    w = Warmer(svc)
    tasks = []
    w._plan_sources(tasks)
    return tasks


def test_planner_plans_core_and_user_tasks(svc):
    from tests.factories import FakeOpgg, install_opgg, opgg_row

    champ = next(iter(svc.dd.champions))
    install_opgg(svc, FakeOpgg(versions=("16.23", "16.22"), tier_lists={"TOP": {champ: opgg_row(51.0)}}))
    svc.lolalytics = _FakeLola()
    tasks = _plan(svc)
    lanes = {t[0] for t in tasks}
    assert {"opgg-web", "opgg-mcp", "lolalytics"} <= lanes
    keys = [t[1] for t in tasks]
    assert ("counters", champ, "TOP", "emerald_plus", "global", "16.23") in keys
    assert any(k[0] == "tier" for k in keys)
    for _, _, fn, _, _ in tasks[:20]:  # las tareas son ejecutables y no fallan con fuentes falsas
        fn()


def test_planner_works_with_lolalytics_only(svc):
    champ = next(iter(svc.dd.champions))
    svc.opgg.enabled = False
    svc.lolalytics = _FakeLola({"MID": {champ: {"wr": 50.0, "pick": 2.0, "role_rate": 0.9, "games": 900}}})
    tasks = _plan(svc)
    keys = [t[1] for t in tasks]
    assert all(t[0] == "lolalytics" or t[0] == "riot-web" for t in tasks)
    assert any(k[0] == "counters" and k[1] == champ and k[2] == "MID" for k in keys)


def test_plan_counts_each_task_once(svc):
    from tests.factories import FakeOpgg, install_opgg, opgg_row

    champ = next(iter(svc.dd.champions))
    install_opgg(svc, FakeOpgg(versions=("16.23",), tier_lists={"TOP": {champ: opgg_row(51.0)}}))
    w = Warmer(svc)
    w._user_champions = lambda: [(champ, "TOP")]  # tus counters también son «esenciales»: no deben contarse dos veces
    w._make_plan(first=False)
    lanes = w.lanes
    queued = sum(lane.pending() for lane in lanes.values())
    assert w.plan["total"] == queued
    for lane in lanes.values():
        while not lane.queue.empty():
            prio, seq, key = lane.queue.get_nowait()
            entry = lane.best.get(key)
            if entry and entry[2] == seq:
                del lane.best[key]
                w._tick(entry[4], prio)
    assert w.plan["done"] == w.plan["total"] and w.plan["essential_done"] == w.plan["essential_total"]


def test_riot_client_installs_json_adds_custom_lol_folder(tmp_path, monkeypatch):
    target = tmp_path / "Riot Games" / "RiotClientInstalls.json"
    target.parent.mkdir(parents=True)
    target.write_text('{"associated_client": {"E:/Juegos/Riot Games/League of Legends/": "x", "E:/Juegos/VALORANT/live/": "y"}}', encoding="utf-8")
    monkeypatch.setenv("PROGRAMDATA", str(tmp_path))
    found = lcu._riot_client_installs()
    assert [str(f).replace("\\\\", "/").rstrip("/") for f in found] == ["E:/Juegos/Riot Games/League of Legends"]
    target.write_text("no es json", encoding="utf-8")
    assert lcu._riot_client_installs() == []


def test_item_catalog_for_each_game_patch(svc):
    import json as _json

    from tests.factories import item_json

    dd = svc.dd
    # La partida es del parche actual: se usa el catálogo cargado.
    assert dd.catalog_for(f"{dd.version}.999") is None or dd.catalog_for(f"{'.'.join(dd.version.split('.')[:2])}.123.456") is dd.item_catalog
    # Otro parche con su item.json en la caché local: se carga sin red y se recuerda.
    old = dd.cache_dir / "15.20.1"
    old.mkdir(parents=True, exist_ok=True)
    (old / "item.json").write_text(_json.dumps(item_json("15.20.1")), encoding="utf-8")
    cat = dd.catalog_for("15.20.712.3344", allow_network=False)
    assert cat is not None and cat.items and cat.version == "15.20.1"
    assert dd.catalog_for("15.20.1.1", allow_network=False) is cat
    # Sin item.json ni red: None (el análisis usa el catálogo actual y, si no encaja, omite oro/build).
    assert dd.catalog_for("14.3.1.1", allow_network=False) is None
    assert dd.catalog_for("raro") is None


# ------------------------------------------------------------ OP.GG: página de counters
def test_opgg_counters_never_cache_another_patch_or_a_broken_page(svc):
    from app.external.opgg import Opgg
    from tests.factories import DARIUS, GAREN, offline_ddragon

    dd = offline_ddragon(svc.settings.ddragon_dir)
    opgg = Opgg(svc.db, dd, enabled=True)
    row = '{"play":120,"win":66,"win_rate":55.0,"champion":{"key":"Garen","name":"Garen"}}'
    pages = {
        "16.23": '"game_tier":"emerald_plus","game_patch_version":"16.23"' + row,
        "16.10": '"game_tier":"emerald_plus","game_patch_version":"16.23"' + row,  # OP.GG ya no tiene ese parche
        "16.22": "<html>Error</html>",  # sin la marca: cambio de formato o página de error
        "16.21": '"game_tier":"emerald_plus","game_patch_version":"16.21"',  # nadie lo juega ahí: vacío legítimo
    }
    opgg._page = lambda path, params: pages[params["patch"]]
    assert opgg.counters(DARIUS, "TOP", "emerald_plus", "global", "16.23") == {GAREN: (120, 66)}
    assert opgg.counters(DARIUS, "TOP", "emerald_plus", "global", "16.10") is None  # no se hace pasar por el 16.10
    assert opgg.counters(DARIUS, "TOP", "emerald_plus", "global", "16.22") is None
    assert opgg.counters(DARIUS, "TOP", "emerald_plus", "global", "16.21") == {}
    cached = {key.split("|")[-1] for key, _ in opgg.cached_entries("counters")}
    assert cached == {"16.23", "16.21"}


def test_a_rate_limited_web_stops_all_requests_for_a_while(db, monkeypatch):
    clock = [1000.0]
    monkeypatch.setattr(base_mod.time, "monotonic", lambda: clock[0])
    source = CachedSource(db)
    calls = []
    blocked = [True]

    class Blocked(Exception):
        pass

    def fetch(key):
        def run():
            calls.append(key)
            if blocked[0]:
                source.trip(429, "90")  # la web dice «vuelve en 90 s»
                raise Blocked()
            return {"ok": key}
        return run

    assert source._cached("x", "a", 60, fetch("a")) is None
    # Mientras dura, ninguna otra clave pide nada (antes cada clave insistía por su cuenta).
    assert source._cached("x", "b", 60, fetch("b")) is None and source._cached("x", "c", 60, fetch("c")) is None
    assert calls == ["a"] and source.circuit_open() and source.stats["circuit_open_for"] == 90
    clock[0] += 91
    blocked[0] = False
    assert not source.circuit_open()
    assert source._cached("x", "b", 60, fetch("b")) == {"ok": "b"}
    # Sin Retry-After la espera crece con cada bloqueo y vuelve a empezar tras una respuesta buena.
    source.trip(403)
    first = source._circuit_until - clock[0]
    source.trip(403)
    assert source._circuit_until - clock[0] > first
    source.trip(404)  # un 404 no es un bloqueo
    assert base_mod.retry_after_seconds("abc") is None and base_mod.retry_after_seconds("12") == 12.0


def test_client_watch_runs_in_its_own_thread(svc, monkeypatch):
    """«En partida» se refresca aunque el seguimiento esté atascado en una sincronización larga."""
    from app.coach import tracker as tracker_mod

    phases = iter(["InProgress"] * 1000)
    monkeypatch.setattr(tracker_mod.lcu, "available", lambda: True)
    monkeypatch.setattr(tracker_mod.lcu, "gameflow_phase", lambda: next(phases))
    blocked = threading.Event()
    monkeypatch.setattr(svc.tracker, "_run", lambda: blocked.wait(5))  # el bucle principal, ocupado
    svc.tracker.start()
    try:
        deadline = time.time() + 3
        while not svc.tracker.state.get("in_game") and time.time() < deadline:
            time.sleep(0.02)
        assert svc.tracker.state["in_game"] is True
        assert any(t.name == "lol-client-watch" for t in threading.enumerate())
    finally:
        blocked.set()
        svc.tracker.shutdown()


def test_opgg_tier_list_is_not_stored_as_another_patch(svc):
    from app.external.opgg import Opgg
    from tests.factories import DARIUS, offline_ddragon

    opgg = Opgg(svc.db, offline_ddragon(svc.settings.ddragon_dir), enabled=True)
    champ = '{"key":"Darius","name":"Darius","positionName":"top","positionWinRate":51.2,"positionPickRate":5,"positionBanRate":3,"positionRoleRate":90,"positionTier":2,"positionRank":7,"positionCounters":[]}'

    def page(path, params):
        applied = "16.23" if params["patch"] in ("16.23", "16.10") else params["patch"]
        return '{"type":"ranked","region":"global","tier":"emerald_plus","position":"top","version":"' + applied + '"}' + champ

    opgg._page = page
    assert opgg.tier_list("TOP", "emerald_plus", "global", "16.23")[DARIUS]["wr"] == 51.2
    assert opgg.tier_list("TOP", "emerald_plus", "global", "16.10") is None  # OP.GG dio el 16.23
