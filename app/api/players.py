"""Perfiles de jugador y análisis de partidas."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query

from app.analytics import matches, players
from app.api.deps import svc_from

router = APIRouter(prefix="/api", tags=["jugadores"])
# Error que documenta la API (OpenAPI) en las rutas de una partida.
MATCH_NOT_FOUND_DOC = {"description": "No se encuentra esa partida"}


@router.get("/players/search")
def search(q: str = Query("", min_length=0), svc=Depends(svc_from)):
    return {"results": players.search_known(svc, q)}


@router.get("/players/{platform}/{game_name}/{tag_line}", responses={404: {"description": "No se encuentra ese jugador"}})
def profile(
    platform: str,
    game_name: str,
    tag_line: str,
    count: int = Query(20, ge=5, le=60),
    queue: str = Query("solo", pattern="^(solo|flex|ranked|all)$"),
    svc=Depends(svc_from),
):
    try:
        return players.get_profile(svc, platform, game_name, tag_line, count, queue)
    except players.NotFound as exc:
        raise HTTPException(404, str(exc)) from exc


@router.get("/matches/{match_id}", responses={404: MATCH_NOT_FOUND_DOC})
def match_detail(match_id: str, svc=Depends(svc_from)):
    try:
        return matches.match_detail(svc, match_id)
    except matches.MatchNotFound as exc:
        raise HTTPException(404, str(exc)) from exc


@router.get("/matches/{match_id}/prediction", responses={404: MATCH_NOT_FOUND_DOC})
def match_prediction(match_id: str, svc=Depends(svc_from)):
    try:
        return {"prediction": matches.match_prediction(svc, match_id)}
    except matches.MatchNotFound as exc:
        raise HTTPException(404, str(exc)) from exc


@router.get("/matches/{match_id}/timeline", responses={404: MATCH_NOT_FOUND_DOC})
def match_timeline(match_id: str, svc=Depends(svc_from)):
    try:
        return matches.match_timeline(svc, match_id)
    except matches.MatchNotFound as exc:
        raise HTTPException(404, str(exc)) from exc
