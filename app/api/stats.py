"""Campeones, matchups y regiones."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query

from app.analytics import champions, matchups, regions
from app.analytics.filters import Filters
from app.api.deps import champ_param, filters_dep, role_param, sources_dep, svc_from
from app.riot.routing import PLATFORMS, ROLES

router = APIRouter(prefix="/api", tags=["estadísticas"])


@router.get("/champions")
def tier_list(
    role: str = Query("TOP"),
    min_games: int | None = Query(None, ge=0),
    f: Filters = Depends(filters_dep),
    sources: str = Depends(sources_dep),
    svc=Depends(svc_from),
):
    return champions.tier_list(svc, f, role_param(role), min_games, sources)


@router.get("/champions/{champ}")
def champion(
    champ: str,
    role: str | None = None,
    f: Filters = Depends(filters_dep),
    sources: str = Depends(sources_dep),
    svc=Depends(svc_from),
):
    return champions.champion_detail(svc, f, champ_param(svc, champ), role_param(role) if role else None, sources)


@router.get("/matchups/{role}/{champ}")
def matchup_list(
    role: str,
    champ: str,
    min_games: int = Query(0, ge=0),
    sort: str = Query("wr_adj"),
    f: Filters = Depends(filters_dep),
    sources: str = Depends(sources_dep),
    svc=Depends(svc_from),
):
    return matchups.matchup_list(svc, f, role_param(role), champ_param(svc, champ), min_games, sort, sources)


@router.get("/matchups/{role}/{champ}/{opp}")
def matchup_detail(
    role: str,
    champ: str,
    opp: str,
    f: Filters = Depends(filters_dep),
    sources: str = Depends(sources_dep),
    svc=Depends(svc_from),
):
    return matchups.matchup_detail(svc, f, role_param(role), champ_param(svc, champ), champ_param(svc, opp), sources)


@router.get("/top-matchups")
def top_matchups(
    guide: str = Query("any", pattern="^(any|with|without)$"),
    limit: int = Query(12, ge=1, le=50),
    f: Filters = Depends(filters_dep),
    svc=Depends(svc_from),
):
    return {role: matchups.top_matchups(svc, f, role, limit, guide) for role in ROLES}


@router.get("/regions")
def regions_overview(f: Filters = Depends(filters_dep), svc=Depends(svc_from)):
    return regions.overview(svc, f)


@router.get("/regions/{target}", responses={404: {"description": "Plataforma desconocida"}})
def region_detail(target: str, f: Filters = Depends(filters_dep), svc=Depends(svc_from)):
    """``target``: una plataforma (euw1, kr…) o ``all``. El filtro global de región se ignora aquí."""
    target = target.lower()
    if target != "all" and target not in PLATFORMS:
        raise HTTPException(404, "Plataforma desconocida")
    return regions.region_detail(svc, f, target)
