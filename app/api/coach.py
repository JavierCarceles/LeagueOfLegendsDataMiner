"""Tu perfil, el seguimiento automático de tus partidas y tus propósitos."""

from __future__ import annotations

import threading
import time

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field

from app import lcu
from app.api.deps import role_param, svc_from
from app.coach import champ_checks, insights, learn, navgrid, otp_compare, store
from app.coach.analyzer import rift_of
from app.coach.elo_profile import EloProfile
from app.coach.benchmarks import METRICS, TARGET_TIERS, References, next_target, normalize_tier, public_metrics
from app.coach.tracker import ProfileNotFound, build_digest, refresh_digests, resolve_account, skill_times
from app.logbus import get_logger
from app.riot.client import RiotAPIError, RiotKeyError
from app.riot.routing import PLATFORMS, QUEUES

router = APIRouter(prefix="/api", tags=["progreso"])
log = get_logger("progreso")
_fetching: set[tuple[int, str]] = set()
HABITS_GAMES = 100  # partidas recientes (de cualquier cola y rol) para el tilt, las sesiones y el pool
GAME_NOT_FOUND = "Esa partida no está entre tus partidas analizadas"
GOAL_NOT_FOUND = "Ese propósito no existe"
# Errores que documenta la API (OpenAPI) en las rutas que los devuelven.
NO_PROFILE_DOC = {"description": "Aún no has guardado tu perfil en «Mi progreso»"}
GAME_NOT_FOUND_DOC = {"description": GAME_NOT_FOUND}
GOAL_NOT_FOUND_DOC = {"description": GOAL_NOT_FOUND}


class ProfileBody(BaseModel):
    riot_id: str | None = Field(None, description="Nombre#TAG")
    game_name: str | None = None
    tag_line: str | None = None
    platform: str = "euw1"
    target_tier: str | None = None
    backfill: int | None = Field(None, ge=20, le=300)
    poll_minutes: int | None = Field(None, ge=1, le=60)
    enabled: bool | None = None


class ProfileUpdate(BaseModel):
    target_tier: str | None = None
    backfill: int | None = Field(None, ge=20, le=300)
    poll_minutes: int | None = Field(None, ge=1, le=60)
    enabled: bool | None = None


class GoalBody(BaseModel):
    metric: str
    op: str = Field(..., pattern=r"^(<=|>=)$")
    target: float
    role: str | None = None
    label: str | None = None


class ReviewAnswer(BaseModel):
    t: float = Field(..., ge=0, le=36_000)
    question: str = Field(..., pattern=r"^(knew|decided|better)$")
    answer: str = Field("", max_length=2000)


class GoalUpdate(BaseModel):
    target: float | None = None
    label: str | None = None
    active: bool | None = None


def _public(profile: dict | None) -> dict | None:
    if not profile:
        return None
    settings = profile["settings"]
    return {
        "game_name": profile["game_name"],
        "tag_line": profile["tag_line"],
        "platform": profile["platform"],
        "platform_label": PLATFORMS.get(profile["platform"], ("", profile["platform"].upper()))[1],
        "linked": bool(profile.get("puuid")),
        "profile_icon": profile.get("profile_icon"),
        "summoner_level": profile.get("summoner_level"),
        "target_tier": profile["target_tier"],
        "created_at": profile.get("created_at"),
        "settings": {k: settings.get(k) for k in ("enabled", "backfill", "poll_minutes", "scanned")},
    }


def _elo_profile(svc) -> EloProfile | None:
    try:
        return EloProfile.of(svc)
    # sin perfil por elo, el resto de «Mi progreso» sigue
    except Exception:  # noqa: BLE001
        log.debug("Sin perfil de errores por elo", exc_info=True)
        return None


def _champ_name(svc, champ: int | None) -> str:
    dd = getattr(svc, "dd", None)
    return (dd.name(int(champ)) if dd is not None and champ and hasattr(dd, "name") else None) or "tu campeón"


def _catalog(svc):
    catalog = getattr(getattr(svc, "dd", None), "item_catalog", None)
    return catalog if catalog else None


def _drop_retired_mistakes(g: dict) -> None:
    """Reglas que en ese rol ya no cuentan (partidas analizadas antes del cambio): fuera de errores y recuentos."""
    if not g.get("mistakes"):
        return
    gone = [
        m for m in g["mistakes"]
        if insights.dropped({"kind": m[0], "tags": m[3]}, g.get("role"), g.get("champ"))
        or insights.excused({"kind": m[0], "tags": m[3]}, g.get("role"))
    ]
    if not gone:
        return
    g["mistakes"] = [m for m in g["mistakes"] if m not in gone]
    issues = g["issues"] = dict(g.get("issues") or {})
    for m in gone:
        issues[m[1]] = max(0, issues.get(m[1], 0) - 1)


def _check_vs_otps(svc, g: dict, prof: EloProfile, catalog) -> None:
    """Errores de una partida frente a los OTPs de su campeón (solo si ya está analizada y puntúa)."""
    cc = g.get("checks")
    if not cc or g.get("mistakes") is None or not insights.reliable(g):
        return
    ref = prof.champ_ref(g.get("champ"), g.get("role"))
    metrics = g.get("metrics") or {}
    rec = {**cc, **{k: metrics.get(k) for k in champ_checks.METRIC_KEYS}, "role": g.get("role")}
    issues = g["issues"] = dict(g.get("issues") or {})
    flags = g["flags"] = []
    for m in champ_checks.check(rec, ref, catalog, _champ_name(svc, g.get("champ"))):
        g["mistakes"].append([m["kind"], m["severity"], m["t"], m["tags"], m["title"][:90]])
        flags.append(m["tags"][-1])
        issues[m["severity"]] = issues.get(m["severity"], 0) + 1  # la lista de repeticiones cuenta lo mismo que se ve dentro


def _add_champ_checks(svc, games: list[dict], prof: EloProfile | None) -> None:
    """Orden de habilidades, primera vuelta y ritmo del primer objeto frente a los OTPs de cada campeón (en sus errores)."""
    if prof is None:
        return
    catalog = _catalog(svc)
    for g in games:
        _drop_retired_mistakes(g)
        _check_vs_otps(svc, g, prof, catalog)


def _require_profile(svc) -> dict:
    profile = store.get_profile(svc.db)
    if not profile:
        raise HTTPException(412, "Guarda primero tu perfil en «Mi progreso»")
    return profile


# ------------------------------------------------------------------ perfil
@router.get("/profile")
def get_profile(svc=Depends(svc_from)):
    return {"profile": _public(store.get_profile(svc.db)), "tracker": svc.tracker.status(), "targets": list(TARGET_TIERS)}


def _riot_id(body: ProfileBody) -> tuple[str, str]:
    """Nombre y TAG del Riot ID (``Nombre#TAG`` o por separado); 400 si falta alguno."""
    name, tag = body.game_name, body.tag_line
    if body.riot_id:
        name, _, tag = body.riot_id.partition("#")
    name, tag = (name or "").strip(), (tag or "").strip().lstrip("#")
    if not name or not tag:
        raise HTTPException(400, "Escribe tu Riot ID completo: Nombre#TAG")
    return name, tag


def _link_account(svc, name: str, tag: str, platform: str) -> tuple[dict, str | None]:
    """La cuenta de Riot del perfil y, si no se ha podido comprobar, el aviso para la web (el perfil se guarda igual)."""
    if not svc.keys.get() or svc.keys.status == "invalid":
        return {}, (
            "Sin API key de Riot: el perfil queda guardado y empezará a recoger partidas en cuanto pegues tu key en "
            "«Datos y API»."
        )
    try:
        return resolve_account(svc, name, tag, platform), None
    except ProfileNotFound as exc:
        raise HTTPException(404, str(exc)) from exc
    except RiotKeyError:
        return {}, "Riot no acepta la API key: el perfil queda guardado y se vinculará al pegar una key válida."
    except RiotAPIError as exc:
        return {}, f"No se pudo comprobar con Riot ahora ({exc.message}). Se reintentará solo."


@router.post(
    "/profile",
    responses={400: {"description": "Riot ID incompleto o región no válida"}, 404: {"description": "No existe ese Riot ID"}},
)
def save_profile(body: ProfileBody, svc=Depends(svc_from)):
    name, tag = _riot_id(body)
    platform = (body.platform or "").lower()
    if platform not in PLATFORMS:
        raise HTTPException(400, "Región no válida")
    resolved, note = _link_account(svc, name, tag, platform)

    settings = {k: v for k, v in (("backfill", body.backfill), ("poll_minutes", body.poll_minutes), ("enabled", body.enabled)) if v is not None}
    profile = store.save_profile(
        svc.db,
        game_name=resolved.get("game_name") or name,
        tag_line=resolved.get("tag_line") or tag,
        platform=resolved.get("platform") or platform,
        puuid=resolved.get("puuid"),
        profile_icon=resolved.get("profile_icon"),
        summoner_level=resolved.get("summoner_level"),
        target_tier=body.target_tier,
        settings=settings,
    )
    svc.db.meta_set("profile_autodetect", "1")
    svc.tracker.trigger()
    return {"profile": _public(profile), "tracker": svc.tracker.status(), "note": note}


@router.patch("/profile", responses={412: NO_PROFILE_DOC})
def update_profile(body: ProfileUpdate, svc=Depends(svc_from)):
    _require_profile(svc)
    settings = {k: v for k, v in (("backfill", body.backfill), ("poll_minutes", body.poll_minutes), ("enabled", body.enabled)) if v is not None}
    fields = {"settings": settings}
    if body.target_tier:
        fields["target_tier"] = normalize_tier(body.target_tier)
    profile = store.update_profile(svc.db, **fields)
    if settings:
        svc.tracker.trigger()
    return {"profile": _public(profile), "tracker": svc.tracker.status()}


@router.delete("/profile")
def delete_profile(purge: bool = Query(False, description="Borrar también las partidas y propósitos guardados"), svc=Depends(svc_from)):
    store.delete_profile(svc.db, purge=purge)
    svc.db.meta_set("profile_autodetect", "0")  # lo has quitado tú: no se vuelve a detectar solo del cliente
    return {"profile": None, "purged": purge}


@router.get("/profile/detect", responses={409: {"description": "El cliente de LoL no está abierto o no ha cargado tu cuenta"}})
def detect_profile():
    """Lee tu Riot ID del cliente de LoL abierto en este ordenador."""
    try:
        return {"account": lcu.current_summoner()}
    except lcu.LCUError as exc:
        raise HTTPException(409, str(exc)) from exc


# ---------------------------------------------------------------- progreso
@router.post("/coach/sync", responses={412: NO_PROFILE_DOC})
def sync_now(svc=Depends(svc_from)):
    _require_profile(svc)
    svc.tracker.trigger()
    return {"tracker": svc.tracker.status()}


def _queue(queue: str) -> str:
    return queue if queue in store.QUEUE_GROUPS else "all"


def _role(role: str | None) -> str | None:
    return None if not role or role.lower() == "all" else role_param(role)


def _row(g: dict, refs: References) -> dict:
    m = g["metrics"] or {}
    keys = (
        "kills", "deaths", "assists", "kda", "cs_min", "cs10", "vision_min", "kp", "deaths_pre14", "deaths_to_jungler_pre14",
        "deaths_ganked", "gold_hoard_min", "idle_pct", "obj_lost_dead", "deaths_before_objective", "time_dead_pct", "ganks_pre10",
        "obj_lost_far", "obj_trades", "obj_fair_trades", "first_item_min", "afk_min", "r_late",
    )
    return {
        "match_id": g["match_id"],
        "queue": g["queue"],
        "queue_label": QUEUES.get(g["queue"], f"Cola {g['queue']}"),
        "game_start": g["game_start"],
        "duration": g["duration"],
        "champ": g["champ"],
        "role": g["role"],
        "win": g["win"],
        "status": g["status"],
        "has_timeline": g["has_timeline"],
        "opponent_champ": g.get("opponent_champ"),
        "enemy_jungler_champ": g.get("enemy_jungler_champ"),
        "issues": g.get("issues") or {},
        "flags": g.get("flags") or [],  # errores frente a los OTPs de tu campeón y de partida entera (champ_checks)
        # Swiftplay o con alguien AFK ≥ 3 min: no se puntúa (igual que en el resumen).
        "score": insights.fundamentals_score(m, insights.game_refs(refs, g), g["role"]) if insights.reliable(g) else None,
        "reliable": insights.reliable(g),
        "unreliable": insights.unreliable_reason(g),  # por qué no tiene nota (se enseña en vez de un «—» mudo)
        **{k: m.get(k) for k in keys},
    }


def _references(svc, profile: dict) -> References:
    """Referencias de tu elo objetivo, calibradas con los jugadores de tus partidas desde tu liga actual."""
    rank = (profile.get("settings") or {}).get("rank") or {}
    return References(svc, profile["target_tier"], current=rank.get("tier"))


def _safe_build(svc, champ, role, **kwargs):
    """Build más jugada (OP.GG) de un campeón y rol, o None si OP.GG falla."""
    try:
        return svc.opgg.champion_build(champ, role, **kwargs)
    # OP.GG es opcional: sin su build, el resto sigue
    except Exception as exc:  # noqa: BLE001
        log.warning("Build de OP.GG de %s (%s): %s", champ, role, exc)
        return None


def _fetch_builds_later(svc, missing: list[tuple]) -> None:
    """Pide a OP.GG en segundo plano las builds que faltan (sin repetir las que ya se están pidiendo)."""
    _fetching.update(missing)

    def run():
        try:
            for champ, role in missing:
                try:
                    svc.opgg.champion_build(champ, role)
                except Exception:
                    log.debug("Sin build de referencia para %s (%s)", champ, role, exc_info=True)
        finally:
            _fetching.difference_update(missing)

    threading.Thread(target=run, name="opgg-builds", daemon=True).start()


def _build_refs(svc, keys) -> dict:
    """Build más jugada (OP.GG) de cada campeón y rol, solo desde la caché; lo que falte se pide en segundo plano."""
    refs: dict = {}
    missing = []
    for champ, role in keys:
        if not champ or not role:
            continue
        ref = _safe_build(svc, champ, role, allow_fetch=False)
        refs[(champ, role)] = ref
        if ref is None and (champ, role) not in _fetching:
            missing.append((champ, role))
    if missing and getattr(svc.opgg, "enabled", False):
        _fetch_builds_later(svc, missing)
    return refs


def _games_or_fallback(svc, puuids, queue: str, role_f: str | None, last: int):
    """Tus partidas con ese filtro: ``(partidas, cola, rol, fallback)``. Tus datos siempre a la vista: si el filtro no
    tiene partidas pero otro sí (casi no juegas clasificatorias, o ese rol no lo juegas), se enseñan las que hay y
    ``fallback`` dice qué filtro se ha usado."""
    games = store.list_games(svc.db, puuids, queue=queue, role=role_f, limit=last)
    if games:
        return games, queue, role_f, None
    for alt_queue, alt_role in ((queue, None), ("normal", role_f), ("normal", None), ("all", role_f), ("all", None)):
        if (alt_queue, alt_role) == (queue, role_f):
            continue
        alt = store.list_games(svc.db, puuids, queue=alt_queue, role=alt_role, limit=last)
        if alt:
            shown = {"queue": alt_queue, "role": alt_role or "all"}
            return alt, alt_queue, alt_role, {"wanted": {"queue": queue, "role": role_f or "all"}, "shown": shown}
    return games, queue, role_f, None


def _top_build_keys(games: list[dict]) -> list[tuple]:
    """Los 4 campeones (con su rol) con más partidas con build entre estas."""
    counts: dict = {}
    for g in games:
        if g.get("build"):
            counts[(g["champ"], g["role"])] = counts.get((g["champ"], g["role"]), 0) + 1
    return [key for key, _ in sorted(counts.items(), key=lambda kv: kv[1], reverse=True)[:4]]


@router.get("/coach/summary")
def summary(
    queue: str = Query("all", pattern="^(all|normal|ranked)$"),
    role: str = Query("all"),
    last: int = Query(30, ge=5, le=300),
    svc=Depends(svc_from),
):
    profile = store.get_profile(svc.db)
    if not profile:
        return {"profile": None, "games": 0}
    puuids = store.known_puuids(profile)
    queue, role_f = _queue(queue), _role(role)
    refresh_digests(svc.db)  # resúmenes de un formato anterior: se rehacen del análisis guardado (solo la primera vez)
    games, queue, role_f, fallback = _games_or_fallback(svc, puuids, queue, role_f, last)
    refs = _references(svc, profile)
    top = _top_build_keys(games)
    prof = _elo_profile(svc)
    _add_champ_checks(svc, games, prof)
    data = insights.summarize(games, refs, store.list_goals(svc.db, active_only=True), _build_refs(svc, top), prof.get() if prof else None)
    # Hábitos entre partidas: con todas tus partidas recientes (los filtros romperían las rachas y las sesiones).
    data["habits"] = insights.habits(store.list_games(svc.db, puuids, limit=HABITS_GAMES), prof.get() if prof else None)
    data["habits"]["reviews"] = store.review_stats(svc.db, since=int(time.time()) - 7 * 86_400)
    roles_played = {g["role"] for g in games if g["role"]}
    rank = (profile.get("settings") or {}).get("rank") or {}
    data.update(
        {
            "profile": _public(profile),
            "target_tier": profile["target_tier"],
            "rank": rank if rank.get("tier") else None,
            # Si te comparas con tu propia liga (o una inferior), qué elo te exigiría lo necesario para subir.
            "target_hint": next_target(rank.get("tier"), profile["target_tier"]),
            "refs_source": {r: refs.source(r) for r in sorted(roles_played)},
            "refs_info": {r: refs.info(r) for r in sorted(roles_played)},
            # Campeones tuyos con referencia propia de farm (OTPs de ese campeón en tu elo objetivo).
            "champion_refs": [
                {"champ": c, "role": r, "games": info["games"], "players": info["players"]}
                for c, r in sorted({(g.get("champ"), g.get("role")) for g in games if g.get("champ") and g.get("role")})
                if (info := refs.champion(c, r))
            ],
            "calibration": svc.calibrator.status() if getattr(svc, "calibrator", None) else None,
            "filters": {"queue": queue, "role": role_f or "all", "last": last},
            "fallback": fallback,
            "recent": [_row(g, refs) for g in games[:60]],
            # Tus campeones frente a sus OTPs (data/otps) en tu elo objetivo y en el más alto que haya.
            "otp_compare": otp_compare.compare(svc, games, profile["target_tier"]),
            # Sin el perfil por elo aún (se calcula en segundo plano): la nota de «¿Listo?» todavía no cuenta tus errores.
            "elo_pending": bool(prof and prof.pending()),
            "all_roles": sorted({p["role"] for p in store.champion_pool(svc.db, puuids) if p["role"]}),
        }
    )
    return data


@router.get("/coach/games", responses={412: NO_PROFILE_DOC})
def list_games(
    queue: str = Query("all", pattern="^(all|normal|ranked)$"),
    role: str = Query("all"),
    limit: int = Query(50, ge=1, le=300),
    svc=Depends(svc_from),
):
    profile = _require_profile(svc)
    refs = _references(svc, profile)
    refresh_digests(svc.db)
    games = store.list_games(svc.db, store.known_puuids(profile), queue=_queue(queue), role=_role(role), limit=limit)
    _add_champ_checks(svc, games, _elo_profile(svc))
    return {"games": [_row(g, refs) for g in games]}


def _keep_valid_moments(analysis: dict) -> None:
    """Fuera los momentos de reglas que ya no cuentan en ese rol; los cambios igualados quedan como información."""
    role, champ = analysis.get("role"), analysis.get("champ")
    analysis["moments"] = [m for m in analysis.get("moments") or [] if not insights.dropped(m, role, champ)]
    for m in analysis["moments"]:
        # Cambio igualado (caísteis los mismos en los dos bandos): se ve en la repetición, pero no es un error.
        if m.get("severity") in ("high", "medium", "low") and insights.excused(m, role):
            m["severity"] = "info"
            m["detail"] = ((m.get("detail") or "") + " Cambio igualado: no cuenta como error.").strip()


def _route_replay(analysis: dict, match_id: str, svc=None) -> None:
    """Rutas que rodean los muros (navgrid): sin ellas la vista 2D los atravesaba en línea recta (11 % del tiempo dentro
    de un muro; con rutas, 1 %). Se calcula al abrir la partida (~0,2 s): no hace falta re-analizar nada."""
    replay = analysis.get("replay")
    if not replay:
        return
    old = "rift" not in replay  # guardada antes de que el análisis apuntara el alma del dragón
    if old:
        try:
            replay["rift"] = rift_of(store.raw_game(svc.db, match_id)[1]) if svc is not None else None
        except Exception:  # noqa: BLE001
            replay["rift"] = None
    if replay.get("routed") and not (old and replay["rift"]):
        return  # ya con rutas (las del análisis llevan el mapa del alma)
    try:
        champs = {str(p.get("pid")): p.get("champ") for p in analysis.get("players") or []}
        analysis["replay"] = navgrid.route_replay(analysis["replay"], champs)
    # sin rutas, la repetición se ve igual que antes
    except Exception:  # noqa: BLE001
        log.warning("No se pudieron calcular las rutas de %s", match_id, exc_info=True)


def _add_otp_moments(svc, analysis: dict, prof: EloProfile | None) -> None:
    """Orden de habilidades, primera vuelta y primer objeto frente a los OTPs de tu campeón (Diamante o más)."""
    champ_ref = prof.champ_ref(analysis.get("champ"), analysis.get("role")) if prof and prof.get() else None
    if not (analysis.get("build") and analysis.get("has_timeline") and analysis.get("reliable")):
        return
    skill_t = None
    if not analysis["build"].get("skill_t"):
        skill_t = skill_times(store.raw_timeline(svc.db, analysis["match_id"]), analysis.get("me"))
    rec = champ_checks.record_from_analysis(analysis, skill_t)
    extra = champ_checks.check(rec, champ_ref, _catalog(svc), _champ_name(svc, analysis.get("champ")))
    if extra:
        analysis["moments"] = sorted((analysis.get("moments") or []) + extra, key=lambda m: m.get("t") or 0)


def _elo_info(m: dict, tiers: dict, scope: str, role: str | None) -> dict | None:
    """Frecuencia de un error en cada elo (None si el momento no es un error que se mida por elo)."""
    if m.get("severity") not in ("high", "medium", "low"):
        return None
    mt = insights.mistake_type(m.get("kind"), set(m.get("tags") or []), role)
    if mt is None:
        return None
    sep = insights.separation(tiers, mt["id"])
    pct = {g: ((tiers[g].get("types") or {}).get(mt["id"]) or {}).get("pct", 0.0) for g in insights.ELO_GROUPS if g in tiers}
    return {
        "type": mt["title"],
        "scope": scope,
        "tiers": pct,
        "separates": (not sep["flat"]) if sep else None,
        "rises": bool(sep and sep["rises"]),
    }


def _add_elo_frequency(analysis: dict, prof: EloProfile | None) -> None:
    """Cada error con su frecuencia en cada elo (tu campeón si hay varios elos; si no, tu rol): ¿lo hace todo el mundo o
    es de lo que separa elos?"""
    data = prof.get(refresh=False) if prof else None
    tiers, scope = insights.elo_tiers(data, analysis.get("role"), analysis.get("champ"))
    if not tiers:
        return
    for m in analysis.get("moments") or []:
        elo = _elo_info(m, tiers, scope, analysis.get("role"))
        if elo is not None:
            m["elo"] = elo


@router.get("/coach/games/{match_id}", responses={404: GAME_NOT_FOUND_DOC, 412: NO_PROFILE_DOC})
def game_detail(match_id: str, svc=Depends(svc_from)):
    profile = _require_profile(svc)
    analysis = store.get_game(svc.db, match_id.strip().upper())
    if not analysis:
        raise HTTPException(404, GAME_NOT_FOUND)
    refs = _references(svc, profile)
    role_refs = insights.game_refs(refs, analysis)
    analysis["refs"] = role_refs
    analysis["refs_source"] = refs.source(analysis["role"]) if analysis.get("role") else None
    analysis["target_tier"] = profile["target_tier"]
    analysis["reliable"] = insights.reliable(analysis)
    analysis["unreliable"] = insights.unreliable_reason(analysis)
    metrics = analysis.get("metrics") or {}
    analysis["score"] = insights.fundamentals_score(metrics, role_refs, analysis.get("role")) if analysis["reliable"] else None
    analysis["metric_info"] = public_metrics()
    with_build = analysis.get("build") and analysis.get("role")
    ref = _safe_build(svc, analysis["champ"], analysis["role"]) if with_build else None
    analysis["build_compare"] = insights.compare_build(build_digest(analysis.get("build")), ref)
    _keep_valid_moments(analysis)
    _route_replay(analysis, match_id, svc)
    prof = _elo_profile(svc)
    _add_otp_moments(svc, analysis, prof)
    _add_elo_frequency(analysis, prof)
    return analysis


@router.get("/coach/games/{match_id}/review", responses={404: GAME_NOT_FOUND_DOC, 412: NO_PROFILE_DOC})
def get_review(match_id: str, svc=Depends(svc_from)):
    """Revisión guiada de una partida: 3 momentos, 3 preguntas en cada uno y tus respuestas guardadas."""
    _require_profile(svc)
    match_id = match_id.strip().upper()
    analysis = store.get_game(svc.db, match_id)
    if not analysis:
        raise HTTPException(404, GAME_NOT_FOUND)
    return {
        "questions": insights.REVIEW_QUESTIONS,
        "moments": insights.review_moments(analysis.get("moments") or []),
        "answers": store.review_answers(svc.db, match_id),
    }


@router.put("/coach/games/{match_id}/review", responses={404: GAME_NOT_FOUND_DOC, 412: NO_PROFILE_DOC})
def put_review(match_id: str, body: ReviewAnswer, svc=Depends(svc_from)):
    _require_profile(svc)
    match_id = match_id.strip().upper()
    if not svc.db.scalar("SELECT 1 FROM my_games WHERE match_id = ?", (match_id,)):
        raise HTTPException(404, GAME_NOT_FOUND)
    store.save_review_answer(svc.db, match_id, body.t, body.question, body.answer)
    return {"ok": True}


@router.get("/coach/learn")
def learn_catalog():
    """Material gratuito de coaches por tema («Aprende esto») y qué etiquetas de cada momento llevan a cada tema."""
    return learn.catalog()


@router.post("/coach/reanalyze")
def reanalyze(svc=Depends(svc_from)):
    return {"reanalyzed": svc.tracker.reanalyze_outdated(force=True)}


@router.get("/coach/pool")
def pool(role: str | None = None, svc=Depends(svc_from)):
    profile = store.get_profile(svc.db)
    return {"pool": store.champion_pool(svc.db, store.known_puuids(profile), _role(role)) if profile else []}


# --------------------------------------------------------------- propósitos
@router.get("/coach/goals")
def goals(svc=Depends(svc_from)):
    catalog = [
        {**item, "label": insights.goal_label(item["label"], item["target"], item["metric"]), "template": item["label"], "metric_label": METRICS[item["metric"]].label}
        for item in insights.GOAL_CATALOG
    ]
    return {"goals": store.list_goals(svc.db), "catalog": catalog, "metrics": public_metrics()}


@router.post("/coach/goals", responses={400: {"description": "Métrica desconocida o rol no válido"}})
def add_goal(body: GoalBody, svc=Depends(svc_from)):
    if body.metric not in METRICS:
        raise HTTPException(400, f"Métrica desconocida: {body.metric}")
    role = _role(body.role)
    label = (body.label or "").strip()
    if not label:
        template = next((g["label"] for g in insights.GOAL_CATALOG if g["metric"] == body.metric and g["op"] == body.op), None)
        m = METRICS[body.metric]
        label = insights.goal_label(template, body.target, body.metric) if template else f"{m.label} {body.op} {body.target:g}{m.unit}"
    return {"goal": store.add_goal(svc.db, metric=body.metric, op=body.op, target=body.target, role=role, label=label)}


@router.patch("/coach/goals/{goal_id}", responses={404: GOAL_NOT_FOUND_DOC})
def update_goal(goal_id: int, body: GoalUpdate, svc=Depends(svc_from)):
    goal = store.update_goal(svc.db, goal_id, target=body.target, label=body.label, active=body.active)
    if not goal:
        raise HTTPException(404, GOAL_NOT_FOUND)
    return {"goal": goal}


@router.delete("/coach/goals/{goal_id}", responses={404: GOAL_NOT_FOUND_DOC})
def delete_goal(goal_id: int, svc=Depends(svc_from)):
    if not store.delete_goal(svc.db, goal_id):
        raise HTTPException(404, GOAL_NOT_FOUND)
    return {"deleted": goal_id}
