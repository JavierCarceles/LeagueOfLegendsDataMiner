"""Tier list por rol y ficha de campeón (OP.GG + tu base de datos)."""

from __future__ import annotations

from app.analytics.evidence import Evidence
from app.analytics.filters import Filters
from app.analytics.matchups import _source, base_view, matchup_list
from app.analytics.metrics import champ_metrics, tier_label
from app.analytics.stats import pct, round_or_none, safe_div, shrink
from app.riot.routing import ROLES


def default_min_games(matches: int) -> int:
    return max(20, min(200, int(matches * 0.001)))


def _left_out(ext: dict | None, own: dict | None, threshold: int, scoped: bool, matches: int) -> bool:
    """¿Se queda fuera de la tier list? Sin muestra propia suficiente o pick residual en el rol."""
    if ext is not None:
        # pick muy residual en este rol
        return (ext.get("role_rate") or 0) < 0.03 and (own or {}).get("games", 0) < threshold
    own_games = (own or {}).get("games") or 0
    # sin muestra propia, o pick residual que OP.GG no lista en este rol (< 1 % de pick en tu BD)
    return own is None or own_games < threshold or (scoped and own_games < 0.01 * max(1, matches))


def _source_label(ext: dict | None, own: dict | None) -> str:
    if ext and own:
        return "both"
    return "opgg" if ext else "own"


def _web_view(ext: dict | None) -> dict | None:
    """Datos de las webs de una fila de la tier list."""
    if not ext:
        return None
    wr = ext.get("wr") if ext.get("source") != "lolalytics" else None
    return {
        "wr": round_or_none(wr, 2),
        "lola_wr": round_or_none(ext.get("lola_wr"), 2),
        "lola_games": ext.get("lola_games"),
        "pick": round_or_none(ext.get("pick"), 2),
        "ban": round_or_none(ext.get("ban"), 2),
        "role_rate": round((ext.get("role_rate") or 0) * 100, 1),
        "tier": ext.get("tier"),
        "rank": ext.get("rank"),
        "counters": ext.get("counters") or [],
    }


def _tier_row(ev: Evidence, role: str, champ: int, ext: dict | None, own: dict | None, totals: dict, bans: dict) -> dict:
    p, _, games = ev.base(role, champ)
    counts = ev.base_counts(role, champ)
    known_games = counts["own"][0] + counts["ext"][0]
    own_metrics = champ_metrics(own, ev.kc, totals["matches"], bans.get(champ), totals["dmatches"]) if own else None
    return {
        "champ": champ,
        "wr_adj": pct(p),
        "tier": tier_label(p, int(games)),
        "games": int(known_games) or None,
        "source": _source_label(ext, own),
        "opgg": _web_view(ext),
        "own": own_metrics,
    }


def tier_list(svc, f: Filters, role: str, min_games: int | None = None, sources: str = "all") -> dict:
    ev = Evidence(svc, f, sources)
    totals = svc.agg.totals(f)
    bans = svc.agg.bans(f)
    own_rows = ev.own_role(role)
    threshold = default_min_games(totals["matches"]) if min_games is None else min_games
    tl = ev.tier_list(role)
    scoped = bool(ev.scope)
    champions = []
    for champ in set(tl) | {c for c, r in own_rows.items() if (r.get("games") or 0) >= threshold}:
        ext, own = tl.get(champ), own_rows.get(champ)
        if not _left_out(ext, own, threshold, scoped, totals["matches"]):
            champions.append(_tier_row(ev, role, champ, ext, own, totals, bans))
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


def _role_entry(role: str, ext: dict | None, own: dict) -> dict:
    """Fila de un rol en la ficha: reparto y WR en las webs y partidas/WR en tu BD."""
    games = own.get("games") or 0
    opgg_role_rate = opgg_wr = None
    if ext:
        opgg_role_rate = round((ext.get("role_rate") or 0) * 100, 1)
        opgg_wr = round_or_none(ext.get("wr"), 2)
    return {
        "role": role,
        "opgg_role_rate": opgg_role_rate,
        "opgg_wr": opgg_wr,
        "own_games": games,
        "own_wr": pct(safe_div(own.get("wins") or 0, games)),
    }


def _champion_roles(ev: Evidence, champ: int, per_role: dict) -> list[dict]:
    """Roles en los que aparece el campeón (webs o tu BD), del más jugado al menos."""
    roles = []
    for r in ROLES:
        ext = ev.tier_list(r).get(champ)
        own = per_role.get(r) or {}
        if ext or own.get("games"):
            roles.append(_role_entry(r, ext, own))
    roles.sort(key=lambda x: (x["opgg_role_rate"] or 0, x["own_games"]), reverse=True)
    return roles


def _matchup_highlights(matchups: list[dict]) -> dict:
    """Mejores y peores enfrentamientos (con muestra sólida si la hay) y los más habituales."""
    solid = [m for m in matchups if m["games"] >= 100] or [m for m in matchups if m["games"] >= 20] or matchups
    return {
        "best": sorted(solid, key=lambda m: m["wr_adj"], reverse=True)[:6],
        "worst": sorted(solid, key=lambda m: m["wr_adj"])[:6],
        "common": sorted(matchups, key=lambda m: m["games"], reverse=True)[:6],
        "count": len(matchups),
    }


def _partner_synergies(svc, ev: Evidence, f: Filters, role: str, champ: int, partner_role: str) -> dict:
    """Mejores y peores compañeros del campeón en ``partner_role`` (≥ 15 partidas juntos)."""
    partners = set(svc.agg.partners(f, role, champ, partner_role)) if ev.use_own else set()
    if ev.scope:
        partners |= set(svc.opgg.synergies(champ, role, partner_role, allow_fetch=False) or {})
    if ev.use_lola and ev.scope:
        for patch in ev.scope.patches:
            partners |= set(svc.lolalytics.synergies(champ, role, partner_role, ev.scope.tier, ev.scope.region, patch, allow_fetch=False) or {})
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
                "sources": {"opgg": _source(*s["opgg"]), "lolalytics": _source(*s["lola"]), "own": _source(*s["own"])},
            }
        )
    items.sort(key=lambda x: x["delta"], reverse=True)
    return {"best": items[:5], "worst": list(reversed(items[-5:])) if len(items) > 5 else []}


def _synergies(svc, ev: Evidence, f: Filters, role: str, champ: int) -> dict:
    """Sinergias del campeón con cada uno de los otros roles."""
    if ev.scope:
        jobs = [lambda pr=pr: svc.opgg.synergies(champ, role, pr) for pr in ROLES if pr != role]
        svc.opgg.prefetch([*jobs, lambda: ev.lola_team(role, champ)], timeout=15)
    return {pr: _partner_synergies(svc, ev, f, role, champ, pr) for pr in ROLES if pr != role}


def _breakdown(svc, ev: Evidence, f: Filters, role: str, champ: int) -> dict:
    """Partidas y WR del campeón por elo, parche y plataforma (tu BD)."""
    breakdown = {}
    for dim in ("tier", "patch", "platform"):
        rows = svc.agg.champ_breakdown(f, role, champ, dim) if ev.use_own else []
        breakdown[dim] = sorted(
            (
                {
                    "key": r["dim"],
                    "games": r["games"],
                    "wr": pct(safe_div(r["wins"], r["games"])),
                    "wr_adj": pct(shrink(r["wins"], r["games"], 0.5, ev.kc)),
                }
                for r in rows
                if r["games"]
            ),
            key=lambda x: x["games"],
            reverse=True,
        )
    return breakdown


def champion_detail(svc, f: Filters, champ: int, role: str | None = None, sources: str = "all") -> dict:
    ev = Evidence(svc, f, sources)
    totals = svc.agg.totals(f)
    bans = svc.agg.bans(f)
    per_role = svc.agg.champ_roles(f, champ) if ev.use_own else {}
    roles = _champion_roles(ev, champ, per_role)
    if role not in ROLES:
        role = roles[0]["role"] if roles else "TOP"

    listing = matchup_list(svc, f, role, champ, sources=sources)
    matchups = listing["matchups"]
    synergies = _synergies(svc, ev, f, role, champ)
    breakdown = _breakdown(svc, ev, f, role, champ)
    damage = (svc.opgg.champion_info(champ, role) or {}).get("damage_type") if ev.scope else None
    own = per_role.get(role)

    return {
        "champ": champ,
        "role": role,
        "roles": roles,
        "filters": f.public(),
        "evidence": listing["evidence"],
        "base": base_view(ev, role, champ),
        "own": champ_metrics(own, ev.kc, totals["matches"], bans.get(champ), totals["dmatches"]) if own else None,
        "damage_type": damage,
        "matchups": _matchup_highlights(matchups),
        "synergies": synergies,
        "breakdown": breakdown,
        "priors": ev.priors,
    }
