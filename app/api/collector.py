"""Control del recolector de partidas."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
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


@router.get("/status")
def status(svc=Depends(svc_from)):
    return svc.crawler.status()


@router.post("/start")
def start(body: CrawlerBody, svc=Depends(svc_from)):
    try:
        return svc.crawler.start(CrawlerConfig(**body.model_dump()))
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    except PermissionError as exc:
        raise HTTPException(412, str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(409, str(exc)) from exc


@router.post("/stop")
def stop(svc=Depends(svc_from)):
    return svc.crawler.stop()
