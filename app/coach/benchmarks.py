"""Métricas de tus partidas y valores de referencia por rol y elo.

Las referencias son **orientativas**: medias aproximadas de clasificatoria
en cada rol, escaladas según el elo objetivo de tu perfil. Si el
recolector ya ha guardado bastantes partidas de ese elo (≥ 300 por rol), las
métricas que existen en tu base de datos (CS/min, visión, muertes, KP…)
se sustituyen por las medias reales de esas partidas.
"""

from __future__ import annotations

from dataclasses import dataclass

from app.analytics.filters import Filters
from app.analytics.stats import safe_div

ROLES = ("TOP", "JUNGLE", "MID", "ADC", "SUPPORT")
TARGET_TIERS = ("SILVER", "GOLD", "PLATINUM", "EMERALD", "DIAMOND", "MASTER")
DEFAULT_TARGET = "PLATINUM"
MIN_DB_GAMES = 300


@dataclass(frozen=True)
class Metric:
    key: str
    label: str
    better: str  # "higher" | "lower"
    category: str
    unit: str = ""
    decimals: int = 1
    roles: tuple[str, ...] | None = None  # None = todos
    scale: bool = True  # se ajusta al elo objetivo
    floor: float = 0.5  # para normalizar métricas que valen ~0
    count: bool = False  # recuento por partida (muertes, objetivos…): se valora con su incertidumbre (Poisson)


CATEGORIES = {
    "jungla_rival": "Trackear al jungla rival",
    "muertes": "Muertes",
    "tiempo": "Uso del tiempo",
    "farm": "Farm y fase de líneas",
    "vision": "Visión",
    "objetivos": "Objetivos",
    "pathing": "Pathing de jungla",
    "peleas": "Peleas",
    "build": "Build y compras",
}

METRICS: dict[str, Metric] = {
    m.key: m
    for m in [
        Metric("deaths_to_jungler_pre14", "Muertes por el jungla rival (antes del 14)", "lower", "jungla_rival", decimals=2, roles=("TOP", "MID", "ADC", "SUPPORT"), count=True),
        Metric("deaths_ganked", "Muertes por gank (antes del 14)", "lower", "jungla_rival", decimals=2, roles=("TOP", "MID", "ADC", "SUPPORT"), count=True),
        Metric("deaths_no_vision_pre14", "Muertes sin ward previo (antes del 14)", "lower", "jungla_rival", decimals=2, count=True),
        Metric("deaths_overextended", "Muertes adelantado en la línea", "lower", "jungla_rival", decimals=2, roles=("TOP", "MID", "ADC", "SUPPORT"), count=True),
        Metric("deaths_pre14", "Muertes antes del minuto 14", "lower", "muertes", decimals=2, count=True),
        Metric("deaths", "Muertes por partida", "lower", "muertes", count=True),
        Metric("deaths_solo", "Muertes en 1 contra 1", "lower", "muertes", decimals=2, count=True),
        Metric("deaths_enemy_jungle", "Muertes en la jungla rival", "lower", "muertes", decimals=2, count=True),
        Metric("deaths_isolated_late", "Muertes aislado (después del 14)", "lower", "muertes", decimals=2, count=True),
        Metric("deaths_unspent_gold", "Muertes con oro para tu build sin gastar", "lower", "tiempo", decimals=2, count=True),
        Metric("time_dead_pct", "Tiempo muerto", "lower", "tiempo", unit="%"),
        Metric("gold_hoard_min", "Minutos de más con oro útil sin gastar", "lower", "tiempo", decimals=1),
        Metric("idle_pct", "Minutos vacíos desde el 15", "lower", "tiempo", unit="%"),
        Metric("cs_min", "CS por minuto", "higher", "farm", decimals=2, roles=("TOP", "JUNGLE", "MID", "ADC")),
        Metric("cs10", "CS a los 10", "higher", "farm", decimals=0, roles=("TOP", "JUNGLE", "MID", "ADC")),
        Metric("cs_diff10", "Δ CS a los 10 vs tu rival", "higher", "farm", decimals=1, roles=("TOP", "JUNGLE", "MID", "ADC"), scale=False, floor=20),
        Metric("gold_diff15", "Δ oro a los 15 vs tu rival", "higher", "farm", decimals=0, scale=False, floor=1500),
        Metric("gold_min", "Oro por minuto", "higher", "farm", decimals=0),
        Metric("vision_min", "Visión por minuto", "higher", "vision", decimals=2),
        Metric("wards_pre14", "Wards antes del 14", "higher", "vision", decimals=1, count=True),
        Metric("control_wards", "Pinks compradas", "higher", "vision", decimals=1, count=True),
        Metric("obj_participation", "Participación en objetivos", "higher", "objetivos", unit="%"),
        Metric("obj_lost_dead", "Objetivos rivales estando tú muerto", "lower", "objetivos", decimals=2, count=True),
        Metric("obj_lost_far", "Objetivos regalados (lejos y sin cambio)", "lower", "objetivos", decimals=2, count=True),
        Metric("deaths_before_objective", "Muertes justo antes de un objetivo rival", "lower", "objetivos", decimals=2, count=True),
        Metric("kp", "Participación en kills", "higher", "peleas", unit="%"),
        Metric("dmg_share", "Daño del equipo", "higher", "peleas", unit="%", roles=("TOP", "MID", "ADC")),
        Metric("ganks_pre10", "Ganks con kill antes del 10", "higher", "pathing", decimals=2, roles=("JUNGLE",), count=True),
        Metric("impact_pre10", "Jugadas con impacto antes del 10", "higher", "pathing", decimals=1, roles=("JUNGLE",), floor=1, count=True),
        Metric("jungle_cs10", "CS de jungla a los 10", "higher", "pathing", decimals=0, roles=("JUNGLE",)),
        Metric("enemy_ganks_far", "Ganks rivales en la otra mitad del mapa", "lower", "pathing", decimals=2, roles=("JUNGLE",), count=True),
        Metric("first_item_min", "Minuto de tu primer objeto", "lower", "build", decimals=1, floor=5),
        Metric("r_late", "Subidas de la R tardías", "lower", "build", decimals=2, count=True),
    ]
}

# Referencia base (≈ Esmeralda). None = no aplica al rol.
BASE: dict[str, dict[str, float | None]] = {
    "TOP": {
        "deaths_to_jungler_pre14": 0.5, "deaths_ganked": 0.6, "deaths_no_vision_pre14": 0.5, "deaths_overextended": 0.45,
        "deaths_pre14": 1.3, "deaths": 5.2, "deaths_solo": 1.1, "deaths_enemy_jungle": 0.5, "deaths_isolated_late": 1.3,
        "deaths_unspent_gold": 0.35, "time_dead_pct": 7.5, "gold_hoard_min": 0.6, "idle_pct": 15,
        "cs_min": 7.1, "cs10": 71, "cs_diff10": 0, "gold_diff15": 0, "gold_min": 400,
        "vision_min": 0.65, "wards_pre14": 4, "control_wards": 1.8,
        "obj_participation": 40, "obj_lost_dead": 0.3, "obj_lost_far": 0.6, "deaths_before_objective": 0.3,
        "kp": 46, "dmg_share": 22, "first_item_min": 12.0, "r_late": 0.2,
    },
    "JUNGLE": {
        "deaths_to_jungler_pre14": None, "deaths_ganked": None, "deaths_no_vision_pre14": 0.5, "deaths_overextended": None,
        "deaths_pre14": 1.3, "deaths": 5.3, "deaths_solo": 0.8, "deaths_enemy_jungle": 0.9, "deaths_isolated_late": 1.0,
        "deaths_unspent_gold": 0.35, "time_dead_pct": 7.5, "gold_hoard_min": 0.5, "idle_pct": 12,
        "cs_min": 6.2, "cs10": 62, "cs_diff10": 0, "gold_diff15": 0, "gold_min": 385,
        "vision_min": 1.15, "wards_pre14": 6, "control_wards": 3.0,
        "obj_participation": 78, "obj_lost_dead": 0.3, "obj_lost_far": 0.4, "deaths_before_objective": 0.3,
        "kp": 60, "dmg_share": None, "first_item_min": 11.5, "r_late": 0.2,
        "ganks_pre10": 1.2, "impact_pre10": 2.5, "jungle_cs10": 55, "enemy_ganks_far": 0.6,
    },
    "MID": {
        "deaths_to_jungler_pre14": 0.45, "deaths_ganked": 0.6, "deaths_no_vision_pre14": 0.5, "deaths_overextended": 0.4,
        "deaths_pre14": 1.3, "deaths": 5.0, "deaths_solo": 1.0, "deaths_enemy_jungle": 0.5, "deaths_isolated_late": 1.1,
        "deaths_unspent_gold": 0.35, "time_dead_pct": 7.0, "gold_hoard_min": 0.6, "idle_pct": 15,
        "cs_min": 7.5, "cs10": 75, "cs_diff10": 0, "gold_diff15": 0, "gold_min": 410,
        "vision_min": 0.75, "wards_pre14": 4, "control_wards": 2.0,
        "obj_participation": 52, "obj_lost_dead": 0.3, "obj_lost_far": 0.5, "deaths_before_objective": 0.3,
        "kp": 54, "dmg_share": 26, "first_item_min": 11.5, "r_late": 0.2,
    },
    "ADC": {
        "deaths_to_jungler_pre14": 0.5, "deaths_ganked": 0.7, "deaths_no_vision_pre14": 0.6, "deaths_overextended": 0.4,
        "deaths_pre14": 1.5, "deaths": 5.6, "deaths_solo": 0.7, "deaths_enemy_jungle": 0.4, "deaths_isolated_late": 1.2,
        "deaths_unspent_gold": 0.35, "time_dead_pct": 8.0, "gold_hoard_min": 0.6, "idle_pct": 15,
        "cs_min": 7.9, "cs10": 76, "cs_diff10": 0, "gold_diff15": 0, "gold_min": 430,
        "vision_min": 0.7, "wards_pre14": 4, "control_wards": 1.6,
        "obj_participation": 58, "obj_lost_dead": 0.3, "obj_lost_far": 0.6, "deaths_before_objective": 0.3,
        "kp": 52, "dmg_share": 27, "first_item_min": 12.0, "r_late": 0.2,
    },
    "SUPPORT": {
        "deaths_to_jungler_pre14": 0.55, "deaths_ganked": 0.7, "deaths_no_vision_pre14": 0.4, "deaths_overextended": 0.4,
        "deaths_pre14": 1.6, "deaths": 6.0, "deaths_solo": 0.8, "deaths_enemy_jungle": 0.7, "deaths_isolated_late": 1.4,
        "deaths_unspent_gold": 0.2, "time_dead_pct": 8.5, "gold_hoard_min": 0.4, "idle_pct": 15,
        "cs_min": None, "cs10": None, "cs_diff10": None, "gold_diff15": 0, "gold_min": 270,
        "vision_min": 2.3, "wards_pre14": 12, "control_wards": 5.0,
        "obj_participation": 66, "obj_lost_dead": 0.3, "obj_lost_far": 0.5, "deaths_before_objective": 0.3,
        "kp": 62, "dmg_share": None, "first_item_min": 14.0, "r_late": 0.2,
    },
}

# Factor sobre la referencia base según el elo objetivo.
UP = {"IRON": 0.72, "BRONZE": 0.78, "SILVER": 0.85, "GOLD": 0.91, "PLATINUM": 0.96, "EMERALD": 1.0, "DIAMOND": 1.05, "MASTER": 1.09}
DOWN = {"IRON": 1.45, "BRONZE": 1.35, "SILVER": 1.25, "GOLD": 1.15, "PLATINUM": 1.07, "EMERALD": 1.0, "DIAMOND": 0.93, "MASTER": 0.88}


def normalize_tier(tier: str | None) -> str:
    tier = (tier or "").upper()
    return tier if tier in UP else DEFAULT_TARGET


def static_refs(role: str, tier: str) -> dict[str, float]:
    tier = normalize_tier(tier)
    base = BASE.get(role) or {}
    out: dict[str, float] = {}
    for key, value in base.items():
        metric = METRICS.get(key)
        if value is None or metric is None:
            continue
        if metric.scale:
            value = value * (UP[tier] if metric.better == "higher" else DOWN[tier])
        out[key] = round(value, 3)
    return out


def _db_refs(svc, role: str, tier: str) -> dict[str, float]:
    """Medias reales de tu BD para ese rol y elo (si hay muestra suficiente)."""
    try:
        f = Filters.parse(svc.db, patch="recent", tier=tier)
        row = svc.agg.role_totals(f).get(role)
    except Exception:  # BD vacía o en migración: se usan las de referencia
        return {}
    if not row or (row.get("dgames") or 0) < MIN_DB_GAMES or not row.get("minutes"):
        return {}
    games, minutes = row["dgames"], row["minutes"]
    out = {
        "deaths": row["deaths"] / games,
        "gold_min": row["gold"] / minutes,
        "vision_min": row["vision"] / minutes,
    }
    if role != "SUPPORT":
        out["cs_min"] = row["cs"] / minutes
        cs10 = safe_div(row.get("cs10_sum") or 0, row.get("cs10_n") or 0)
        if cs10:
            out["cs10"] = cs10
    kp = safe_div(row.get("kp_sum") or 0, row.get("kp_n") or 0)
    if kp:
        out["kp"] = kp * 100
    share = safe_div(row.get("dshare_sum") or 0, row.get("dshare_n") or 0)
    if share and role in ("TOP", "MID", "ADC"):
        out["dmg_share"] = share * 100
    return {k: round(v, 3) for k, v in out.items()}


class References:
    """Referencias por rol para un elo objetivo (con caché por petición)."""

    def __init__(self, svc, tier: str | None) -> None:
        self.svc = svc
        self.tier = normalize_tier(tier)
        self._cache: dict[str, dict] = {}

    def for_role(self, role: str | None) -> dict[str, float]:
        if not role:
            return {}
        if role not in self._cache:
            refs = static_refs(role, self.tier)
            db = _db_refs(self.svc, role, self.tier) if self.svc is not None else {}
            refs.update(db)
            self._cache[role] = refs
            self._cache[f"{role}:db"] = db
        return self._cache[role]

    def source(self, role: str) -> str:
        self.for_role(role)
        return "db" if self._cache.get(f"{role}:db") else "static"


def applies(metric: Metric, role: str | None) -> bool:
    return metric.roles is None or (role in metric.roles if role else False)


def public_metrics() -> list[dict]:
    return [
        {
            "key": m.key,
            "label": m.label,
            "better": m.better,
            "category": m.category,
            "category_label": CATEGORIES.get(m.category, m.category),
            "unit": m.unit,
            "decimals": m.decimals,
            "floor": m.floor,
            "roles": list(m.roles) if m.roles else None,
        }
        for m in METRICS.values()
    ]
