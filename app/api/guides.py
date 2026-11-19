"""Guías de matchup guardadas y amigos del cliente."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException

from app import lcu
from app.api.deps import champ_param, role_param, svc_from

router = APIRouter(prefix="/api", tags=["guías"])


@router.get("/guides")
def tree(svc=Depends(svc_from)):
    return svc.guides.tree()


@router.get("/guides/{role}/{champ}/{opp}")
def get_guide(role: str, champ: str, opp: str, svc=Depends(svc_from)):
    guide = svc.guides.find(role_param(role), champ_param(svc, champ), champ_param(svc, opp))
    if not guide:
        raise HTTPException(404, "No hay guía para este enfrentamiento")
    return guide


@router.get("/lcu/friends")
def friends():
    try:
        return {"friends": lcu.friends()}
    except lcu.LCUError as exc:
        raise HTTPException(404, str(exc)) from exc
