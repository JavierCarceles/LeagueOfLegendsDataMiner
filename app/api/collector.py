"""Control del recolector de partidas."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field

from app.api.deps import svc_from
from app.ingest import crawler as crawler_ingest
from app.ingest import otp_details
from app.ingest.crawler import (
    CrawlerConfig,
    cap_count,
    otp_champs,
    otp_depth_start,
    otp_history_state,
    otp_list_key,
    otp_rows,
    search_funnel,
    sourced_champs,
    target_sql,
)
from app.riot.routing import TIER_INDEX

router = APIRouter(prefix="/api/crawler", tags=["recolector"])


class CrawlerBody(BaseModel):
    platforms: list[str] = Field(default_factory=lambda: ["euw1", "kr", "na1"])
    tiers: list[str] = Field(default_factory=lambda: ["EMERALD", "DIAMOND", "MASTER", "GRANDMASTER", "CHALLENGER"])
    include_flex: bool = False
    matches_per_player: int = 20
    recrawl_hours: float = 12.0
    champions: list[int | str] = Field(default_factory=list, description="Vacío (y sin líneas) = todo el ranking; con campeones, solo OTPs / maestría alta")
    roles: list[str] = Field(default_factory=list, description="Líneas completas: OTPs de todos los campeones que se juegan en ellas")
    focus: str = Field("otp", pattern="^(otp|mastery|games)$", description="otp, mastery o games (partidas de un campeón, sea OTP o no)")
    min_mastery: int = 300_000
    min_share: float = Field(0.5, ge=0, le=1, description="Especialistas (otp): parte mínima de sus partidas con ese campeón (0, 0.25, 0.5 o 0.75)")
    only_target_games: bool = True
    otp_depth: str = Field("season", pattern="^(30|90|180|season|365|all)$", description="Periodo: cuenta quien jugó el campeón en él y se bajan sus partidas de ese periodo")
    games_per_otp: int = Field(30, ge=0, le=1000, description="Partidas por OTP mientras se buscan más (0 = sin tope)")
    champion_roles: dict[str, list[str]] = Field(default_factory=dict, description="Rol buscado de cada campeón (id o nombre → roles); sin entrada = cualquiera")


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
    roles = {}
    for value, wanted in (data.pop("champion_roles") or {}).items():
        champ = svc.dd.resolve(value)
        if not champ:
            raise HTTPException(400, f"Campeón desconocido: {value}")
        roles[champ] = wanted
    try:
        return svc.crawler.start(CrawlerConfig(**data, champions=champs, champion_roles=roles))
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    except PermissionError as exc:
        raise HTTPException(412, str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(409, str(exc)) from exc


@router.get("/role-pool")
def role_pool_preview(roles: str = Query(..., description="Líneas separadas por comas: TOP,JUNGLE…"), svc=Depends(svc_from)):
    """Campeones que entrarían buscando OTPs de toda una línea (de más a menos jugados)."""
    wanted = [r.strip().upper() for r in roles.split(",") if r.strip()]
    return {"roles": wanted, "champions": crawler_ingest.role_pool(svc, wanted)}


@router.post("/stop")
def stop(svc=Depends(svc_from)):
    return svc.crawler.stop()


OTP_SORTS = ("games", "winrate", "elo", "points", "name")


@router.get("/otps")
def otps(
    limit: int = Query(60, ge=1, le=300),
    offset: int = Query(0, ge=0),
    champ: int | None = Query(None, description="Solo los jugadores de este campeón"),
    platform: str | None = Query(None),
    q: str | None = Query(None, max_length=40, description="Busca en el Riot ID"),
    sort: str = Query("games", pattern="^(games|winrate|elo|points|name)$"),
    svc=Depends(svc_from),
):
    """OTPs (o jugadores con mucha maestría) encontrados con la configuración actual: un resumen por campeón
    (``by_champion``) y la lista de jugadores (filtrable y paginada) con sus partidas y cuánto historial se lleva."""
    cfg = svc.crawler.config
    if cfg is None or not cfg.targets:
        return {"champions": [], "focus": None, "players": [], "by_champion": []}
    target_cond, target_params = target_sql(cfg)
    games_cond, games_params = target_sql(cfg, "pa")
    found = otp_rows(svc.db, cfg, cfg.platforms, cfg.tiers)  # los de los servidores y elos elegidos (los que se visitan)
    cap = int(cfg.games_per_otp or 0) if svc.crawler.running and svc.crawler.searching() else 0
    list_key = otp_list_key(cfg)
    depth_start, _ = otp_depth_start(cfg, getattr(getattr(svc, "dd", None), "version", None))

    # Partidas (y su rol) de cada jugador con cada campeón elegido, en el rol que buscas.
    stats: dict[tuple[int, int], dict] = {}
    ids = [row["id"] for row, _ in found]
    for i in range(0, len(ids), 400):
        chunk = ids[i : i + 400]
        for r in svc.db.query(
            f"SELECT player_id, champ, role, COUNT(*) AS games, SUM(win) AS wins FROM participants "
            f"WHERE player_id IN ({','.join('?' * len(chunk))}) AND {target_cond} GROUP BY player_id, champ, role",
            (*chunk, *target_params),
        ):
            st = stats.setdefault((r["player_id"], int(r["champ"])), {"games": 0, "wins": 0, "roles": {}})
            st["games"] += int(r["games"] or 0)
            st["wins"] += int(r["wins"] or 0)
            if r["role"]:
                st["roles"][r["role"]] = st["roles"].get(r["role"], 0) + int(r["games"] or 0)

    entries = []
    for row, top in found:
        history = otp_history_state(row, list_key, depth_start)
        if history == "loading" and cap and cap_count(cfg, row["hits"], row["seen"]) >= cap:
            history = "capped"  # ya tiene sus partidas; el resto de su historial, cuando acabe la búsqueda
        seen = int(row["seen"] or 0)
        points_of = {int(e[0]): int(e[1]) for e in top if len(e) >= 2}
        for c in otp_champs(cfg, top, sourced_champs(row)) or [next((int(e[0]) for e in top if int(e[0]) in cfg.targets), 0)]:
            st = stats.get((row["id"], c), {"games": 0, "wins": 0, "roles": {}})
            entries.append(
                {
                    "id": row["id"],
                    "puuid": row["puuid"],
                    "platform": row["platform"],
                    "game_name": row["game_name"],
                    "tag_line": row["tag_line"],
                    "tier": row["tier"],
                    "division": row["division"],
                    "lp": row["lp"],
                    "champ": c,
                    "points": points_of.get(c, 0),
                    "top": top,
                    "checked_at": row["checked_at"],
                    "total": st["games"],
                    "wins": st["wins"],
                    "roles": dict(sorted(st["roles"].items(), key=lambda kv: -kv[1])),
                    "history": history,
                    "history_checked": int(row["back_offset"] or 0),
                    "share": round(int(row["hits"] or 0) / seen, 3) if seen >= 10 else None,
                }
            )

    # Resumen por campeón (en el orden en que los elegiste; los de una línea, de más a menos jugados).
    by_champion = []
    for c in cfg.targets:
        mine = [e for e in entries if e["champ"] == c]
        roles: dict[str, int] = {}
        for e in mine:
            for role, n in e["roles"].items():
                roles[role] = roles.get(role, 0) + n
        platforms: dict[str, int] = {}
        for e in mine:
            platforms[e["platform"]] = platforms.get(e["platform"], 0) + 1
        best = max(mine, key=lambda e: (TIER_INDEX.get(e["tier"] or "", -1), e["lp"] or 0), default=None)
        leaders = sorted(mine, key=lambda e: (-e["total"], -e["points"]))[:3]
        by_champion.append(
            {
                "champ": c,
                "roles_wanted": cfg.roles_for(c) or [],
                "found": len(mine),
                "with_games": sum(1 for e in mine if e["total"]),
                "complete": sum(1 for e in mine if e["history"] in ("done", "share")),
                "games": sum(e["total"] for e in mine),
                "wins": sum(e["wins"] for e in mine),
                "roles": dict(sorted(roles.items(), key=lambda kv: -kv[1])),
                "platforms": dict(sorted(platforms.items(), key=lambda kv: -kv[1])),
                "best": {k: best[k] for k in ("game_name", "tag_line", "platform", "tier", "division", "lp")} if best else None,
                "leaders": [{k: e[k] for k in ("game_name", "tag_line", "platform", "tier", "lp", "total", "wins")} for e in leaders],
            }
        )

    # Lista de jugadores: filtros, orden y página.
    rows = entries
    if champ is not None:
        rows = [e for e in rows if e["champ"] == champ]
    if platform:
        rows = [e for e in rows if e["platform"] == platform.lower()]
    if q and q.strip():
        needle = q.strip().lower()
        rows = [e for e in rows if needle in f"{e['game_name'] or ''}#{e['tag_line'] or ''}".lower()]
    if sort == "winrate":
        rows.sort(key=lambda e: (-(e["wins"] / e["total"]) if e["total"] >= 5 else 1, -e["total"]))
    elif sort == "elo":
        rows.sort(key=lambda e: (-TIER_INDEX.get(e["tier"] or "", -1), -(e["lp"] or 0), -e["total"]))
    elif sort == "points":
        rows.sort(key=lambda e: (-e["points"], -e["total"]))
    elif sort == "name":
        rows.sort(key=lambda e: ((e["game_name"] or "~").lower(), -e["total"]))
    else:
        rows.sort(key=lambda e: (-e["total"], -e["points"]))
    matched = len(rows)
    page = rows[offset : offset + limit]
    for p in page:
        games = svc.db.query(
            f"SELECT pa.match_id, pa.champ, pa.role, pa.win, pa.kills, pa.deaths, pa.assists, ma.game_start, ma.duration "
            f"FROM participants pa JOIN matches ma ON ma.match_id = pa.match_id "
            f"WHERE pa.player_id = ? AND pa.champ = ? AND {games_cond} ORDER BY ma.game_start DESC LIMIT 8",
            (p.pop("id"), p["champ"], *games_params),
        )
        p.pop("puuid", None)
        p["games"] = [dict(g) for g in games]
    return {
        "champions": cfg.targets,
        "champion_roles": {str(c): r for c, r in cfg.champion_roles.items()},
        "lines": cfg.roles,
        "focus": cfg.focus,
        "min_mastery": cfg.min_mastery,
        "min_share": cfg.min_share,
        "otp_depth": cfg.otp_depth,
        "games_per_otp": cfg.games_per_otp,
        "searching": bool(cap),
        "found": len(found),
        "complete": sum(1 for row, _ in found if otp_history_state(row, list_key, depth_start) in ("done", "share")),
        # Mientras se buscan más jugadores, de cada uno solo se bajan sus N primeras: cuántos ya las tienen.
        "capped": sum(
            1 for row, _ in found
            if cap and otp_history_state(row, list_key, depth_start) == "loading" and cap_count(cfg, row["hits"], row["seen"]) >= cap
        ),
        "games": sum(e["total"] for e in entries),
        "by_champion": by_champion,
        "matched": matched,
        "offset": offset,
        "players": page,
        "details": otp_details.counts(svc.db) | {"enabled": cfg.save_details} | _files_state(svc),
        "funnel": search_funnel(svc.db, cfg),
    }


def _files_state(svc) -> dict:
    files = otp_details.Files.of(svc)
    players = otp_details.Files.of(svc, "player")
    from app.autopush import AutoPush

    auto = AutoPush.of(svc)
    push = auto.public() if auto is not None else None
    if files is None:
        return {"path": None, "files": None, "players_path": None, "autopush": push}
    return {"path": str(files.base), "files": files.state, "players_path": str(players.base) if players is not None else None, "autopush": push}


@router.post("/otps/push")
def otps_push_now(svc=Depends(svc_from)):
    """«Subir ahora»: commit «datos» y push de lo recogido, sin esperar a las 500 partidas."""
    from app.autopush import AutoPush

    auto = AutoPush.of(svc)
    if auto is None or not auto.public().get("enabled"):
        raise HTTPException(409, "La subida automática está desactivada (AUTO_PUSH_GAMES=0).")
    if auto.state.get("git_ok") is False:
        raise HTTPException(409, f"No se puede subir: {auto.state.get('git')}")
    started = auto.start()
    return {"started": started, "autopush": auto.public()}


@router.post("/otps/files/rebuild")
def otps_files_rebuild(svc=Depends(svc_from)):
    """Reescribe las carpetas ``otps`` y ``partidas_campeon`` desde la base de datos (normalmente se escriben solas)."""
    files = otp_details.Files.of(svc)
    if files is None or not otp_details.counts(svc.db)["games"]:
        raise HTTPException(409, "Aún no hay partidas analizadas minuto a minuto")
    players = otp_details.Files.of(svc, "player")
    if players is not None:
        players.rebuild_in_background()
    return files.rebuild_in_background()
