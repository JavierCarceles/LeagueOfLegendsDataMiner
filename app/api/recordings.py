"""Grabaciones de la pantalla de tus partidas (app/recorder.py): activar o no, la lista y ver un vídeo."""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel

from app.api.deps import svc_from

router = APIRouter(prefix="/api/recordings", tags=["grabaciones"])
Svc = Annotated[Any, Depends(svc_from)]


class EnableBody(BaseModel):
    activo: bool


def _summary(rec: dict) -> dict:
    """Sin los frames (pesan ~100 KB por partida): lo que enseña la lista."""
    frames = rec.get("frames") or {}
    return {
        **{k: v for k, v in rec.items() if k != "frames"},
        "campeon": (frames.get("campeon") or {}).get("nombre"),
        "hay_video": None,
    }


@router.get("")
def recordings(svc: Svc):
    rec = svc.recorder
    items = []
    for r in rec.recordings()[:50]:
        item = _summary(r)
        item["hay_video"] = rec.video_path(r.get("nombre", "")) is not None
        items.append(item)
    return {**rec.status(), "grabaciones": items}


@router.post("/enable")
def enable(body: EnableBody, svc: Svc):
    return svc.recorder.set_enabled(body.activo)


@router.get("/{name}/video", responses={404: {"description": "Ese vídeo ya no está (solo se guardan los más recientes)"}})
def video(name: str, svc: Svc):
    path = svc.recorder.video_path(name)
    if path is None:
        raise HTTPException(404, "Ese vídeo ya no está (solo se guardan los más recientes)")
    return FileResponse(path, media_type="video/mp4")
