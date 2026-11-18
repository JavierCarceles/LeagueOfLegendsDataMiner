"""Estado y sincronización de las fuentes externas (OP.GG)."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from app.api.deps import champ_param, role_param, svc_from
from app.external.opgg import REGIONS, TIERS

router = APIRouter(prefix="/api/opgg", tags=["op.gg"])


class SyncBody(BaseModel):
    tier: str = "emerald_plus"
    region: str = "global"
    patches: list[str] = Field(default_factory=list, description="Vacío = parche actual y anterior")


@router.get("/status")
def status(svc=Depends(svc_from)):
    opgg = svc.opgg
    return {
        "enabled": opgg.enabled,
        "versions": opgg.versions(allow_fetch=False),
        "tiers": TIERS,
        "regions": [{"id": k, "label": v} for k, v in REGIONS.items()],
        "cache": opgg.cache_summary(),
        "stats": opgg.stats,
        "sync": opgg.sync_state,
    }


@router.post("/sync")
def sync(body: SyncBody, svc=Depends(svc_from)):
    if not svc.opgg.enabled:
        raise HTTPException(412, "OP.GG está desactivado (OPGG_ENABLED=0)")
    if body.tier not in TIERS or body.region not in REGIONS:
        raise HTTPException(400, "Tier o región no válidos")
    versions = svc.opgg.versions()
    patches = [p for p in body.patches if p in versions] or versions[:2]
    try:
        return svc.start_opgg_sync(body.tier, body.region, patches)
    except RuntimeError as exc:
        raise HTTPException(409, str(exc)) from exc


@router.post("/sync/stop")
def stop_sync(svc=Depends(svc_from)):
    svc.opgg_stop.set()
    return svc.opgg.sync_state


@router.get("/guide/{role}/{champ}/{opp}")
def guide(role: str, champ: str, opp: str, svc=Depends(svc_from)):
    data = svc.opgg.matchup_guide(champ_param(svc, champ), champ_param(svc, opp), role_param(role))
    if not data:
        raise HTTPException(404, "OP.GG no tiene guía para este enfrentamiento")
    return data
