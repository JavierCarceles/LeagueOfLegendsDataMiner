"""La validación de recorridos que se ejecuta sola al arrancar y deja su resultado con fecha en data/registro."""

from __future__ import annotations

import threading
from types import SimpleNamespace

from app.coach import track_check_job as job

SALIDA = "1) Posición real de un evento quitada y reconstruida con el resto:\n   Línea recta   10 casos"


def _svc(tmp_path, in_game=False):
    (tmp_path / "mis_partidas" / "JUNGLE" / "Lee Sin").mkdir(parents=True)
    for i in range(3):
        (tmp_path / "mis_partidas" / "JUNGLE" / "Lee Sin" / f"EUW1_{i}.json.gz").write_bytes(b"")
    return SimpleNamespace(settings=SimpleNamespace(data_dir=tmp_path), tracker=SimpleNamespace(state={"in_game": in_game}),
                           shutdown_event=threading.Event())


def test_se_escribe_con_fecha_y_lo_anterior_debajo(tmp_path):
    svc = _svc(tmp_path)
    path = tmp_path / "registro" / job.FILE
    path.parent.mkdir()
    path.write_text("salida pegada a mano de la terminal\n", encoding="utf-8")  # el fichero de antes se conserva
    assert job.run(svc, delay=0, execute=lambda: (SALIDA, True))
    assert job.run(svc, delay=0, execute=lambda: ("Traceback: algo", False))
    text = path.read_text(encoding="utf-8")
    assert text.startswith(job.HEADER)
    runs = job.previous_runs(text)
    assert len(runs) == 3
    assert "3 partidas" in runs[0] and "FALLÓ" in runs[0] and "Traceback" in runs[0]
    assert SALIDA in runs[1] and "FALLÓ" not in runs[1].splitlines()[0]
    assert "copiado a mano" in runs[2] and "pegada a mano" in runs[2]


def test_solo_las_ultimas_ejecuciones(tmp_path):
    path = tmp_path / job.FILE
    for i in range(5):
        job.write(path, f"{job.RUN_MARK}ejecución {i} ===\nsalida {i}\n", keep=3)
    runs = job.previous_runs(path.read_text(encoding="utf-8"))
    assert [r.splitlines()[0] for r in runs] == [f"{job.RUN_MARK}ejecución {i} ===" for i in (4, 3, 2)]


def test_no_se_programa_sin_partidas_y_espera_si_juegas(tmp_path):
    empty = SimpleNamespace(settings=SimpleNamespace(data_dir=tmp_path / "vacio"), tracker=None, shutdown_event=threading.Event())
    assert job.start(empty) is None
    svc = _svc(tmp_path, in_game=True)
    svc.shutdown_event.set()  # jugando y la app se cierra: no se ejecuta nada
    assert job.run(svc, delay=0, execute=lambda: (SALIDA, True)) is False
    assert not (tmp_path / "registro" / job.FILE).exists()
