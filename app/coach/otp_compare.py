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
# Lo que más te separa primero (peor), luego lo parecido y al final en lo que les ganas.
_STATUS_ORDER = {"bad": 0, "even": 1, "good": 2}


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
        item = _champ_compare((champ, role), mine, base, dd, target_tier)
        if item is not None:
            out.append(item)
    return out


def _champ_compare(key: tuple, mine: list[dict], base: Path, dd, target_tier: str | None) -> dict | None:
    """Tus medias con un campeón y rol frente a las de sus OTPs (None si no hay OTPs con muestra en ese rol)."""
    champ, role = key
    name = dd.name(champ) if dd is not None and hasattr(dd, "name") else str(champ)
    summary = _summary(base, name)
    role_data = ((summary or {}).get("roles") or {}).get(role)
    if not role_data:
        return None
    target, top = _otp_refs(role_data, target_tier)
    if not target and not top:
        return None
    main = target or top
    rows = _metric_rows(mine, main, top)
    queues = Counter(g.get("queue") for g in mine)
    return {
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


def _otp_refs(role_data: dict, target_tier: str | None) -> tuple[dict | None, dict | None]:
    """Referencias de los OTPs de ese rol: la de tu elo objetivo y la del elo más alto con muestra que no sea el tuyo."""
    by_tier = role_data.get("by_tier") or {}
    target = _ref(by_tier.get(target_tier or ""), target_tier or "")
    top_tier = next((t for t in TIER_ORDER if t in TOP_TIERS and t != target_tier and _ref(by_tier.get(t), t)), None)
    top = _ref(by_tier.get(top_tier), top_tier) if top_tier else None
    return target, top


def _metric_rows(mine: list[dict], main: dict, top: dict | None) -> list[dict]:
    """Tus medias frente a las de los OTPs: primero en lo que peor estás, luego lo parecido y al final en lo que les ganas."""
    rows = []
    for key in COMPARE:
        row = _metric_row(key, mine, main, top)
        if row is not None:
            rows.append(row)
    rows.sort(key=lambda r: (_STATUS_ORDER[r["status"]], -abs(r["gap"])))
    return rows


def _metric_row(key: str, mine: list[dict], main: dict, top: dict | None) -> dict | None:
    """Tu media de esa métrica frente a la de los OTPs (None si te faltan partidas o no tienen referencia)."""
    vals = [g["metrics"].get(key) for g in mine if isinstance(g["metrics"].get(key), (int, float)) and not isinstance(g["metrics"].get(key), bool)]
    ref_main = (main["metrics"].get(key) or {}).get("mean")
    if len(vals) < MIN_MY_GAMES or ref_main is None:
        return None
    value = sum(vals) / len(vals)
    ref_top = _top_mean(top, main, key)
    metric = METRICS.get(key)
    floor = metric.floor if metric else 0.5
    return {
        "metric": key,
        **_metric_info(metric, key),
        "mine": round(value, 2),
        "ref": round(ref_main, 2),
        "ref_top": round(ref_top, 2) if ref_top is not None else None,
        "status": _status(key, value, ref_main),
        "gap": round((value - ref_main) / max(abs(ref_main), floor), 3),
    }


def _top_mean(top: dict | None, main: dict, key: str) -> float | None:
    """Media de esa métrica en el elo más alto (None si no lo hay o es la misma referencia que la principal)."""
    if not top or top is main:
        return None
    return (top.get("metrics", {}).get(key) or {}).get("mean")


def _metric_info(metric, key: str) -> dict:
    """Etiqueta, unidad, decimales y sentido de una métrica (los de por defecto si no está en ``METRICS``)."""
    if not metric:
        return {"label": key, "unit": "", "decimals": 1, "better": "higher"}
    return {"label": metric.label, "unit": metric.unit, "decimals": metric.decimals, "better": metric.better}


def _first(items):
    """El primero de la lista (None si está vacía o no hay)."""
    return items[0] if items else None


def _most_common(counter: Counter) -> dict | None:
    """El valor más repetido y en cuántas partidas (None si no hay ninguno)."""
    if not counter:
        return None
    key, games = counter.most_common(1)[0]
    return {"key": key, "games": games}


def _my_build_counts(mine: list[dict]) -> tuple[Counter, Counter]:
    """Tus primeros objetos y tus órdenes de habilidades (las 3 primeras que maximizas), con sus partidas."""
    my_first = Counter()
    my_skill = Counter()
    for g in mine:
        b = g.get("build") or {}
        items = b.get("items") or []
        if items:
            my_first[items[0]] += 1
        if b.get("skill_max"):
            my_skill[">".join(b["skill_max"][:3])] += 1
    return my_first, my_skill


def _build(ref_block: dict, top_block: dict | None, mine: list[dict], dd) -> dict:
    """Su primer objeto, orden de habilidades y runa principal más jugados, y los tuyos."""
    my_first, my_skill = _my_build_counts(mine)
    catalog = getattr(dd, "item_catalog", None)
    name = catalog.name if catalog is not None and getattr(catalog, "items", None) else None
    out = {
        "otp_first_item": _first(ref_block.get("first_legendary")),
        "otp_skill_max": _first(ref_block.get("skill_max")),
        "otp_keystone": _first(ref_block.get("keystones")),
        "top_first_item": _first(top_block.get("first_legendary")) if top_block else None,
        "my_first_item": _most_common(my_first),
        "my_skill_max": _most_common(my_skill),
    }
    if name and out["my_first_item"]:
        out["my_first_item"]["value"] = name(out["my_first_item"]["key"])
    return out
