"""Base común de las fuentes externas (OP.GG, Lolalytics, notas de parche…).

Todas comparten la misma caché en SQLite (tabla ``ext_cache``) con estas reglas,
pensadas para que la web nunca se quede esperando:

* **Fresco** (dentro del TTL): se devuelve al instante.
* **Caducado**: se devuelve igualmente al instante (*stale-while-revalidate*) y
  se encarga una actualización al precargador en segundo plano.
* **Sin datos**: en un hilo de fondo se descarga sin límite; atendiendo una
  petición de la web, se descarga como mucho durante el presupuesto que le
  quede (:mod:`app.budget`). Si no da tiempo, la descarga sigue en segundo
  plano, la respuesta sale sin ese dato y se marca como pendiente.
* **Una sola descarga por clave** (*single-flight*): si dos peticiones piden lo
  mismo a la vez, la segunda espera a la primera en vez de repetirla.
* **Errores**: se reintenta con espera creciente (10 min, 20, 40… hasta 6 h)
  para no martillear una web caída ni una página que no existe.
"""

from __future__ import annotations

import contextvars
import json
import threading
import time
from collections import OrderedDict
from concurrent.futures import ThreadPoolExecutor

from app import budget
from app.data.db import Database, now_s
from app.logbus import get_logger

log = get_logger("datos")

# Prioridades del precargador (menor = antes).
P_DEMAND = 0  # lo que una petición de la web no pudo esperar
P_USER = 10  # tus campeones y tus matchups
P_CORE = 20  # lo que usa la web con los filtros por defecto
P_STALE = 25  # refresco de algo caducado que alguien acaba de usar
P_EXTRA = 30
P_BACKGROUND = 40

FAIL_BACKOFF_S = 600
FAIL_BACKOFF_MAX_S = 6 * 3600
# Cortacircuitos por web: si responde 429/403 (límite o bloqueo) o 503, se deja de pedirle NADA durante un rato (lo que
# diga Retry-After o una espera creciente) y se sirve lo cacheado. Sin esto, cada clave distinta seguía insistiendo.
CIRCUIT_STATUSES = {403, 429, 503}
CIRCUIT_BASE_S = 60
CIRCUIT_MAX_S = 1800


def retry_after_seconds(value) -> float | None:
    """Segundos de la cabecera Retry-After (número; las fechas HTTP no se usan)."""
    try:
        seconds = float(str(value).strip())
    except (TypeError, ValueError):
        return None
    return seconds if seconds > 0 else None
MEM_ITEMS = 4000

# Dentro de un hilo del precargador lo caducado se refresca en línea (en vez de
# re-encolarlo): así «asegurar que está fresco» significa de verdad descargarlo.
inline_refresh: contextvars.ContextVar[bool] = contextvars.ContextVar("tempu_inline_refresh", default=False)

# Hilos compartidos para las descargas lanzadas desde peticiones de la web: así
# la petición puede dejar de esperar sin cancelar la descarga. Se crean al primer uso
# y se cierran al apagar la app (``shutdown_fetches``), sin esperar a las que estén en curso.
_EXECUTOR: ThreadPoolExecutor | None = None
_EXECUTOR_LOCK = threading.Lock()


def _executor() -> ThreadPoolExecutor:
    global _EXECUTOR
    with _EXECUTOR_LOCK:
        if _EXECUTOR is None:
            _EXECUTOR = ThreadPoolExecutor(max_workers=8, thread_name_prefix="fetch")
        return _EXECUTOR


def shutdown_fetches() -> None:
    """Cierra los hilos de descarga (las pendientes se cancelan; una nueva petición los vuelve a crear)."""
    global _EXECUTOR
    with _EXECUTOR_LOCK:
        executor, _EXECUTOR = _EXECUTOR, None
    if executor is not None:
        executor.shutdown(wait=False, cancel_futures=True)


SUMMARY_TTL_S = 30  # resumen de la caché para «Datos y API»

class _Flight:
    __slots__ = ("done", "value")

    def __init__(self) -> None:
        self.done = threading.Event()
        self.value = None


class Throttle:
    """Separación mínima entre peticiones a un mismo host (cliente educado)."""

    def __init__(self, per_second: float) -> None:
        self.interval = 1.0 / per_second
        self._lock = threading.Lock()
        self._next = 0.0

    def wait(self) -> None:
        with self._lock:
            now = time.monotonic()
            delay = self._next - now
            self._next = max(now, self._next) + self.interval
        if delay > 0:
            time.sleep(delay)


class CachedSource:
    """Caché con SWR, single-flight y backoff. Las subclases definen ``name`` y su carril del precargador."""

    name = "ext"
    default_lane = "web"
    # Carril por tipo de dato cuando no es ``default_lane`` (OP.GG reparte entre su web y su MCP).
    kind_lanes: dict[str, str] = {}

    def __init__(self, db: Database, enabled: bool = True, warmer=None) -> None:
        self.db = db
        self.enabled = enabled
        self.warmer = warmer
        self._mem: OrderedDict = OrderedDict()
        self._mem_lock = threading.Lock()
        self._inflight: dict[tuple[str, str], _Flight] = {}
        self._inflight_lock = threading.Lock()
        self._failed: dict[tuple[str, str], tuple[float, int]] = {}
        self._circuit_until = 0.0  # monotonic: hasta cuándo no se le pide nada a esta web
        self._circuit_trips = 0
        self.stats: dict = {"requests": 0, "cache_hits": 0, "stale_served": 0, "errors": 0, "last_error": None, "last_ok": None, "circuit_open_for": 0}

    # ------------------------------------------------------------ carriles
    def lane_for(self, kind: str) -> str:
        return self.kind_lanes.get(kind, self.default_lane)

    # -------------------------------------------------------------- caché
    def _remember(self, kind: str, key: str, fetched_at: int, value) -> None:
        with self._mem_lock:
            self._mem[(kind, key)] = (fetched_at, value)
            self._mem.move_to_end((kind, key))
            while len(self._mem) > MEM_ITEMS:
                self._mem.popitem(last=False)

    def peek(self, kind: str, key: str):
        """``(fetched_at, valor)`` de la caché sin mirar su edad, o ``None``."""
        with self._mem_lock:
            item = self._mem.get((kind, key))
        if item is not None:
            return item
        try:
            row = self.db.one("SELECT fetched_at, payload FROM ext_cache WHERE source = ? AND key = ?", (kind, key))
        except Exception:  # BD ocupada o cerrándose: mejor un fallo de caché que una excepción
            log.debug("No se pudo leer la caché %s/%s", kind, key, exc_info=True)
            return None
        if not row:
            return None
        try:
            item = (int(row["fetched_at"]), json.loads(row["payload"]))
        except (TypeError, ValueError):
            return None
        self._remember(kind, key, *item)
        return item

    def _cache_put(self, kind: str, key: str, payload) -> None:
        ts = now_s()
        with self.db.write() as conn:
            conn.execute(
                "INSERT OR REPLACE INTO ext_cache(source, key, fetched_at, payload) VALUES (?, ?, ?, ?)",
                (kind, key, ts, json.dumps(payload, separators=(",", ":"), ensure_ascii=False)),
            )
        self._remember(kind, key, ts, payload)

    # ------------------------------------------------------------ backoff
    def trip(self, status: int, retry_after=None) -> None:
        """La web respondió con un estado de límite o bloqueo: se abre el cortacircuitos de toda la fuente."""
        if status not in CIRCUIT_STATUSES:
            return
        self._circuit_trips += 1
        wait = retry_after_seconds(retry_after) or min(CIRCUIT_MAX_S, CIRCUIT_BASE_S * (2 ** (self._circuit_trips - 1)))
        self._circuit_until = max(self._circuit_until, time.monotonic() + min(wait, CIRCUIT_MAX_S))
        log.warning("%s respondió %s: no se le pide nada durante %d s (se sirve lo cacheado).", self.name, status, round(wait))

    def circuit_open(self) -> bool:
        remaining = self._circuit_until - time.monotonic()
        self.stats["circuit_open_for"] = max(0, round(remaining))
        return remaining > 0

    def _backing_off(self, kind: str, key: str) -> bool:
        if self.circuit_open():
            return True
        failed = self._failed.get((kind, key))
        if not failed:
            return False
        at, count = failed
        wait = min(FAIL_BACKOFF_MAX_S, FAIL_BACKOFF_S * (2 ** max(0, count - 1)))
        return time.monotonic() - at < wait

    def _fail(self, kind: str, key: str, exc: Exception | None) -> None:
        _, count = self._failed.get((kind, key), (0.0, 0))
        self._failed[(kind, key)] = (time.monotonic(), count + 1)
        if exc is not None:
            self.stats["errors"] += 1
            self.stats["last_error"] = f"{type(exc).__name__}: {exc}"[:200]

    def clear_failures(self) -> None:
        self._failed.clear()

    # ------------------------------------------------------------- lectura
    def _cached(self, kind: str, key: str, ttl: float, fetch, allow_fetch: bool = True, priority: int = P_STALE):
        item = self.peek(kind, key)
        if item is not None and now_s() - item[0] <= ttl:
            self.stats["cache_hits"] += 1
            return item[1]
        if item is not None:
            if inline_refresh.get() and allow_fetch and self.enabled and not self._backing_off(kind, key):
                return self._fetch(kind, key, fetch, None)
            # Caducado: se sirve ya y se actualiza en segundo plano.
            self.stats["stale_served"] += 1
            if (
                allow_fetch
                and self.enabled
                and not self._backing_off(kind, key)
                and not self._schedule(kind, key, ttl, fetch, priority)
                and not budget.interactive()
            ):
                return self._fetch(kind, key, fetch, None)  # sin precargador (tests, scripts): en línea
            return item[1]
        if not allow_fetch or not self.enabled or self._backing_off(kind, key):
            return None
        current = budget.current()
        if current is not None and current.expired():
            self._schedule(kind, key, ttl, fetch, P_DEMAND)
            current.mark(self.name)
            return None
        return self._fetch(kind, key, fetch, current)

    def _fetch(self, kind: str, key: str, fetch, current):
        """Descarga con single-flight. Con presupuesto, espera como mucho lo que quede."""
        k = (kind, key)
        with self._inflight_lock:
            flight = self._inflight.get(k)
            leader = flight is None
            if leader:
                flight = _Flight()
                self._inflight[k] = flight
        if leader:
            if current is None:
                self._lead(kind, key, fetch, flight)
                return flight.value
            _executor().submit(budget.run_detached, self._lead, kind, key, fetch, flight)
        wait = None if current is None else current.remaining()
        if flight.done.wait(wait):
            return flight.value
        if current is not None:
            current.mark(self.name)
        return None

    def _lead(self, kind: str, key: str, fetch, flight: _Flight) -> None:
        try:
            value = None
            try:
                self.stats["requests"] += 1
                value = fetch()
            # una web caída o con otro formato nunca debe tumbar una vista
            except Exception as exc:  # noqa: BLE001
                self._fail(kind, key, exc)
                log.warning("%s (%s %s): %s", self.name, kind, key, exc)
            else:
                if value is not None:
                    self._cache_put(kind, key, value)
                    self._failed.pop((kind, key), None)
                    self._circuit_trips = 0  # vuelve a responder bien: la próxima espera empieza de cero
                    self.stats["last_ok"] = now_s()
                else:
                    self._fail(kind, key, None)
            if value is None:
                item = self.peek(kind, key)  # mejor algo viejo que nada
                value = item[1] if item else None
            flight.value = value
        finally:
            flight.done.set()
            with self._inflight_lock:
                self._inflight.pop((kind, key), None)

    def refresh(self, kind: str, key: str, ttl: float, fetch):
        """Descarga si no está fresco (lo usa el precargador)."""
        item = self.peek(kind, key)
        if item is not None and now_s() - item[0] <= ttl:
            return item[1]
        if not self.enabled or self._backing_off(kind, key):
            return item[1] if item else None
        return self._fetch(kind, key, fetch, None)

    def _schedule(self, kind: str, key: str, ttl: float, fetch, priority: int) -> bool:
        warmer = self.warmer
        if warmer is None or not self.enabled:
            return False
        return warmer.submit(
            self.lane_for(kind),
            (self.name, kind, key),
            lambda: self.refresh(kind, key, ttl, fetch),
            priority,
            label=f"{self.name} · {kind}",
        )

    # ------------------------------------------------------------- resumen
    def cache_summary(self, kinds: tuple[str, ...] | None = None) -> dict:
        # «Datos y API» lo pide cada 2,5 s y recorre toda la caché (~0,75 s en Windows con el recolector a tope): se
        # reutiliza ``SUMMARY_TTL_S``.
        cached = getattr(self, "_summary_cache", None)
        if cached is not None and time.time() - cached[0] < SUMMARY_TTL_S:
            out = cached[1]
        else:
            rows = self.db.query("SELECT source, COUNT(*) AS n, MAX(fetched_at) AS latest FROM ext_cache GROUP BY source")
            out = {r["source"]: {"entries": r["n"], "latest": r["latest"]} for r in rows}
            self._summary_cache = (time.time(), out)
        return {k: v for k, v in out.items() if kinds is None or k in kinds}

    def cached_entries(self, kind: str):
        """(clave, datos) de todo lo cacheado de un tipo."""
        for row in self.db.query("SELECT key, payload FROM ext_cache WHERE source = ?", (kind,)):
            try:
                yield row["key"], json.loads(row["payload"])
            except ValueError:
                continue
