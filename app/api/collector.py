"""Control del recolector de partidas."""

from __future__ import annotations

import json

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field

from app.api.deps import svc_from
from app.ingest.crawler import CrawlerConfig

router = APIRouter(prefix="/api/crawler", tags=["recolector"])


class CrawlerBody(BaseModel):
    platforms: list[str] = Field(default_factory=lambda: ["euw1", "kr", "na1"])
    tiers: list[str] = Field(default_factory=lambda: ["EMERALD", "DIAMOND", "MASTER", "GRANDMASTER", "CHALLENGER"])
    include_flex: bool = False
    days: int = 14
    matches_per_player: int = 20
    recrawl_hours: float = 12.0
    champions: list[int | str] = Field(default_factory=list, description="Vacío = todo el ranking; con campeones, solo OTPs / maestría alta")
    focus: str = Field("otp", pattern="^(otp|mastery)$")
    min_mastery: int = 300_000
    only_target_games: bool = True


@router.get("/status")
def status(svc=Depends(svc_from)):
    return svc.crawler.status()


@router.post("/start")
def start(body: CrawlerBody, svc=Depends(svc_from)):
    data = body.model_dump()
    champs = []
    for value in data.pop("champions") or []:
        champ = svc.dd.resolve(value)
        if not champ:
            raise HTTPException(400, f"Campeón desconocido: {value}")
        champs.append(champ)
    try:
        return svc.crawler.start(CrawlerConfig(**data, champions=champs))
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    except PermissionError as exc:
        raise HTTPException(412, str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(409, str(exc)) from exc


@router.post("/stop")
def stop(svc=Depends(svc_from)):
    return svc.crawler.stop()


@router.get("/otps")
def otps(limit: int = Query(60, ge=1, le=300), svc=Depends(svc_from)):
    """OTPs (o jugadores con mucha maestría) encontrados con la configuración actual y sus partidas guardadas."""
    cfg = svc.crawler.config
    if cfg is None or not cfg.champions:
        return {"champions": [], "focus": None, "players": []}
    targets = set(cfg.champions)
    marks = ",".join("?" * len(targets))
    rows = svc.db.query(
        "SELECT m.puuid, m.platform, m.top, m.checked_at, p.id, p.game_name, p.tag_line, p.tier, p.division, p.lp "
        "FROM mastery_checks m JOIN players p ON p.puuid = m.puuid"
    )
    players = []
    for r in rows:
        try:
            top = json.loads(r["top"])
        except (TypeError, ValueError):
            continue
        if not cfg.qualifies(top):
            continue
        # Entradas [campeón, puntos] o [campeón, puntos, última partida]: se aceptan ambas.
        champ, points = next(((int(e[0]), int(e[1])) for e in top if len(e) >= 2 and int(e[0]) in targets), (None, 0))
        games = svc.db.query(
            f"SELECT pa.match_id, pa.champ, pa.win, pa.kills, pa.deaths, pa.assists, ma.game_start, ma.duration "
            f"FROM participants pa JOIN matches ma ON ma.match_id = pa.match_id "
            f"WHERE pa.player_id = ? AND pa.champ IN ({marks}) ORDER BY ma.game_start DESC LIMIT 8",
            (r["id"], *targets),
        )
        players.append(
            {
                "platform": r["platform"],
                "game_name": r["game_name"],
                "tag_line": r["tag_line"],
                "tier": r["tier"],
                "division": r["division"],
                "lp": r["lp"],
                "champ": champ,
                "points": points,
                "top": top,
                "checked_at": r["checked_at"],
                "games": [dict(g) for g in games],
            }
        )
    players.sort(key=lambda x: (-len(x["games"]), -x["points"]))
    return {"champions": cfg.champions, "focus": cfg.focus, "min_mastery": cfg.min_mastery, "players": players[:limit]}
