"""Control del recolector de partidas."""

from __future__ import annotations

import threading
import time

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

# La lista de OTPs de «Datos y API» lanzaba en cada petición la lista de jugadores (hasta 18 s en Windows) y el embudo
# (hasta 22 s): CPU que le faltaba al recolector y lecturas tan largas que el WAL no se podía recortar. Ahora se reutilizan
# un rato (el embudo, el que escribe el recolector cada 5 min).
ROWS_TTL_S = 300
FUNNEL_TTL_S = 360
_heavy_lock = threading.Lock()


def _cached(svc, key, ttl: float, compute):
    """Caché en el propio servicio (``svc._heavy``): cada app (y cada test) la suya."""
    with _heavy_lock:
        store = getattr(svc, "_heavy", None)
        if store is None:
            store = svc._heavy = {}
        hit = store.get(key)
        if hit is not None and time.time() - hit[0] < ttl:
            return hit[1]
    value = compute()
    with _heavy_lock:
        store[key] = (time.time(), value)
    return value


def _cfg_key(cfg) -> str:
    import json
    from dataclasses import asdict

    return json.dumps(asdict(cfg), sort_keys=True, default=str)

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
    patterns: list[dict] = Field(
        default_factory=list,
        description="Búsqueda por partidas: ventanas {kind: matchup|comp, name, a: [{champ, role}], b: [{champ, role}], min_a, min_b}. "
        "Con ventanas, los campeones salen de ellas y el elo no filtra",
    )


@router.get("/status")
def status(svc=Depends(svc_from)):
    return svc.crawler.status()


def _champ_id(svc, value) -> int:
    """Id de un campeón (id, clave o nombre) o 400."""
    champ = svc.dd.resolve(value)
    if not champ:
        raise HTTPException(400, f"Campeón desconocido: {value}")
    return champ


def _resolve_windows(svc, raw: list) -> list[dict]:
    """Las ventanas de la búsqueda por partidas con los campeones como id (admiten nombres)."""
    from app.ingest import patterns as pattern_search

    windows = []
    for w in raw or []:
        if not isinstance(w, dict):
            continue
        w = dict(w)
        for side in ("a", "b"):
            w[side] = [
                {**slot, "champ": _champ_id(svc, slot.get("champ"))}
                for slot in w.get(side) or []
                if isinstance(slot, dict) and slot.get("champ") not in (None, "")
            ]
        windows.append(w)
    if windows and not pattern_search.clean(windows):
        raise HTTPException(400, "Las ventanas de partida no tienen ningún campeón válido (en un matchup, cada campeón con su rol)")
    return windows


@router.post(
    "/start",
    responses={
        400: {"description": "Configuración no válida: campeón desconocido, ventana sin campeones, plataforma o tier"},
        409: {"description": "El recolector ya está en marcha"},
        412: {"description": "Falta configurar la RIOT_API_KEY"},
    },
)
def start(body: CrawlerBody, svc=Depends(svc_from)):
    data = body.model_dump()
    champs = [_champ_id(svc, value) for value in data.pop("champions") or []]
    roles = {_champ_id(svc, value): wanted for value, wanted in (data.pop("champion_roles") or {}).items()}
    windows = _resolve_windows(svc, data.pop("patterns"))
    try:
        return svc.crawler.start(CrawlerConfig(**data, champions=champs, champion_roles=roles, patterns=windows))
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
    summary: bool = Query(False, description="Solo los totales (lo que enseña «Datos y API»), sin la lista por campeón"),
    svc=Depends(svc_from),
):
    """OTPs (o jugadores con mucha maestría) encontrados con la configuración actual: un resumen por campeón
    (``by_champion``) y la lista de jugadores (filtrable y paginada) con sus partidas y cuánto historial se lleva."""
    cfg = svc.crawler.config
    if cfg is None or not cfg.targets:
        return {"champions": [], "focus": None, "players": [], "by_champion": []}
    games_cond, games_params = target_sql(cfg, "pa")
    cap = int(cfg.games_per_otp or 0) if svc.crawler.running and svc.crawler.searching() else 0
    if summary:
        return _otp_summary(svc, cfg, cap)
    # Lo pesado (partidas de cada jugador con cada campeón: ~26 s con 30.000 jugadores en Windows, y la web lo pedía cada
    # ~10 s: 57 veces en 35 min, CPU que le faltaba al recolector, 24-11-2026), una vez cada ``ROWS_TTL_S``.
    heavy = _cached(svc, ("otps", _cfg_key(cfg), cap), ROWS_TTL_S, lambda: _otp_entries(svc, cfg, cap))
    entries, by_champion = heavy["entries"], heavy["by_champion"]

    # Lista de jugadores: filtros, orden y página (sobre una copia: lo de la caché no se toca).
    rows = _filter_players(entries, champ, platform, q)
    rows.sort(key=OTP_SORT_KEYS.get(sort, OTP_SORT_KEYS["games"]))
    page = [dict(e) for e in rows[offset : offset + limit]]
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
        **_search_config(cfg, cap),
        "found": heavy["found"],
        "complete": heavy["complete"],
        # Mientras se buscan más jugadores, de cada uno solo se bajan sus N primeras: cuántos ya las tienen.
        "capped": heavy["capped"],
        "games": heavy["games"],
        "by_champion": by_champion,
        "matched": len(rows),
        "offset": offset,
        "players": page,
        "details": otp_details.live_counts(svc.db) | {"enabled": cfg.save_details} | _files_state(svc),
        "funnel": _funnel(svc, cfg),
    }


OTP_SORT_KEYS = {
    "winrate": lambda e: (-(e["wins"] / e["total"]) if e["total"] >= 5 else 1, -e["total"]),
    "elo": lambda e: (-TIER_INDEX.get(e["tier"] or "", -1), -(e["lp"] or 0), -e["total"]),
    "points": lambda e: (-e["points"], -e["total"]),
    "name": lambda e: ((e["game_name"] or "~").lower(), -e["total"]),
    "games": lambda e: (-e["total"], -e["points"]),
}


def _filter_players(entries: list[dict], champ: int | None, platform: str | None, q: str | None) -> list[dict]:
    rows = list(entries)
    if champ is not None:
        rows = [e for e in rows if e["champ"] == champ]
    if platform:
        rows = [e for e in rows if e["platform"] == platform.lower()]
    needle = (q or "").strip().lower()
    if needle:
        rows = [e for e in rows if needle in f"{e['game_name'] or ''}#{e['tag_line'] or ''}".lower()]
    return rows


def _search_config(cfg, cap: int) -> dict:
    """Lo de la búsqueda que la web enseña junto a los jugadores."""
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
    }


def _otp_summary(svc, cfg, cap: int) -> dict:
    """Los totales de «Datos y API» sin consultas propias: de la cola del recolector (todos los jugadores ya visitados,
    los únicos que pueden tener partidas o el historial completo, y los siguientes por visitar; se rehace sola cada
    minuto en segundo plano) y del embudo que escribe cada 5 min. Antes, cada petición contaba las partidas de cada
    jugador con cada campeón: ~26 s con 30.000 jugadores, pedida cada ~10 s (24-11-2026)."""
    rows, _ = svc.crawler.otp_rows_cached(cfg)
    list_key = otp_list_key(cfg)
    depth_start, _ = otp_depth_start(cfg, getattr(getattr(svc, "dd", None), "version", None))
    states = [otp_history_state(row, list_key, depth_start) for row, _ in rows]
    funnel = _funnel(svc, cfg)
    found = int(((funnel or {}).get("total") or {}).get("active") or 0) or len(rows)
    return {
        **_search_config(cfg, cap),
        "found": found,
        "complete": sum(1 for st in states if st in ("done", "share")),
        "capped": sum(
            1 for (row, _), st in zip(rows, states)
            if cap and st == "loading" and cap_count(cfg, row["hits"], row["seen"]) >= cap
        ),
        "games": sum(int(row["hits"] or 0) for row, _ in rows),
        "by_champion": [],
        "matched": 0,
        "offset": 0,
        "players": [],
        "details": otp_details.live_counts(svc.db) | {"enabled": cfg.save_details} | _files_state(svc),
        "funnel": funnel,
    }


def _target_stats(svc, cfg, ids: list[int]) -> dict[tuple[int, int], dict]:
    """Partidas (y su rol) de cada jugador con cada campeón elegido, en el rol que buscas."""
    target_cond, target_params = target_sql(cfg)
    stats: dict[tuple[int, int], dict] = {}
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
    return stats


def _player_entries(cfg, row, top, history: str, stats: dict) -> list[dict]:
    """Una fila por campeón buscado del jugador, con sus partidas con él."""
    seen = int(row["seen"] or 0)
    points_of = {int(e[0]): int(e[1]) for e in top if len(e) >= 2}
    champs = otp_champs(cfg, top, sourced_champs(row)) or [next((int(e[0]) for e in top if int(e[0]) in cfg.targets), 0)]
    out = []
    for c in champs:
        st = stats.get((row["id"], c), {"games": 0, "wins": 0, "roles": {}})
        out.append({
            "id": row["id"], "puuid": row["puuid"], "platform": row["platform"], "game_name": row["game_name"],
            "tag_line": row["tag_line"], "tier": row["tier"], "division": row["division"], "lp": row["lp"], "champ": c,
            "points": points_of.get(c, 0), "top": top, "checked_at": row["checked_at"], "total": st["games"], "wins": st["wins"],
            "roles": dict(sorted(st["roles"].items(), key=lambda kv: -kv[1])), "history": history,
            "history_checked": int(row["back_offset"] or 0), "share": round(int(row["hits"] or 0) / seen, 3) if seen >= 10 else None,
        })
    return out


def _champion_block(cfg, c: int, mine: list[dict]) -> dict:
    """Resumen de un campeón: jugadores, historiales completos, partidas, roles, servidores, el mejor y los que más juegan."""
    roles: dict[str, int] = {}
    platforms: dict[str, int] = {}
    for e in mine:
        platforms[e["platform"]] = platforms.get(e["platform"], 0) + 1
        for role, n in e["roles"].items():
            roles[role] = roles.get(role, 0) + n
    best = max(mine, key=lambda e: (TIER_INDEX.get(e["tier"] or "", -1), e["lp"] or 0), default=None)
    leaders = sorted(mine, key=lambda e: (-e["total"], -e["points"]))[:3]
    return {
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


def _otp_entries(svc, cfg, cap: int) -> dict:
    """Jugadores encontrados (uno por campeón) con sus partidas y su historial, y el resumen por campeón."""
    # Los de los servidores y elos elegidos (los que se visitan).
    found = _cached(svc, ("rows", _cfg_key(cfg)), ROWS_TTL_S, lambda: otp_rows(svc.db, cfg, cfg.platforms, cfg.tiers))
    list_key = otp_list_key(cfg)
    depth_start, _ = otp_depth_start(cfg, getattr(getattr(svc, "dd", None), "version", None))
    stats = _target_stats(svc, cfg, [row["id"] for row, _ in found])
    states = [otp_history_state(row, list_key, depth_start) for row, _ in found]
    capped = [bool(cap) and st == "loading" and cap_count(cfg, row["hits"], row["seen"]) >= cap for (row, _), st in zip(found, states)]
    entries = []
    for (row, top), st, full in zip(found, states, capped):
        # «capped»: ya tiene sus partidas; el resto de su historial, cuando acabe la búsqueda.
        entries.extend(_player_entries(cfg, row, top, "capped" if full else st, stats))
    # Resumen por campeón (en el orden en que los elegiste; los de una línea, de más a menos jugados).
    by_champ: dict[int, list] = {}
    for e in entries:
        by_champ.setdefault(e["champ"], []).append(e)
    return {
        "entries": entries,
        "by_champion": [_champion_block(cfg, c, by_champ.get(c, [])) for c in cfg.targets],
        "found": len(found),
        "complete": sum(1 for st in states if st in ("done", "share")),
        "capped": sum(capped),
        "games": sum(e["total"] for e in entries),
    }


def _funnel(svc, cfg) -> dict:
    last = getattr(svc.crawler, "last_funnel", None)
    if last is not None and last[0] == _cfg_key(cfg) and time.time() - last[1] < FUNNEL_TTL_S:
        return last[2]
    return _cached(svc, ("funnel", _cfg_key(cfg)), FUNNEL_TTL_S, lambda: search_funnel(svc.db, cfg))


def _files_state(svc) -> dict:
    files = otp_details.Files.of(svc)
    players = otp_details.Files.of(svc, "player")
    from app.autopush import AutoPush

    auto = AutoPush.of(svc)
    push = auto.public() if auto is not None else None
    if files is None:
        return {"path": None, "files": None, "players_path": None, "autopush": push}
    return {"path": str(files.base), "files": files.state, "players_path": str(players.base) if players is not None else None, "autopush": push}


@router.get("/otps/live")
def otps_live(svc=Depends(svc_from)):
    """Lo que cambia cada segundo (partidas analizadas y subida a GitHub), aparte de la lista de OTPs, que tarda más."""
    cfg = svc.crawler.config
    return (
        otp_details.live_counts(svc.db)
        | {"enabled": bool(cfg and cfg.save_details), "session_tiers": svc.crawler.session_tiers()}
        | _files_state(svc)
    )


@router.get("/otps/by-tier")
def otps_by_tier(champ: int = Query(..., description="Id del campeón"), svc=Depends(svc_from)):
    """Partidas analizadas de un campeón por elo (OTPs y resto), para el buscador de la gráfica del total por elo."""
    return {"champ": champ, **otp_details.champ_tier_counts(svc.db, champ)}


@router.post("/otps/push", responses={409: {"description": "La subida automática está desactivada o git no puede subir"}})
def otps_push_now(svc=Depends(svc_from)):
    """Commit «datos» y push de lo recogido ya (o en cola si hay otra subida en marcha). La web usa /api/system/push."""
    from app import autopush

    try:
        return autopush.push_now(svc)
    except autopush.PushUnavailable as exc:
        raise HTTPException(409, str(exc)) from exc


@router.post("/otps/files/rebuild", responses={409: {"description": "Aún no hay partidas analizadas minuto a minuto"}})
def otps_files_rebuild(svc=Depends(svc_from)):
    """Reescribe las carpetas ``otps`` y ``partidas_campeon`` desde la base de datos (normalmente se escriben solas)."""
    files = otp_details.Files.of(svc)
    if files is None or not otp_details.counts(svc.db)["games"]:
        raise HTTPException(409, "Aún no hay partidas analizadas minuto a minuto")
    players = otp_details.Files.of(svc, "player")
    if players is not None:
        players.rebuild_in_background()
    return files.rebuild_in_background()


# ------------------------------------------------------------------ búsqueda por partidas
def _window_stats(rows: list) -> dict:
    """Partidas, ganadas por el equipo A, por lado y por elo, y las iguales (composiciones)."""
    by_side: dict[str, list[int]] = {}
    by_tier: dict[str, list[int]] = {}
    exact = [0, 0]
    for r in rows:
        win = int(r["win_a"])
        for bucket, key in ((by_side, "azul" if r["side_a"] == 100 else "rojo"), (by_tier, r["tier"] or "?")):
            acc = bucket.setdefault(key, [0, 0])
            acc[0] += 1
            acc[1] += win
        if r["exact"]:
            exact[0] += 1
            exact[1] += win
    games = len(rows)
    wins = sum(int(r["win_a"]) for r in rows)
    return {
        "games": games,
        "wins_a": wins,
        "winrate_a": round(100 * wins / games, 1) if games else None,
        "exact": exact[0],
        "exact_wins_a": exact[1],
        "by_side": by_side,
        "by_tier": by_tier,
    }


def _add_lineups(db, games: list[dict]) -> None:
    """Los 10 campeones (por equipo, con su KDA) y la duración de cada partida."""
    ids = [g["match_id"] for g in games]
    marks = ",".join("?" * len(ids))
    lineup: dict[str, dict] = {}
    players_sql = f"SELECT match_id, team, role, champ, kills, deaths, assists FROM participants WHERE match_id IN ({marks})"
    for r in db.query(players_sql, ids):
        lineup.setdefault(r["match_id"], {}).setdefault(str(r["team"]), []).append(
            {"role": r["role"], "champ": r["champ"], "kda": [r["kills"], r["deaths"], r["assists"]]}
        )
    duration_sql = f"SELECT match_id, duration FROM matches WHERE match_id IN ({marks})"
    duration = {r["match_id"]: r["duration"] for r in db.query(duration_sql, ids)}
    for g in games:
        g["teams"] = lineup.get(g["match_id"], {})
        g["duration"] = duration.get(g["match_id"])


def _recent_games(db, rows: list, limit: int) -> list[dict]:
    """Las últimas partidas con los 10 campeones (por equipo) y su duración."""
    recent = [dict(r) for r in rows[:limit]]
    if recent:
        _add_lineups(db, recent)
    return recent


@router.get("/patterns")
def pattern_results(limit: int = Query(12, ge=0, le=100), svc=Depends(svc_from)):
    """Resultados de cada ventana de la búsqueda por partidas: cuántas encajan, cuántas gana el equipo A (por lado y por
    elo; en composiciones, las iguales frente a las parecidas) y las últimas con los 10 campeones."""
    from app.ingest import patterns as pattern_search

    cfg = svc.crawler.config
    out = []
    for w in list(cfg.patterns) if cfg is not None else []:
        rows = svc.db.query(
            "SELECT match_id, side_a, win_a, exact, tier, platform, game_start FROM pattern_games WHERE window_id = ? AND analyzed = 1 "
            "ORDER BY game_start DESC",
            (w["id"],),
        )
        out.append({"window": w, "label": pattern_search.label(w, svc.dd.name), **_window_stats(rows), "recent": _recent_games(svc.db, rows, limit)})
    return {"windows": out, "live": svc.crawler.pattern_report()}
