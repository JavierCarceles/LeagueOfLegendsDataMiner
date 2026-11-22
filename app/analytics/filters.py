"""Filtros comunes (parche, elo, región) traducidos a SQL sobre los agregados."""

from __future__ import annotations

from dataclasses import dataclass

from app.data.db import Database
from app.riot.routing import PLATFORMS, REGIONS, TIERS, UNRANKED, platforms_in_region, tiers_from

LEGACY = "legacy"
TIER_PRESETS = {
    "gold_plus": tiers_from("GOLD"),
    "platinum_plus": tiers_from("PLATINUM"),
    "emerald_plus": tiers_from("EMERALD"),
    "diamond_plus": tiers_from("DIAMOND"),
    "master_plus": tiers_from("MASTER"),
    "low_elo": ["IRON", "BRONZE", "SILVER", "GOLD"],
}


def patch_sort_key(patch: str) -> tuple:
    try:
        major, minor = patch.split(".")[:2]
        return (int(major), int(minor))
    except ValueError:
        return (-1, -1)


def available_patches(db: Database) -> list[str]:
    rows = db.query("SELECT DISTINCT patch FROM agg_totals WHERE patch <> ?", (LEGACY,))
    return sorted((r[0] for r in rows), key=patch_sort_key, reverse=True)


@dataclass(frozen=True)
class Filters:
    patches: tuple[str, ...] | None = None
    exclude_legacy: bool = False
    tiers: tuple[str, ...] | None = None
    platforms: tuple[str, ...] | None = None
    label_patch: str = "all"
    label_tier: str = "all"
    label_platform: str = "all"

    @classmethod
    def parse(cls, db: Database, patch: str | None = None, tier: str | None = None, platform: str | None = None) -> Filters:
        patch = (patch or "all").strip()
        tier = (tier or "all").strip()
        platform = (platform or "all").strip().lower()

        patches: tuple[str, ...] | None = None
        exclude_legacy = False
        if patch in ("", "all"):
            pass
        elif patch in ("recent", "new"):
            exclude_legacy = True
        elif patch == "latest" or patch.startswith("last"):
            n = 1 if patch == "latest" else max(1, int(patch[4:] or 1)) if patch[4:].isdigit() else 3
            patches = tuple(available_patches(db)[:n]) or ("__none__",)
        else:
            patches = tuple(p.strip() for p in patch.split(",") if p.strip())

        tiers: tuple[str, ...] | None = None
        if tier.lower() in TIER_PRESETS:
            tiers = tuple(TIER_PRESETS[tier.lower()])
        elif tier.lower() not in ("", "all"):
            valid = set(TIERS) | {UNRANKED}
            tiers = tuple(t for t in (x.strip().upper() for x in tier.split(",")) if t in valid) or None

        platforms: tuple[str, ...] | None = None
        if platform not in ("", "all"):
            selected: list[str] = []
            for item in (x.strip() for x in platform.split(",")):
                if item in REGIONS:
                    selected.extend(platforms_in_region(item))
                elif item in PLATFORMS:
                    selected.append(item)
            platforms = tuple(dict.fromkeys(selected)) or None

        return cls(patches, exclude_legacy, tiers, platforms, patch, tier, platform)

    @property
    def includes_legacy(self) -> bool:
        if self.platforms is not None or self.exclude_legacy:
            return False
        return self.patches is None or LEGACY in self.patches

    def clause(self, alias: str = "") -> tuple[str, list]:
        prefix = f"{alias}." if alias else ""
        parts: list[str] = []
        params: list = []
        if self.patches is not None:
            parts.append(f"{prefix}patch IN ({','.join('?' * len(self.patches))})")
            params.extend(self.patches)
        elif self.exclude_legacy:
            parts.append(f"{prefix}patch <> '{LEGACY}'")
        if self.tiers:
            parts.append(f"{prefix}tier IN ({','.join('?' * len(self.tiers))})")
            params.extend(self.tiers)
        if self.platforms:
            parts.append(f"{prefix}platform IN ({','.join('?' * len(self.platforms))})")
            params.extend(self.platforms)
        return (" AND ".join(parts) if parts else "1=1"), params

    def match_clause(self, alias: str = "m") -> tuple[str, list]:
        """Igual que :meth:`clause` pero para la tabla ``matches`` (sin legacy)."""
        sql, params = self.clause(alias)
        return f"{alias}.aggregated = 1 AND {sql}", params

    def cache_key(self) -> str:
        return f"{self.patches}|{self.exclude_legacy}|{self.tiers}|{self.platforms}"

    def public(self) -> dict:
        return {
            "patch": self.label_patch,
            "tier": self.label_tier,
            "platform": self.label_platform,
            "patches": list(self.patches) if self.patches is not None else None,
            "tiers": list(self.tiers) if self.tiers else None,
            "platforms": list(self.platforms) if self.platforms else None,
            "includes_legacy": self.includes_legacy,
        }
