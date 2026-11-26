"""Grabación de partidas (app/recorder.py) y vistazos de cámara (app/videos/camera.py)."""

from __future__ import annotations

import json
import subprocess
from types import SimpleNamespace

from app import recorder as rec_mod
from app.videos.camera import glances


def _frames(camera, minutes=12):
    return {
        "partida": True,
        "hud": "jugador",
        "reloj_ok": True,
        "reloj": [[t, t] for t in range(0, minutes * 60, 10)],  # vídeo = reloj de la partida
        "camara": camera,
    }


def test_vistazo_a_otra_zona_y_vuelta():
    home = (7000, 4000)  # jungla, fuera de las bases
    cam = [[t, *home] for t in range(0, 600, 2)]
    for start in (100, 200, 300):  # tres vistazos a top que vuelven a los 4 s
        for i, row in enumerate(cam):
            if start <= row[0] < start + 4:
                cam[i] = [row[0], 1500, 12000]
    out = glances(_frames(cam))
    assert out["total"] == 3
    assert out["por_minuto"][0] is not None
    assert out["zonas"] == {"top": 3}
    assert out["antes_del_15"] > 0


def test_sin_volver_no_es_vistazo():
    cam = [[t, 7000, 4000] for t in range(0, 300, 2)] + [[t, 1500, 12000] for t in range(300, 600, 2)]
    assert glances(_frames(cam))["total"] == 0


def test_sin_hud_de_jugador_no_hay_vistazos():
    assert glances({**_frames([[0, 1, 1]]), "hud": "espectador"}) is None


class _Db:
    def __init__(self):
        self.meta = {}

    def meta_get(self, key, default=None):
        return self.meta.get(key, default)

    def meta_set(self, key, value):
        self.meta[key] = value


class _Proc:
    def __init__(self):
        self.stdin = SimpleNamespace(write=lambda b: None, flush=lambda: None)
        self.done = None

    def poll(self):
        return self.done

    def wait(self, timeout=None):
        self.done = 0
        return 0

    def kill(self):
        self.done = -9


def _recorder(tmp_path, monkeypatch):
    svc = SimpleNamespace(db=_Db(), settings=SimpleNamespace(data_dir=tmp_path), videos=None)
    r = rec_mod.GameRecorder(svc)
    r.root.mkdir(parents=True)
    monkeypatch.setattr(rec_mod, "WINDOWS", True)
    monkeypatch.setattr(rec_mod, "ffmpeg_exe", lambda: "ffmpeg")
    monkeypatch.setattr(rec_mod.lcu, "available", lambda: True)
    monkeypatch.setattr(r, "_game_info", lambda: {"game_id": 123})
    return r


def test_graba_al_empezar_y_queda_pendiente_al_acabar(tmp_path, monkeypatch):
    r = _recorder(tmp_path, monkeypatch)
    launched = []

    def launch(exe, args, path):
        launched.append(args)
        path.write_bytes(b"video")
        return _Proc()

    monkeypatch.setattr(r, "_launch", launch)
    phase = {"v": "InProgress"}
    monkeypatch.setattr(rec_mod.lcu, "gameflow_phase", lambda: phase["v"])
    monkeypatch.setattr(r, "_analyze_pending", lambda: None)
    r._tick()
    assert r.current and r.current["game_id"] == 123 and len(launched) == 1
    saved = json.loads(next(r.root.glob("*.json")).read_text(encoding="utf-8"))
    assert saved["estado"] == "grabando"
    r.current["inicio"] -= 20 * 60  # 20 min de partida
    phase["v"] = "EndOfGame"
    r._tick()
    assert r.current is None
    saved = json.loads(next(r.root.glob("*.json")).read_text(encoding="utf-8"))
    assert saved["estado"] == "pendiente" and saved["duracion"] >= 1200


def test_si_ninguna_forma_graba_no_reintenta_en_la_misma_partida(tmp_path, monkeypatch):
    r = _recorder(tmp_path, monkeypatch)
    calls = []
    monkeypatch.setattr(r, "_launch", lambda exe, args, path: calls.append(1))
    monkeypatch.setattr(rec_mod.lcu, "gameflow_phase", lambda: "InProgress")
    r._tick()
    n = len(calls)
    r._tick()
    assert n == len(rec_mod.capture_methods(False)) or n == len(rec_mod.capture_methods(True))
    assert len(calls) == n


def test_desactivada_no_graba(tmp_path, monkeypatch):
    r = _recorder(tmp_path, monkeypatch)
    r.set_enabled(False)
    monkeypatch.setattr(r, "_launch", lambda *a: (_ for _ in ()).throw(AssertionError("no debía grabar")))
    monkeypatch.setattr(rec_mod.lcu, "gameflow_phase", lambda: "InProgress")
    r._tick()
    assert r.current is None


def test_la_forma_que_funciono_se_prueba_primero(tmp_path, monkeypatch):
    r = _recorder(tmp_path, monkeypatch)
    r.svc.db.meta_set(rec_mod.META_METHOD, "gdigrab+x264")
    tried = []

    def launch(exe, args, path):
        tried.append(args)
        return _Proc()

    monkeypatch.setattr(r, "_launch", launch)
    monkeypatch.setattr(rec_mod.lcu, "gameflow_phase", lambda: "InProgress")
    r._tick()
    assert r.current["metodo"] == "gdigrab+x264" and "gdigrab" in tried[0]


def test_ffmpeg_real_rechaza_una_captura_que_no_existe(tmp_path, monkeypatch):
    """Con un ffmpeg de verdad (si lo hay), una entrada imposible se descarta en vez de quedarse colgada."""
    exe = rec_mod.ffmpeg_exe()
    if exe is None:
        return
    r = _recorder(tmp_path, monkeypatch)
    proc = r._launch(exe, ["-f", "lavfi", "-i", "no_existe_este_filtro"], tmp_path / "x.mp4")
    assert proc is None
    assert subprocess is not None
