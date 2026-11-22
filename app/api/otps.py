"""Biblioteca de OTPs analizados (sus partidas con micro y macro en ``otp_games``, solo las marcadas como de OTP).

* ``/api/otps/library``: totales, ranking de campeones (más OTPs primero; a igualdad, más elo y más partidas) y reparto
  por elo, servidor y rol.
* ``/api/otps/players``: la lista, filtrable por campeón, rol, elo, servidor, nombre y mínimo de partidas, ordenable y
  paginada.
* ``/api/otps/players/{puuid}``: el detalle de un OTP con un campeón (y rol): sus partidas, sus medias frente a las del
  resto de OTPs de ese campeón en su elo, y su build.
"""

from __future__ import annotations

import json
from collections import Counter, defaultdict
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Query

from app.api.deps import svc_from
from app.coach.insights import reliable
from app.coach.store import unpack
from app.ingest.otp_details import FILES_DIR, TIER_ORDER, _slug

router = APIRouter(prefix="/api/otps", tags=["otps"])

TIER_RANK = {t: i for i, t in enumerate(TIER_ORDER)}  # 0 = Aspirante (más alto)
SORTS = ("games", "wr", "elo", "recent", "name")
DETAIL_METRICS = ("kda", "deaths", "kp", "cs_min", "cs10", "gold_min", "vision_min", "control_wards", "obj_participation", "ganks_pre10", "impact_pre10", "first_item_min")


def _entries(db) -> list[dict]:
    """Un OTP = un jugador con un campeón y un rol. Elo y servidor: los de su partida más reciente."""
    groups: dict[tuple, dict] = {}
    for r in db.query(
        "SELECT o.puuid, o.champ, o.role, o.tier, o.division, o.lp, o.platform, o.win, o.game_start, "
        "p.game_name, p.tag_line FROM otp_games o LEFT JOIN players p ON p.puuid = o.puuid WHERE o.kind = 'otp'"
    ):
        key = (r["puuid"], int(r["champ"]), r["role"] or "SIN_ROL")
        g = groups.get(key)
        if g is None:
            g = groups[key] = {
                "puuid": r["puuid"], "champ": int(r["champ"]), "role": key[2], "game_name": r["game_name"],
                "tag_line": r["tag_line"], "games": 0, "wins": 0, "first": None, "last": 0, "tiers": Counter(),
                "tier": None, "division": None, "lp": None, "platform": r["platform"],
            }
        g["games"] += 1
        g["wins"] += int(r["win"] or 0)
        g["tiers"][r["tier"] or "?"] += 1
        start = int(r["game_start"] or 0)
        g["first"] = start if g["first"] is None else min(g["first"], start)
        if start >= g["last"]:
            g.update(last=start, tier=r["tier"], division=r["division"], lp=r["lp"], platform=r["platform"])
    out = []
    for g in groups.values():
        g["wr"] = round(100 * g["wins"] / g["games"], 1) if g["games"] else None
        g["tiers"] = dict(g["tiers"])
        out.append(g)
    return out


def _elo_key(e: dict) -> tuple:
    return (TIER_RANK.get(e["tier"] or "", 99), -(e["lp"] or 0))


@router.get("/library")
def library(svc=Depends(svc_from)):
    entries = _entries(svc.db)
    champs: dict[int, dict] = {}
    by_tier: dict[str, dict] = defaultdict(lambda: {"otps": 0, "games": 0})
    by_platform: dict[str, dict] = defaultdict(lambda: {"otps": 0, "games": 0})
    by_role: dict[str, dict] = defaultdict(lambda: {"otps": 0, "games": 0})
    for e in entries:
        c = champs.setdefault(e["champ"], {"champ": e["champ"], "otps": 0, "games": 0, "wins": 0, "tiers": Counter(), "roles": Counter(), "best": None})
        c["otps"] += 1
        c["games"] += e["games"]
        c["wins"] += e["wins"]
        c["tiers"][e["tier"] or "?"] += 1
        c["roles"][e["role"]] += 1
        if c["best"] is None or _elo_key(e) < _elo_key(c["best"]):
            c["best"] = e
        for bucket, key in ((by_tier, e["tier"] or "?"), (by_platform, e["platform"] or "?"), (by_role, e["role"])):
            bucket[key]["otps"] += 1
            bucket[key]["games"] += e["games"]
    ranking = []
    for c in champs.values():
        best = c.pop("best")
        c["wr"] = round(100 * c["wins"] / c["games"], 1) if c["games"] else None
        c["tiers"] = {t: c["tiers"][t] for t in sorted(c["tiers"], key=lambda t: TIER_RANK.get(t, 99))}
        c["roles"] = dict(c["roles"].most_common())
        c["best_tier"] = best["tier"] if best else None
        c["best_lp"] = best["lp"] if best else None
        ranking.append(c)
    # Más OTPs primero; a igualdad, el de mejor elo y luego el de más partidas.
    ranking.sort(key=lambda c: (-c["otps"], TIER_RANK.get(c["best_tier"] or "", 99), -(c["best_lp"] or 0), -c["games"]))
    for i, c in enumerate(ranking, 1):
        c["rank"] = i
    return {
        "totals": {
            "otps": len(entries),
            "players": len({e["puuid"] for e in entries}),
            "games": sum(e["games"] for e in entries),
            "champions": len(champs),
            "platforms": len(by_platform),
        },
        "champions": ranking,
        "tiers": {t: by_tier[t] for t in sorted(by_tier, key=lambda t: TIER_RANK.get(t, 99))},
        "platforms": dict(sorted(by_platform.items(), key=lambda kv: -kv[1]["otps"])),
        "roles": dict(sorted(by_role.items(), key=lambda kv: -kv[1]["otps"])),
    }


@router.get("/players")
def players(
    champ: int | None = None,
    role: str | None = None,
    tier: str | None = None,
    platform: str | None = None,
    q: str | None = Query(None, max_length=40),
    min_games: int = Query(0, ge=0),
    sort: str = Query("games", pattern="^(games|wr|elo|recent|name)$"),
    limit: int = Query(30, ge=1, le=200),
    offset: int = Query(0, ge=0),
    svc=Depends(svc_from),
):
    rows = _entries(svc.db)
    if champ:
        rows = [e for e in rows if e["champ"] == champ]
    if role:
        rows = [e for e in rows if e["role"] == role.upper()]
    if tier:
        rows = [e for e in rows if (e["tier"] or "") == tier.upper()]
    if platform:
        rows = [e for e in rows if (e["platform"] or "") == platform.lower()]
    if q and q.strip():
        needle = q.strip().lower()
        rows = [e for e in rows if needle in f"{e['game_name'] or ''}#{e['tag_line'] or ''}".lower()]
    if min_games:
        rows = [e for e in rows if e["games"] >= min_games]
    if sort == "wr":
        rows.sort(key=lambda e: (-(e["wr"] or 0) if e["games"] >= 10 else 1, -e["games"]))
    elif sort == "elo":
        rows.sort(key=lambda e: (*_elo_key(e), -e["games"]))
    elif sort == "recent":
        rows.sort(key=lambda e: -e["last"])
    elif sort == "name":
        rows.sort(key=lambda e: ((e["game_name"] or "~").lower(), -e["games"]))
    else:
        rows.sort(key=lambda e: (-e["games"], *_elo_key(e)))
    return {"matched": len(rows), "offset": offset, "players": rows[offset : offset + limit]}


@router.get("/players/{puuid}")
def player(puuid: str, champ: int = Query(...), role: str | None = None, svc=Depends(svc_from)):
    db = svc.db
    params: list = [puuid, champ]
    role_sql = ""
    if role and role.upper() != "SIN_ROL":
        role_sql = " AND role = ?"
        params.append(role.upper())
    rows = db.query(
        f"SELECT match_id, dossier, tier FROM otp_games WHERE puuid = ? AND champ = ? AND kind = 'otp'{role_sql} ORDER BY game_start DESC", params
    )
    if not rows:
        raise HTTPException(404, "No hay partidas analizadas de ese OTP con ese campeón")
    info = db.one("SELECT platform, tier, division, lp, game_name, tag_line FROM players WHERE puuid = ?", (puuid,))
    games, metrics, first_items, skills, keystones = [], defaultdict(list), Counter(), Counter(), Counter()
    tier = rows[0]["tier"]
    for r in rows:
        d = unpack(r["dossier"]) or {}
        a = d.get("analysis") or {}
        m = a.get("metrics") or {}
        for k in DETAIL_METRICS if reliable(a) else ():  # partidas con AFK: se listan, pero no cuentan en sus medias
            if isinstance(m.get(k), (int, float)):
                metrics[k].append(float(m[k]))
        build = a.get("build") or {}
        legend = [o["item"] for o in build.get("order") or [] if o.get("kind") == "legendary"]
        if legend:
            first_items[legend[0]] += 1
        if build.get("skill_max"):
            skills[">".join(build["skill_max"][:3])] += 1
        if (d.get("runes") or {}).get("keystone"):
            keystones[d["runes"]["keystone"]] += 1
        games.append(
            {
                "match_id": r["match_id"], "game_start": d.get("game_start"), "duration": d.get("duration"), "win": d.get("win"),
                "tier": d.get("tier"), "opponent": d.get("opponent_champ"), "patch": d.get("patch"),
                "kills": m.get("kills"), "deaths": m.get("deaths"), "assists": m.get("assists"),
            }
        )
    # Referencia: el resto de OTPs de ese campeón y rol en su mismo elo (medias de su resumen).
    ref = _reference(svc, champ, role, tier)
    catalog = getattr(getattr(svc, "dd", None), "item_catalog", None)
    item_name = catalog.name if catalog is not None and getattr(catalog, "items", None) else (lambda i: None)
    wins = sum(1 for g in games if g["win"])
    return {
        "player": dict(info) if info else {"puuid": puuid},
        "champ": champ,
        "role": role,
        "games_count": len(games),
        "wins": wins,
        "wr": round(100 * wins / len(games), 1),
        "tier": tier,
        "metrics": {
            k: {"mine": round(sum(v) / len(v), 2), "ref": (ref.get(k) or {}).get("mean") if ref else None} for k, v in metrics.items() if v
        },
        "ref_tier": tier if ref else None,
        "build": {
            "first_items": [{"key": i, "name": item_name(i), "games": n} for i, n in first_items.most_common(3)],
            "skill_max": [{"key": k, "games": n} for k, n in skills.most_common(2)],
            "keystones": [{"key": k, "games": n} for k, n in keystones.most_common(2)],
        },
        "games": games[:50],
    }


def _reference(svc, champ: int, role: str | None, tier: str | None) -> dict | None:
    dd = getattr(svc, "dd", None)
    name = dd.name(champ) if dd is not None and hasattr(dd, "name") else str(champ)
    try:
        summary = json.loads((Path(svc.settings.data_dir) / FILES_DIR / _slug(name) / "resumen.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    role_data = (summary.get("roles") or {}).get((role or "").upper()) or {}
    block = (role_data.get("by_tier") or {}).get(tier or "") or role_data.get("all")
    return (block or {}).get("metrics")
