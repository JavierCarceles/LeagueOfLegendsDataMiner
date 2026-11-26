"""La validación de la reconstrucción de recorridos (``app.coach.track_check``), sola al arrancar la app.

Unos minutos después de abrir la app (nunca mientras juegas) se ejecuta como un proceso aparte a prioridad baja: si
empiezas a elegir campeón o a jugar a mitad, el proceso se congela (no gasta CPU) y sigue donde iba al acabar, y si se
cierra la app, se corta (no se queda suelto gastando CPU junto al de la app nueva). Su
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
TIMEOUT_S = 45 * 60  # de trabajo de verdad: el tiempo congelado mientras juegas no cuenta
CHECK_S = 3.0  # cada cuánto se mira si estás eligiendo campeón o jugando
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
    output, ok = execute() if execute else _execute(stop, lambda: _in_game(svc))
    if stop is not None and stop.is_set():
        return False
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
    state = getattr(tracker, "state", None) or {}
    return bool(tracker is not None and (state.get("busy") or state.get("in_game")))


def _execute(stop=None, busy=None) -> tuple[str, bool]:
    """``python -m app.coach.track_check`` aparte (si falla o tarda, la app ni se entera) y a prioridad baja. Mientras
    ``busy()`` (eliges campeón o juegas) el proceso está congelado; si ``stop`` se activa (se cierra la app), se corta."""
    env = {**os.environ, "PYTHONUTF8": "1", "PYTHONIOENCODING": "utf-8"}
    flags = getattr(subprocess, "CREATE_NO_WINDOW", 0) | getattr(subprocess, "BELOW_NORMAL_PRIORITY_CLASS", 0)
    try:
        proc = subprocess.Popen(
            [sys.executable, "-m", "app.coach.track_check"], cwd=BASE_DIR, env=env, stdout=subprocess.PIPE,
            stderr=subprocess.PIPE, text=True, encoding="utf-8", errors="replace", creationflags=flags,
        )
    except OSError as exc:
        return f"No se pudo ejecutar: {exc}", False
    worked, frozen = 0.0, False
    while True:
        try:
            out, err = proc.communicate(timeout=CHECK_S)  # sin perder salida: se puede volver a llamar
            break
        except subprocess.TimeoutExpired:
            pass
        if (stop is not None and stop.is_set()) or worked > TIMEOUT_S:
            _freeze(proc, False)
            proc.kill()
            proc.communicate()
            if worked > TIMEOUT_S:
                return f"Tardó más de {TIMEOUT_S // 60} min y se cortó.", False
            return "Se cerró la app a mitad.", False
        playing = bool(busy and busy())
        if playing != frozen:
            frozen = _freeze(proc, playing) and playing
        if not frozen:
            worked += CHECK_S
    if proc.returncode != 0:
        return f"{out}\n{err}".strip()[-4000:], False
    return out.strip(), True


def _freeze(proc, on: bool) -> bool:
    """Congela (``on``) o despierta el proceso y sus hijos. ``False`` si no se pudo (sigue funcionando sin congelar)."""
    try:
        import psutil

        root = psutil.Process(proc.pid)
        for p in [root, *root.children(recursive=True)]:
            p.suspend() if on else p.resume()
        return True
    except Exception:  # noqa: BLE001 - sin psutil o el proceso ya acabó: no es imprescindible
        log.debug("No se pudo %s la validación", "congelar" if on else "despertar", exc_info=True)
        return False


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
