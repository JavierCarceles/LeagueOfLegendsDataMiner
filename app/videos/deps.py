"""Dependencias del recolector de vídeos, instaladas solas en segundo plano la primera vez que hacen falta.

``run.py`` instala ``requirements-videos.txt`` al arrancar (como ``requirements.txt``, pero si falla la app arranca
igual). Si al usar la sección todavía falta algo, se instala aquí en segundo plano y se reintenta cada 10 min.
yt-dlp se actualiza solo una vez al día (YouTube cambia a menudo y una versión vieja deja de funcionar).
"""

from __future__ import annotations

import importlib
import importlib.util
import json
import shutil
import subprocess
import sys
import threading
import time
from pathlib import Path

from app.config import BASE_DIR
from app.logbus import get_logger

log = get_logger("videos")

BASE_REQ = BASE_DIR / "requirements-videos.txt"
BASE_MODULES = ("yt_dlp", "youtube_transcript_api", "cv2", "numpy")
VOICE_MODULES = ("faster_whisper",)
STAMP = ".tempu-videos.json"  # en el entorno de Python (como la huella de requirements.txt de run.py)
UPDATE_EVERY_S = 24 * 3600
PIP_TIMEOUT_S = 30 * 60
RETRY_AFTER_S = 10 * 60

_lock = threading.Lock()
_state: dict = {"estado": "sin comprobar", "detalle": None, "voz": None}


def _stamp_path() -> Path:
    return Path(sys.prefix) / STAMP


def _read_stamp() -> dict:
    try:
        data = json.loads(_stamp_path().read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def _write_stamp(data: dict) -> None:
    try:
        _stamp_path().write_text(json.dumps(data, indent=1), encoding="utf-8")
    except OSError:
        log.debug("No se pudo guardar %s", _stamp_path(), exc_info=True)


def _importable(modules: tuple[str, ...]) -> bool:
    importlib.invalidate_caches()
    return all(importlib.util.find_spec(name) is not None for name in modules)


def _pip(*args: str) -> tuple[bool, str]:
    cmd = [sys.executable, "-m", "pip", "install", "--disable-pip-version-check", "--no-input", "-q", *args]
    flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    try:
        proc = subprocess.run(
            cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, errors="replace",
            timeout=PIP_TIMEOUT_S, check=False, creationflags=flags,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        return False, str(exc)
    return proc.returncode == 0, (proc.stdout or "")[-1500:]


def state() -> dict:
    """Para la web: ``estado`` (listo, instalando, error…), el detalle y las versiones."""
    with _lock:
        out = dict(_state)
    out["yt_dlp"] = _version("yt_dlp")
    out["deno"] = deno_path()
    return out


def _version(module: str) -> str | None:
    if importlib.util.find_spec(module) is None:
        return None
    try:
        if module == "yt_dlp":
            from yt_dlp.version import __version__

            return __version__
        return getattr(importlib.import_module(module), "__version__", None)
    except Exception:  # noqa: BLE001 - solo es informativo
        return None


def _set(estado: str, detalle: str | None = None, **extra) -> None:
    with _lock:
        _state.update(estado=estado, detalle=detalle, **extra)


def ensure_base() -> bool:
    """yt-dlp, OpenCV… listos. Normalmente ya los instaló ``run.py`` al arrancar; si falta algo, se instala aquí."""
    stamp = _read_stamp()
    if _importable(BASE_MODULES):
        _set("listo")
        _maybe_update_ytdlp(stamp)
        return True
    if time.time() - float(stamp.get("base_failed_at") or 0) < RETRY_AFTER_S:
        return False
    _set("instalando", "yt-dlp, OpenCV, Deno y voz a texto (falló o faltaba al arrancar)")
    log.info("Vídeos: instalando las herramientas (yt-dlp, OpenCV, Deno, voz a texto)…")
    ok, output = _pip("-r", str(BASE_REQ))
    stamp = _read_stamp()
    if ok and _importable(BASE_MODULES):
        stamp.update(base_failed_at=None, ytdlp_updated_at=time.time())
        _write_stamp(stamp)
        _set("listo")
        log.info("Vídeos: herramientas instaladas.")
        return True
    stamp["base_failed_at"] = time.time()
    _write_stamp(stamp)
    _set("error", f"No se pudieron instalar las herramientas de vídeo: {output.strip()[-400:]}")
    log.warning("Vídeos: no se pudieron instalar las herramientas (se reintenta en 10 min): %s", output.strip()[-1500:])
    return False


def _maybe_update_ytdlp(stamp: dict) -> None:
    if time.time() - float(stamp.get("ytdlp_updated_at") or 0) < UPDATE_EVERY_S:
        return
    update_ytdlp(stamp)


def update_ytdlp(stamp: dict | None = None) -> bool:
    """``pip install -U yt-dlp[default]`` (también cuando YouTube rompe la versión instalada)."""
    stamp = stamp if stamp is not None else _read_stamp()
    ok, output = _pip("-U", "yt-dlp[default]")
    stamp["ytdlp_updated_at"] = time.time()
    _write_stamp(stamp)
    if ok:
        # Si yt-dlp ya estaba cargado, la versión nueva se usa al volver a abrir la app (recargarlo a medias es peor).
        log.info("Vídeos: yt-dlp actualizado (se usa la versión nueva desde el próximo arranque si ya estaba cargado).")
    else:
        log.warning("Vídeos: no se pudo actualizar yt-dlp: %s", output.strip()[-300:])
    return ok


def ensure_voice() -> bool:
    """faster-whisper (voz a texto) solo cuando algún vídeo no tiene subtítulos."""
    if _importable(VOICE_MODULES):
        _set(_state.get("estado") or "listo", _state.get("detalle"), voz=True)
        return True
    stamp = _read_stamp()
    if time.time() - float(stamp.get("voice_failed_at") or 0) < RETRY_AFTER_S:
        return False
    log.info("Vídeos: instalando la transcripción local (faster-whisper) para los vídeos sin subtítulos…")
    ok, output = _pip("faster-whisper>=1.1")
    if ok and _importable(VOICE_MODULES):
        _set(_state.get("estado") or "listo", _state.get("detalle"), voz=True)
        return True
    stamp["voice_failed_at"] = time.time()
    _write_stamp(stamp)
    _set(_state.get("estado") or "listo", _state.get("detalle"), voz=False)
    log.warning("Vídeos: no se pudo instalar faster-whisper: %s", output.strip()[-300:])
    return False


def deno_path() -> str | None:
    """El Deno instalado con pip (o el del sistema): yt-dlp lo usa para los retos de JavaScript de YouTube."""
    if importlib.util.find_spec("deno") is not None:
        try:
            from deno import find_deno_bin

            path = find_deno_bin()
            if path and Path(path).exists():
                return str(path)
        except Exception:
            log.debug("find_deno_bin falló", exc_info=True)
    return shutil.which("deno")


def js_runtimes() -> dict:
    """Motores de JavaScript para yt-dlp: Deno (pip o sistema) y, si está, Node."""
    runtimes: dict = {}
    deno = deno_path()
    if deno:
        runtimes["deno"] = {"path": deno}
    node = shutil.which("node")
    if node:
        runtimes["node"] = {"path": node}
    return runtimes
