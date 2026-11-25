"""Recolector de vídeos de YouTube: añadir enlaces o búsquedas, ver la cola y pausar."""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field

from app.api.deps import svc_from
from app.videos.collector import ROLES, TOPICS

router = APIRouter(prefix="/api/videos", tags=["vídeos"])
Svc = Annotated[Any, Depends(svc_from)]
NOT_FOUND = "Ese vídeo no está en la cola"


class AddBody(BaseModel):
    texto: str = Field("", max_length=2000, description="Enlace de vídeo, lista o canal, o lo que buscar")
    rol: str = ""
    tema: str = ""
    campeon: str = Field("", max_length=40)
    idiomas: list[str] = Field(default_factory=lambda: ["es", "en"])
    por_idioma: int = Field(10, ge=1, le=100, description="Resultados por búsqueda e idioma")
    limite: int | None = Field(50, ge=1, le=5000, description="Vídeos como mucho de un canal o lista (None = todos)")
    min_minutos: float = Field(3, ge=0, le=180)


class PauseBody(BaseModel):
    pausado: bool


@router.get("/status")
def status(svc: Svc):
    return {**svc.videos.status(), "roles": list(ROLES), "temas": list(TOPICS)}


@router.get("/list")
def videos(svc: Svc, estado: str = Query("", max_length=20), limit: int = Query(300, ge=1, le=5000)):
    return {"videos": svc.videos.store.videos(estado or None, limit), "fuentes": svc.videos.store.sources()[:100]}


@router.post("/add")
def add(body: AddBody, svc: Svc):
    langs = tuple(lang for lang in body.idiomas if lang in ("es", "en")) or ("es", "en")
    sources = svc.videos.add(body.texto, body.rol, body.tema, body.campeon, langs, body.por_idioma, body.limite, body.min_minutos)
    return {"fuentes": sources}


@router.post("/pause")
def pause(body: PauseBody, svc: Svc):
    svc.videos.set_paused(body.pausado)
    return svc.videos.status()


@router.post("/{vid}/retry", responses={404: {"description": NOT_FOUND}})
def retry(vid: str, svc: Svc):
    if not svc.videos.store.retry(vid):
        raise HTTPException(404, NOT_FOUND)
    svc.videos.start()
    return {"ok": True}


@router.delete("/{vid}", responses={404: {"description": NOT_FOUND}})
def remove(vid: str, svc: Svc):
    if not svc.videos.store.remove(vid):
        raise HTTPException(404, NOT_FOUND)
    return {"ok": True}


@router.get("/{vid}", responses={404: {"description": "Todavía no hay datos de ese vídeo"}})
def collected(vid: str, svc: Svc):
    data = svc.videos.store.load_result(vid)
    if data is None:
        raise HTTPException(404, "Todavía no hay datos de ese vídeo")
    return data


@router.post("/push")
def push(svc: Svc):
    """Sube ya lo recogido a GitHub (con el resto de ``data/``)."""
    from app import autopush

    auto = autopush.AutoPush.of(svc)
    if auto is None:
        raise ValueError("La subida automática no está disponible")
    return {"started": auto.start()}
