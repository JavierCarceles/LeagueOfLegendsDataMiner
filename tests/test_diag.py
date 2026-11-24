"""Por qué va lento: el muestreo de hilos (``app.diag``) y las consultas lentas de la base de datos."""

import threading
import time

from app import diag
from app.data import db as dbmod
from app.data.db import Database


def test_grupos_de_hilos():
    assert diag.group_of("crawler-euw1-otps") == "busquedas"
    assert diag.group_of("AnyIO worker thread") == "web"
    assert diag.group_of("warm-opgg-mcp-1") == "warm-opgg-mcp"
    assert diag.group_of("crawler-europe") == "crawler-europe"


def test_el_muestreo_distingue_esperar_el_candado_de_estar_parado(tmp_path):
    db = Database(tmp_path / "t.db")
    release = threading.Event()
    held = threading.Event()

    def holder():
        with db.write():
            held.set()
            release.wait(3)

    def waiter():
        held.wait(3)
        with db.write():
            pass

    threads = [threading.Thread(target=holder, name="crawler-europe"), threading.Thread(target=waiter, name="crawler-asia"),
               threading.Thread(target=lambda: release.wait(3), name="warm-opgg-mcp-0")]
    for t in threads:
        t.start()
    held.wait(3)
    time.sleep(0.2)
    s = diag.Sampler()
    s.sample()
    release.set()
    for t in threads:
        t.join()
    snap = s.snapshot()
    assert snap["crawler-asia"]["en"] == {diag.CATEGORY_LABEL["candado"]: 100}
    assert snap["warm-opgg-mcp"]["en"] == {diag.CATEGORY_LABEL["sin_trabajo"]: 100}
    assert "crawler-asia" in s.summary()
    db.close()


def test_las_consultas_lentas_dicen_desde_donde(tmp_path, monkeypatch):
    monkeypatch.setattr(dbmod, "SLOW_QUERY_S", 0.0)
    db = Database(tmp_path / "t.db")
    db.query("SELECT 1")
    db.scalar("SELECT 2")
    stats = db.slow_stats()
    assert stats and all("test_diag.py" in x["que"] for x in stats)
    assert sum(x["veces"] for x in stats) == 2
    db.close()
