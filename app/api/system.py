"""Estado del sistema, API key, metadatos y logs en tiempo real."""

from __future__ import annotations

import asyncio
import json
import threading

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from app import __version__
from app.analytics.draft import get_alpha, last_backtest
from app.analytics.filters import available_patches
from app.api.deps import svc_from
from app.ingest.legacy import legacy_status
from app.lcu import available as lcu_available
from app.logbus import bus
from app.riot.routing import PLATFORMS, QUEUES, REGION_LABELS, ROLE_LABELS, ROLES, TIER_LABELS, TIERS

router = APIRouter(prefix="/api", tags=["sistema"])


class KeyBody(BaseModel):
    key: str


def _db_counts(svc) -> dict:
    db = svc.db
    return {
        "matches": db.scalar("SELECT COUNT(*) FROM matches WHERE aggregated = 1", default=0),
        "stored_matches": db.scalar("SELECT COUNT(*) FROM matches", default=0),
        "players": db.scalar("SELECT COUNT(*) FROM players", default=0),
        "seeds": db.scalar("SELECT COUNT(*) FROM players WHERE is_seed = 1", default=0),
        "legacy_matches": db.scalar("SELECT SUM(matches) FROM agg_totals WHERE patch = 'legacy'", default=0),
        "size_mb": round(svc.settings.db_path.stat().st_size / 1_048_576, 1) if svc.settings.db_path.exists() else 0,
    }


def _tracker_brief(svc) -> dict:
    from app.coach import store

    profile = store.get_profile(svc.db)
    state = svc.tracker.state
    return {
        "profile": {
            "game_name": profile["game_name"],
            "tag_line": profile["tag_line"],
            "platform": profile["platform"],
            "profile_icon": profile.get("profile_icon"),
            "linked": bool(profile.get("puuid")),
        }
        if profile
        else None,
        "status": state.get("status"),
        "queue": state.get("queue"),
        "in_game": state.get("in_game"),
        "last_new_at": state.get("last_new_at"),
    }


@router.get("/ping")
def ping():
    """Respuesta inmediata (sin tocar la base de datos): la usa run.py para saber si la app ya está abierta."""
    return {"app": "tempu-drafter", "version": __version__}


@router.get("/system/status")
def status(svc=Depends(svc_from)):
    return {
        "version": __version__,
        "ready": svc.startup_done.is_set(),
        "key": svc.keys.public(),
        "db": _db_counts(svc),
        "legacy": {**legacy_status(svc.db), "running": svc.legacy_running},
        "crawler": svc.crawler.status(),
        "tracker": _tracker_brief(svc),
        "warmer": svc.warmer.status(),
        "ratelimit": svc.limiter.snapshot(),
        "model": {"alpha": get_alpha(svc), "priors": svc.priors.get() if svc.startup_done.is_set() else None, "backtest": last_backtest(svc)},
        "features": {
            "lcu": lcu_available(),
        },
        "guides_dir": str(svc.settings.coachings_dir),
        "push": _push_brief(svc),
    }


def _push_brief(svc) -> dict | None:
    """Lo que necesita el botón «Subir a GitHub» de arriba: si sube, el paso, si hay otra en cola y el último error."""
    from app.autopush import AutoPush

    auto = AutoPush.of(svc)
    if auto is None:
        return None
    p = auto.public()
    keys = ("enabled", "running", "queued", "step", "last_push_at", "last_error", "git_ok", "git", "pending")
    return {k: p.get(k) for k in keys}


@router.post("/system/push", responses={409: {"description": "La subida está desactivada o git no puede subir"}})
def push(svc=Depends(svc_from)):
    """«Subir a GitHub» (botón de arriba): todo lo de data/ ya, o en cola si hay otra subida en marcha."""
    from app import autopush

    try:
        return autopush.push_now(svc)
    except autopush.PushUnavailable as exc:
        raise HTTPException(409, str(exc)) from exc


@router.post("/system/riot-key", responses={400: {"description": "La API key no tiene el formato RGAPI-…"}})
def set_key(body: KeyBody, svc=Depends(svc_from)):
    try:
        svc.keys.set(body.key)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    check = svc.riot.check_key()
    if check["valid"] is False:
        # Una key recién generada puede tardar unos minutos en activarse.
        svc.schedule_key_recheck()
    return {"key": svc.keys.public(), "valid": check["valid"], "check": check}


@router.post("/system/validate-key", responses={412: {"description": "No hay RIOT_API_KEY configurada"}})
def validate_key(svc=Depends(svc_from)):
    if not svc.keys.get():
        raise HTTPException(412, "No hay RIOT_API_KEY configurada")
    check = svc.riot.check_key()
    age_h = svc.keys.public()["age_hours"]
    if check["valid"] is False and age_h is not None and age_h < 0.25:
        svc.schedule_key_recheck()
    return {"valid": check["valid"], "check": check, "key": svc.keys.public()}


@router.post("/system/legacy-import", responses={409: {"description": "Data Dragon aún no está cargado"}})
def legacy_import(force: bool = False, svc=Depends(svc_from)):
    if not svc.dd.champions:
        raise HTTPException(409, "Data Dragon aún no está cargado")
    threading.Thread(target=svc.run_legacy_import, kwargs={"force": force}, daemon=True).start()
    return {"status": "started"}


@router.get("/meta")
def meta(svc=Depends(svc_from)):
    patches = available_patches(svc.db)
    has_legacy = bool(svc.db.scalar("SELECT 1 FROM agg_totals WHERE patch = 'legacy' LIMIT 1"))
    return {
        "ddragon": svc.dd.public(),
        "roles": [{"id": r, "label": ROLE_LABELS[r]} for r in ROLES],
        "tiers": [{"id": t, "label": TIER_LABELS[t]} for t in TIERS],
        "platforms": [
            {"id": p, "region": r, "label": label, "name": name} for p, (r, label, name) in PLATFORMS.items()
        ],
        "regions": [{"id": r, "label": label} for r, label in REGION_LABELS.items()],
        "patches": patches,
        "has_legacy": has_legacy,
        "queues": {str(k): v for k, v in QUEUES.items()},
    }


@router.get("/system/logs/recent")
def recent_logs(limit: int = 100):
    return {"logs": bus.recent(min(max(limit, 1), 400))}


@router.get("/system/logs")
async def stream_logs(request: Request):
    queue = bus.subscribe()

    async def events():
        try:
            yield "retry: 3000\n\n"
            while True:
                if await request.is_disconnected():
                    break
                try:
                    entry = await asyncio.wait_for(queue.get(), timeout=15)
                    yield f"data: {json.dumps(entry, ensure_ascii=False)}\n\n"
                except asyncio.TimeoutError:
                    yield ": keep-alive\n\n"
        finally:
            bus.unsubscribe(queue)

    return StreamingResponse(events(), media_type="text/event-stream", headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})
