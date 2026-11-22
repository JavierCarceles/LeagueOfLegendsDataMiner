"""Control del recolector de partidas."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field

from app.api.deps import svc_from
from app.ingest import otp_details
from app.ingest.crawler import CrawlerConfig, otp_depth_start, otp_history_state, otp_list_key, otp_rows, target_sql
from app.riot.routing import TIER_INDEX

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
    save_details: bool = Field(True, description="Guardar micro y macro (timeline) de cada partida de los OTPs")
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


@router.post("/stop")
def stop(svc=Depends(svc_from)):
    return svc.crawler.stop()


OTP_SORTS = ("games", "winrate", "elo", "points", "name")


def _otp_champs(cfg: CrawlerConfig, top: list) -> list[int]:
    """Campeones elegidos por los que cuenta este jugador: su principal (OTP) o los de su top 3 con maestría alta."""
    targets = set(cfg.champions)
    entries = [(int(e[0]), int(e[1])) for e in top if len(e) >= 2]
    if cfg.focus == "otp":
        return [entries[0][0]] if entries and entries[0][0] in targets else []
    return list(dict.fromkeys(c for c, pts in entries if c in targets and pts >= cfg.min_mastery))


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
    if cfg is None or not cfg.champions:
        return {"champions": [], "focus": None, "players": [], "by_champion": []}
    target_cond, target_params = target_sql(cfg)
    games_cond, games_params = target_sql(cfg, "pa")
    found = otp_rows(svc.db, cfg, cfg.platforms, cfg.tiers)  # los de los servidores y elos elegidos (los que se visitan)
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
        seen = int(row["seen"] or 0)
        points_of = {int(e[0]): int(e[1]) for e in top if len(e) >= 2}
        for c in _otp_champs(cfg, top) or [next((int(e[0]) for e in top if int(e[0]) in cfg.champions), 0)]:
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

    # Resumen por campeón (en el orden en que los elegiste).
    by_champion = []
    for c in cfg.champions:
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
                "roles_wanted": cfg.champion_roles.get(c, []),
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
        "champions": cfg.champions,
        "champion_roles": {str(c): r for c, r in cfg.champion_roles.items()},
        "focus": cfg.focus,
        "min_mastery": cfg.min_mastery,
        "otp_depth": cfg.otp_depth,
        "found": len(found),
        "complete": sum(1 for row, _ in found if otp_history_state(row, list_key, depth_start) in ("done", "share")),
        "games": sum(e["total"] for e in entries),
        "by_champion": by_champion,
        "matched": matched,
        "offset": offset,
        "players": page,
        "details": otp_details.counts(svc.db) | {"enabled": cfg.save_details} | _files_state(svc),
    }


def _files_state(svc) -> dict:
    files = otp_details.Files.of(svc)
    return {"path": str(files.base), "files": files.state} if files is not None else {"path": None, "files": None}


@router.post("/otps/files/rebuild")
def otps_files_rebuild(svc=Depends(svc_from)):
    """Reescribe la carpeta ``otps`` desde la base de datos (normalmente no hace falta: se escribe sola)."""
    files = otp_details.Files.of(svc)
    if files is None or not otp_details.counts(svc.db)["games"]:
        raise HTTPException(409, "Aún no hay partidas de OTPs con micro y macro guardados")
    return files.rebuild_in_background()
