"""Tier list por rol y ficha de campeón (OP.GG + tu base de datos)."""

from __future__ import annotations

from app.analytics.evidence import Evidence
from app.analytics.filters import Filters
from app.analytics.matchups import _source, base_view, matchup_list
from app.analytics.metrics import champ_metrics, tier_label
from app.analytics.stats import pct, safe_div, shrink
from app.riot.routing import ROLES


def default_min_games(matches: int) -> int:
    return max(20, min(200, int(matches * 0.001)))


def tier_list(svc, f: Filters, role: str, min_games: int | None = None, sources: str = "all") -> dict:
    ev = Evidence(svc, f, sources)
    totals = svc.agg.totals(f)
    bans = svc.agg.bans(f)
    own_rows = ev.own_role(role)
    threshold = default_min_games(totals["matches"]) if min_games is None else min_games
    tl = ev.tier_list(role)
    champions = []
    for champ in set(tl) | {c for c, r in own_rows.items() if (r.get("games") or 0) >= threshold}:
        ext = tl.get(champ)
        own = own_rows.get(champ)
        if ext is None and (own is None or (own.get("games") or 0) < threshold):
            continue
        if ext is None and ev.scope and (own.get("games") or 0) < 0.01 * max(1, totals["matches"]):
            continue  # pick residual que OP.GG no lista en este rol (< 1 % de pick en tu BD)
        if ext is not None and (ext.get("role_rate") or 0) < 0.03 and (own or {}).get("games", 0) < threshold:
            continue  # pick muy residual en este rol
        p, _, games = ev.base(role, champ)
        counts = ev.base_counts(role, champ)
        known_games = counts["own"][0] + counts["ext"][0]
        own_metrics = champ_metrics(own, ev.kc, totals["matches"], bans.get(champ), totals["dmatches"]) if own else None
        champions.append(
            {
                "champ": champ,
                "wr_adj": pct(p),
                "tier": tier_label(p, int(games), None),
                "games": int(known_games) or None,
                "source": "both" if ext and own else "opgg" if ext else "own",
                "opgg": {
                    "wr": round(ext["wr"], 2) if ext.get("wr") is not None and ext.get("source") != "lolalytics" else None,
                    "lola_wr": round(ext["lola_wr"], 2) if ext.get("lola_wr") is not None else None,
                    "lola_games": ext.get("lola_games"),
                    "pick": round(ext["pick"], 2) if ext.get("pick") is not None else None,
                    "ban": round(ext["ban"], 2) if ext.get("ban") is not None else None,
                    "role_rate": round((ext.get("role_rate") or 0) * 100, 1),
                    "tier": ext.get("tier"),
                    "rank": ext.get("rank"),
                    "counters": ext.get("counters") or [],
                }
                if ext
                else None,
                "own": own_metrics,
            }
        )
    champions.sort(key=lambda c: (c["wr_adj"] or 0, c["games"] or 0), reverse=True)
    return {
        "role": role,
        "filters": f.public(),
        "evidence": ev.public(),
        "totals": totals,
        "min_games": threshold,
        "priors": ev.priors,
        "champions": champions,
    }


def champion_detail(svc, f: Filters, champ: int, role: str | None = None, sources: str = "all") -> dict:
    ev = Evidence(svc, f, sources)
    totals = svc.agg.totals(f)
    bans = svc.agg.bans(f)
    per_role = svc.agg.champ_roles(f, champ) if ev.use_own else {}
    roles = []
    for r in ROLES:
        ext = ev.tier_list(r).get(champ)
        own = per_role.get(r) or {}
        if not ext and not own.get("games"):
            continue
        roles.append(
            {
                "role": r,
                "opgg_role_rate": round((ext.get("role_rate") or 0) * 100, 1) if ext else None,
                "opgg_wr": round(ext["wr"], 2) if ext and ext.get("wr") is not None else None,
                "own_games": own.get("games") or 0,
                "own_wr": pct(safe_div(own.get("wins") or 0, own.get("games") or 0)),
            }
        )
    roles.sort(key=lambda x: (x["opgg_role_rate"] or 0, x["own_games"]), reverse=True)
    if role not in ROLES:
        role = roles[0]["role"] if roles else "TOP"

    listing = matchup_list(svc, f, role, champ, sources=sources)
    matchups = listing["matchups"]
    solid = [m for m in matchups if m["games"] >= 100] or [m for m in matchups if m["games"] >= 20] or matchups
    best = sorted(solid, key=lambda m: m["wr_adj"], reverse=True)[:6]
    worst = sorted(solid, key=lambda m: m["wr_adj"])[:6]
    common = sorted(matchups, key=lambda m: m["games"], reverse=True)[:6]

    synergies = {}
    if ev.scope:
        svc.opgg.prefetch([lambda pr=pr: svc.opgg.synergies(champ, role, pr) for pr in ROLES if pr != role], timeout=15)
    for partner_role in ROLES:
        if partner_role == role:
            continue
        partners = set(svc.agg.partners(f, role, champ, partner_role)) if ev.use_own else set()
        if ev.scope:
            partners |= set(svc.opgg.synergies(champ, role, partner_role, allow_fetch=False) or {})
        items = []
        for partner in partners:
            s = ev.synergy(role, champ, partner_role, partner)
            if s["games"] < 15:
                continue
            items.append(
                {
                    "champ": partner,
                    "games": int(s["games"]),
                    "wr": pct(safe_div(s["wins"], s["games"])),
                    "wr_adj": pct(s["adjusted"]),
                    "delta": round((s["adjusted"] - s["expected"]) * 100, 2),
                    "sources": {"opgg": _source(*s["ext"]), "own": _source(*s["own"])},
                }
            )
        items.sort(key=lambda x: x["delta"], reverse=True)
        synergies[partner_role] = {"best": items[:5], "worst": list(reversed(items[-5:])) if len(items) > 5 else []}

    breakdown = {}
    for dim in ("tier", "patch", "platform"):
        rows = svc.agg.champ_breakdown(f, role, champ, dim) if ev.use_own else []
        breakdown[dim] = sorted(
            (
                {"key": r["dim"], "games": r["games"], "wr": pct(safe_div(r["wins"], r["games"])), "wr_adj": pct(shrink(r["wins"], r["games"], 0.5, ev.kc))}
                for r in rows
                if r["games"]
            ),
            key=lambda x: x["games"],
            reverse=True,
        )

    damage = None
    if ev.scope:
        info = svc.opgg.champion_info(champ, role) or {}
        damage = info.get("damage_type")

    return {
        "champ": champ,
        "role": role,
        "roles": roles,
        "filters": f.public(),
        "evidence": listing["evidence"],
        "base": base_view(ev, role, champ),
        "own": champ_metrics(per_role.get(role), ev.kc, totals["matches"], bans.get(champ), totals["dmatches"]) if per_role.get(role) else None,
        "damage_type": damage,
        "matchups": {"best": best, "worst": worst, "common": common, "count": len(matchups)},
        "synergies": synergies,
        "breakdown": breakdown,
        "priors": ev.priors,
    }
