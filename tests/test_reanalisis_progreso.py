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


def test_las_del_ranking_se_vuelven_a_bajar_y_se_sustituyen_al_subir_la_version(keyed_svc, monkeypatch):
    from types import SimpleNamespace

    from tests.factories import make_match, make_timeline

    db = keyed_svc.db
    match = make_match("EUW1_901", puuids={1: "p-1"})
    timeline = make_timeline(match)
    svc = SimpleNamespace(db=db, dd=keyed_svc.dd, settings=keyed_svc.settings)
    monkeypatch.setattr(od, "ANALYSIS_VERSION", od.ANALYSIS_VERSION - 1)  # guardada con la versión de antes
    players = od.save_ranking_game(db, svc, match, timeline, "GOLD")
    assert players >= 1
    monkeypatch.undo()
    assert od.outdated_ranking(db) == 1
    asked = []

    class _Riot:
        def match(self, match_id, **kw):
            asked.append(match_id)
            return match

        def timeline(self, match_id, **kw):
            return timeline

    svc.riot = _Riot()
    done, finished = od.refetch_ranking(db, svc)
    assert (done, finished, asked) == (1, True, ["EUW1_901"])
    assert od.outdated_ranking(db) == 0
    rows = db.query("SELECT COUNT(*) AS n FROM otp_games WHERE match_id = 'EUW1_901' AND kind = 'ranking'")
    assert rows[0]["n"] == players  # sustituidas, no duplicadas


def test_la_etiqueta_suma_tus_partidas_y_las_recogidas():
    db = _Db(0)
    assert od.progress_begin(db, 40, part="mine")
    od.progress_tick("mine")
    assert od.reanalysis_status() == {"running": True, "done": 1, "total": 40, "pct": 2}
    assert od.progress_begin(db, 60)
    assert od.reanalysis_status()["total"] == 100
    od.progress_end(db, stopped=False)
    od.progress_end(db, stopped=False, part="mine", left=0)
    assert od.reanalysis_status() == {"running": False}
