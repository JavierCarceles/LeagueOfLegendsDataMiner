"""Plataformas, routing regional, colas, tiers y roles.

Riot usa dos tipos de host:

* **Plataforma** (``euw1``, ``kr``…): summoner-v4, league-v4, status-v4.
* **Región** (``europe``, ``americas``, ``asia``, ``sea``): match-v5 y account-v1.

Los límites de la API key se aplican *por host*, así que cada región se puede
explotar en paralelo (el código antiguo iba en serie y además enviaba OCE/SEA
a ``asia``, donde match-v5 no los encuentra).
"""

from __future__ import annotations

# plataforma -> (región match-v5, etiqueta corta, nombre)
PLATFORMS: dict[str, tuple[str, str, str]] = {
    "euw1": ("europe", "EUW", "Europa Oeste"),
    "eun1": ("europe", "EUNE", "Europa Nórdica y Este"),
    "tr1": ("europe", "TR", "Turquía"),
    "ru": ("europe", "RU", "Rusia"),
    "me1": ("europe", "ME", "Oriente Medio"),
    "na1": ("americas", "NA", "Norteamérica"),
    "br1": ("americas", "BR", "Brasil"),
    "la1": ("americas", "LAN", "Latinoamérica Norte"),
    "la2": ("americas", "LAS", "Latinoamérica Sur"),
    "kr": ("asia", "KR", "Corea"),
    "jp1": ("asia", "JP", "Japón"),
    "oc1": ("sea", "OCE", "Oceanía"),
    "sg2": ("sea", "SEA", "Sudeste Asiático"),
    "tw2": ("sea", "TW", "Taiwán"),
    "vn2": ("sea", "VN", "Vietnam"),
}

# Plataformas fusionadas en SG2 (2025) que aún aparecen en IDs antiguos.
LEGACY_PLATFORMS = {"ph2": "sea", "th2": "sea"}

REGIONS = ("europe", "americas", "asia", "sea")
REGION_LABELS = {"europe": "Europa", "americas": "América", "asia": "Asia", "sea": "SEA / Oceanía"}

# account-v1 solo existe en americas/asia/europe (los datos son globales).
ACCOUNT_ROUTE = {"europe": "europe", "americas": "americas", "asia": "asia", "sea": "asia"}

ROLES = ("TOP", "JUNGLE", "MID", "ADC", "SUPPORT")
ROLE_INDEX = {role: i for i, role in enumerate(ROLES)}
ROLE_LABELS = {"TOP": "Top", "JUNGLE": "Jungla", "MID": "Mid", "ADC": "ADC", "SUPPORT": "Support"}
TEAM_POSITION_TO_ROLE = {
    "TOP": "TOP",
    "JUNGLE": "JUNGLE",
    "MIDDLE": "MID",
    "BOTTOM": "ADC",
    "UTILITY": "SUPPORT",
}

TIERS = (
    "IRON",
    "BRONZE",
    "SILVER",
    "GOLD",
    "PLATINUM",
    "EMERALD",
    "DIAMOND",
    "MASTER",
    "GRANDMASTER",
    "CHALLENGER",
)
TIER_INDEX = {tier: i for i, tier in enumerate(TIERS)}
APEX_TIERS = ("MASTER", "GRANDMASTER", "CHALLENGER")
DIVISIONS = ("I", "II", "III", "IV")
UNRANKED = "UNRANKED"
TIER_LABELS = {
    "IRON": "Hierro",
    "BRONZE": "Bronce",
    "SILVER": "Plata",
    "GOLD": "Oro",
    "PLATINUM": "Platino",
    "EMERALD": "Esmeralda",
    "DIAMOND": "Diamante",
    "MASTER": "Maestro",
    "GRANDMASTER": "Gran Maestro",
    "CHALLENGER": "Aspirante",
    "UNRANKED": "Sin clasificar",
}

QUEUE_SOLO = 420
QUEUE_FLEX = 440
LEAGUE_QUEUE = {QUEUE_SOLO: "RANKED_SOLO_5x5", QUEUE_FLEX: "RANKED_FLEX_SR"}
QUEUES = {
    420: "Clasificatoria Solo/Dúo",
    440: "Clasificatoria Flexible",
    400: "Normal (Reclutamiento)",
    430: "Normal (Selección oculta)",
    480: "Swiftplay",
    490: "Partida rápida",
    700: "Clash",
    450: "ARAM",
    900: "ARURF",
    1020: "Uno para todos",
    1300: "Nexus Blitz",
    1700: "Arena",
    1710: "Arena",
    1900: "URF",
}
SUMMONERS_RIFT = 11


def region_of(platform: str) -> str:
    platform = (platform or "").lower()
    if platform in PLATFORMS:
        return PLATFORMS[platform][0]
    if platform in LEGACY_PLATFORMS:
        return LEGACY_PLATFORMS[platform]
    raise ValueError(f"Plataforma desconocida: {platform}")


def platform_from_match_id(match_id: str) -> str:
    """``EUW1_7234`` -> ``euw1``."""
    return match_id.split("_", 1)[0].lower()


def account_route(platform: str) -> str:
    return ACCOUNT_ROUTE[region_of(platform)]


def platforms_in_region(region: str) -> list[str]:
    return [p for p, (r, _, _) in PLATFORMS.items() if r == region]


def normalize_tier(tier: str | None) -> str:
    tier = (tier or "").upper().strip()
    return tier if tier in TIER_INDEX else UNRANKED


def tiers_from(minimum: str) -> list[str]:
    """Todos los tiers desde ``minimum`` hacia arriba."""
    idx = TIER_INDEX.get(minimum.upper(), 0)
    return list(TIERS[idx:])
