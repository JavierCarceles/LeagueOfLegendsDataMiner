"""Control del recolector de partidas."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field

from app.api.deps import svc_from
from app.ingest.crawler import CrawlerConfig, otp_depth_start, otp_history_state, otp_list_key, otp_rows

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
    otp_depth: str = Field("season", pattern="^(season|90|180|365|all)$", description="Historial de cada OTP: temporada, N días o todo")


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
    """OTPs (o jugadores con mucha maestría) encontrados con la configuración actual, sus partidas guardadas y cuánto
    historial se lleva bajado de cada uno."""
    cfg = svc.crawler.config
    if cfg is None or not cfg.champions:
        return {"champions": [], "focus": None, "players": []}
    targets = list(cfg.champions)
    marks = ",".join("?" * len(targets))
    found = otp_rows(svc.db, cfg, cfg.platforms, cfg.tiers)  # los de los servidores y elos elegidos (los que se visitan)
    list_key = otp_list_key(cfg)
    depth_start, _ = otp_depth_start(cfg, getattr(getattr(svc, "dd", None), "version", None))
    totals: dict[int, tuple[int, int]] = {}
    ids = [row["id"] for row, _ in found]
    for i in range(0, len(ids), 400):
        chunk = ids[i : i + 400]
        for r in svc.db.query(
            f"SELECT player_id, COUNT(*) AS games, SUM(win) AS wins FROM participants "
            f"WHERE player_id IN ({','.join('?' * len(chunk))}) AND champ IN ({marks}) GROUP BY player_id",
            (*chunk, *targets),
        ):
            totals[r["player_id"]] = (int(r["games"] or 0), int(r["wins"] or 0))
    players = []
    for row, top in found:
        # Entradas [campeón, puntos] o [campeón, puntos, última partida]: se aceptan ambas.
        champ, points = next(((int(e[0]), int(e[1])) for e in top if len(e) >= 2 and int(e[0]) in targets), (None, 0))
        games, wins = totals.get(row["id"], (0, 0))
        history = otp_history_state(row, list_key, depth_start)
        seen = int(row["seen"] or 0)
        players.append(
            {
                "id": row["id"],
                "platform": row["platform"],
                "game_name": row["game_name"],
                "tag_line": row["tag_line"],
                "tier": row["tier"],
                "division": row["division"],
                "lp": row["lp"],
                "champ": champ,
                "points": points,
                "top": top,
                "checked_at": row["checked_at"],
                "total": games,
                "wins": wins,
                "history": history,
                "history_checked": int(row["back_offset"] or 0),
                "share": round(int(row["hits"] or 0) / seen, 3) if seen >= 10 else None,
            }
        )
    players.sort(key=lambda x: (-x["total"], -x["points"]))
    complete = sum(1 for p in players if p["history"] in ("done", "share"))
    games_total = sum(p["total"] for p in players)
    players = players[:limit]
    for p in players:
        games = svc.db.query(
            f"SELECT pa.match_id, pa.champ, pa.win, pa.kills, pa.deaths, pa.assists, ma.game_start, ma.duration "
            f"FROM participants pa JOIN matches ma ON ma.match_id = pa.match_id "
            f"WHERE pa.player_id = ? AND pa.champ IN ({marks}) ORDER BY ma.game_start DESC LIMIT 8",
            (p.pop("id"), *targets),
        )
        p["games"] = [dict(g) for g in games]
    return {
        "champions": cfg.champions,
        "focus": cfg.focus,
        "min_mastery": cfg.min_mastery,
        "otp_depth": cfg.otp_depth,
        "found": len(found),
        "complete": complete,
        "games": games_total,
        "players": players,
    }
