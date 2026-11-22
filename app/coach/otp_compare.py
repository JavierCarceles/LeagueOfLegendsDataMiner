"""Tú frente a los OTPs de tu campeón (data/otps): en tu elo objetivo y en el más alto que haya.

Para cada campeón y rol que juegas a menudo, compara tus medias con las de sus OTPs (los ``resumen.json`` que escribe
el recolector, por rol y por elo), y su build más jugada con la tuya. Así «Mi progreso» no solo te compara con la media
de un rol, sino con quien mejor juega tu campeón.
"""

from __future__ import annotations

import json
from collections import Counter, defaultdict
from pathlib import Path

from app.coach.benchmarks import METRICS
from app.ingest.otp_details import FILES_DIR, TIER_ORDER, _slug

MIN_MY_GAMES = 5  # partidas tuyas con ese campeón y rol para compararlas
MIN_OTP_GAMES = 40  # partidas de OTPs de un elo para usarlo como referencia
MIN_OTP_PLAYERS = 3
GAP = 0.10  # ±10 % frente a la referencia: por encima o por debajo; dentro, «parecido»
TOP_TIERS = ("CHALLENGER", "GRANDMASTER", "MASTER", "DIAMOND")
# Las que tienen sentido comparar con OTPs (las del resumen de cada partida).
COMPARE = (
    "deaths", "deaths_pre14", "kp", "cs_min", "cs10", "jungle_cs10", "gold_min", "vision_min", "wards_pre14",
    "control_wards", "obj_participation", "obj_lost_far", "obj_lost_dead", "deaths_before_objective", "impact_pre10",
    "ganks_pre10", "first_item_min", "time_dead_pct", "gold_hoard_min", "deaths_isolated_late", "deaths_solo",
    "deaths_enemy_jungle", "enemy_ganks_far", "idle_pct", "dmg_share",
)


def _summary(base: Path, champ_name: str) -> dict | None:
    try:
        return json.loads((base / _slug(champ_name) / "resumen.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def _ref(block: dict | None, tier: str) -> dict | None:
    if not block or block.get("games", 0) < MIN_OTP_GAMES or block.get("players", 0) < MIN_OTP_PLAYERS:
        return None
    return {"tier": tier, "games": block["games"], "players": block.get("players"), "wr": block.get("wr"), "metrics": block.get("metrics") or {}, "block": block}


def _status(key: str, mine: float, ref: float) -> str:
    metric = METRICS.get(key)
    floor = metric.floor if metric else 0.5
    gap = (mine - ref) / max(abs(ref), floor)
    better = metric.better if metric else "higher"
    if abs(gap) < GAP:
        return "even"
    return "good" if (gap > 0) == (better == "higher") else "bad"


def compare(svc, games: list[dict], target_tier: str | None) -> list[dict]:
    """Por campeón y rol (de más a menos partidas tuyas): tus medias, las de sus OTPs y la build que más juegan."""
    dd = getattr(svc, "dd", None)
    base = Path(svc.settings.data_dir) / FILES_DIR
    if not base.is_dir():
        return []
    groups: dict[tuple, list[dict]] = defaultdict(list)
    for g in games:
        if g.get("champ") and g.get("role") and g.get("has_timeline"):
            groups[(g["champ"], g["role"])].append(g)
    out = []
    for (champ, role), mine in sorted(groups.items(), key=lambda kv: -len(kv[1])):
        if len(mine) < MIN_MY_GAMES:
            continue
        name = dd.name(champ) if dd is not None and hasattr(dd, "name") else str(champ)
        summary = _summary(base, name)
        role_data = ((summary or {}).get("roles") or {}).get(role)
        if not role_data:
            continue
        by_tier = role_data.get("by_tier") or {}
        target = _ref(by_tier.get(target_tier or ""), target_tier or "")
        top_tier = next((t for t in TIER_ORDER if t in TOP_TIERS and t != target_tier and _ref(by_tier.get(t), t)), None)
        top = _ref(by_tier.get(top_tier), top_tier) if top_tier else None
        if not target and not top:
            continue
        main = target or top
        rows = []
        for key in COMPARE:
            vals = [g["metrics"].get(key) for g in mine if isinstance(g["metrics"].get(key), (int, float)) and not isinstance(g["metrics"].get(key), bool)]
            ref_main = (main["metrics"].get(key) or {}).get("mean")
            if len(vals) < MIN_MY_GAMES or ref_main is None:
                continue
            value = sum(vals) / len(vals)
            ref_top = ((top or {}).get("metrics", {}).get(key) or {}).get("mean") if top and top is not main else None
            metric = METRICS.get(key)
            floor = metric.floor if metric else 0.5
            rows.append(
                {
                    "metric": key,
                    "label": metric.label if metric else key,
                    "unit": metric.unit if metric else "",
                    "decimals": metric.decimals if metric else 1,
                    "better": metric.better if metric else "higher",
                    "mine": round(value, 2),
                    "ref": round(ref_main, 2),
                    "ref_top": round(ref_top, 2) if ref_top is not None else None,
                    "status": _status(key, value, ref_main),
                    "gap": round((value - ref_main) / max(abs(ref_main), floor), 3),
                }
            )
        # Lo que más te separa primero (peor), luego lo parecido y al final en lo que les ganas.
        order = {"bad": 0, "even": 1, "good": 2}
        rows.sort(key=lambda r: (order[r["status"]], -abs(r["gap"])))
        queues = Counter(g.get("queue") for g in mine)
        out.append(
            {
                "champ": champ,
                "role": role,
                "my_games": len(mine),
                "my_wr": round(100 * sum(1 for g in mine if g.get("win")) / len(mine), 1),
                "ranked_share": round(sum(n for q, n in queues.items() if q in (420, 440)) / len(mine), 2),
                "ref": {k: main[k] for k in ("tier", "games", "players", "wr")},
                "ref_top": {k: top[k] for k in ("tier", "games", "players", "wr")} if top and top is not main else None,
                "metrics": rows,
                "build": _build(main["block"], top["block"] if top else None, mine, dd),
            }
        )
    return out


def _build(ref_block: dict, top_block: dict | None, mine: list[dict], dd) -> dict:
    """Su primer objeto, orden de habilidades y runa principal más jugados, y los tuyos."""
    def first(items):
        return items[0] if items else None

    my_first = Counter()
    my_skill = Counter()
    for g in mine:
        b = g.get("build") or {}
        items = b.get("items") or []
        if items:
            my_first[items[0]] += 1
        if b.get("skill_max"):
            my_skill[">".join(b["skill_max"][:3])] += 1
    catalog = getattr(dd, "item_catalog", None)
    name = catalog.name if catalog is not None and getattr(catalog, "items", None) else None
    out = {
        "otp_first_item": first(ref_block.get("first_legendary")),
        "otp_skill_max": first(ref_block.get("skill_max")),
        "otp_keystone": first(ref_block.get("keystones")),
        "top_first_item": first((top_block or {}).get("first_legendary")) if top_block else None,
        "my_first_item": {"key": my_first.most_common(1)[0][0], "games": my_first.most_common(1)[0][1]} if my_first else None,
        "my_skill_max": {"key": my_skill.most_common(1)[0][0], "games": my_skill.most_common(1)[0][1]} if my_skill else None,
    }
    if name and out["my_first_item"]:
        out["my_first_item"]["value"] = name(out["my_first_item"]["key"])
    return out
