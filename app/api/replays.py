"""Repeticiones reales en el cliente de LoL (abrir y saltar al momento de un error)."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from app import lcu
from app.api.deps import svc_from

router = APIRouter(prefix="/api/replay", tags=["repeticiones"])


class SeekBody(BaseModel):
    t: float = Field(0, ge=0, le=6 * 3600, description="Segundo de la partida")


def _guard(fn, *args):
    try:
        return fn(*args)
    except lcu.LCUError as exc:
        raise HTTPException(409, str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc


@router.get("/{match_id}/client")
def client_status(match_id: str, svc=Depends(svc_from)):
    return _guard(svc.client_replays.status, match_id.strip().upper())


@router.post("/{match_id}/client/download")
def client_download(match_id: str, svc=Depends(svc_from)):
    return _guard(svc.client_replays.download, match_id.strip().upper())


@router.post("/{match_id}/client/watch")
def client_watch(match_id: str, body: SeekBody | None = None, svc=Depends(svc_from)):
    return _guard(svc.client_replays.watch, match_id.strip().upper(), (body.t if body else 0.0))


@router.post("/client/seek")
def client_seek(body: SeekBody, svc=Depends(svc_from)):
    return _guard(svc.client_replays.seek, body.t)


@router.post("/client/enable-api")
def client_enable_api():
    path = _guard(lcu.enable_replay_api)
    return {"message": "Salto automático activado (EnableReplayApi=1). Se aplica al abrir la siguiente repetición.", "path": str(path)}
