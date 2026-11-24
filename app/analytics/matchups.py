"""Análisis de enfrentamientos directos (mismo rol, equipos contrarios).

Cada matchup combina OP.GG + tu base de datos + la estimación por
estadísticas de campeón (ver :mod:`app.analytics.evidence`).
"""

from __future__ import annotations

from app.analytics.evidence import Evidence
from app.analytics.filters import Filters
from app.analytics.metrics import game_verdict, lane_analysis
from app.analytics.stats import pct, safe_div, sample_confidence, wilson
from app.logbus import get_logger

log = get_logger("matchups")

SORTS = {
    "wr_adj": lambda m: m["wr_adj"],
    "games": lambda m: m["games"],
    "delta": lambda m: m["delta"],
    "lane": lambda m: (m["lane"] or {}).get("score") or 0,
    "cs10": lambda m: (m["lane"] or {}).get("cs10_diff") or 0,
}


def _source(games: float, wins: float) -> dict | None:
    if not games:
        return None
    lo, hi = wilson(wins, games)
    return {"games": int(games), "wins": int(wins), "wr": pct(wins / games), "ci": [pct(lo), pct(hi)]}


def pair_view(p: dict) -> dict:
    """Vista pública de :meth:`Evidence.pair`."""
    games, wins = p["games"], p["wins"]
    lo, hi = wilson(wins, games)
    delta = p["adjusted"] - p["expected"]
    counter = None
    if games >= 100 and delta >= 0.015:
        counter = "counter"
    elif games >= 100 and delta <= -0.015:
        counter = "countered"
    return {
        "games": int(games),
        "wins": int(wins),
        "wr": pct(safe_div(wins, games)),
        "wr_adj": pct(p["adjusted"]),
        "expected": pct(p["expected"]),
        "delta": round(delta * 100, 2),
        "ci": [pct(lo), pct(hi)] if games else None,
        "verdict": game_verdict(p["adjusted"], games) if games else {"code": "estimado", "label": "Estimado por campeones"},
        "counter": counter,
        "confidence": sample_confidence(games),
        "sources": {"opgg": _source(*p["ext"]), "own": _source(*p["own"])},
        "lane": lane_analysis(p["own_row"]) if p["own_row"] else None,
    }


def base_view(ev: Evidence, role: str, champ: int) -> dict:
    counts = ev.base_counts(role, champ)
    p, _, games = ev.base(role, champ)
    tl = ev.tier_list(role).get(champ)
    own_games, own_wins = counts["own"]
    return {
        "champ": champ,
        "wr_adj": pct(p),
        "games": int(games),
        "opgg": {
            "wr": round(tl["wr"], 2) if tl and tl.get("wr") is not None and tl.get("source") != "lolalytics" else None,
            "pick": round(tl["pick"], 2) if tl and tl.get("pick") is not None else None,
            "ban": round(tl["ban"], 2) if tl and tl.get("ban") is not None else None,
            "tier": tl.get("tier") if tl else None,
            "rank": tl.get("rank") if tl else None,
            "games": int(counts["ext"][0]) if counts["ext_kind"] == "counters" else None,
            "lola_wr": round(tl["lola_wr"], 2) if tl and tl.get("lola_wr") is not None else None,
            "lola_games": tl.get("lola_games") if tl else None,
        }
        if tl or counts["ext_kind"]
        else None,
        "own": _source(own_games, own_wins),
    }


def matchup_list(svc, f: Filters, role: str, champ: int, min_games: int = 0, sort: str = "wr_adj", sources: str = "all") -> dict:
    ev = Evidence(svc, f, sources)
    ext = ev.counters(role, champ) or {}
    own = svc.agg.matchups_of(f, role, champ) if ev.use_own else {}
    items = []
    for opp in set(ext) | set(own):
        view = pair_view(ev.pair(role, champ, opp))
        if view["games"] < min_games:
            continue
        view["opp"] = opp
        view["opp_wr"] = pct(ev.base(role, opp)[0])
        view["guide"] = svc.guides.exists(role, champ, opp)
        items.append(view)
    items.sort(key=SORTS.get(sort, SORTS["wr_adj"]), reverse=True)
    counts: dict[str, int] = {}
    for item in items:
        counts[item["verdict"]["code"]] = counts.get(item["verdict"]["code"], 0) + 1
    return {
        "role": role,
        "champ": champ,
        "filters": f.public(),
        "evidence": ev.public(),
        "base": base_view(ev, role, champ),
        "priors": ev.priors,
        "summary": counts,
        "matchups": items,
    }


def matchup_detail(svc, f: Filters, role: str, champ: int, opp: int, sources: str = "all", with_guide: bool = True) -> dict:
    ev = Evidence(svc, f, sources)
    if ev.scope:
        svc.opgg.prefetch([lambda: ev.counters(role, champ), lambda: ev.counters(role, opp)], timeout=20)
    pair = pair_view(ev.pair(role, champ, opp))

    breakdown = {}
    for dim in ("tier", "patch", "platform"):
        out = []
        if ev.use_own:
            for r in svc.agg.matchup_breakdown(f, role, champ, opp, dim):
                if r["games"]:
                    src = _source(r["games"], r["wins"])
                    out.append({"key": r["dim"], **src, "lane": lane_analysis(r)})
        out.sort(key=lambda x: x["games"], reverse=True)
        breakdown[dim] = out
    breakdown["opgg_patch"] = [{**row, "wr": pct(safe_div(row["wins"], row["games"]))} for row in ev.counters_by_patch(role, champ, opp)]
    web_sources = {name: (_source(*value) if value else None) for name, value in ev.pair_sources(role, champ, opp).items()} if ev.scope else {}

    patch_changes = {}
    notes = getattr(svc, "patchnotes", None)
    if notes is not None and notes.enabled:
        for c in (champ, opp):
            try:
                patch_changes[str(c)] = notes.champion_changes(c, 2, allow_fetch=False)
            # las notas de parche son un extra: el matchup se muestra igual
            except Exception as exc:  # noqa: BLE001
                log.warning("Notas de parche de %s: %s", c, exc)
                patch_changes[str(c)] = []

    opgg_guide = None
    if with_guide and ev.scope:
        try:
            opgg_guide = svc.opgg.matchup_guide(champ, opp, role)
        # el MCP de OP.GG puede fallar; el resto del análisis sigue valiendo
        except Exception as exc:  # noqa: BLE001
            log.warning("Guía de OP.GG de %s contra %s: %s", champ, opp, exc)
            opgg_guide = None

    return {
        "role": role,
        "champ": champ,
        "opp": opp,
        "filters": f.public(),
        "evidence": ev.public(),
        "a": base_view(ev, role, champ),
        "b": base_view(ev, role, opp),
        "pair": pair,
        "breakdown": breakdown,
        "web_sources": web_sources,
        "patch_changes": patch_changes,
        "opgg_guide": opgg_guide,
        "guide": svc.guides.find(role, champ, opp, include_content=False),
        "priors": ev.priors,
        "legacy_note": f.includes_legacy and ev.use_own,
    }


def top_matchups(svc, f: Filters, role: str, limit: int = 15, guide: str = "any") -> list[dict]:
    """Enfrentamientos más jugados de un rol (para la sección de guías)."""
    where, params = f.clause()
    rows = svc.db.query(
        f"SELECT champ, opp, SUM(games) AS games, SUM(wins) AS wins FROM agg_matchup "
        f"WHERE role = ? AND champ < opp AND {where} GROUP BY champ, opp ORDER BY games DESC LIMIT ?",
        [role, *params, max(limit * 6, 60)],
    )
    out = []
    for r in rows:
        has_guide = svc.guides.exists(role, r["champ"], r["opp"]) or svc.guides.exists(role, r["opp"], r["champ"])
        if guide == "with" and not has_guide:
            continue
        if guide == "without" and has_guide:
            continue
        out.append(
            {
                "role": role,
                "champ": r["champ"],
                "opp": r["opp"],
                "games": r["games"],
                "wr": pct(safe_div(r["wins"], r["games"])),
                "guide": has_guide,
            }
        )
        if len(out) >= limit:
            break
    return out
