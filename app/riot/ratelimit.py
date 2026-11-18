"""Limitador de peticiones adaptativo para la API de Riot.

Una *development key* gratuita permite **20 peticiones/1 s y 100/2 min por
host** (cada plataforma y cada región tienen su propio cupo). Este limitador:

* Aplica ventanas deslizantes por host (límite de aplicación) y por endpoint
  (límite de método), así que nunca debería recibir un 429.
* Se **auto-ajusta** leyendo ``X-App-Rate-Limit`` / ``X-Method-Rate-Limit``:
  si mañana usas una key personal o de producción, aprovecha su cupo.
* Sincroniza los contadores con ``*-Count``: tras reiniciar el servidor no
  vuelve a quemar el cupo que Riot aún recuerda.
* Respeta ``Retry-After`` en los 429.
* Tiene **prioridades**: el recolector en segundo plano deja libre un
  porcentaje del cupo para que las búsquedas interactivas respondan rápido.
"""

from __future__ import annotations

import math
import threading
import time
from collections import defaultdict, deque

DEV_KEY_LIMITS: list[tuple[int, int]] = [(20, 1), (100, 120)]
MARGIN = 0.12  # segundos extra por jitter de red / reloj


class Priority:
    HIGH = 0  # el usuario está esperando la respuesta
    LOW = 1  # recolector en segundo plano


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

    def record(self, now: float) -> None:
        for window in self.windows:
            window.stamps.append(now)

    def set_limits(self, limits: list[tuple[int, int]]) -> bool:
        if not limits or sorted(limits) == sorted((w.limit, w.seconds) for w in self.windows):
            return False
        history = max(self.windows, key=lambda w: w.seconds).stamps if self.windows else deque()
        self.windows = [_Window(limit, seconds, deque(history)) for limit, seconds in limits]
        return True

    def sync_counts(self, counts: list[tuple[int, int]], now: float) -> None:
        server = {seconds: count for count, seconds in counts}
        for window in self.windows:
            count = server.get(window.seconds)
            if count is None:
                continue
            window.purge(now)
            missing = count - len(window.stamps)
            if missing > 1:  # tolerancia para peticiones en vuelo
                window.stamps.extend([now] * missing)

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
        self._clock = time.monotonic

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
        reserve = self.reserve_frac if priority == Priority.LOW else 0.0
        deadline = None if timeout is None else self._clock() + timeout
        method_key = f"{host}|{method}"
        with self._cond:
            while True:
                if stop is not None and stop.is_set():
                    return False
                now = self._clock()
                app = self._app_bucket(host)
                meth = self._method.get((host, method))
                wait = max(
                    self._blocked.get(host, 0.0) - now,
                    self._blocked.get(method_key, 0.0) - now,
                    app.wait(now, reserve),
                    meth.wait(now) if meth else 0.0,
                )
                if wait <= 0:
                    app.record(now)
                    if meth:
                        meth.record(now)
                    self._stats[host]["requests"] += 1
                    return True
                if deadline is not None and now + wait > deadline:
                    return False
                self._cond.wait(timeout=min(wait, 0.5))

    def update(self, host: str, method: str, status: int, headers) -> float:
        """Procesa las cabeceras de la respuesta. Devuelve la espera aplicada si fue 429."""
        applied = 0.0
        with self._cond:
            now = self._clock()
            app = self._app_bucket(host)
            app_limits = parse_limits(headers.get("X-App-Rate-Limit"))
            if app_limits:
                app.set_limits(app_limits)
            app_counts = parse_limits(headers.get("X-App-Rate-Limit-Count"))
            if app_counts:
                app.sync_counts(app_counts, now)

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
                hosts[host] = {
                    "windows": bucket.usage(now),
                    "blocked_for": round(max(0.0, blocked), 1),
                    **self._stats[host],
                }
            return hosts
