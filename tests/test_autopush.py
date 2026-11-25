"""Subida automática de los datos a GitHub (con repos git locales: un «remoto» desnudo y dos clones)."""

from __future__ import annotations

import shutil
import subprocess
import json
import threading
from pathlib import Path
from types import SimpleNamespace

import pytest

from app import autopush

pytestmark = pytest.mark.skipif(shutil.which("git") is None, reason="git no instalado")


def git(cwd: Path, *args: str) -> str:
    return subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True, check=True).stdout.strip()


def clone(remote: Path, dst: Path) -> Path:
    subprocess.run(["git", "clone", "-q", str(remote), str(dst)], check=True, capture_output=True)
    git(dst, "config", "user.email", "test@example.com")
    git(dst, "config", "user.name", "Test")
    return dst


@pytest.fixture
def repos(tmp_path):
    remote = tmp_path / "remote.git"
    subprocess.run(["git", "init", "-q", "--bare", "-b", "main", str(remote)], check=True)
    seed = tmp_path / "seed"
    seed.mkdir()
    git(seed, "init", "-q", "-b", "main")
    git(seed, "config", "user.email", "test@example.com")
    git(seed, "config", "user.name", "Test")
    (seed / "codigo.py").write_text("x = 1\n")
    (seed / ".gitignore").write_text("data/.autopush.json\n*.tmp\n")  # como el del proyecto
    (seed / "data").mkdir()
    (seed / "data" / "a.json").write_text("{}")
    git(seed, "add", ".")
    git(seed, "commit", "-qm", "inicio")
    git(seed, "remote", "add", "origin", str(remote))
    git(seed, "push", "-q", "-u", "origin", "main")
    a = clone(remote, tmp_path / "a")
    b = clone(remote, tmp_path / "b")
    return SimpleNamespace(remote=remote, a=a, b=b)


def test_push_solo_datos_y_con_el_remoto_por_delante(repos):
    a, b = repos.a, repos.b
    # El otro ordenador sube código mientras tanto.
    (b / "codigo.py").write_text("x = 2\n")
    git(b, "commit", "-qam", "código")
    git(b, "push", "-q")
    (a / "data" / "otps").mkdir()
    (a / "data" / "otps" / "p.jsonl").write_text("1\n")

    result = autopush.push_data(a, a / "data", [threading.RLock()])

    assert result == {"committed": True, "pushed": True, "branch": "main"}
    # Se junta con un merge (no rebase: en Windows el rebase no podía reescribir ficheros de data/ abiertos).
    log = git(repos.remote, "log", "--format=%s", "main").splitlines()
    assert log[0].startswith("Merge") and {"datos", "código"} <= set(log[1:3])
    assert git(repos.remote, "diff", "--name-only", "main~1", "main").splitlines() in (["codigo.py"], ["data/otps/p.jsonl"])
    assert (a / "codigo.py").read_text() == "x = 2\n"  # el código del otro ordenador ya está aquí


def test_codigo_a_medias_nunca_entra_ni_se_mezcla(repos):
    a, b = repos.a, repos.b
    (a / "data" / "p.jsonl").write_text("1\n")
    (a / "codigo.py").write_text("x = 3  # a medias\n")
    # Remoto al día: se sube solo «datos», el código a medias se queda como estaba.
    assert autopush.push_data(a, a / "data")["pushed"]
    assert git(repos.remote, "show", "--name-only", "--format=", "main").splitlines() == ["data/p.jsonl"]
    # Ahora el otro ordenador cambia ese mismo fichero: no se hace pull (no se tocan tus ficheros) y se avisa.
    git(b, "pull", "-q")
    (b / "codigo.py").write_text("x = 2\n")
    git(b, "commit", "-qam", "código")
    git(b, "push", "-q")
    (a / "data" / "q.jsonl").write_text("2\n")
    with pytest.raises(autopush.GitError, match="cambios de código sin commit"):
        autopush.push_data(a, a / "data")
    assert (a / "codigo.py").read_text() == "x = 3  # a medias\n"
    assert git(a, "log", "-1", "--format=%s") == "datos"
    assert git(repos.remote, "log", "-1", "--format=%s", "main") == "código"


def test_sin_cambios_no_hace_nada(repos):
    assert autopush.push_data(repos.a, repos.a / "data") == {"committed": False, "pushed": False, "branch": "main"}


def test_conflicto_no_fuerza_y_deja_el_commit_en_local(repos):
    a, b = repos.a, repos.b
    (b / "data" / "a.json").write_text('{"b": 1}')
    git(b, "commit", "-qam", "datos b")
    git(b, "push", "-q")
    (a / "data" / "a.json").write_text('{"a": 1}')

    with pytest.raises(autopush.GitError, match="git pull falló"):
        autopush.push_data(a, a / "data")

    assert git(a, "log", "-1", "--format=%s") == "datos"  # se queda en local para el siguiente intento
    assert git(repos.remote, "log", "-1", "--format=%s", "main") == "datos b"  # el remoto, intacto
    assert git(a, "status", "--porcelain") == ""  # sin merge a medias


def test_cuenta_partidas_y_sube_al_llegar(repos, monkeypatch):
    a = repos.a
    monkeypatch.setattr(autopush, "every", lambda: 3)
    svc = SimpleNamespace(settings=SimpleNamespace(data_dir=a / "data"), tracker=None, shutdown_event=threading.Event())
    auto = autopush.AutoPush(svc, repo=a)
    (a / "data" / "nueva.json").write_text("{}")
    auto.note_game()
    auto.note_game()
    assert auto.thread is None and auto.state["pending"] == 2
    auto.note_game()
    auto.thread.join(30)

    assert auto.state["pending"] == 0 and auto.state["last_push_games"] == 3
    assert git(repos.remote, "log", "-1", "--format=%s", "main") == "datos"
    # La cuenta sobrevive a reiniciar la app (y el fichero no se sube: está en .gitignore del proyecto).
    assert autopush.AutoPush(svc, repo=a).state["pending"] == 0


def test_desactivado_con_cero(repos, monkeypatch):
    monkeypatch.setattr(autopush, "every", lambda: 0)
    svc = SimpleNamespace(settings=SimpleNamespace(data_dir=repos.a / "data"), tracker=None)
    auto = autopush.AutoPush(svc, repo=repos.a)
    auto.note_game()
    assert auto.state["pending"] == 0 and auto.thread is None


def test_al_arrancar_se_comprueba_git_y_se_ve_el_problema(repos, tmp_path):
    svc = SimpleNamespace(settings=SimpleNamespace(data_dir=repos.a / "data"), tracker=None)
    ok = autopush.AutoPush(svc, repo=repos.a).check()
    assert ok["git_ok"] is True and "rama main" in ok["git"]
    plain = tmp_path / "zip"
    (plain / "data").mkdir(parents=True)
    bad = autopush.AutoPush(SimpleNamespace(settings=SimpleNamespace(data_dir=plain / "data"), tracker=None), repo=plain).check()
    assert bad["git_ok"] is False and ".zip" in bad["git"]


def test_sin_nada_nuevo_en_data_lo_dice(repos, monkeypatch):
    monkeypatch.setattr(autopush, "every", lambda: 2)
    svc = SimpleNamespace(settings=SimpleNamespace(data_dir=repos.a / "data"), tracker=None, shutdown_event=threading.Event())
    auto = autopush.AutoPush(svc, repo=repos.a)
    auto.note_game()
    auto.note_game()
    auto.thread.join(30)
    assert auto.state["pending"] == 0 and auto.state["last_result"] == "nothing" and not auto.state.get("last_push_at")


def test_subir_ahora_sin_esperar_al_limite(repos, monkeypatch):
    from app.api import collector

    a = repos.a
    monkeypatch.setattr(autopush, "every", lambda: 500)
    svc = SimpleNamespace(settings=SimpleNamespace(data_dir=a / "data"), tracker=None, shutdown_event=threading.Event())
    auto = autopush.AutoPush(svc, repo=a)
    monkeypatch.setattr(autopush.AutoPush, "of", classmethod(lambda cls, s: auto))
    (a / "data" / "pocas.json").write_text("{}")
    auto.note_game()
    out = collector.otps_push_now(svc)
    assert out["started"]
    auto.thread.join(30)
    assert auto.state["pending"] == 0 and git(repos.remote, "log", "-1", "--format=%s", "main") == "datos"


def test_antes_de_subir_se_pone_al_dia_el_estado_de_la_busqueda(repos, monkeypatch):
    a = repos.a
    monkeypatch.setattr(autopush, "every", lambda: 500)
    calls = []
    crawler = SimpleNamespace(running=True, _funnel_thread=None, write_funnel=lambda force=False: calls.append(force))
    svc = SimpleNamespace(settings=SimpleNamespace(data_dir=a / "data"), tracker=None, shutdown_event=threading.Event(), crawler=crawler)
    auto = autopush.AutoPush(svc, repo=a)
    (a / "data" / "x.json").write_text("{}")
    auto.note_game()
    auto.start()
    auto.thread.join(30)
    assert calls == [True] and git(repos.remote, "log", "-1", "--format=%s", "main") == "datos"


def test_cada_subida_lleva_el_registro_y_lo_que_hace_cada_hilo(repos, monkeypatch):
    import logging

    from app.logbus import BusHandler, bus

    a = repos.a
    monkeypatch.setattr(autopush, "every", lambda: 500)
    svc = SimpleNamespace(settings=SimpleNamespace(data_dir=a / "data"), tracker=None, shutdown_event=threading.Event())
    auto = autopush.AutoPush(svc, repo=a)
    try:
        raise ValueError("fallo de prueba")
    except ValueError:
        import sys

        record = logging.LogRecord("tempu.crawler", logging.ERROR, __file__, 1, "Error inesperado en el recolector europe", None, sys.exc_info())
        BusHandler(bus).emit(record)
    (a / "data" / "y.json").write_text("{}")
    auto.note_game()
    auto.start()
    auto.thread.join(30)
    out = json.loads((a / "data" / "otps" / "_registro.json").read_text(encoding="utf-8"))
    entry = next(e for e in out["registro"] if "Error inesperado" in e["message"])
    assert "fallo de prueba" in entry["trace"] and entry["hora"]
    assert any("autopush" in name for name in out["hilos"])
    assert git(repos.remote, "show", "--stat", "main").count("_registro.json") == 1


def test_el_registro_completo_va_a_un_fichero_dentro_de_data(tmp_path):
    import logging

    from app import logbus

    root = logging.getLogger("tempu")
    before = list(root.handlers)
    try:
        logbus.add_file_log(tmp_path)
        logbus.add_file_log(tmp_path)  # una sola vez
        added = [h for h in root.handlers if h not in before]
        assert len(added) == 1
        old_level = root.level
        root.setLevel(logging.INFO)
        logbus.get_logger("crawler").info("Estado europe: 10 partidas nuevas")
        added[0].flush()
        text = (tmp_path / "registro" / "app_vivo.log").read_text(encoding="utf-8")
        assert "Estado europe: 10 partidas nuevas" in text
        root.setLevel(old_level)
    finally:
        for h in root.handlers[:]:
            if h not in before:
                root.removeHandler(h)
                h.close()


def test_la_base_de_datos_cuenta_quien_espera_el_candado_y_vuelca_el_wal_aparte(tmp_path):
    import threading as th

    from app.data.db import Database

    db = Database(tmp_path / "t.db")
    stop = th.Event()
    db.start_checkpointer(stop)
    with db.write() as conn:
        conn.execute("INSERT INTO meta(key, value) VALUES('x', '1')")
    st = db.lock_stats.get(th.current_thread().name.rsplit("_", 1)[0])
    assert st and st[2] == 1 and st[0] >= 0 and st[1] >= 0
    assert db.wal_mb() is not None
    stop.set()
    db.close()



def test_el_recorte_del_wal_espera_a_las_escrituras_y_lo_deja_a_cero(tmp_path):
    """Con escrituras sin parar (el recolector) el recorte coge el candado de la app en vez de competir con ellas."""
    import threading as th

    from app.data.db import Database

    db = Database(tmp_path / "t.db")
    stop = th.Event()

    def writer():
        i = 0
        while not stop.is_set():
            with db.write() as conn:
                conn.execute("INSERT OR REPLACE INTO meta(key, value) VALUES(?, ?)", (f"k{i % 50}", "x" * 2000))
            i += 1

    t = th.Thread(target=writer, daemon=True)
    t.start()
    try:
        assert db.checkpoint("TRUNCATE")
        assert db.wal_stats["ultimo"]["ok"] and db.wal_stats["recortes"] == 1
    finally:
        stop.set()
        t.join()
    db.close()


def test_antes_de_subir_se_copia_el_final_del_registro_vivo(repos, monkeypatch):
    """La app escribe en app_vivo.log (abierto todo el rato, fuera de GitHub); se sube una copia de su final en app.log."""
    from app import logbus

    a = repos.a
    monkeypatch.setattr(logbus, "LOG_SNAPSHOT_BYTES", 40)
    svc = SimpleNamespace(settings=SimpleNamespace(data_dir=a / "data"), tracker=None, shutdown_event=threading.Event())
    auto = autopush.AutoPush(svc, repo=a)
    folder = a / "data" / "registro"
    folder.mkdir(parents=True)
    (folder / logbus.LIVE_LOG).write_text("linea vieja 1\nlinea vieja 2\nlinea nueva 3\nlinea nueva 4\n", encoding="utf-8")
    auto._snapshot_log()
    copy = (folder / "app.log").read_text(encoding="utf-8")
    assert copy.endswith("linea nueva 4\n") and copy.startswith("linea") and "vieja 1" not in copy
    # Y el registro del recolector de vídeos, igual (videos_vivo.log → videos.log).
    (folder / logbus.VIDEOS_LIVE_LOG).write_text("video viejo\nvideo nuevo 1\nvideo nuevo 2\n", encoding="utf-8")
    auto._snapshot_log()
    assert (folder / logbus.VIDEOS_LOG).read_text(encoding="utf-8").endswith("video nuevo 2\n")


def test_la_subida_dice_en_que_fase_va(repos):
    """Antes solo decía «subiendo los datos ahora…»: ahora cada fase y el porcentaje del push."""
    work = repos.a
    (work / "data" / "otps").mkdir()
    (work / "data" / "otps" / "nuevo.json").write_text("{}", encoding="utf-8")
    seen = []
    autopush.push_data(work, work / "data", progress=lambda step, detail=None: seen.append((step, detail)))
    steps = [s for s, _ in seen]
    assert steps[:4] == ["locks", "add", "commit", "pull"] and steps[4] == "push"
    assert dict(seen)["commit"] == "1 ficheros"


def test_la_fase_de_la_subida_se_ve_en_el_estado(repos, tmp_path):
    from app.autopush import AutoPush

    work = repos.a
    svc = SimpleNamespace(settings=SimpleNamespace(data_dir=work / "data"), crawler=None, tracker=None)
    auto = AutoPush(svc, repo=work)
    auto._step("pull")
    st = auto.public()["step"]
    assert st["n"] == 5 and st["of"] == 6 and "git pull" in st["label"]
    at = st["at"]
    auto._step("push", "enviando 40 % (2/5 objetos)")
    auto._step("push", "enviando 80 % (4/5 objetos)")
    st = auto.public()["step"]
    assert st["detail"].startswith("enviando 80") and st["at"] >= at


def test_las_partidas_analizadas_se_cuentan_al_momento_sin_recorrer_la_tabla(tmp_path):
    from app.data.db import Database
    from app.ingest import otp_details

    db = Database(tmp_path / "t.db")
    base = otp_details.live_counts(db)
    assert base["games"] == 0
    otp_details._note_live(db, "GOLD", "otp", True)
    otp_details._note_live(db, "GOLD", "player", False)
    now = otp_details.live_counts(db)
    assert now["games"] == 2 and now["timelines"] == 1 and now["otp_games"] == 1 and now["tiers"]["GOLD"] == 2
    assert now["tiers_otp"] == {"GOLD": 1} and now["tiers_player"] == {"GOLD": 1}  # gráfica del total: OTPs y resto
    assert otp_details.counts(db)["tiers_otp"] == {}  # el recuento de la tabla (aquí vacía) da lo mismo por elo
    db.close()


def test_las_peticiones_lentas_quedan_en_el_registro(monkeypatch, caplog):
    """«Datos y API» se quedaba congelado con el recolector a tope y no se sabía qué petición tardaba."""
    import logging
    import time as _time

    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from app import budget

    monkeypatch.setattr(budget, "SLOW_REQUEST_S", 0.05)
    app = FastAPI()
    app.add_middleware(budget.BudgetMiddleware)

    @app.get("/api/lenta")
    def lenta():
        _time.sleep(0.1)
        return {}

    @app.get("/api/rapida")
    def rapida():
        return {}

    with caplog.at_level(logging.INFO):
        client = TestClient(app)
        client.get("/api/lenta")
        client.get("/api/rapida")
    slow = [r.getMessage() for r in caplog.records if "Petición lenta" in r.getMessage()]
    assert len(slow) == 1 and "/api/lenta" in slow[0]


def test_el_registro_no_se_pierde_si_no_puede_rotar(tmp_path, monkeypatch):
    """En Windows renombrar el registro falla si alguien lo tiene abierto: antes se perdía todo lo que venía después."""
    import logging
    import os

    from app import logbus

    path = tmp_path / "vivo.log"
    handler = logbus._safe_rotating_handler(path, 200, 2)
    handler.setFormatter(logging.Formatter("%(message)s"))
    real_rename = os.rename

    def broken(*a, **k):
        raise PermissionError("en uso")

    monkeypatch.setattr(os, "rename", broken)
    monkeypatch.setattr(os, "replace", broken)
    logger = logging.getLogger("tempu.test-rotacion")
    logger.addHandler(handler)
    logger.setLevel(logging.INFO)
    try:
        for i in range(30):
            logger.info("linea %02d con algo de texto", i)
    finally:
        logger.removeHandler(handler)
        handler.close()
        monkeypatch.setattr(os, "rename", real_rename)
    assert "linea 29" in path.read_text(encoding="utf-8")
    assert (tmp_path / "vivo.log.1").exists()


def test_el_limitador_dice_cuanto_usa_y_por_que_espera():
    from app.riot.ratelimit import Priority, RateLimiter

    clock = [1000.0]
    lim = RateLimiter()
    lim._clock = lambda: clock[0]
    for _ in range(40):
        lim.acquire("europe", "match", Priority.LOW, timeout=0)  # sin esperar: las que no caben no salen
        clock[0] += 1.5
    snap = lim.snapshot()["europe"]
    assert snap["llamadas_min_recolector"] == snap["llamadas_min"] and 30 <= snap["llamadas_min"] <= 41
    assert snap["max_min_recolector"] == 49.5 and snap["reserva_pct"] == 1  # 99 de 100 cada 2 min: ~198/min con 4 regiones


def test_la_grafica_por_elo_cuenta_hasta_la_subida(repos):
    """Partidas por elo para la gráfica de «Datos y API»: vuelve a 0 al subir (lo que llegó mientras, se queda)."""
    a = repos.a
    svc = SimpleNamespace(settings=SimpleNamespace(data_dir=a / "data"), tracker=None, shutdown_event=threading.Event(), crawler=None)
    auto = autopush.AutoPush(svc, repo=a)
    auto.note_game("GOLD", analyzed=True)
    auto.note_game("gold", analyzed=True)
    auto.note_game("DIAMOND", analyzed=False)
    assert auto.public()["tiers"] == {"analizadas": {"GOLD": 2}, "guardadas": {"DIAMOND": 1}}
    (a / "data" / "otps").mkdir(exist_ok=True)
    (a / "data" / "otps" / "x.json").write_text("{}", encoding="utf-8")
    auto._run()
    assert auto.public()["tiers"] == {"analizadas": {}, "guardadas": {}}


def test_al_detener_la_subida_espera_a_los_resumenes(repos, monkeypatch):
    """Al detener se rehacen los resúmenes por campeón en otro hilo: antes se escribían después del commit (40-50
    ficheros sin subir). Ahora la subida espera a que acaben."""
    from app.ingest import otp_details

    order = []
    monkeypatch.setattr(otp_details, "wait_summaries", lambda svc: order.append("resúmenes") or True)
    real_push = autopush.push_data
    monkeypatch.setattr(autopush, "push_data", lambda *a, **k: order.append("subida") or real_push(*a, **k))
    a = repos.a
    svc = SimpleNamespace(settings=SimpleNamespace(data_dir=a / "data"), tracker=None, shutdown_event=threading.Event(), crawler=None)
    auto = autopush.AutoPush(svc, repo=a)
    auto._run(wait_summaries=True)
    assert order == ["resúmenes", "subida"]


def test_esperar_a_los_resumenes(monkeypatch):
    import time as _time

    from app.ingest import otp_details

    t = threading.Thread(target=lambda: _time.sleep(0.3))
    t.start()
    files = SimpleNamespace(thread=t)
    monkeypatch.setattr(otp_details.Files, "of", classmethod(lambda cls, svc, kind="otp": files if kind == "otp" else None))
    t0 = _time.monotonic()
    assert otp_details.wait_summaries(object(), timeout=5)
    assert _time.monotonic() - t0 >= 0.25 and not t.is_alive()
