"""Analizador de drafts."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field

from app.analytics import draft
from app.analytics.filters import Filters
from app.api.deps import filters_dep, role_param, sources_dep, svc_from

router = APIRouter(prefix="/api/draft", tags=["draft"])


class DraftBody(BaseModel):
    ally: dict[str, str | int | None] = Field(default_factory=dict, description="{rol: campeón}")
    enemy: dict[str, str | int | None] = Field(default_factory=dict)


def _resolve_team(svc, team: dict) -> dict[str, int]:
    out: dict[str, int] = {}
    for role, value in (team or {}).items():
        if value in (None, ""):
            continue
        champ = svc.dd.resolve(value)
        if not champ:
            raise HTTPException(404, f"Campeón desconocido: {value}")
        out[role_param(role)] = champ
    return out


@router.post("/analyze")
def analyze(body: DraftBody, f: Filters = Depends(filters_dep), sources: str = Depends(sources_dep), svc=Depends(svc_from)):
    ally, enemy = _resolve_team(svc, body.ally), _resolve_team(svc, body.enemy)
    if len(set(ally.values()) | set(enemy.values())) < len(ally) + len(enemy):
        raise HTTPException(400, "Un campeón no puede aparecer dos veces en el draft")
    return draft.analyze(svc, f, ally, enemy, sources)


@router.post("/suggest")
def suggest(
    body: DraftBody,
    role: str = Query(...),
    side: str = Query("ally", pattern="^(ally|enemy)$"),
    limit: int = Query(12, ge=1, le=40),
    min_games: int = Query(200, ge=0),
    f: Filters = Depends(filters_dep),
    sources: str = Depends(sources_dep),
    svc=Depends(svc_from),
):
    ally, enemy = _resolve_team(svc, body.ally), _resolve_team(svc, body.enemy)
    return draft.suggest(svc, f, ally, enemy, role_param(role), side, limit, min_games, sources)


@router.get("/backtest")
def get_backtest(svc=Depends(svc_from)):
    return {"alpha": draft.get_alpha(svc), "report": draft.last_backtest(svc)}


@router.post("/backtest")
def run_backtest(sample: int = Query(3000, ge=200, le=20000), sources: str = Depends(sources_dep), svc=Depends(svc_from)):
    return draft.backtest(svc, sample, sources=sources)
