"""Estadísticas por región (plataforma): meta, objetivos, duración, elo."""

from __future__ import annotations

from app.analytics.champions import tier_list
from app.analytics.filters import Filters, patch_sort_key
from app.analytics.matches import OBJECTIVES
from app.analytics.stats import pct, safe_div, wilson
from app.riot.routing import PLATFORMS, ROLES, TIER_INDEX, TIERS


def overview(svc, f: Filters) -> dict:
    where, params = f.match_clause("m")
    rows = svc.db.query(
        f"SELECT m.platform AS platform, COUNT(*) AS matches, AVG(m.duration) / 60.0 AS minutes, "
        f"AVG(m.winner = 100) AS blue_wr, AVG(m.surrender) AS surrender, MAX(m.game_start) AS latest "
        f"FROM matches m WHERE {where} GROUP BY m.platform",
        params,
    )
    tiers: dict[str, dict[str, int]] = {}
    for r in svc.db.query(f"SELECT m.platform, m.tier, COUNT(*) FROM matches m WHERE {where} GROUP BY m.platform, m.tier", params):
        tiers.setdefault(r[0], {})[r[1]] = r[2]
    platforms = []
    for r in rows:
        dist = tiers.get(r["platform"], {})
        top_tier = max(dist, key=dist.get) if dist else None
        platforms.append(
            {
                "platform": r["platform"],
                "label": PLATFORMS.get(r["platform"], ("", r["platform"].upper(), ""))[1],
                "name": PLATFORMS.get(r["platform"], ("", "", r["platform"]))[2],
                "region": PLATFORMS.get(r["platform"], ("?", "", ""))[0],
                "matches": r["matches"],
                "avg_minutes": round(r["minutes"] or 0, 1),
                "blue_wr": pct(r["blue_wr"]),
                "surrender": pct(r["surrender"]),
                "latest": r["latest"],
                "tiers": dist,
                "main_tier": top_tier,
            }
        )
    platforms.sort(key=lambda p: p["matches"], reverse=True)
    legacy = svc.db.scalar("SELECT SUM(matches) FROM agg_totals WHERE patch = 'legacy'", default=0)
    return {"filters": f.public(), "platforms": platforms, "legacy_matches": legacy}


def region_detail(svc, f: Filters, platform: str) -> dict:
    # La región la decide la ruta (``all`` = todas); el filtro global de región no aplica.
    f = Filters.parse(svc.db, patch=f.label_patch, tier=f.label_tier, platform=platform)
    where, params = f.match_clause("m")
    base = svc.db.one(
        f"SELECT COUNT(*) AS n, SUM(m.winner = 100) AS blue, AVG(m.duration) / 60.0 AS minutes, AVG(m.surrender) AS surrender, "
        f"AVG(m.blue_dragons + m.red_dragons) AS dragons, AVG(m.blue_barons + m.red_barons) AS barons, "
        f"AVG(m.blue_towers + m.red_towers) AS towers, AVG(m.blue_grubs + m.red_grubs) AS grubs, "
        f"AVG(m.blue_kills + m.red_kills) AS kills "
        f"FROM matches m WHERE {where}",
        params,
    )
    n = base["n"] or 0
    remake_where = where.replace("m.aggregated = 1 AND ", "")
    remakes = svc.db.scalar(f"SELECT COUNT(*) FROM matches m WHERE m.is_remake = 1 AND {remake_where}", params, default=0)

    objectives = []
    for key, label in OBJECTIVES:
        row = svc.db.one(
            f"SELECT COUNT(m.{key}) AS taken, SUM(m.{key} = m.winner) AS converted, SUM(m.{key} = 100) AS blue "
            f"FROM matches m WHERE {where}",
            params,
        )
        taken = row["taken"] or 0
        if not taken:
            continue
        lo, hi = wilson(row["converted"] or 0, taken)
        objectives.append(
            {
                "key": key,
                "label": label,
                "taken_rate": pct(safe_div(taken, n)),
                "win_rate": pct(safe_div(row["converted"] or 0, taken)),
                "ci": [pct(lo), pct(hi)],
                "blue_share": pct(safe_div(row["blue"] or 0, taken)),
            }
        )
    objectives.sort(key=lambda o: o["win_rate"] or 0, reverse=True)

    duration = []
    for r in svc.db.query(
        f"SELECT MIN(MAX(CAST(m.duration / 300 AS INTEGER), 3), 9) AS b, COUNT(*) AS n, AVG(m.winner = 100) AS blue "
        f"FROM matches m WHERE {where} GROUP BY b ORDER BY b",
        params,
    ):
        start = r["b"] * 5
        label = f"< {start + 5} min" if r["b"] == 3 else f"≥ {start} min" if r["b"] == 9 else f"{start}-{start + 5} min"
        duration.append({"label": label, "matches": r["n"], "share": pct(safe_div(r["n"], n)), "blue_wr": pct(r["blue"])})

    tiers = {r[0]: r[1] for r in svc.db.query(f"SELECT m.tier, COUNT(*) FROM matches m WHERE {where} GROUP BY m.tier", params)}
    tier_dist = [{"tier": t, "matches": tiers[t], "share": pct(safe_div(tiers[t], n))} for t in sorted(tiers, key=lambda t: TIER_INDEX.get(t, -1), reverse=True)]
    patches = {r[0]: r[1] for r in svc.db.query(f"SELECT m.patch, COUNT(*) FROM matches m WHERE {where} GROUP BY m.patch", params)}
    patch_dist = [{"patch": p, "matches": patches[p]} for p in sorted(patches, key=patch_sort_key, reverse=True)]

    meta = {}
    for role in ROLES:
        champions = tier_list(svc, f, role)["champions"]
        meta[role] = {
            "best": champions[:5],
            "popular": sorted(champions, key=lambda c: c["games"] or 0, reverse=True)[:5],
        }
    bans = svc.agg.bans(f)
    totals = svc.agg.totals(f)
    top_bans = sorted(bans.items(), key=lambda kv: kv[1], reverse=True)[:10]
    blue_lo, blue_hi = wilson(base["blue"] or 0, n)
    return {
        "platform": platform,
        "label": PLATFORMS.get(platform, ("", "Todas", ""))[1] if platform != "all" else "Todas",
        "filters": f.public(),
        "matches": n,
        "remakes": remakes,
        "avg_minutes": round(base["minutes"] or 0, 1),
        "blue_wr": pct(safe_div(base["blue"] or 0, n)),
        "blue_ci": [pct(blue_lo), pct(blue_hi)] if n else None,
        "surrender": pct(base["surrender"]),
        "per_match": {
            "kills": round(base["kills"] or 0, 1),
            "dragons": round(base["dragons"] or 0, 2),
            "barons": round(base["barons"] or 0, 2),
            "towers": round(base["towers"] or 0, 1),
            "grubs": round(base["grubs"] or 0, 2),
        },
        "objectives": objectives,
        "duration": duration,
        "tiers": tier_dist,
        "patches": patch_dist,
        "meta": meta,
        "bans": [{"champ": c, "bans": b, "rate": pct(safe_div(b, totals["dmatches"]), 2)} for c, b in top_bans],
        "known_tiers": list(TIERS),
    }
