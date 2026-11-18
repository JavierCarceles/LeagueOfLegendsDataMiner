"""Guías de matchup, IA, vídeos de YouTube y amigos del cliente."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from app import lcu
from app.analytics import matchups
from app.analytics.filters import Filters
from app.api.deps import champ_param, role_param, svc_from
from app.coaching.gemini import GuideError, generate_matchup_guide

router = APIRouter(prefix="/api", tags=["guías"])


class GuideBody(BaseModel):
    role: str
    champ: str | int
    opp: str | int


class UrlBody(BaseModel):
    url: str


@router.get("/guides")
def tree(svc=Depends(svc_from)):
    return svc.guides.tree()


@router.get("/guides/{role}/{champ}/{opp}")
def get_guide(role: str, champ: str, opp: str, svc=Depends(svc_from)):
    guide = svc.guides.find(role_param(role), champ_param(svc, champ), champ_param(svc, opp))
    if not guide:
        raise HTTPException(404, "No hay guía para este enfrentamiento")
    return guide


@router.post("/guides/generate")
def generate(body: GuideBody, svc=Depends(svc_from)):
    role = role_param(body.role)
    champ, opp = champ_param(svc, body.champ), champ_param(svc, body.opp)
    detail = matchups.matchup_detail(svc, Filters(), role, champ, opp)
    try:
        return generate_matchup_guide(svc, role, champ, opp, detail)
    except GuideError as exc:
        raise HTTPException(503, str(exc)) from exc


@router.post("/coaching/youtube")
def youtube(body: UrlBody, svc=Depends(svc_from)):
    if not body.url.strip().startswith(("http://", "https://")):
        raise HTTPException(400, "Introduce una URL de YouTube")
    try:
        return svc.youtube.start(body.url.strip())
    except RuntimeError as exc:
        raise HTTPException(412, str(exc)) from exc


@router.get("/coaching/youtube")
def youtube_jobs(svc=Depends(svc_from)):
    return {"jobs": svc.youtube.list()}


@router.get("/lcu/friends")
def friends():
    try:
        return {"friends": lcu.friends()}
    except lcu.LCUError as exc:
        raise HTTPException(404, str(exc)) from exc
