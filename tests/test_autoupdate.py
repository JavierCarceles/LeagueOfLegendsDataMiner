"""Actualización automática sin perder progreso: código nuevo de GitHub, reinicio, vuelta atrás si no arranca, vídeos a
medias, partidas que no reescriben ficheros ya subidos y el cupo de la API entre reinicios."""

from __future__ import annotations

import gzip
import json
import shutil
import subprocess
import sys
import threading
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

from app import autopush, restart, updater

needs_git = pytest.mark.skipif(shutil.which("git") is None, reason="git no instalado")


def git(cwd: Path, *args: str) -> str:
    return subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True, check=True).stdout.strip()


def _clone(remote: Path, dst: Path) -> Path:
    subprocess.run(["git", "clone", "-q", str(remote), str(dst)], check=True, capture_output=True)
    git(dst, "config", "user.email", "test@example.com")
    git(dst, "config", "user.name", "Test")
    return dst


@pytest.fixture
def repos(tmp_path):
    """Un «GitHub» (repositorio desnudo), el ordenador con la app (``win``) y el que sube código (``mac``)."""
    remote = tmp_path / "remote.git"
    subprocess.run(["git", "init", "-q", "--bare", "-b", "main", str(remote)], check=True)
    seed = tmp_path / "seed"
    (seed / "app").mkdir(parents=True)
    git(seed, "init", "-q", "-b", "main")
    git(seed, "config", "user.email", "test@example.com")
    git(seed, "config", "user.name", "Test")
    (seed / "app" / "codigo.py").write_text("x = 1\n")
    (seed / ".gitignore").write_text("data/.autopush.json\ndata/.update.json\n*.tmp\n")
    (seed / "data").mkdir()
    (seed / "data" / "a.json").write_text("{}")
    git(seed, "add", ".")
    git(seed, "commit", "-qm", "inicio")
    git(seed, "remote", "add", "origin", str(remote))
    git(seed, "push", "-q", "-u", "origin", "main")
    return SimpleNamespace(remote=remote, win=_clone(remote, tmp_path / "win"), mac=_clone(remote, tmp_path / "mac"))


def _svc(repo: Path, **extra):
    state = {"tracker": SimpleNamespace(state={"in_game": False}), "draft_live": SimpleNamespace(state={"active": False})}
    return SimpleNamespace(settings=SimpleNamespace(data_dir=repo / "data"), shutdown_event=threading.Event(), **{**state, **extra})


@pytest.fixture
def restarted(monkeypatch):
    calls: list[str] = []
    monkeypatch.setattr(restart, "_stopper", lambda: calls.append("stop"))
    monkeypatch.setattr(restart, "_reason", None)
    return calls


def _mac_pushes(repos, text: str = "x = 2\n") -> str:
    (repos.mac / "app" / "codigo.py").write_text(text)
    git(repos.mac, "commit", "-qam", "código nuevo")
    git(repos.mac, "push", "-q")
    return git(repos.mac, "rev-parse", "HEAD")


def _updater(repos, monkeypatch, import_error: str | None = None) -> updater.Updater:
    monkeypatch.setattr(autopush, "every", lambda: 2000)
    monkeypatch.setattr(updater, "SETTLE_S", 0)
    svc = _svc(repos.win)
    auto = autopush.AutoPush.of(svc)
    auto.repo = repos.win
    auto.state["git_ok"] = True
    up = updater.Updater(svc, repo=repos.win)
    up.loaded = up._head()  # el código que «cargó» la app (lo hace start())
    monkeypatch.setattr(up, "_import_error", lambda: import_error)
    return up


@needs_git
def test_code_from_github_is_pulled_with_the_data_and_the_app_restarts(repos, monkeypatch, restarted):
    """El Mac sube código; Windows lo ve, sube sus datos (el pull trae el código sin tocar los suyos) y se reinicia."""
    up = _updater(repos, monkeypatch)
    before = git(repos.win, "rev-parse", "HEAD")
    assert up.tick() == "same"
    new = _mac_pushes(repos)
    (repos.win / "data" / "nueva.json").write_text('{"partida": 1}')  # lo que recogía Windows mientras tanto
    assert up.tick() == "seen"  # primero espera a que dejen de llegar commits…
    assert up.tick() == "restarting"  # …y luego lo aplica
    assert (repos.win / "app" / "codigo.py").read_text() == "x = 2\n"
    assert (repos.win / "data" / "nueva.json").read_text() == '{"partida": 1}'
    assert restarted == ["stop"] and restart.requested()
    info = json.loads((repos.win / "data" / ".update.json").read_text())
    assert info["status"] == "installed" and info["from"] == before and git(repos.win, "merge-base", "--is-ancestor", new, info["to"]) == ""
    assert "nueva.json" in git(repos.remote, "ls-tree", "-r", "--name-only", "main", "data")  # los datos, subidos


@needs_git
def test_code_pulled_by_a_normal_data_push_also_restarts(repos, monkeypatch, restarted):
    """El pull de una subida de datos cualquiera (al parar el recolector, por ejemplo) trae el código: la app no puede
    seguir con el de antes. A la siguiente vuelta del actualizador se reinicia igual."""
    up = _updater(repos, monkeypatch)
    loaded = up.loaded
    _mac_pushes(repos)
    (repos.win / "data" / "nueva.json").write_text("{}")
    autopush.push_data(repos.win, repos.win / "data")  # la subida de datos de siempre: su pull trae el código
    assert git(repos.win, "rev-parse", "HEAD") != loaded
    assert up.tick() == "restarting" and restarted == ["stop"]
    info = json.loads((repos.win / "data" / ".update.json").read_text())
    assert info["from"] == loaded and info["to"] == git(repos.win, "rev-parse", "HEAD")
    assert up.tick() == "restarting" and restarted == ["stop"]  # otra vuelta mientras se cierra: no lo pide dos veces


@needs_git
def test_docs_pulled_by_a_data_push_do_not_restart(repos, monkeypatch, restarted):
    up = _updater(repos, monkeypatch)
    (repos.mac / "docs").mkdir()
    (repos.mac / "docs" / "videos.md").write_text("conclusiones\n")
    git(repos.mac, "add", ".")
    git(repos.mac, "commit", "-qm", "docs")
    git(repos.mac, "push", "-q")
    (repos.win / "data" / "nueva.json").write_text("{}")
    autopush.push_data(repos.win, repos.win / "data")
    assert up.tick() == "same" and restarted == [] and up.loaded == git(repos.win, "rev-parse", "HEAD")


@needs_git
def test_data_with_non_english_names_does_not_restart(repos, monkeypatch, restarted):
    """git escribe entre comillas las rutas con letras no inglesas («"data/otps/…/vn2_M\341\273\227i…"»): no son código."""
    up = _updater(repos, monkeypatch)
    folder = repos.win / "data" / "otps" / "Akali" / "MID" / "vn2_Mỗi_ngày_1_đấu_x#89206"
    folder.mkdir(parents=True)
    (folder / "perfil.json").write_text("{}")
    (repos.win / "data" / "otps" / "ru_Аге#AGE.json").write_text("{}")
    autopush.push_data(repos.win, repos.win / "data")
    assert up.tick() == "same" and restarted == []
    assert updater._names("data/a\0docs/b.md\0") == ["data/a", "docs/b.md"]


@needs_git
def test_never_restarts_during_a_game_or_champ_select(repos, monkeypatch, restarted):
    up = _updater(repos, monkeypatch)
    _mac_pushes(repos)
    up.svc.tracker.state["in_game"] = True
    assert up.tick() == "seen" and up.tick() == "waiting" and "partida" in up.state["detail"]
    up.svc.tracker.state["in_game"] = False
    up.svc.draft_live.state["active"] = True
    assert up.tick() == "waiting" and "selección" in up.state["detail"]
    assert restarted == []


@needs_git
def test_docs_or_data_from_github_do_not_restart(repos, monkeypatch, restarted):
    up = _updater(repos, monkeypatch)
    (repos.mac / "docs").mkdir()
    (repos.mac / "docs" / "notas.md").write_text("hola\n")
    git(repos.mac, "add", ".")
    git(repos.mac, "commit", "-qm", "docs")
    git(repos.mac, "push", "-q")
    assert up.tick() == "seen" and up.tick() == "no_code" and restarted == []
    assert updater.needs_restart(["app/x.py"]) and updater.needs_restart(["run.py"]) and not updater.needs_restart(["data/a", "docs/b.md"])


@needs_git
def test_new_code_that_does_not_load_is_reverted_and_reported(repos, monkeypatch, restarted):
    up = _updater(repos, monkeypatch, import_error="SyntaxError: invalid syntax")
    _mac_pushes(repos, "x = (\n")
    assert up.tick() == "seen" and up.tick() == "failed"
    assert (repos.win / "app" / "codigo.py").read_text() == "x = 1\n"  # el código vuelve al de antes
    assert git(repos.win, "log", "-1", "--format=%s").startswith("Vuelta atrás automática")
    assert "SyntaxError" in (repos.win / "data" / "registro" / "actualizacion.log").read_text()
    assert restarted == []
    up.failed = None
    assert up.tick() == "same"  # lo de GitHub ya está en este ordenador (con su vuelta atrás): no lo vuelve a intentar


@needs_git
def test_a_fix_after_a_failed_version_is_tried_at_once(repos, monkeypatch, restarted):
    """Tras una versión que no carga, la siguiente que suba (el arreglo, hecho sobre la vuelta atrás) se prueba ya."""
    up = _updater(repos, monkeypatch, import_error="SyntaxError")
    _mac_pushes(repos, "x = (\n")
    assert up.tick() == "seen" and up.tick() == "failed"
    auto = autopush.AutoPush.of(up.svc)
    auto.thread.join(30)  # la vuelta atrás ya está en GitHub
    monkeypatch.setattr(up, "_import_error", lambda: None)
    git(repos.mac, "pull", "-q")
    assert git(repos.mac, "log", "-1", "--format=%s").startswith(("Vuelta atrás", "datos", "Merge"))
    _mac_pushes(repos, "x = 3\n")
    assert up.tick() == "seen" and up.tick() == "restarting"
    assert (repos.win / "app" / "codigo.py").read_text() == "x = 3\n" and restarted == ["stop"]


@needs_git
def test_a_fix_made_before_the_revert_reached_github_wins(repos, monkeypatch, restarted):
    """Carrera: el arreglo se sube sin la vuelta atrás (aún no estaba en GitHub). Al bajarlo choca en el código y manda
    GitHub (lo único propio de este ordenador era la vuelta atrás); los datos de este ordenador no se tocan."""
    up = _updater(repos, monkeypatch, import_error="SyntaxError")
    auto = autopush.AutoPush.of(up.svc)
    original_start = auto.start
    monkeypatch.setattr(auto, "start", lambda *a, **k: False)  # la vuelta atrás se queda en local de momento
    _mac_pushes(repos, "x = (\n")
    assert up.tick() == "seen" and up.tick() == "failed"
    monkeypatch.setattr(auto, "start", original_start)
    monkeypatch.setattr(up, "_import_error", lambda: None)  # el arreglo sí carga
    (repos.win / "data" / "mientras.json").write_text('{"de": "windows"}')
    _mac_pushes(repos, "x = 3\n")  # el Mac no tiene la vuelta atrás: choca con ella en codigo.py
    assert up.tick() == "seen" and up.tick() == "restarting"
    assert (repos.win / "app" / "codigo.py").read_text() == "x = 3\n"
    assert (repos.win / "data" / "mientras.json").read_text() == '{"de": "windows"}'
    assert git(repos.win, "status", "--porcelain", "--untracked-files=no", "--", ".", ":(exclude)data") == ""  # código limpio
    assert "mientras.json" in git(repos.remote, "ls-tree", "-r", "--name-only", "main", "data")


@needs_git
def test_supervisor_rolls_back_a_version_that_does_not_start(repos):
    """``run.py``: la app nueva se cae al arrancar; el código vuelve a la versión de antes con un commit."""
    import run

    good = git(repos.win, "rev-parse", "HEAD")
    (repos.win / "app" / "codigo.py").write_text("roto\n")
    git(repos.win, "commit", "-qam", "versión rota")
    bad = git(repos.win, "rev-parse", "HEAD")
    data = repos.win / "data"
    (data / ".update.json").write_text(json.dumps({"status": "installed", "from": good, "to": bad, "at": time.time()}))
    (data / "registro").mkdir()
    (data / "registro" / "arranque.log").write_text("Traceback\nNameError: name 'roto' is not defined\n")
    assert run.rollback(1, root=repos.win) is True
    assert (repos.win / "app" / "codigo.py").read_text() == "x = 1\n"
    assert "NameError" in git(repos.win, "log", "-1", "--format=%B")
    assert json.loads((data / ".update.json").read_text())["status"] == "rolled_back"
    assert run.rollback(1, root=repos.win) is False  # ya no hay actualización pendiente de arrancar


def test_restart_needs_a_supervisor(monkeypatch):
    monkeypatch.setattr(restart, "_stopper", None)
    assert not restart.available() and restart.request("prueba") is False


def test_new_games_after_a_commit_go_to_a_new_file(tmp_path):
    """Un fichero ya subido no se reescribe: lo que llega después de cada commit «datos» va a otro."""
    from app.ingest import otp_details as od

    folder = tmp_path / "Ahri" / "MID" / "euw1_x"
    folder.mkdir(parents=True)
    assert od.games_target(folder, 0) == folder / od.GAMES_FILE  # sin subida automática: el de siempre
    base = folder / od.GAMES_FILE
    with gzip.open(base, "at", encoding="utf-8") as fh:
        fh.write('{"match_id": "A"}\n')
    committed = time.time() + 5  # el commit fue después de escribirlo
    shard = od.games_target(folder, committed)
    assert shard.name == f"partidas-{int(committed)}.jsonl.gz"
    with gzip.open(shard, "at", encoding="utf-8") as fh:
        fh.write('{"match_id": "B"}\n')
    assert od.games_target(folder, time.time() - 60) == folder / od.GAMES_FILE  # escrito después del último commit
    assert [f.name for f in od.games_files(folder)] == [od.GAMES_FILE, shard.name]
    od._drop_shards(folder)  # al reescribir desde la base de datos lleva todas
    assert [f.name for f in od.games_files(folder)] == [od.GAMES_FILE]


def test_api_quota_survives_a_restart():
    """Riot sigue contando las llamadas del arranque anterior: el limitador nuevo empieza con ellas."""
    from app.riot.ratelimit import RateLimiter

    old = RateLimiter()
    for _ in range(15):
        assert old.acquire("euw1", "match", timeout=1)
    state = json.loads(json.dumps(old.export_state()))
    new = RateLimiter()
    assert new.import_state(state) == 15
    usage = {(u["limit"], u["seconds"]): u["used"] for u in new.snapshot()["euw1"]["windows"]}
    assert usage[(100, 120)] == 15
    state["saved_at"] -= 200
    state["app"]["euw1"]["stamps"] = [t - 200 for t in state["app"]["euw1"]["stamps"]]
    assert RateLimiter().import_state(state) == 0  # de hace más de 2 min: ya no cuentan


def test_video_checkpoint_keeps_what_was_done(tmp_path):
    """Un vídeo a medias: metadatos, transcripción y trozos de frames se guardan y siguen tras reiniciar."""
    from app.videos import collector as C
    from app.videos import frames as vf

    folder = tmp_path / "vid"
    ckpt = C.Checkpoint(folder, "abcdefghijk")
    assert not ckpt.resumed
    ckpt.put("info", {"title": "Guía", "duration": 600})
    ckpt.put("transcripcion", {"texto": {"metodo": "subtitulos"}, "avisos": []})
    done, on_done = ckpt.frames("video.mp4|1,2,3|None")
    assert done == {}
    box = vf.Box(1, 2, 3, "derecha", 0.9)
    on_done("0", vf.Collected(box, (1080, 1920, 3), present=[(0.0, 0.9)], samples=1))
    again = C.Checkpoint(folder, "abcdefghijk")
    assert again.resumed and again.get("info")["title"] == "Guía" and "transcripción" in again.summary()
    done, _ = again.frames("video.mp4|1,2,3|None")
    part = vf.Collected.from_json(box, done["0"])
    assert part.present == [(0.0, 0.9)] and part.shape == (1080, 1920, 3) and part.samples == 1
    assert again.frames("otro.mp4|1,2,3|None")[0] == {}  # otra descarga u otro minimapa: los trozos no valen
    (folder / C.CHECKPOINT).write_text(json.dumps({"id": "abcdefghijk", "version": -1}))
    assert not C.Checkpoint(folder, "abcdefghijk").resumed  # otra versión del recolector: de cero


def test_frame_pass_resumes_from_saved_pieces(tmp_path):
    """Los trozos ya hechos no se vuelven a leer del vídeo (ni falta el fichero para ellos)."""
    pytest.importorskip("cv2")
    from app.videos import frames as vf

    box = vf.Box(1, 2, 3, "derecha", 0.9)
    done = {str(k): vf.Collected(box, (360, 640, 3), present=[(k * 60.0, 0.9)], samples=1).to_json() for k in range(3)}
    tools = SimpleNamespace(maps=None)
    out = vf.sample_pass(str(tmp_path / "no-existe.mp4"), box, None, tools, 170.0, lambda: False, done=done)
    assert out.samples == 3 and [t for t, _ in out.present] == [0.0, 60.0, 120.0] and out.complete


@pytest.mark.skipif(sys.platform == "win32", reason="señales POSIX")
def test_supervisor_restarts_the_app_on_code_75(tmp_path, monkeypatch):
    """``run.supervise`` vuelve a lanzar la app cuando sale con 75 (sin el navegador) y acaba cuando sale con 0."""
    import run

    launches: list[list[str]] = []
    codes = iter([run.RESTART_CODE, 0])

    class FakeProc:
        def __init__(self, cmd, **_):
            launches.append(cmd)

        def wait(self, timeout=None):
            return next(codes)

    monkeypatch.setattr(run.subprocess, "Popen", FakeProc)
    monkeypatch.setattr(run, "_fresh_update", lambda root=run.ROOT: None)
    monkeypatch.setattr(run, "QUICK_S", 0)
    assert run.supervise(Path(sys.executable), ["--port", "9"], None) == 0
    assert len(launches) == 2 and "--no-browser" not in launches[0] and "--no-browser" in launches[1]


def test_log_stream_ends_by_itself_when_the_app_stops(monkeypatch):
    """Al reiniciar (o cerrar) la app, el directo de los registros de la web acaba solo: antes uvicorn esperaba 3 s, lo
    cortaba y escribía «timeout graceful shutdown exceeded» con una traza de error en cada actualización."""
    import asyncio

    from app.api import system

    stop = {"now": False}
    monkeypatch.setattr(restart, "_exiting", lambda: stop["now"])
    monkeypatch.setattr(system, "STREAM_POLL_S", 0.05)

    class Request:
        async def is_disconnected(self):
            return False

    async def run():
        response = await system.stream_logs(Request())
        events = response.body_iterator
        await events.__anext__()  # «retry: 3000»
        stop["now"] = True
        rest = [chunk async for chunk in events]
        return rest

    assert asyncio.run(asyncio.wait_for(run(), timeout=2)) == []
