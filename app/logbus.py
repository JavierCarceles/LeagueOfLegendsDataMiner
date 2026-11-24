"""Bus de logs en memoria con difusión SSE a todos los navegadores conectados.

Cualquier ``logging.getLogger("tempu....")`` acaba aquí. Se guarda un buffer
circular con los últimos mensajes para que una pestaña nueva vea el
contexto reciente, y cada suscriptor tiene su propia cola (antes una única
``queue.Queue`` repartía los mensajes entre pestañas y se perdían).
"""

from __future__ import annotations

import asyncio
import itertools
import logging
import threading
import time
from collections import deque

LEVEL_TO_KIND = {
    logging.DEBUG: "debug",
    logging.INFO: "info",
    logging.WARNING: "warning",
    logging.ERROR: "error",
    logging.CRITICAL: "error",
}


class LogBus:
    def __init__(self, maxlen: int = 400) -> None:
        self._buffer: deque[dict] = deque(maxlen=maxlen)
        self._subscribers: set[asyncio.Queue] = set()
        self._lock = threading.Lock()
        self._loop: asyncio.AbstractEventLoop | None = None
        self._ids = itertools.count(1)

    def attach_loop(self, loop: asyncio.AbstractEventLoop) -> None:
        self._loop = loop

    def publish(self, message: str, kind: str = "info", source: str = "app", trace: str | None = None) -> None:
        entry = {
            "id": next(self._ids),
            "ts": time.time(),
            "kind": kind,
            "source": source,
            "message": message,
        }
        if trace:
            entry["trace"] = trace  # dónde falló (los errores con excepción): lo sube «Subir ahora» en _registro.json
        with self._lock:
            self._buffer.append(entry)
            subscribers = list(self._subscribers)
        loop = self._loop
        if loop is None or loop.is_closed():
            return
        for queue in subscribers:
            try:
                loop.call_soon_threadsafe(_offer, queue, entry)
            except RuntimeError:  # loop cerrado durante el apagado
                pass

    def recent(self, limit: int = 100) -> list[dict]:
        with self._lock:
            return list(self._buffer)[-limit:]

    def subscribe(self) -> asyncio.Queue:
        queue: asyncio.Queue = asyncio.Queue(maxsize=500)
        with self._lock:
            self._subscribers.add(queue)
        return queue

    def unsubscribe(self, queue: asyncio.Queue) -> None:
        with self._lock:
            self._subscribers.discard(queue)


def _offer(queue: asyncio.Queue, entry: dict) -> None:
    if queue.full():
        try:
            queue.get_nowait()
        except asyncio.QueueEmpty:
            pass
    queue.put_nowait(entry)


class BusHandler(logging.Handler):
    def __init__(self, bus: LogBus) -> None:
        super().__init__(level=logging.INFO)
        self.bus = bus

    def emit(self, record: logging.LogRecord) -> None:
        try:
            kind = getattr(record, "kind", None) or LEVEL_TO_KIND.get(record.levelno, "info")
            source = record.name.split(".", 1)[-1] if "." in record.name else record.name
            trace = None
            if record.exc_info:
                import traceback

                trace = "".join(traceback.format_exception(*record.exc_info))[-2000:]
            self.bus.publish(record.getMessage(), kind=kind, source=source, trace=trace)
        except Exception:  # noqa: BLE001  (patrón de logging.Handler: un log nunca rompe nada)
            self.handleError(record)


bus = LogBus()


def setup_logging() -> None:
    root = logging.getLogger("tempu")
    if getattr(root, "_tempu_configured", False):
        return
    root.setLevel(logging.INFO)
    console = logging.StreamHandler()
    console.setFormatter(logging.Formatter("%(asctime)s %(levelname)-7s %(name)s: %(message)s", "%H:%M:%S"))
    root.addHandler(console)
    root.addHandler(BusHandler(bus))
    root.propagate = False
    root._tempu_configured = True  # type: ignore[attr-defined]


LOG_FILE_BYTES = 1_000_000  # ``data/registro/app_vivo.log`` y 2 anteriores: ~3 MB como mucho
# La app escribe en ``app_vivo.log`` (fuera de GitHub: lo tiene abierto y en Windows git no puede reescribirlo); antes de
# cada subida se copia su final a ``app.log``, que es el que se sube.
LIVE_LOG = "app_vivo.log"
LOG_SNAPSHOT_BYTES = 1_000_000


def _safe_rotating_handler(path, max_bytes: int, backups: int):
    """``RotatingFileHandler`` que no pierde el registro en Windows.

    Para rotar renombra el fichero, y en Windows eso falla si alguien lo tiene abierto (la copia que se sube a GitHub, un
    editor…): el ``logging`` estándar se traga el error y vuelve a intentarlo en cada línea, que se pierde. Pasó el
    24-11-2026: el registro subido se cortaba a las 10:57. Si no puede renombrar, copia el fichero a ``.1`` y lo vacía."""
    import shutil
    from logging.handlers import RotatingFileHandler

    class SafeRotatingFileHandler(RotatingFileHandler):
        def doRollover(self) -> None:
            try:
                super().doRollover()
                return
            except OSError:
                pass
            try:
                if self.stream:
                    self.stream.close()
                    self.stream = None
                shutil.copyfile(self.baseFilename, f"{self.baseFilename}.1")
            except OSError:
                pass
            try:
                with open(self.baseFilename, "w", encoding=self.encoding or "utf-8"):
                    pass  # vaciarlo
            except OSError:
                pass
            if not self.delay:
                self.stream = self._open()

    return SafeRotatingFileHandler(path, maxBytes=max_bytes, backupCount=backups, encoding="utf-8", delay=True)


def add_file_log(data_dir) -> None:
    """Registro completo en ``data/registro/app_vivo.log`` (su final se sube a GitHub como ``app.log``): todo lo que sale en la ventana
    negra, con fecha y, en los errores, dónde fallaron. Así se puede revisar desde otro ordenador qué pasó entre dos
    subidas."""
    from pathlib import Path

    root = logging.getLogger("tempu")
    if any(getattr(h, "_tempu_file", False) for h in root.handlers):
        return
    folder = Path(data_dir) / "registro"
    try:
        folder.mkdir(parents=True, exist_ok=True)
        handler = _safe_rotating_handler(folder / LIVE_LOG, LOG_FILE_BYTES, 2)
    except OSError:
        return
    handler.setLevel(logging.INFO)
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)-7s %(threadName)s %(name)s: %(message)s", "%Y-%m-%d %H:%M:%S"))
    handler._tempu_file = True  # type: ignore[attr-defined]
    root.addHandler(handler)


def get_logger(name: str) -> logging.Logger:
    return logging.getLogger(f"tempu.{name}")
