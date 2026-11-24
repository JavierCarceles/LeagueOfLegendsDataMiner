"""Limitador de peticiones adaptativo para la API de Riot.

Una *development key* gratuita permite **20 peticiones/1 s y 100/2 min por
host** (cada plataforma y cada región tienen su propio cupo). Este limitador:

* Aplica ventanas deslizantes por host (límite de aplicación) y por endpoint
  (límite de método), así que nunca debería recibir un 429.
* Se **auto-ajusta** leyendo ``X-App-Rate-Limit`` / ``X-Method-Rate-Limit``:
  si mañana usas una key personal o de producción, aprovecha su cupo.
* Sincroniza los contadores con ``*-Count``: tras reiniciar el servidor no
  vuelve a quemar el cupo que Riot aún recuerda. Lo que Riot cuenta y este
  proceso no pidió es de **otra app con la misma key** (otra copia abierta en
  otro ordenador, por ejemplo): se mide para avisarte, porque os repartís el cupo.
* Respeta ``Retry-After`` en los 429.
* Tiene **prioridades**: el recolector en segundo plano deja libre un
  porcentaje del cupo para que las búsquedas interactivas respondan rápido, y
  el seguimiento de tus partidas va por delante del recolector (con el
  recolector a tope, tus partidas nuevas se siguen analizando).
"""

from __future__ import annotations

import math
import threading
import time
from collections import Counter, defaultdict, deque

DEV_KEY_LIMITS: list[tuple[int, int]] = [(20, 1), (100, 120)]
MARGIN = 0.12  # segundos extra por jitter de red / reloj
FOREIGN_GRACE_S = 130  # al arrancar, lo que Riot recuerda de la ejecución anterior no es «otra app»
FOREIGN_WINDOW_S = 120
DIAG_WINDOW_S = 300  # llamadas por minuto de los últimos 5 min (diagnóstico)
# Si llevas un rato sin usar la web (ninguna petición de prioridad alta), el recolector puede usar casi todo el cupo:
# la reserva para tus búsquedas baja de ``reserve_frac`` a ``IDLE_RESERVE``; vuelve en cuanto haces algo. 2 min (antes
# 10): con el recolector en marcha casi nunca hay búsquedas, y cada llamada esperaba 1,41 s en vez de 1,26 s.
IDLE_AFTER_S = 120
IDLE_RESERVE = 0.05


class Priority:
    HIGH = 0  # el usuario está esperando la respuesta
    LOW = 1  # recolector en segundo plano
    NORMAL = 2  # seguimiento de tus partidas: delante del recolector, detrás de lo interactivo


def _reserve_for(priority: int, reserve_frac: float) -> float:
    """Parte del cupo largo que una prioridad deja libre a las superiores."""
    if priority == Priority.LOW:
        return reserve_frac
    if priority == Priority.NORMAL:
        return reserve_frac / 3
    return 0.0


def parse_limits(header: str | None) -> list[tuple[int, int]]:
    """``"20:1,100:120"`` -> ``[(20, 1), (100, 120)]``."""
    result: list[tuple[int, int]] = []
    for part in (header or "").split(","):
        try:
            count, seconds = part.strip().split(":")
            result.append((int(count), int(seconds)))
        except ValueError:
            continue
    return result


class _Window:
    __slots__ = ("limit", "seconds", "stamps")

    def __init__(self, limit: int, seconds: int, stamps: deque | None = None) -> None:
        self.limit = limit
        self.seconds = seconds
        self.stamps: deque[float] = stamps if stamps is not None else deque()

    def purge(self, now: float) -> None:
        cutoff = now - self.seconds - MARGIN
        stamps = self.stamps
        while stamps and stamps[0] <= cutoff:
            stamps.popleft()

    def capacity(self, reserve_frac: float) -> int:
        cap = self.limit
        if self.seconds <= 1:
            cap = max(1, cap - 1)
        if reserve_frac > 0 and self.seconds >= 10:
            cap = max(1, cap - max(1, math.ceil(self.limit * reserve_frac)))
        return cap

    def wait(self, now: float, reserve_frac: float) -> float:
        self.purge(now)
        cap = self.capacity(reserve_frac)
        if len(self.stamps) < cap:
            return 0.0
        oldest_blocking = self.stamps[len(self.stamps) - cap]
        return max(0.0, oldest_blocking + self.seconds + MARGIN - now)


class _Bucket:
    def __init__(self, limits: list[tuple[int, int]]) -> None:
        self.windows = [_Window(limit, seconds) for limit, seconds in limits]

    def wait(self, now: float, reserve_frac: float = 0.0) -> float:
        return max((w.wait(now, reserve_frac) for w in self.windows), default=0.0)

    def pace(self, reserve_frac: float) -> float:
        """Separación mínima para repartir una cuota a lo largo de la ventana larga (s por petición)."""
        longest = max(self.windows, key=lambda w: w.seconds, default=None)
        if longest is None or longest.seconds < 10:
            return 0.0
        return longest.seconds / max(1, longest.capacity(reserve_frac))

    def record(self, now: float) -> None:
        for window in self.windows:
            window.stamps.append(now)

    def set_limits(self, limits: list[tuple[int, int]]) -> bool:
        if not limits or sorted(limits) == sorted((w.limit, w.seconds) for w in self.windows):
            return False
        history = max(self.windows, key=lambda w: w.seconds).stamps if self.windows else deque()
        self.windows = [_Window(limit, seconds, deque(history)) for limit, seconds in limits]
        return True

    def sync_counts(self, counts: list[tuple[int, int]], now: float) -> int:
        """Ajusta las ventanas a lo que cuenta Riot. Devuelve cuántas peticiones de la ventana larga no eran nuestras."""
        server = {seconds: count for count, seconds in counts}
        longest = max((w.seconds for w in self.windows), default=0)
        foreign = 0
        for window in self.windows:
            count = server.get(window.seconds)
            if count is None:
                continue
            window.purge(now)
            missing = count - len(window.stamps)
            if missing > 1:  # tolerancia para peticiones en vuelo
                window.stamps.extend([now] * missing)
                if window.seconds == longest:
                    foreign = missing
        return foreign

    def usage(self, now: float) -> list[dict]:
        out = []
        for w in self.windows:
            w.purge(now)
            out.append({"limit": w.limit, "seconds": w.seconds, "used": len(w.stamps)})
        return out


class RateLimiter:
    def __init__(self, app_limits: list[tuple[int, int]] | None = None, reserve_frac: float = 0.15) -> None:
        self._cond = threading.Condition()
        self._default_limits = list(app_limits or DEV_KEY_LIMITS)
        self._app: dict[str, _Bucket] = {}
        self._method: dict[tuple[str, str], _Bucket] = {}
        self._blocked: dict[str, float] = {}
        self.reserve_frac = reserve_frac
        self._stats: dict[str, dict[str, int]] = defaultdict(lambda: {"requests": 0, "rate_limited": 0})
        self._foreign: dict[str, deque] = defaultdict(lambda: deque(maxlen=5000))  # host -> [(cuándo, peticiones ajenas)]
        self._low_next: dict[str, float] = {}  # host -> desde cuándo puede salir la siguiente de prioridad baja
        self._born: float | None = None
        self._clock = time.monotonic
        self._last_high: float | None = None  # última petición de prioridad alta (el usuario usando la web)
        # Diagnóstico (por qué va lento): cuándo salió cada petición y cuánto se esperó por cada motivo, por host.
        self._calls: dict[str, deque] = defaultdict(lambda: deque(maxlen=4000))
        self._waited: dict[str, Counter] = defaultdict(Counter)

    def _reserve_now(self, priority: int, now: float) -> float:
        """Reserva según la prioridad; menor mientras nadie usa la web (más cupo para el recolector)."""
        idle = self._last_high is None or now - self._last_high >= IDLE_AFTER_S
        base = min(self.reserve_frac, IDLE_RESERVE) if idle else self.reserve_frac
        return _reserve_for(priority, base)

    def interactive(self) -> bool:
        with self._cond:
            return self._last_high is not None and self._clock() - self._last_high < IDLE_AFTER_S

    def _app_bucket(self, host: str) -> _Bucket:
        bucket = self._app.get(host)
        if bucket is None:
            bucket = self._app[host] = _Bucket(self._default_limits)
        return bucket

    def acquire(
        self,
        host: str,
        method: str,
        priority: int = Priority.HIGH,
        stop: threading.Event | None = None,
        timeout: float | None = None,
    ) -> bool:
        """Bloquea hasta poder lanzar la petición. False si se cancela/expira."""
        paced = priority == Priority.LOW
        deadline = None if timeout is None else self._clock() + timeout
        method_key = f"{host}|{method}"
        with self._cond:
            if priority == Priority.HIGH:
                self._last_high = self._clock()
            while True:
                if stop is not None and stop.is_set():
                    return False
                now = self._clock()
                reserve = self._reserve_now(priority, now)
                app = self._app_bucket(host)
                meth = self._method.get((host, method))
                terms = {
                    "bloqueo_429": max(self._blocked.get(host, 0.0), self._blocked.get(method_key, 0.0)) - now,
                    "ventana_llena": app.wait(now, reserve),
                    "limite_metodo": meth.wait(now) if meth else 0.0,
                    # Prioridad baja repartida a lo largo de la ventana: si llenara la ventana de golpe, sus marcas
                    # caducarían todas a la vez al final y una búsqueda tuya esperaría hasta 2 min (se midieron 92 s).
                    "ritmo": self._low_next.get(host, 0.0) - now if paced else 0.0,
                }
                reason = max(terms, key=terms.get)
                wait = terms[reason]
                if wait <= 0:
                    if paced:
                        self._low_next[host] = max(now, self._low_next.get(host, now)) + app.pace(reserve)
                    app.record(now)
                    if meth:
                        meth.record(now)
                    self._stats[host]["requests"] += 1
                    self._calls[host].append((now, priority))
                    return True
                if deadline is not None and now + wait > deadline:
                    return False
                t0 = self._clock()
                self._cond.wait(timeout=min(wait, 0.5))
                self._waited[host][reason] += self._clock() - t0

    def update(self, host: str, method: str, status: int, headers) -> float:
        """Procesa las cabeceras de la respuesta. Devuelve la espera aplicada si fue 429."""
        applied = 0.0
        with self._cond:
            now = self._clock()
            if self._born is None:
                self._born = now
            app = self._app_bucket(host)
            app_limits = parse_limits(headers.get("X-App-Rate-Limit"))
            if app_limits:
                app.set_limits(app_limits)
            app_counts = parse_limits(headers.get("X-App-Rate-Limit-Count"))
            if app_counts:
                foreign = app.sync_counts(app_counts, now)
                if foreign and now - self._born >= FOREIGN_GRACE_S:
                    seen = self._foreign[host]
                    seen.append((now, foreign))
                    while seen and seen[0][0] < now - FOREIGN_WINDOW_S:  # sin la web abierta nadie lo podaba
                        seen.popleft()

            method_limits = parse_limits(headers.get("X-Method-Rate-Limit"))
            if method_limits:
                bucket = self._method.get((host, method))
                if bucket is None:
                    bucket = self._method[(host, method)] = _Bucket(method_limits)
                    bucket.record(now)  # la petición actual
                else:
                    bucket.set_limits(method_limits)
                method_counts = parse_limits(headers.get("X-Method-Rate-Limit-Count"))
                if method_counts:
                    bucket.sync_counts(method_counts, now)

            if status == 429:
                kind = (headers.get("X-Rate-Limit-Type") or "").lower()
                try:
                    retry = float(headers.get("Retry-After") or 0)
                except ValueError:
                    retry = 0.0
                if retry <= 0:
                    retry = 2.0 if kind == "service" else 5.0
                key = host if kind == "application" else f"{host}|{method}"
                self._blocked[key] = max(self._blocked.get(key, 0.0), now + retry + 0.25)
                self._stats[host]["rate_limited"] += 1
                applied = retry
            self._cond.notify_all()
        return applied

    def snapshot(self) -> dict[str, dict]:
        with self._cond:
            now = self._clock()
            hosts = {}
            for host, bucket in sorted(self._app.items()):
                blocked = max(
                    [self._blocked.get(host, 0.0) - now]
                    + [until - now for key, until in self._blocked.items() if key.startswith(f"{host}|")]
                )
                foreign = self._foreign.get(host)
                while foreign and foreign[0][0] < now - FOREIGN_WINDOW_S:
                    foreign.popleft()
                calls = [c for c in self._calls.get(host, ()) if c[0] >= now - DIAG_WINDOW_S]
                span = min(DIAG_WINDOW_S, now - calls[0][0]) if calls else 0
                low_reserve = self._reserve_now(Priority.LOW, now)
                hosts[host] = {
                    "windows": bucket.usage(now),
                    # Lo que de verdad sale por minuto (últimos 5 min), el máximo para el recolector con la reserva de
                    # ahora y por qué se espera (s en total desde que arrancó).
                    "llamadas_min": round(60 * len(calls) / span, 1) if span >= 30 else None,
                    "llamadas_min_recolector": round(60 * sum(1 for c in calls if c[1] == Priority.LOW) / span, 1) if span >= 30 else None,
                    "max_min_recolector": round(60 / bucket.pace(low_reserve), 1) if bucket.pace(low_reserve) else None,
                    "reserva_pct": round(100 * low_reserve),
                    "espera_s": {k: round(v) for k, v in self._waited.get(host, Counter()).most_common() if v >= 1},
                    "blocked_for": round(max(0.0, blocked), 1),
                    **self._stats[host],
                    # Peticiones de otra app con esta misma key en los últimos 2 minutos (os repartís el cupo).
                    "foreign": sum(n for _, n in foreign) if foreign else 0,
                }
            return hosts
