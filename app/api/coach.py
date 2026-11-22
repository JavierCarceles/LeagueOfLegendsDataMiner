"""Tu perfil, el seguimiento automático de tus partidas y tus propósitos."""

from __future__ import annotations

import threading
import time

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field

from app import lcu
from app.api.deps import role_param, svc_from
from app.coach import insights, learn, otp_compare, store
from app.coach.benchmarks import METRICS, TARGET_TIERS, References, next_target, normalize_tier, public_metrics
from app.coach.tracker import ProfileNotFound, build_digest, resolve_account
from app.logbus import get_logger
from app.riot.client import RiotAPIError, RiotKeyError
from app.riot.routing import PLATFORMS, QUEUES

router = APIRouter(prefix="/api", tags=["progreso"])
log = get_logger("progreso")
_fetching: set[tuple[int, str]] = set()
HABITS_GAMES = 100  # partidas recientes (de cualquier cola y rol) para el tilt, las sesiones y el pool


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


def _require_profile(svc) -> dict:
    profile = store.get_profile(svc.db)
    if not profile:
        raise HTTPException(412, "Guarda primero tu perfil en «Mi progreso»")
    return profile


# ------------------------------------------------------------------ perfil
@router.get("/profile")
def get_profile(svc=Depends(svc_from)):
    return {"profile": _public(store.get_profile(svc.db)), "tracker": svc.tracker.status(), "targets": list(TARGET_TIERS)}


@router.post("/profile")
def save_profile(body: ProfileBody, svc=Depends(svc_from)):
    name, tag = body.game_name, body.tag_line
    if body.riot_id:
        name, _, tag = body.riot_id.partition("#")
    name, tag = (name or "").strip(), (tag or "").strip().lstrip("#")
    if not name or not tag:
        raise HTTPException(400, "Escribe tu Riot ID completo: Nombre#TAG")
    platform = (body.platform or "").lower()
    if platform not in PLATFORMS:
        raise HTTPException(400, "Región no válida")

    resolved: dict = {}
    note = None
    if svc.keys.get() and svc.keys.status != "invalid":
        try:
            resolved = resolve_account(svc, name, tag, platform)
        except ProfileNotFound as exc:
            raise HTTPException(404, str(exc)) from exc
        except RiotKeyError:
            note = "Riot no acepta la API key: el perfil queda guardado y se vinculará al pegar una key válida."
        except RiotAPIError as exc:
            note = f"No se pudo comprobar con Riot ahora ({exc.message}). Se reintentará solo."
    else:
        note = "Sin API key de Riot: el perfil queda guardado y empezará a recoger partidas en cuanto pegues tu key en «Datos y API»."

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
    svc.tracker.trigger()
    return {"profile": _public(profile), "tracker": svc.tracker.status(), "note": note}


@router.patch("/profile")
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
    return {"profile": None, "purged": purge}


@router.get("/profile/detect")
def detect_profile():
    """Lee tu Riot ID del cliente de LoL abierto en este ordenador."""
    try:
        return {"account": lcu.current_summoner()}
    except lcu.LCUError as exc:
        raise HTTPException(409, str(exc)) from exc


# ---------------------------------------------------------------- progreso
@router.post("/coach/sync")
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
        # Swiftplay o con alguien AFK ≥ 3 min: no se puntúa (igual que en el resumen).
        "score": insights.fundamentals_score(m, refs.for_role(g["role"]), g["role"]) if insights.reliable(g) else None,
        "reliable": insights.reliable(g),
        **{k: m.get(k) for k in keys},
    }


def _references(svc, profile: dict) -> References:
    """Referencias de tu elo objetivo, calibradas con los jugadores de tus partidas desde tu liga actual."""
    rank = (profile.get("settings") or {}).get("rank") or {}
    return References(svc, profile["target_tier"], current=rank.get("tier"))


def _build_refs(svc, keys) -> dict:
    """Build más jugada (OP.GG) de cada campeón y rol, solo desde la caché; lo que falte se pide en segundo plano."""
    refs: dict = {}
    missing = []
    for champ, role in keys:
        if not champ or not role:
            continue
        try:
            ref = svc.opgg.champion_build(champ, role, allow_fetch=False)
        except Exception:  # OP.GG es opcional
            ref = None
        refs[(champ, role)] = ref
        if ref is None and (champ, role) not in _fetching:
            missing.append((champ, role))
    if missing and getattr(svc.opgg, "enabled", False):
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
    return refs


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
    games = store.list_games(svc.db, puuids, queue=_queue(queue), role=_role(role), limit=last)
    refs = _references(svc, profile)
    counts: dict = {}
    for g in games:
        if g.get("build"):
            counts[(g["champ"], g["role"])] = counts.get((g["champ"], g["role"]), 0) + 1
    top = [key for key, _n in sorted(counts.items(), key=lambda kv: kv[1], reverse=True)[:4]]
    data = insights.summarize(games, refs, store.list_goals(svc.db, active_only=True), _build_refs(svc, top))
    # Hábitos entre partidas: con todas tus partidas recientes (los filtros romperían las rachas y las sesiones).
    data["habits"] = insights.habits(store.list_games(svc.db, puuids, limit=HABITS_GAMES))
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
            "calibration": svc.calibrator.status() if getattr(svc, "calibrator", None) else None,
            "filters": {"queue": _queue(queue), "role": _role(role) or "all", "last": last},
            "recent": [_row(g, refs) for g in games[:60]],
            # Tus campeones frente a sus OTPs (data/otps) en tu elo objetivo y en el más alto que haya.
            "otp_compare": otp_compare.compare(svc, games, profile["target_tier"]),
            "all_roles": sorted({p["role"] for p in store.champion_pool(svc.db, puuids) if p["role"]}),
        }
    )
    return data


@router.get("/coach/games")
def list_games(
    queue: str = Query("all", pattern="^(all|normal|ranked)$"),
    role: str = Query("all"),
    limit: int = Query(50, ge=1, le=300),
    svc=Depends(svc_from),
):
    profile = _require_profile(svc)
    refs = _references(svc, profile)
    games = store.list_games(svc.db, store.known_puuids(profile), queue=_queue(queue), role=_role(role), limit=limit)
    return {"games": [_row(g, refs) for g in games]}


@router.get("/coach/games/{match_id}")
def game_detail(match_id: str, svc=Depends(svc_from)):
    profile = _require_profile(svc)
    analysis = store.get_game(svc.db, match_id.strip().upper())
    if not analysis:
        raise HTTPException(404, "Esa partida no está entre tus partidas analizadas")
    refs = _references(svc, profile)
    role_refs = refs.for_role(analysis.get("role"))
    analysis["refs"] = role_refs
    analysis["refs_source"] = refs.source(analysis["role"]) if analysis.get("role") else None
    analysis["target_tier"] = profile["target_tier"]
    analysis["reliable"] = insights.reliable(analysis)
    analysis["score"] = insights.fundamentals_score(analysis.get("metrics") or {}, role_refs, analysis.get("role")) if analysis["reliable"] else None
    analysis["metric_info"] = public_metrics()
    ref = None
    if analysis.get("build") and analysis.get("role"):
        try:
            ref = svc.opgg.champion_build(analysis["champ"], analysis["role"])
        except Exception:  # OP.GG es opcional
            ref = None
    analysis["build_compare"] = insights.compare_build(build_digest(analysis.get("build")), ref)
    return analysis


@router.get("/coach/games/{match_id}/review")
def get_review(match_id: str, svc=Depends(svc_from)):
    """Revisión guiada de una partida: 3 momentos, 3 preguntas en cada uno y tus respuestas guardadas."""
    _require_profile(svc)
    match_id = match_id.strip().upper()
    analysis = store.get_game(svc.db, match_id)
    if not analysis:
        raise HTTPException(404, "Esa partida no está entre tus partidas analizadas")
    return {
        "questions": insights.REVIEW_QUESTIONS,
        "moments": insights.review_moments(analysis.get("moments") or []),
        "answers": store.review_answers(svc.db, match_id),
    }


@router.put("/coach/games/{match_id}/review")
def put_review(match_id: str, body: ReviewAnswer, svc=Depends(svc_from)):
    _require_profile(svc)
    match_id = match_id.strip().upper()
    if not svc.db.scalar("SELECT 1 FROM my_games WHERE match_id = ?", (match_id,)):
        raise HTTPException(404, "Esa partida no está entre tus partidas analizadas")
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


@router.post("/coach/goals")
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


@router.patch("/coach/goals/{goal_id}")
def update_goal(goal_id: int, body: GoalUpdate, svc=Depends(svc_from)):
    goal = store.update_goal(svc.db, goal_id, target=body.target, label=body.label, active=body.active)
    if not goal:
        raise HTTPException(404, "Ese propósito no existe")
    return {"goal": goal}


@router.delete("/coach/goals/{goal_id}")
def delete_goal(goal_id: int, svc=Depends(svc_from)):
    if not store.delete_goal(svc.db, goal_id):
        raise HTTPException(404, "Ese propósito no existe")
    return {"deleted": goal_id}
