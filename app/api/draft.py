"""Analizador de drafts."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field

from app.analytics import draft
from app.analytics.filters import Filters
from app.api.deps import filters_dep, role_param, sources_dep, svc_from
from app.riot.routing import ROLES

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


class Slot(BaseModel):
    side: str = Field("ally", pattern="^(ally|enemy)$")
    role: str


class BatchBody(DraftBody):
    slots: list[Slot] = Field(default_factory=list, max_length=10)


@router.post("/suggest-batch")
def suggest_batch(
    body: BatchBody,
    limit: int = Query(8, ge=1, le=40),
    min_games: int = Query(200, ge=0),
    f: Filters = Depends(filters_dep),
    sources: str = Depends(sources_dep),
    svc=Depends(svc_from),
):
    """Recomendaciones de varios huecos de una vez (las que el draft enseña sin pulsar nada).

    Si tienes perfil guardado incluye tu pool: los campeones que juegas en
    cada rol de tus huecos aliados, con partidas y WR de tus partidas analizadas.
    """
    from app.coach import store

    ally, enemy = _resolve_team(svc, body.ally), _resolve_team(svc, body.enemy)
    slots = [(s.side, role_param(s.role)) for s in body.slots] or [("ally", r) for r in ROLES if r not in ally]
    result = draft.suggest_many(svc, f, ally, enemy, slots, limit, min_games, sources)
    profile = store.get_profile(svc.db)
    puuids = store.known_puuids(profile)
    pool = {}
    for side, role in slots:
        if side == "ally" and puuids:
            pool[role] = store.champion_pool(svc.db, puuids, role)[:10]
    result["pool"] = pool
    return result


@router.get("/backtest")
def get_backtest(svc=Depends(svc_from)):
    return {"alpha": draft.get_alpha(svc), "report": draft.last_backtest(svc)}


@router.post("/backtest")
def run_backtest(sample: int = Query(3000, ge=200, le=20000), sources: str = Depends(sources_dep), svc=Depends(svc_from)):
    return draft.backtest(svc, sample, sources=sources)
