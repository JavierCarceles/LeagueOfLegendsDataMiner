#!/usr/bin/env python3
"""Tempu Drafter: ejecuta este fichero y ya está (doble clic, el botón «Run» de tu editor o ``py run.py``).

Hace todo solo, sin instalar nada a mano:

1. Comprueba que tienes Python 3.10 o superior (recomendado 3.14).
2. La primera vez crea su entorno de Python (en Windows en ``%LOCALAPPDATA%\\TempuDrafter``, fuera de la carpeta
   del proyecto y de OneDrive; en macOS/Linux en ``.venv``) e instala las dependencias, también las de la sección
   «Vídeos» (``requirements-videos.txt``; si esas fallan, la app arranca igual). Después solo las reinstala si cambian.
   Si el entorno quedó a medias (ventana cerrada a mitad, antivirus…), lo repara.
3. Se relanza dentro de ese entorno y arranca la web; el navegador se abre solo cuando está lista.
4. Si la app ya está abierta (o arrancando), solo abre el navegador: nunca hay dos copias con la misma base de datos.
5. Se queda vigilando la app: cuando llega código nuevo de GitHub, la app se cierra ordenadamente (guardando lo que
   llevaba a medias) y este proceso la vuelve a lanzar con la versión nueva, reinstalando dependencias si cambian. Si
   la versión nueva no arranca, vuelve sola a la anterior (un commit «Vuelta atrás automática» que se sube con los
   datos) y apunta el error en ``data/registro/actualizacion.log``.

Opciones: ``--port 8000``, ``--host 127.0.0.1``, ``--no-browser``, ``--reload`` (desarrollo) y ``--no-venv``
(usar el Python actual tal cual, con las dependencias ya instaladas).
"""

from __future__ import annotations

import argparse
import contextlib
import hashlib
import http.client
import json
import os
import shutil
import socket
import subprocess
import sys
import threading
import time
import traceback
import urllib.request
import venv
import webbrowser
from pathlib import Path

ROOT = Path(__file__).resolve().parent
# En una constante (y no ``os.name == "nt"`` en cada sitio): así el editor analiza las dos ramas en cualquier sistema.
WINDOWS = os.name == "nt"
REQUIREMENTS = ROOT / "requirements.txt"
# Herramientas de la sección «Vídeos» (yt-dlp, OpenCV, Deno, voz a texto): también al arrancar, pero si fallan la app
# arranca igual (la sección lo reintenta sola) y el motivo queda en el registro de vídeos que se sube a GitHub.
VIDEO_REQUIREMENTS = ROOT / "requirements-videos.txt"
VIDEO_LOG = ROOT / "data" / "registro" / "videos_vivo.log"
INSIDE = "TEMPU_DRAFTER_ENV"  # marca del proceso relanzado dentro del entorno
REQ_STAMP = ".tempu-requirements"  # huella de requirements.txt instalada
VIDEO_STAMP = ".tempu-requirements-videos"  # y la de requirements-videos.txt
# 0 normal; -2/-15 y 130/143 señales en POSIX; 0xC000013A Ctrl+C o cerrar la ventana en Windows.
CLEAN_EXIT_CODES = {0, -2, -15, 130, 143, 3221225786, -1073741510}
RESTART_CODE = 75  # la app pide que la vuelvan a lanzar (código nuevo de GitHub): app/restart.py
UPDATE_FILE = ROOT / "data" / ".update.json"  # qué versión instaló el actualizador y si ya arrancó (app/updater.py)
CRASH_LOG = ROOT / "data" / "registro" / "arranque.log"  # por qué no arrancó la app (lo lee la vuelta atrás)
UPDATE_LOG = ROOT / "data" / "registro" / "actualizacion.log"  # se sube con los datos: actualizaciones y vueltas atrás
HEALTH_S = 300  # tras instalar una versión nueva, tiempo para que responda antes de darla por rota
ROLLBACK_WINDOW_S = 1800
MAX_QUICK_RESTARTS = 5  # reinicios seguidos en los que la app no dura ni QUICK_S: algo va mal, se para
QUICK_S = 60
# Las llamadas a la propia app (127.0.0.1) nunca deben pasar por el proxy del sistema.
LOCAL = urllib.request.build_opener(urllib.request.ProxyHandler({}))


def say(text: str = "") -> None:
    print(f"  {text}" if text else "", flush=True)


def _utf8_console() -> None:
    """Tildes, «» y → aunque la consola de Windows use otra página de códigos."""
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass


def _own_console() -> bool:
    """¿Esta ventana de consola se abrió solo para nosotros (doble clic)? Entonces hay que pausar antes de cerrar."""
    if not WINDOWS:
        return False
    try:
        import ctypes

        return ctypes.windll.kernel32.GetConsoleProcessList((ctypes.c_uint * 4)(), 4) <= 2
    except (AttributeError, OSError):  # sin consola de Windows (IDE, salida redirigida)
        return False


def _fail(message: str) -> int:
    say()
    for line in message.splitlines():
        say(line)
    say()
    if _own_console():
        try:
            input("  Pulsa Intro para cerrar…")
        except EOFError:
            pass
    return 1


# ====================================================================== entorno
def venv_dir() -> Path:
    custom = os.environ.get("TEMPU_VENV")
    if custom:
        return Path(custom)
    if WINDOWS:
        base = Path(os.environ.get("LOCALAPPDATA") or Path.home() / "AppData" / "Local")
        return base / "TempuDrafter" / f"venv-py{sys.version_info[0]}{sys.version_info[1]}"
    return ROOT / ".venv"


def venv_python(path: Path) -> Path:
    return path / ("Scripts/python.exe" if WINDOWS else "bin/python")


def _run_quiet(cmd: list[str], timeout: float = 120) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, errors="replace", timeout=timeout, check=False)


def _healthy(path: Path) -> bool:
    """Python y pip funcionan (un entorno creado a medias tiene python.exe pero no pip)."""
    python = venv_python(path)
    if not python.exists():
        return False
    try:
        return _run_quiet([str(python), "-m", "pip", "--version"], timeout=90).returncode == 0
    except (OSError, subprocess.SubprocessError):
        return False


class _SetupLock:
    """Evita que dos dobles clics preparen el mismo entorno a la vez."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self.handle = None

    def __enter__(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.handle = open(self.path, "a+b")
        if not _try_lock(self.handle, wait=True):
            raise OSError("no se pudo bloquear el entorno")
        return self

    def __exit__(self, *_) -> None:
        _unlock(self.handle)
        self.handle.close()


def ensure_venv(path: Path) -> Path:
    if _healthy(path):
        return venv_python(path)
    say("Preparando el entorno de Python (solo la primera vez, 1-2 minutos)…")
    try:
        venv.EnvBuilder(with_pip=True, clear=path.exists(), symlinks=not WINDOWS).create(path)
    except (OSError, subprocess.CalledProcessError) as exc:
        output = getattr(exc, "output", None)
        raise RuntimeError(f"No se pudo crear el entorno en {path}: {exc}" + (f"\n{output}" if output else "")) from exc
    python = venv_python(path)
    check = _run_quiet([str(python), "-m", "pip", "--version"])
    if check.returncode != 0:
        raise RuntimeError(f"El entorno se creó sin pip:\n{check.stdout[-1500:]}")
    (path / REQ_STAMP).unlink(missing_ok=True)
    return python


def _requirements_digest(requirements: Path = REQUIREMENTS) -> str:
    data = requirements.read_bytes() + f"|{sys.version_info[0]}.{sys.version_info[1]}".encode()
    return hashlib.sha256(data).hexdigest()


def _installed(stamp: Path, digest: str) -> bool:
    try:
        return stamp.read_text(encoding="utf-8").strip() == digest
    except OSError:
        return False


def _pip_install(python: Path, requirements: Path) -> subprocess.CompletedProcess:
    cmd = [str(python), "-m", "pip", "install", "--disable-pip-version-check", "--no-input", "-q", "-r", str(requirements)]
    return subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, errors="replace", check=False)


def ensure_requirements(path: Path, python: Path) -> None:
    stamp = path / REQ_STAMP
    digest = _requirements_digest()
    if _installed(stamp, digest):
        return
    say("Instalando dependencias (solo cuando cambian)…")
    result = _pip_install(python, REQUIREMENTS)
    if result.returncode != 0:
        raise RuntimeError(
            "No se pudieron instalar las dependencias:\n"
            + result.stdout[-2500:]
            + "\nRevisa tu conexión a internet (o el proxy/antivirus) y vuelve a ejecutar run.py."
        )
    stamp.write_text(digest, encoding="utf-8")


def ensure_video_requirements(path: Path, python: Path) -> None:
    """``requirements-videos.txt`` al arrancar (solo cuando cambia). Si falla, la app arranca igual: se avisa aquí y en
    ``data/registro/videos_vivo.log`` (su final se sube a GitHub como ``videos.log``)."""
    if not VIDEO_REQUIREMENTS.exists():
        return
    stamp = path / VIDEO_STAMP
    digest = _requirements_digest(VIDEO_REQUIREMENTS)
    if _installed(stamp, digest):
        return
    say("Instalando las herramientas de vídeo: yt-dlp, OpenCV, Deno y voz a texto (solo cuando cambian, unos minutos)…")
    result = _pip_install(python, VIDEO_REQUIREMENTS)
    if result.returncode != 0:
        say("No se pudieron instalar las herramientas de vídeo: la app arranca igual y se reintenta al volver a abrirla.")
        _video_log("WARNING", "Instalación de requirements-videos.txt fallida al arrancar:\n" + result.stdout[-2500:])
        return
    stamp.write_text(digest, encoding="utf-8")
    _video_log("INFO", "Herramientas de vídeo instaladas al arrancar (requirements-videos.txt).")


def _video_log(level: str, text: str) -> None:
    """Una línea en el registro de vídeos (antes de que arranque la app, que luego sigue escribiendo en él)."""
    try:
        VIDEO_LOG.parent.mkdir(parents=True, exist_ok=True)
        with open(VIDEO_LOG, "a", encoding="utf-8") as fh:
            fh.write(f"{time.strftime('%Y-%m-%d %H:%M:%S')} {level:<7s} run.py: {text}\n")
    except OSError:
        pass


def _venv_in_use(path: Path) -> bool:
    """¿Alguien está usando ya este entorno? (nuestro propio proceso o la terminal que lo tiene activado)."""
    try:
        if Path(sys.executable).resolve().is_relative_to(path.resolve()):
            return True
    except (OSError, ValueError):
        return True
    active = os.environ.get("VIRTUAL_ENV")
    if active:
        try:
            if Path(active).resolve() == path.resolve():
                return True
        except (OSError, ValueError):
            return True
    return False


def _remove_old_project_venv(current: Path) -> None:
    """El lanzador antiguo creaba ``.venv`` dentro del proyecto en Windows; ahora vive fuera y se borra (es regenerable).

    Nunca se borra un entorno que se esté usando: en Windows ``rmtree`` lo dejaba a medias (borraba ``pyvenv.cfg``
    pero no ``python.exe``, que estaba abierto) y luego ``python`` fallaba con «failed to locate pyvenv.cfg».
    """
    old = ROOT / ".venv"
    try:
        if not WINDOWS or old.resolve() == current.resolve():
            return
    except OSError:
        return
    legacy = (old / ".req-hash").exists()  # entorno del lanzador anterior
    broken = old.exists() and not (old / "pyvenv.cfg").exists()  # restos de un borrado a medias: no sirve para nada
    if not (legacy or broken):
        return
    if _venv_in_use(old):
        say("El .venv antiguo del proyecto lo tiene activado esta terminal: escribe «deactivate» para soltarlo.")
        return
    shutil.rmtree(old, ignore_errors=True)
    if not old.exists():
        say("Borrada la carpeta .venv del proyecto (el entorno ahora está en %LOCALAPPDATA%\\TempuDrafter).")


def _in_dir(path: Path) -> bool:
    try:
        return Path(sys.prefix).resolve() == path.resolve()
    except OSError:
        return False


def bootstrap(argv: list[str]) -> int:
    """Proceso lanzado con el Python del sistema: prepara el entorno y lanza run.py dentro de él (y lo vigila)."""
    path = venv_dir()
    try:
        # El candado va fuera de la raíz del proyecto: junto al entorno en Windows, en .cache/ en macOS/Linux.
        lock_dir = path.parent if WINDOWS else ROOT / ".cache"
        with _SetupLock(lock_dir / f"{path.name.lstrip('.')}.lock"):
            python = ensure_venv(path)
            ensure_requirements(path, python)
            ensure_video_requirements(path, python)
    except (RuntimeError, OSError) as exc:
        return _fail(f"{exc}")
    _remove_old_project_venv(path)
    return supervise(python, argv, path)


# ====================================================================== supervisor
def _digest(path: Path) -> str:
    try:
        return hashlib.sha256(path.read_bytes()).hexdigest()
    except OSError:
        return ""


def _read_json(path: Path) -> dict:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def _write_json(path: Path, data: dict) -> None:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_name(path.name + ".tmp")
        tmp.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
        tmp.replace(path)
    except OSError:
        pass


def _log_update(text: str, root: Path = ROOT) -> None:
    """Una línea en ``data/registro/actualizacion.log`` (se sube a GitHub con los datos)."""
    path = root / UPDATE_LOG.relative_to(ROOT)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "a", encoding="utf-8") as fh:
            fh.write(f"{time.strftime('%Y-%m-%d %H:%M:%S')} {text}\n")
    except OSError:
        pass


def _no_browser(args: list[str]) -> list[str]:
    """Al relanzar, el navegador ya está abierto (la web se recarga sola al volver la app)."""
    return args if "--no-browser" in args else [*args, "--no-browser"]


def _poll(proc: subprocess.Popen) -> int | None:
    """El código de salida de la app si ya acabó (``None`` si sigue en marcha)."""
    try:
        # Con espera corta: en Windows un wait() sin límite no deja que llegue el Ctrl+C.
        return proc.wait(timeout=0.5)
    except subprocess.TimeoutExpired:
        return None
    except KeyboardInterrupt:
        # La app también recibe el Ctrl+C (misma consola) y se cierra ordenadamente: se le da un momento.
        try:
            return proc.wait(timeout=10)
        except (subprocess.TimeoutExpired, KeyboardInterrupt):
            proc.terminate()
            return 0


def _health(proc: subprocess.Popen, elapsed: float) -> str:
    """Versión recién instalada: «ok» si ya responde (queda dada por buena), «wait» si aún no y «broken» si lleva
    ``HEALTH_S`` sin responder (se cierra: cuenta como que no arranca)."""
    port = _running_port(ROOT / "data" / "run.json")
    if port and _ping(port):
        _mark_update("ok")
        return "ok"
    if elapsed <= HEALTH_S:
        return "wait"
    say("La versión nueva no responde: la cierro y vuelvo a la anterior.")
    proc.terminate()
    try:
        proc.wait(timeout=20)
    except subprocess.TimeoutExpired:
        proc.kill()
    return "broken"


def _wait_child(proc: subprocess.Popen, health: bool) -> int:
    """Espera a que acabe la app; con ``health`` (versión recién instalada), vigila que llegue a responder."""
    started = time.monotonic()
    next_ping = started + 5
    while True:
        code = _poll(proc)
        if code is not None:
            return code
        now = time.monotonic()
        if health and now >= next_ping:
            next_ping = now + 5
            state = _health(proc, now - started)
            if state == "broken":
                return -9
            health = state == "wait"


def _mark_update(status: str, root: Path = ROOT, **extra) -> None:
    path = root / UPDATE_FILE.relative_to(ROOT)
    info = _read_json(path)
    if info:
        info.update(status=status, **extra)
        _write_json(path, info)


def _fresh_update(root: Path = ROOT) -> dict | None:
    """La actualización recién instalada que aún no ha arrancado bien (None si no hay ninguna)."""
    info = _read_json(root / UPDATE_FILE.relative_to(ROOT))
    if info.get("status") != "installed" or not info.get("from"):
        return None
    if time.time() - float(info.get("at") or 0) > ROLLBACK_WINDOW_S:
        return None
    return info


def _git_run(root: Path, *args: str) -> subprocess.CompletedProcess:
    flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    env = {**os.environ, "GIT_TERMINAL_PROMPT": "0"}
    return subprocess.run(["git", *args], cwd=root, capture_output=True, text=True, encoding="utf-8", errors="replace",
                          timeout=120, env=env, creationflags=flags, check=False)


def _crash_text(root: Path = ROOT) -> str:
    try:
        lines = (root / CRASH_LOG.relative_to(ROOT)).read_text(encoding="utf-8", errors="replace").strip().splitlines()
    except OSError:
        return ""
    return " | ".join(line.strip() for line in lines[-3:])[:400]


def rollback(code: int, root: Path = ROOT) -> bool:
    """La versión recién instalada no arranca: el código vuelve al de antes (commit «Vuelta atrás automática», que se sube
    con los datos) y se apunta por qué. ``False`` si no había actualización reciente o no se pudo."""
    info = _fresh_update(root)
    if info is None:
        return False
    good, bad = info["from"], str(info.get("to") or "?")
    why = _crash_text(root) or f"código de salida {code}"
    restored = _git_run(root, "restore", f"--source={good}", "--staged", "--worktree", "--", ".", ":(exclude)data")
    if restored.returncode != 0:
        _log_update(f"No se pudo volver a {good[:9]}: {restored.stderr.strip()[:300]}", root)
        return False
    message = f"Vuelta atrás automática: la versión {bad[:9]} no arrancaba en este ordenador\n\n{why}"
    committed = _git_run(root, "commit", "-q", "-m", message)
    if committed.returncode != 0 and "nothing" not in committed.stdout + committed.stderr:
        _log_update(f"Vuelta atrás a {good[:9]} sin commit: {committed.stderr.strip()[:300]}", root)
    _mark_update("rolled_back", root, error=why, rolled_back_at=time.time())
    _log_update(f"La versión {bad[:9]} no arrancaba ({why}); vuelvo a {good[:9]}.", root)
    say(f"La versión nueva no arrancaba ({why[:160]}). He vuelto a la anterior; el error se sube con los datos.")
    return True


def _requirements_again(venv: Path | None, python: Path) -> None:
    """Tras una actualización: dependencias nuevas, si las hay (si fallan, la app arranca igual y se ve el aviso)."""
    if venv is None:
        return
    try:
        ensure_requirements(venv, python)
    except RuntimeError as exc:
        say(f"No se pudieron instalar las dependencias nuevas: {str(exc)[:300]}")
    ensure_video_requirements(venv, python)


def supervise(python: Path, argv: list[str], venv: Path | None) -> int:
    """Lanza la app y la vuelve a lanzar cuando pide reiniciarse (código nuevo de GitHub, ver ``app/updater.py``). Si
    una versión recién instalada no arranca, vuelve a la anterior y la relanza."""
    env = {**os.environ, INSIDE: str(venv or sys.prefix), "PYTHONUTF8": "1", "PYTHONIOENCODING": "utf-8"}
    me, args = _digest(Path(__file__)), list(argv)
    quick = 0  # reinicios seguidos en los que la app no llegó a durar ni un minuto
    while quick < MAX_QUICK_RESTARTS:
        started = time.monotonic()
        proc = subprocess.Popen([str(python), str(Path(__file__).resolve()), *args], env=env, cwd=str(ROOT))
        code = _wait_child(proc, health=_fresh_update() is not None)
        quick = quick + 1 if time.monotonic() - started < QUICK_S else 0
        if code == RESTART_CODE:
            say("Reiniciando Tempu Drafter con la versión nueva…")
            _requirements_again(venv, python)
            if _digest(Path(__file__)) != me:  # run.py también cambió: lo relanza la versión nueva
                subprocess.Popen([sys.executable, str(Path(__file__).resolve()), *_no_browser(argv)], cwd=str(ROOT))
                return 0
            args = _no_browser(args)
            continue
        if code in CLEAN_EXIT_CODES:
            return 0
        if not rollback(code):
            return _fail(f"Tempu Drafter se ha cerrado con un error (código {code}). Revisa el mensaje de arriba.")
        args = _no_browser(args)
    return _fail("Tempu Drafter se ha reiniciado demasiadas veces seguidas sin llegar a arrancar. Ciérralo y vuelve a abrirlo.")


# ====================================================================== una sola copia
def _try_lock(handle, wait: bool = False) -> bool:
    try:
        if WINDOWS:
            import msvcrt

            handle.seek(0)
            while True:
                try:
                    msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
                    return True
                except OSError:
                    if not wait:
                        return False
                    time.sleep(0.5)
        import fcntl

        fcntl.flock(handle.fileno(), fcntl.LOCK_EX | (0 if wait else fcntl.LOCK_NB))
        return True
    except OSError:
        return False


def _unlock(handle) -> None:
    try:
        if WINDOWS:
            import msvcrt

            handle.seek(0)
            msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
        else:
            import fcntl

            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
    except OSError:
        pass


def _ping(port: int, timeout: float = 2.0) -> bool:
    """¿Responde Tempu Drafter en ese puerto? (sin proxy: es este mismo ordenador)."""
    try:
        with LOCAL.open(f"http://127.0.0.1:{port}/api/ping", timeout=timeout) as response:
            return json.loads(response.read().decode("utf-8")).get("app") == "tempu-drafter"
    except (OSError, ValueError, AttributeError, http.client.HTTPException):  # nadie, otra app o respuesta rara
        return False


def _url(host: str, port: int) -> str:
    """Dirección de la web. El servidor es local (este ordenador, o la red de casa con ``HOST``) y solo habla HTTP: no
    hay certificado que poner para ``localhost``."""
    shown = "localhost" if host in ("127.0.0.1", "0.0.0.0", "::", "") else host
    # Servidor local sin TLS (ver la docstring): HTTP a propósito.
    return f"http://{shown}:{port}"  # NOSONAR


def _port_free(host: str, port: int) -> bool:
    bind_host = "127.0.0.1" if host in ("", "localhost") else host
    with socket.socket(socket.AF_INET6 if ":" in bind_host else socket.AF_INET, socket.SOCK_STREAM) as sock:
        if not WINDOWS:
            # Igual que uvicorn: un puerto con conexiones en TIME_WAIT (reinicio rápido) sí se puede usar.
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            sock.bind((bind_host, port))
        except OSError:
            return False
    return True


def _disable_quick_edit() -> None:
    """Windows 10: con QuickEdit, un clic en la consola congela la app hasta pulsar Esc. Se desactiva."""
    if not WINDOWS:
        return
    with contextlib.suppress(Exception):  # consola sin modo (redirigida, IDE): no hace falta
        import ctypes

        kernel32 = ctypes.windll.kernel32
        handle = kernel32.GetStdHandle(-10)  # STD_INPUT_HANDLE
        mode = ctypes.c_uint32()
        if kernel32.GetConsoleMode(handle, ctypes.byref(mode)):
            kernel32.SetConsoleMode(handle, (mode.value & ~0x0040) | 0x0080)  # sin QUICK_EDIT, con EXTENDED_FLAGS


def _lower_priority(enabled: bool) -> None:
    if not WINDOWS or not enabled:
        return
    with contextlib.suppress(Exception):  # sin psutil o sin permiso: no es imprescindible
        import psutil

        psutil.Process().nice(psutil.BELOW_NORMAL_PRIORITY_CLASS)


def _open_when_ready(url: str, port: int, open_browser: bool, low_priority: bool) -> None:
    def run() -> None:
        for _ in range(240):
            if _ping(port):
                if open_browser:
                    webbrowser.open(url)
                    time.sleep(2)  # que el navegador arranque con prioridad normal (no hereda la baja)
                break
            time.sleep(0.5)
        # Prioridad baja después de abrir el navegador: las descargas de fondo nunca le quitan CPU al juego.
        _lower_priority(low_priority)

    threading.Thread(target=run, name="open-browser", daemon=True).start()


def _running_port(info_path: Path) -> int | None:
    try:
        return int(json.loads(info_path.read_text(encoding="utf-8"))["port"])
    except (OSError, ValueError, KeyError, TypeError):
        return None


def _wait_other_copy(args, lock, info_path: Path) -> int | None:
    """Ya hay una copia abierta o arrancando con estos datos: se espera a que responda y se abre el navegador (0). Si la
    anterior se está cerrando (tiene el candado pero ya no responde), al soltarlo arranca esta (``None``)."""
    for _ in range(60):
        port = _running_port(info_path)
        if port and _ping(port):
            url = _url(args.host, port)
            say(f"Tempu Drafter ya está abierto → {url}")
            if not args.no_browser:
                webbrowser.open(url)
            return 0
        if _try_lock(lock):
            return None
        time.sleep(0.5)
    return _fail("Otra copia de Tempu Drafter está arrancando con estos datos y no responde. Ciérrala y vuelve a probar.")


def _free_port(host: str, port: int) -> int | None:
    """El puerto pedido o el siguiente libre (de los 19 siguientes); ``None`` si no hay."""
    if _port_free(host, port):
        return port
    for candidate in range(port + 1, port + 20):
        if _port_free(host, candidate):
            say(f"El puerto {port} está ocupado por otro programa: uso el {candidate}.")
            return candidate
    return None


def serve(args) -> int:
    """Dentro del entorno: una sola copia por carpeta de datos, puerto libre y servidor."""
    import uvicorn

    from app.config import settings

    data_dir = Path(settings.data_dir)
    data_dir.mkdir(parents=True, exist_ok=True)
    info_path = data_dir / "run.json"
    # vive lo que vive el proceso
    lock = open(data_dir / ".run.lock", "a+b")  # noqa: SIM115
    if not _try_lock(lock):
        waited = _wait_other_copy(args, lock, info_path)
        if waited is not None:
            return waited
    port = _free_port(args.host, args.port)
    if port is None:
        return _fail(f"No hay ningún puerto libre entre {args.port} y {args.port + 19}. Cierra el programa que los usa o pasa --port.")
    info_path.write_text(json.dumps({"port": port, "pid": os.getpid()}), encoding="utf-8")

    url = _url(args.host, port)
    say()
    say(f"Tempu Drafter → {url}")
    say("(Ctrl+C o cerrar esta ventana para salir)")
    say()
    _disable_quick_edit()
    _open_when_ready(url, port, not args.no_browser, settings.low_priority)
    try:
        if args.reload:
            uvicorn.run("app.main:create_app", factory=True, host=args.host, port=port, reload=True, log_level="warning")
            return 0
        _load_app()
        from app import restart

        # timeout_graceful_shutdown: la web abierta (stream de logs) no impide cerrar con Ctrl+C
        server = uvicorn.Server(uvicorn.Config("app.main:create_app", factory=True, host=args.host, port=port, log_level="warning",
                                               timeout_graceful_shutdown=3))
        restart.register(lambda: setattr(server, "should_exit", True), lambda: bool(server.should_exit))
        server.run()
        return RESTART_CODE if restart.requested() else 0
    finally:
        _unlock(lock)
        lock.close()


def _load_app() -> None:
    """Carga el código de la app antes de arrancar el servidor: si no carga (una versión nueva rota), el error queda en
    ``data/registro/arranque.log`` para la vuelta atrás (uvicorn solo lo escribiría en la consola)."""
    try:
        import app.main  # noqa: F401
    except Exception:
        _crash(traceback.format_exc())
        raise


def _crash(text: str) -> None:
    try:
        CRASH_LOG.parent.mkdir(parents=True, exist_ok=True)
        CRASH_LOG.write_text(f"{time.strftime('%Y-%m-%d %H:%M:%S')}\n{text[-8000:]}", encoding="utf-8")
    except OSError:
        pass


def _outer(args) -> int | None:
    """El proceso que se abre a mano: prepara el entorno y lanza la app vigilada (``supervise``). ``None``: este proceso
    es la app (lanzada por el supervisor, o en desarrollo con ``--reload``)."""
    if os.environ.get(INSIDE):
        return None
    if args.no_venv:
        return supervise(Path(sys.executable), sys.argv[1:], None)
    path = venv_dir()
    if not _in_dir(path):
        return bootstrap(sys.argv[1:])
    # Lanzado directamente con el Python del entorno (p. ej. desde el editor): solo falta mirar las dependencias.
    try:
        ensure_requirements(path, Path(sys.executable))
    except RuntimeError as exc:
        return _fail(str(exc))
    ensure_video_requirements(path, Path(sys.executable))
    if args.reload:
        return None  # desarrollo: la app en este mismo proceso, con el recargador de uvicorn
    return supervise(Path(sys.executable), sys.argv[1:], path)


def main() -> int:
    _utf8_console()
    # run.py se abre con cualquier Python: avisa si es antiguo
    if sys.version_info < (3, 10):  # noqa: UP036
        return _fail("Tempu Drafter necesita Python 3.10 o superior (recomendado 3.14): https://www.python.org/downloads/")
    os.chdir(ROOT)
    if str(ROOT) not in sys.path:
        sys.path.insert(0, str(ROOT))
    parser = argparse.ArgumentParser(description="Tempu Drafter — prepara el entorno y abre la web")
    parser.add_argument("--host", default=None, help="Por defecto solo local (127.0.0.1)")
    parser.add_argument("--port", type=int, default=None)
    parser.add_argument("--reload", action="store_true", help="Recarga automática al editar código")
    parser.add_argument("--no-browser", action="store_true", help="No abrir el navegador al arrancar")
    parser.add_argument("--no-venv", action="store_true", help="Usar el Python actual con sus dependencias")
    args, _ = parser.parse_known_args()
    outer = _outer(args)
    if outer is not None:
        return outer
    try:
        from app.config import settings
    except ImportError as exc:
        return _fail(f"Falta una dependencia ({exc.name}). Ejecuta run.py sin --no-venv para que la instale sola.")
    args.host = args.host or settings.host
    args.port = args.port or settings.port
    try:
        return serve(args)
    except KeyboardInterrupt:
        return 0
    except Exception:
        _crash(traceback.format_exc())
        raise


if __name__ == "__main__":
    sys.exit(main())
