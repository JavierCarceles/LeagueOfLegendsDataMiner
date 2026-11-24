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
    assert git(repos.remote, "log", "--format=%s", "main").splitlines()[:2] == ["datos", "código"]
    assert git(repos.remote, "show", "--name-only", "--format=", "main").splitlines() == ["data/otps/p.jsonl"]
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

    with pytest.raises(autopush.GitError, match="pull --rebase"):
        autopush.push_data(a, a / "data")

    assert git(a, "log", "-1", "--format=%s") == "datos"  # se queda en local para el siguiente intento
    assert git(repos.remote, "log", "-1", "--format=%s", "main") == "datos b"  # el remoto, intacto
    assert git(a, "status", "--porcelain") == ""  # sin rebase a medias


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
