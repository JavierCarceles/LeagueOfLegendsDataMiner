"""Aplicación FastAPI: arranque, rutas y gestión de errores."""

from __future__ import annotations

import asyncio
import mimetypes
import threading
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from app import __version__
from app.api import coach, collector, draft, external, guides, otps, players, replays, sources, stats, system, videos
from app.budget import BudgetMiddleware
from app.config import WEB_DIR, Settings
from app.config import settings as default_settings
from app.logbus import add_file_log, bus, get_logger, setup_logging
from app.riot.client import Cancelled, RiotAPIError, RiotKeyError
from app.services import Services

log = get_logger("app")


class StaticNoCache(StaticFiles):
    """Ficheros estáticos que el navegador siempre revalida (ETag): tras
    actualizar el proyecto nunca se queda con JS/CSS antiguos en caché."""

    async def get_response(self, path, scope):
        response = await super().get_response(path, scope)
        response.headers["Cache-Control"] = "no-cache"
        return response


def create_app(settings: Settings | None = None, run_startup: bool = True) -> FastAPI:
    setup_logging()
    settings = settings or default_settings
    if run_startup:
        add_file_log(settings.data_dir)  # data/registro/app.log (se sube con los datos)
    svc = Services(settings)

    @asynccontextmanager
    async def lifespan(_: FastAPI):
        bus.attach_loop(asyncio.get_running_loop())
        log.info("Tempu Drafter %s arrancando…", __version__)
        if run_startup:
            threading.Thread(target=svc.startup, name="startup", daemon=True).start()
        else:
            svc.startup_done.set()
        try:
            yield
        finally:
            log.info("Apagando…")
            svc.shutdown()

    app = FastAPI(title="Tempu Drafter", version=__version__, lifespan=lifespan)
    app.state.svc = svc

    @app.exception_handler(RiotKeyError)
    async def _key_error(_: Request, exc: RiotKeyError):
        return JSONResponse({"detail": exc.message, "code": "riot_key"}, status_code=412)

    @app.exception_handler(RiotAPIError)
    async def _riot_error(_: Request, exc: RiotAPIError):
        code = 404 if exc.status == 404 else 502
        return JSONResponse({"detail": exc.message, "code": "riot_api", "status": exc.status}, status_code=code)

    @app.exception_handler(Cancelled)
    async def _cancelled(*_: object):
        return JSONResponse({"detail": "Operación cancelada"}, status_code=499)

    @app.exception_handler(ValueError)
    async def _value_error(_: Request, exc: ValueError):
        return JSONResponse({"detail": str(exc)}, status_code=400)

    @app.exception_handler(Exception)
    async def _unexpected(request: Request, _: Exception):
        # Nunca una página en blanco: el error llega a la web con un mensaje legible y queda en el log.
        log.exception("Error inesperado en %s", request.url.path)
        return JSONResponse({"detail": "Error interno inesperado. El detalle está en «Actividad en tiempo real».", "code": "internal"}, status_code=500)

    for router in (system.router, stats.router, draft.router, players.router, collector.router, guides.router, external.router, coach.router, sources.router, replays.router, otps.router, videos.router):
        app.include_router(router)

    # Ninguna vista espera a una descarga más de unos segundos (ver app/budget.py).
    app.add_middleware(BudgetMiddleware)

    # En algunos Windows el registro dice que .js es «text/plain» y el navegador se niega a ejecutar la web (página en
    # blanco sin ningún error en la consola del servidor): se fijan los tipos correctos.
    for ext, mime in ((".js", "text/javascript"), (".mjs", "text/javascript"), (".css", "text/css"), (".svg", "image/svg+xml"), (".json", "application/json")):
        mimetypes.add_type(mime, ext)
    app.mount("/static", StaticNoCache(directory=WEB_DIR), name="static")

    @app.get("/", include_in_schema=False)
    def index():
        return FileResponse(WEB_DIR / "index.html", headers={"Cache-Control": "no-cache"})

    @app.get("/favicon.ico", include_in_schema=False)
    def favicon():
        return FileResponse(WEB_DIR / "img" / "favicon.svg", media_type="image/svg+xml")

    return app
