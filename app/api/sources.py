"""Fuentes de datos: precarga en segundo plano, Lolalytics y notas de parche."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from app.api.deps import champ_param, svc_from

router = APIRouter(prefix="/api", tags=["fuentes"])


class PauseBody(BaseModel):
    paused: bool


@router.get("/warmer/status")
def warmer_status(svc=Depends(svc_from)):
    return svc.warmer.status()


@router.post("/warmer/pause")
def warmer_pause(body: PauseBody, svc=Depends(svc_from)):
    svc.warmer.set_paused(body.paused)
    return svc.warmer.status()


@router.post("/warmer/replan")
def warmer_replan(svc=Depends(svc_from)):
    """Vuelve a revisar ya qué falta por descargar (y reintenta lo que falló)."""
    for source in (svc.opgg, svc.lolalytics, svc.patchnotes):
        clear = getattr(source, "clear_failures", None)
        if clear:
            clear()
    svc.warmer.replan()
    return svc.warmer.status()


@router.get("/sources/status")
def sources_status(svc=Depends(svc_from)):
    """Resumen de cada web: activada, caché, peticiones y último error."""
    out = {}
    for name, source, kinds in (
        ("opgg", svc.opgg, ("versions", "tier", "counters", "guide", "build", "synergy", "champion")),
        ("lolalytics", svc.lolalytics, ("lola_tier", "lola_counters")),
        ("patchnotes", svc.patchnotes, ("patch_index", "patch_notes")),
    ):
        try:
            cache = source.cache_summary(kinds)
        except Exception:
            cache = {}
        if hasattr(source, "circuit_open"):
            source.circuit_open()  # refresca «en pausa durante N s» (solo se recalcula al consultarlo)
        out[name] = {"enabled": bool(source.enabled), "cache": cache, "stats": dict(getattr(source, "stats", {}) or {})}
    return out


@router.get("/patches")
def patches(svc=Depends(svc_from)):
    """Últimas notas de parche (de la caché; si faltan, se piden en segundo plano)."""
    notes = svc.patchnotes
    if not notes.enabled:
        return {"enabled": False, "patches": [], "latest": {"patch": None, "champions": {}}}
    recent = notes.recent(3, allow_fetch=True)
    return {
        "enabled": True,
        "patches": [
            {
                "patch": p["patch"],
                "title": p.get("title"),
                "url": p.get("url"),
                "champions": {k: {"name": v["name"], "verdict": v["verdict"]} for k, v in (p.get("champions") or {}).items()},
                "items": {k: {"name": v["name"], "verdict": v["verdict"]} for k, v in (p.get("items") or {}).items()},
            }
            for p in recent
        ],
        "latest": notes.latest_verdicts(allow_fetch=False),
    }


@router.get("/patches/champion/{champ}")
def champion_patches(champ: str, count: int = 3, svc=Depends(svc_from)):
    if not svc.patchnotes.enabled:
        return {"changes": []}
    champ_id = champ_param(svc, champ)
    return {"champ": champ_id, "changes": svc.patchnotes.champion_changes(champ_id, max(1, min(count, 6)), allow_fetch=True)}


@router.get("/patches/{patch}")
def patch_detail(patch: str, svc=Depends(svc_from)):
    data = svc.patchnotes.notes(patch, allow_fetch=True) if svc.patchnotes.enabled else None
    if not data:
        raise HTTPException(404, f"Sin notas de la versión {patch} (todavía)")
    return data
