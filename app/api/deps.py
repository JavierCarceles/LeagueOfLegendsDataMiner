"""Dependencias comunes de los endpoints."""

from __future__ import annotations

from fastapi import HTTPException, Query, Request

from app.analytics.filters import Filters
from app.riot.routing import ROLES


def svc_from(request: Request):
    return request.app.state.svc


def filters_dep(
    request: Request,
    patch: str = Query("all", description="all | recent | latest | last3 | legacy | 16.22,16.23"),
    tier: str = Query("all", description="all | emerald_plus | diamond_plus | master_plus | GOLD,PLATINUM…"),
    platform: str = Query("all", description="all | europe | euw1,kr…"),
) -> Filters:
    return Filters.parse(svc_from(request).db, patch=patch, tier=tier, platform=platform)


def sources_dep(sources: str = Query("all", pattern="^(all|opgg|own)$", description="all = OP.GG + tu BD")) -> str:
    return sources


def role_param(role: str) -> str:
    value = (role or "").upper()
    aliases = {"MIDDLE": "MID", "BOTTOM": "ADC", "BOT": "ADC", "UTILITY": "SUPPORT", "SUP": "SUPPORT", "JG": "JUNGLE"}
    value = aliases.get(value, value)
    if value not in ROLES:
        raise HTTPException(400, f"Rol no válido: {role}")
    return value


def champ_param(svc, value) -> int:
    champ = svc.dd.resolve(value)
    if not champ:
        raise HTTPException(404, f"Campeón desconocido: {value}")
    return champ
