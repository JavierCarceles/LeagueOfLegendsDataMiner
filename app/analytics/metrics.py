"""Cálculo de métricas derivadas a partir de las sumas agregadas."""

from __future__ import annotations

from app.analytics.stats import pct, round_or_none, safe_div, sample_confidence, shrink, wilson

DURATION_LABELS = ["< 25 min", "25-30 min", "30-35 min", "≥ 35 min"]
LANE_LABELS = {"gana": "Gana línea", "pierde": "Pierde línea", "igualada": "Línea igualada", "sin_datos": "Pocos datos de línea"}


def tier_label(wr_adj: float, games: int) -> str:
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


def _num(row: dict, key: str, default: float = 0) -> float:
    """Valor de la fila; ``default`` si falta o es nulo/cero."""
    return row.get(key) or default


def _rate(row: dict, key: str, den: float, digits: int = 1) -> float | None:
    """``row[key] / den`` redondeado (None si ``den`` es 0)."""
    return round_or_none(safe_div(_num(row, key), den), digits)


def _duration_curve(row: dict, wr_adj: float) -> list[dict]:
    """WR por tramo de duración, encogido hacia el WR ajustado del campeón."""
    curve = []
    for b, label in enumerate(DURATION_LABELS):
        g, w = _num(row, f"g{b}"), _num(row, f"w{b}")
        curve.append(
            {
                "bucket": b,
                "label": label,
                "games": g,
                "wr": pct(safe_div(w, g)),
                "wr_adj": pct(shrink(w, g, wr_adj, 60.0)) if g else None,
            }
        )
    return curve


def _damage_profile(row: dict) -> dict | None:
    """Reparto del daño por tipo (None sin daño registrado)."""
    phys, magic, true = _num(row, "dmg_phys"), _num(row, "dmg_magic"), _num(row, "dmg_true")
    total = phys + magic + true
    if not total:
        return None
    return {"phys": pct(safe_div(phys, total)), "magic": pct(safe_div(magic, total)), "true": pct(safe_div(true, total))}


def _detail_stats(row: dict, dgames: int, minutes: float) -> dict:
    """Medias por partida y por minuto de las partidas con detalle."""
    kills, deaths, assists = _num(row, "kills"), _num(row, "deaths"), _num(row, "assists")
    lane_n = _num(row, "lane_n")
    kda = safe_div(kills + assists, max(1, deaths)) if dgames else None
    return {
        "kda": round_or_none(kda, 2),
        "kills": round_or_none(safe_div(kills, dgames)),
        "deaths": round_or_none(safe_div(deaths, dgames)),
        "assists": round_or_none(safe_div(assists, dgames)),
        "cs_min": _rate(row, "cs", minutes, 2),
        "gold_min": _rate(row, "gold", minutes, 0),
        "dmg_min": _rate(row, "dmg", minutes, 0),
        "vision_min": _rate(row, "vision", minutes, 2),
        "tank_min": round_or_none(safe_div(_num(row, "taken") + _num(row, "mitigated"), minutes), 0),
        "cc_min": _rate(row, "cc", minutes, 2),
        "cs10": _rate(row, "cs10_sum", _num(row, "cs10_n")),
        "kp": pct(safe_div(_num(row, "kp_sum"), _num(row, "kp_n"))),
        "dmg_share": pct(safe_div(_num(row, "dshare_sum"), _num(row, "dshare_n"))),
        "lane_win": pct(safe_div(_num(row, "lane_w"), lane_n)),
        "lane_loss": pct(safe_div(_num(row, "lane_l"), lane_n)),
        "solo_kills": _rate(row, "solo_kills", dgames, 2),
        "avg_minutes": round_or_none(safe_div(minutes, dgames)),
    }


def champ_metrics(row: dict | None, k_champ: float, matches: int = 0, bans: int | None = None, dmatches: int = 0) -> dict:
    row = row or {}
    games = _num(row, "games")
    wins = _num(row, "wins")
    dgames = _num(row, "dgames")
    minutes = _num(row, "minutes", 0.0)
    wr_adj = shrink(wins, games, 0.5, k_champ)
    lo, hi = wilson(wins, games)
    ban_rate = safe_div(bans or 0, dmatches) if dmatches else None
    return {
        "games": games,
        "wins": wins,
        "wr": pct(safe_div(wins, games)),
        "wr_adj": pct(wr_adj),
        "ci": [pct(lo), pct(hi)],
        "pick_rate": pct(safe_div(games, matches), 2),
        "ban_rate": pct(ban_rate, 2),
        "tier": tier_label(wr_adj, games),
        "confidence": sample_confidence(games),
        "dgames": dgames,
        **_detail_stats(row, dgames, minutes),
        "damage_profile": _damage_profile(row),
        "duration_curve": _duration_curve(row, wr_adj) if dgames else None,
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


def _lane_band(value: float, threshold: float) -> str:
    """«gana» / «pierde» / «igualada» según ``value`` frente a ±``threshold``."""
    if value >= threshold:
        return "gana"
    if value <= -threshold:
        return "pierde"
    return "igualada"


def _lane_verdict(score: float | None, lane_n: int, cs10: float | None, cs10_n: int) -> dict:
    """Veredicto de línea: la puntuación si hay ≥ 10 líneas medidas; si no, la diferencia de CS a los 10 min."""
    if score is not None and lane_n >= 10:
        code = _lane_band(score, 0.06)
    elif cs10 is not None and cs10_n >= 10:
        code = _lane_band(cs10, 5)
    else:
        code = "sin_datos"
    return {"code": code, "label": LANE_LABELS[code]}


def lane_analysis(row: dict) -> dict | None:
    dgames = _num(row, "dgames")
    if not dgames:
        return None
    lane_n, lane_w, lane_l = _num(row, "lane_n"), _num(row, "lane_w"), _num(row, "lane_l")
    cs10_n = _num(row, "cs10_n")
    cs10 = safe_div(_num(row, "cs10_diff", 0.0), cs10_n)
    # Puntuación de línea: ventaja neta encogida (40 partidas virtuales a 0).
    score = (lane_w - lane_l) / (lane_n + 40) if lane_n else None
    return {
        "dgames": dgames,
        "cs10_diff": round_or_none(cs10),
        "gpm_diff": round_or_none(safe_div(_num(row, "gpm_diff", 0.0), dgames), 1),
        "dpm_diff": round_or_none(safe_div(_num(row, "dpm_diff", 0.0), dgames), 0),
        "lane_win": pct(safe_div(lane_w, lane_n)),
        "lane_loss": pct(safe_div(lane_l, lane_n)),
        "lane_n": lane_n,
        "solo_kills_for": _rate(row, "solo_k", dgames, 2),
        "solo_kills_against": _rate(row, "solo_d", dgames, 2),
        "plates_for": _rate(row, "plates_for", dgames, 2),
        "plates_against": _rate(row, "plates_against", dgames, 2),
        "score": round_or_none(score, 3),
        "verdict": _lane_verdict(score, lane_n, cs10, cs10_n),
    }
