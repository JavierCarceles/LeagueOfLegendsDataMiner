"""La validación de la reconstrucción de recorridos (``app.coach.track_check``), sola al arrancar la app.

Unos minutos después de abrir la app (nunca mientras juegas) se ejecuta como un proceso aparte a prioridad baja, y su
resultado queda en ``data/registro/track_check.txt`` con la fecha, la versión del código y cuántas partidas usó: lo
más reciente arriba y las anteriores debajo, así se ve si las cifras mejoran con cada cambio del análisis. El fichero
se sube a GitHub con el resto de ``data/``.
"""

from __future__ import annotations

import os
import re
import subprocess
import sys
import threading
import time
from pathlib import Path

from app.config import BASE_DIR
from app.logbus import get_logger

log = get_logger("track_check")

FILE = "track_check.txt"
HEADER = "Validación de la reconstrucción de recorridos (app.coach.track_check): la más reciente arriba. Se ejecuta sola al arrancar la app."
RUN_MARK = "=== "
KEEP_RUNS = 20
DELAY_S = 180  # que arranque antes todo lo demás
IN_GAME_WAIT_S = 60
TIMEOUT_S = 45 * 60
_RUN_SPLIT = re.compile(r"(?m)^(?==== )")


def games(data_dir: Path) -> list[Path]:
    """Las partidas que usa ``track_check`` (``data/mis_partidas``: traen la partida y el timeline en bruto)."""
    return sorted((Path(data_dir) / "mis_partidas").glob("*/*/*.json.gz"))


def start(svc, delay: float = DELAY_S) -> threading.Thread | None:
    """Programa la validación en segundo plano (nada si no hay partidas tuyas guardadas)."""
    if not games(svc.settings.data_dir):
        return None
    thread = threading.Thread(target=run, args=(svc, delay), name="track-check", daemon=True)
    thread.start()
    return thread


def run(svc, delay: float = DELAY_S, execute=None) -> bool:
    """Espera, ejecuta y escribe el resultado. ``False`` si se cerró la app antes de terminar."""
    stop = getattr(svc, "shutdown_event", None)
    if _wait(stop, delay):
        return False
    while _in_game(svc):
        if _wait(stop, IN_GAME_WAIT_S):
            return False
    t0 = time.time()
    started = time.strftime("%Y-%m-%d %H:%M")
    output, ok = (execute or _execute)()
    data_dir = Path(svc.settings.data_dir)
    entry = _entry(started, ok, len(games(data_dir)), time.time() - t0, output)
    write(data_dir / "registro" / FILE, entry)
    log.info("Validación de recorridos %s en %s (data/registro/track_check.txt).", "hecha" if ok else "fallida", _duration(time.time() - t0))
    return True


def _wait(stop, seconds: float) -> bool:
    """Espera ``seconds``; ``True`` si se está cerrando la app."""
    if stop is None:
        time.sleep(seconds)
        return False
    return stop.wait(seconds)


def _in_game(svc) -> bool:
    tracker = getattr(svc, "tracker", None)
    return bool(tracker is not None and getattr(tracker, "state", {}).get("in_game"))


def _execute() -> tuple[str, bool]:
    """``python -m app.coach.track_check`` aparte (si falla o tarda, la app ni se entera) y a prioridad baja."""
    env = {**os.environ, "PYTHONUTF8": "1", "PYTHONIOENCODING": "utf-8"}
    flags = getattr(subprocess, "CREATE_NO_WINDOW", 0) | getattr(subprocess, "BELOW_NORMAL_PRIORITY_CLASS", 0)
    try:
        proc = subprocess.run(
            [sys.executable, "-m", "app.coach.track_check"], cwd=BASE_DIR, env=env, capture_output=True, text=True,
            encoding="utf-8", errors="replace", timeout=TIMEOUT_S, creationflags=flags, check=False,
        )
    except subprocess.TimeoutExpired:
        return f"Tardó más de {TIMEOUT_S // 60} min y se cortó.", False
    except OSError as exc:
        return f"No se pudo ejecutar: {exc}", False
    if proc.returncode != 0:
        return f"{proc.stdout}\n{proc.stderr}".strip()[-4000:], False
    return proc.stdout.strip(), True


def _commit() -> str:
    """Versión del código con la que se midió (el commit de git)."""
    try:
        proc = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"], cwd=BASE_DIR, capture_output=True, text=True, timeout=20,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0), check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return "?"
    return proc.stdout.strip() or "?"


def _duration(seconds: float) -> str:
    seconds = round(seconds)
    return f"{seconds // 60} min {seconds % 60} s" if seconds >= 60 else f"{seconds} s"


def _entry(started: str, ok: bool, n_games: int, seconds: float, output: str) -> str:
    failed = "" if ok else " · FALLÓ"
    return f"{RUN_MARK}{started} · código {_commit()} · {n_games} partidas · {_duration(seconds)}{failed} ===\n{output.strip()}\n"


def previous_runs(text: str) -> list[str]:
    """Las ejecuciones que ya había en el fichero. Lo de antes de que se ejecutara solo (pegado a mano) se conserva."""
    if not text.strip():
        return []
    if not text.startswith(HEADER):
        return [f"{RUN_MARK}antes de ejecutarse solo (copiado a mano de la terminal) ===\n{text.strip()}\n"]
    body = text[len(HEADER):].strip("\n")
    return [chunk.rstrip("\n") + "\n" for chunk in _RUN_SPLIT.split(body) if chunk.strip()]


def write(path: Path, entry: str, keep: int = KEEP_RUNS) -> None:
    """La ejecución nueva arriba y las ``keep - 1`` anteriores debajo (escritura atómica)."""
    try:
        old = path.read_text(encoding="utf-8")
    except OSError:
        old = ""
    runs = [entry, *previous_runs(old)][:keep]
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(HEADER + "\n\n" + "\n".join(runs), encoding="utf-8")
    tmp.replace(path)
