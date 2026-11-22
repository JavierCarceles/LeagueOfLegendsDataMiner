"""Presupuesto de tiempo de red por petición web.

Ninguna vista debe quedarse esperando a una descarga: cada petición a
``/api/…`` tiene unos segundos como mucho para traer datos externos que aún no
estén en caché. Pasado ese tiempo, lo que falte se encarga al precargador en
segundo plano (:mod:`app.warmer`), la respuesta sale con lo que ya hay y lleva
la cabecera ``X-Data-Pending: 1`` para que la web se complete sola en cuanto
lleguen los datos.

Se implementa con una ``ContextVar``: Starlette ejecuta los endpoints síncronos
en un hilo con una copia del contexto, así que el objeto :class:`Budget` (que
es mutable) se comparte entre el middleware y el endpoint.
"""

from __future__ import annotations

import contextvars
import time

DEFAULT_SECONDS = 3.0
_current: contextvars.ContextVar[Budget | None] = contextvars.ContextVar("tempu_budget", default=None)

# Rutas que no tienen sentido con presupuesto (flujo de logs) o que el usuario
# lanza a propósito y esperan su resultado (el backtest del modelo…).
EXEMPT_PREFIXES = ("/api/system/logs", "/api/draft/backtest")


class Budget:
    __slots__ = ("deadline", "pending", "reasons")

    def __init__(self, seconds: float = DEFAULT_SECONDS) -> None:
        self.deadline = time.monotonic() + seconds
        self.pending = False
        self.reasons: set[str] = set()

    def remaining(self) -> float:
        return max(0.0, self.deadline - time.monotonic())

    def expired(self) -> bool:
        return time.monotonic() >= self.deadline

    def mark(self, reason: str) -> None:
        self.pending = True
        if len(self.reasons) < 20:
            self.reasons.add(reason)


def current() -> Budget | None:
    return _current.get()


def remaining(default: float | None = None) -> float | None:
    """Segundos que le quedan a la petición en curso (``default`` fuera de una petición)."""
    budget = _current.get()
    return budget.remaining() if budget is not None else default


def mark_pending(reason: str) -> None:
    budget = _current.get()
    if budget is not None:
        budget.mark(reason)


def interactive() -> bool:
    """¿Estamos atendiendo una petición de la web (y no en un hilo de fondo)?"""
    return _current.get() is not None


def run_detached(fn, *args, **kwargs):
    """Ejecuta ``fn`` fuera del presupuesto (p. ej. una tarea de fondo lanzada desde una petición)."""
    ctx = contextvars.copy_context()
    ctx.run(_current.set, None)
    return ctx.run(fn, *args, **kwargs)


class BudgetMiddleware:
    """Middleware ASGI puro (compatible con respuestas en streaming como los logs SSE)."""

    def __init__(self, app, seconds: float = DEFAULT_SECONDS) -> None:
        self.app = app
        self.seconds = seconds

    async def __call__(self, scope, receive, send):
        path = scope.get("path") or ""
        if scope.get("type") != "http" or not path.startswith("/api/") or path.startswith(EXEMPT_PREFIXES):
            await self.app(scope, receive, send)
            return
        budget = Budget(self.seconds)
        token = _current.set(budget)

        async def send_wrapper(message):
            if message.get("type") == "http.response.start":
                headers = list(message.get("headers") or [])
                headers.append((b"x-data-pending", b"1" if budget.pending else b"0"))
                if budget.pending and budget.reasons:
                    detail = ",".join(sorted(budget.reasons))[:300]
                    headers.append((b"x-data-pending-detail", detail.encode("ascii", "replace")))
                message = {**message, "headers": headers}
            await send(message)

        try:
            await self.app(scope, receive, send_wrapper)
        finally:
            _current.reset(token)
