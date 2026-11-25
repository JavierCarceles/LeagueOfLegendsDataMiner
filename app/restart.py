"""Reinicio de la app desde dentro (lo pide ``updater`` al llegar código nuevo de GitHub).

``run.py`` arranca el servidor con :func:`register` (cómo pararlo) y, si al acabar :func:`requested` es cierto, sale con
:data:`EXIT_CODE`: el proceso de fuera (el supervisor de ``run.py``) instala lo que haga falta y vuelve a lanzar la app con
el código nuevo. El cierre es el normal (``Services.shutdown``): el recolector guarda el progreso de cada jugador y el de
vídeos deja el vídeo en curso en su carpeta de trabajo para seguir donde iba.

Sin ``run.py`` (``uvicorn`` a mano, los tests) no hay quien vuelva a lanzar la app: ``available()`` es falso y el
actualizador solo avisa de que hay una versión nueva.
"""

from __future__ import annotations

import threading
from collections.abc import Callable

from app.logbus import get_logger

log = get_logger("restart")

EXIT_CODE = 75  # «vuelve a lanzarme»: el supervisor de run.py lo distingue de un cierre normal o de un fallo

_lock = threading.Lock()
_stopper: Callable[[], None] | None = None
_reason: str | None = None


def register(stopper: Callable[[], None]) -> None:
    """``run.py``: cómo cerrar el servidor ordenadamente (``server.should_exit = True``)."""
    global _stopper
    with _lock:
        _stopper = stopper


def available() -> bool:
    """¿Hay un supervisor que vuelva a lanzar la app al cerrarla? (solo arrancando con ``run.py``)."""
    return _stopper is not None


def request(reason: str) -> bool:
    """Cierra la app para que el supervisor la vuelva a lanzar. ``False`` si no hay supervisor."""
    global _reason
    with _lock:
        stopper = _stopper
        if stopper is None:
            return False
        _reason = reason
    log.warning("Reiniciando la app: %s.", reason)
    stopper()
    return True


def requested() -> str | None:
    """El motivo del reinicio pedido (``None`` si el cierre es normal)."""
    return _reason
