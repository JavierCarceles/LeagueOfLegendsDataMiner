"""Cálculo de métricas derivadas a partir de las sumas agregadas."""

from __future__ import annotations

from app.analytics.stats import pct, round_or_none, safe_div, sample_confidence, shrink, wilson

DURATION_LABELS = ["< 25 min", "25-30 min", "30-35 min", "≥ 35 min"]


def tier_label(wr_adj: float, games: int, pick_rate: float | None) -> str:
    """Tier S+…D según el winrate ajustado (puntos sobre 50 %)."""
    if games < 30:
        return "?"
    edge = (wr_adj - 0.5) * 100
    if edge >= 3.0:
        label = "S+"
    elif edge >= 2.0:
        label = "S"
    elif edge >= 1.0:
        label = "A"
    elif edge >= -0.5:
        label = "B"
    elif edge >= -2.0:
        label = "C"
    else:
        label = "D"
    return label


def champ_metrics(row: dict | None, k_champ: float, matches: int = 0, bans: int | None = None, dmatches: int = 0) -> dict:
    row = row or {}
    games = row.get("games") or 0
    wins = row.get("wins") or 0
    dgames = row.get("dgames") or 0
    minutes = row.get("minutes") or 0.0
    wr = safe_div(wins, games)
    wr_adj = shrink(wins, games, 0.5, k_champ)
    lo, hi = wilson(wins, games)
    pick_rate = safe_div(games, matches)
    ban_rate = safe_div(bans or 0, dmatches) if dmatches else None
    dmg_total = (row.get("dmg_phys") or 0) + (row.get("dmg_magic") or 0) + (row.get("dmg_true") or 0)
    lane_n = row.get("lane_n") or 0
    curve = []
    for b in range(4):
        g, w = row.get(f"g{b}") or 0, row.get(f"w{b}") or 0
        curve.append(
            {
                "bucket": b,
                "label": DURATION_LABELS[b],
                "games": g,
                "wr": pct(safe_div(w, g)),
                "wr_adj": pct(shrink(w, g, wr_adj, 60.0)) if g else None,
            }
        )
    return {
        "games": games,
        "wins": wins,
        "wr": pct(wr),
        "wr_adj": pct(wr_adj),
        "ci": [pct(lo), pct(hi)],
        "pick_rate": pct(pick_rate, 2),
        "ban_rate": pct(ban_rate, 2),
        "tier": tier_label(wr_adj, games, pick_rate),
        "confidence": sample_confidence(games),
        "dgames": dgames,
        "kda": round_or_none(safe_div((row.get("kills") or 0) + (row.get("assists") or 0), max(1, row.get("deaths") or 0)) if dgames else None, 2),
        "kills": round_or_none(safe_div(row.get("kills") or 0, dgames)),
        "deaths": round_or_none(safe_div(row.get("deaths") or 0, dgames)),
        "assists": round_or_none(safe_div(row.get("assists") or 0, dgames)),
        "cs_min": round_or_none(safe_div(row.get("cs") or 0, minutes), 2),
        "gold_min": round_or_none(safe_div(row.get("gold") or 0, minutes), 0),
        "dmg_min": round_or_none(safe_div(row.get("dmg") or 0, minutes), 0),
        "vision_min": round_or_none(safe_div(row.get("vision") or 0, minutes), 2),
        "tank_min": round_or_none(safe_div((row.get("taken") or 0) + (row.get("mitigated") or 0), minutes), 0),
        "cc_min": round_or_none(safe_div(row.get("cc") or 0, minutes), 2),
        "cs10": round_or_none(safe_div(row.get("cs10_sum") or 0, row.get("cs10_n") or 0)),
        "kp": pct(safe_div(row.get("kp_sum") or 0, row.get("kp_n") or 0)),
        "dmg_share": pct(safe_div(row.get("dshare_sum") or 0, row.get("dshare_n") or 0)),
        "lane_win": pct(safe_div(row.get("lane_w") or 0, lane_n)),
        "lane_loss": pct(safe_div(row.get("lane_l") or 0, lane_n)),
        "solo_kills": round_or_none(safe_div(row.get("solo_kills") or 0, dgames), 2),
        "avg_minutes": round_or_none(safe_div(minutes, dgames)),
        "damage_profile": {
            "phys": pct(safe_div(row.get("dmg_phys") or 0, dmg_total)),
            "magic": pct(safe_div(row.get("dmg_magic") or 0, dmg_total)),
            "true": pct(safe_div(row.get("dmg_true") or 0, dmg_total)),
        }
        if dmg_total
        else None,
        "duration_curve": curve if dgames else None,
    }


def game_verdict(wr_adj: float, games: int) -> dict:
    if games == 0:
        return {"code": "sin_datos", "label": "Sin datos"}
    if wr_adj >= 0.54:
        return {"code": "muy_favorable", "label": "Muy favorable"}
    if wr_adj >= 0.515:
        return {"code": "favorable", "label": "Favorable"}
    if wr_adj > 0.485:
        return {"code": "equilibrado", "label": "Equilibrado"}
    if wr_adj > 0.46:
        return {"code": "desfavorable", "label": "Desfavorable"}
    return {"code": "muy_desfavorable", "label": "Muy desfavorable"}


def lane_analysis(row: dict) -> dict | None:
    dgames = row.get("dgames") or 0
    if not dgames:
        return None
    lane_n = row.get("lane_n") or 0
    lane_w = row.get("lane_w") or 0
    lane_l = row.get("lane_l") or 0
    cs10 = safe_div(row.get("cs10_diff") or 0.0, row.get("cs10_n") or 0)
    # Puntuación de línea: ventaja neta encogida (40 partidas virtuales a 0).
    score = (lane_w - lane_l) / (lane_n + 40) if lane_n else None
    if score is not None and lane_n >= 10:
        if score >= 0.06:
            verdict = {"code": "gana", "label": "Gana línea"}
        elif score <= -0.06:
            verdict = {"code": "pierde", "label": "Pierde línea"}
        else:
            verdict = {"code": "igualada", "label": "Línea igualada"}
    elif cs10 is not None and (row.get("cs10_n") or 0) >= 10:
        verdict = (
            {"code": "gana", "label": "Gana línea"}
            if cs10 >= 5
            else {"code": "pierde", "label": "Pierde línea"}
            if cs10 <= -5
            else {"code": "igualada", "label": "Línea igualada"}
        )
    else:
        verdict = {"code": "sin_datos", "label": "Pocos datos de línea"}
    return {
        "dgames": dgames,
        "cs10_diff": round_or_none(cs10),
        "gpm_diff": round_or_none(safe_div(row.get("gpm_diff") or 0.0, dgames), 1),
        "dpm_diff": round_or_none(safe_div(row.get("dpm_diff") or 0.0, dgames), 0),
        "lane_win": pct(safe_div(lane_w, lane_n)),
        "lane_loss": pct(safe_div(lane_l, lane_n)),
        "lane_n": lane_n,
        "solo_kills_for": round_or_none(safe_div(row.get("solo_k") or 0, dgames), 2),
        "solo_kills_against": round_or_none(safe_div(row.get("solo_d") or 0, dgames), 2),
        "plates_for": round_or_none(safe_div(row.get("plates_for") or 0, dgames), 2),
        "plates_against": round_or_none(safe_div(row.get("plates_against") or 0, dgames), 2),
        "score": round_or_none(score, 3),
        "verdict": verdict,
    }


