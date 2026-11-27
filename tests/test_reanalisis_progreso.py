"""El contador del re-análisis de las partidas guardadas sigue por donde iba al reiniciar la app y desaparece al acabar."""

from __future__ import annotations

from app.ingest import otp_details as od


class _Db:
    def __init__(self, left: int) -> None:
        self.meta: dict[str, str] = {}
        self.left = left

    def meta_get(self, key, default=None):
        return self.meta.get(key, default)

    def meta_set(self, key, value):
        self.meta[key] = value

    def scalar(self, sql, params=()):
        return self.left


def test_al_reiniciar_sigue_la_cuenta_y_al_acabar_desaparece():
    db = _Db(100_000)
    assert od.progress_begin(db, 100_000)
    assert od.reanalysis_status() == {"running": True, "done": 0, "total": 100_000, "pct": 0}
    od._progress["done"] += 10_000  # rehizo 10.000 y se cerró la app a medias
    od.progress_end(db, stopped=True)
    assert od.reanalysis_status() == {"running": False}

    db.left = 90_000  # al volver a abrirla quedan 90.000 sin rehacer
    assert od.progress_begin(db, 90_000)
    assert od.reanalysis_status()["done"] == 10_000 and od.reanalysis_status()["total"] == 100_000

    od._progress["done"] = 99_990
    db.left = 10  # 10 sin el bruto guardado: no se pueden rehacer
    od.progress_end(db, stopped=False)
    assert od.reanalysis_status() == {"running": False}
    assert not od.progress_begin(db, 10)  # al arrancar otra vez no sale la etiqueta por esas 10
    assert od.reanalysis_status() == {"running": False}


def test_con_una_version_nueva_la_cuenta_empieza_de_nuevo(monkeypatch):
    db = _Db(5)
    od.progress_begin(db, 5)
    od.progress_end(db, stopped=False)
    monkeypatch.setattr(od, "ANALYSIS_VERSION", od.ANALYSIS_VERSION + 1)
    assert od.progress_begin(db, 120_000)
    assert od.reanalysis_status() == {"running": True, "done": 0, "total": 120_000, "pct": 0}
    od.progress_end(db, stopped=True)
