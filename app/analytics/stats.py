"""Herramientas estadísticas.

Por qué hacen falta (el análisis antiguo usaba ``wins / total`` a pelo):

* **Tamaño de muestra**: 3-0 no es un 100 % de winrate. Se usa un
  estimador bayesiano (Beta-Binomial) que encoge los resultados con pocas
  partidas hacia un valor esperado razonable.
* **Valor esperado (log5)**: si Aatrox gana el 52 % de sus partidas y su rival
  el 48 %, lo "normal" es que Aatrox gane ~54 % de ese enfrentamiento aunque
  no haya ninguna ventaja específica. El *delta* (observado − esperado) mide
  la ventaja propia del matchup, que es lo que de verdad aporta un counter.
* **Intervalo de Wilson**: rango creíble del winrate real con 95 % de confianza.
"""

from __future__ import annotations

import math

Z95 = 1.959963984540054
EPS = 1e-6


def safe_div(num: float, den: float, default: float | None = None) -> float | None:
    return num / den if den else default


def clamp(value: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, value))


def logit(p: float) -> float:
    p = clamp(p, EPS, 1 - EPS)
    return math.log(p / (1 - p))


def sigmoid(x: float) -> float:
    if x >= 0:
        z = math.exp(-x)
        return 1 / (1 + z)
    z = math.exp(x)
    return z / (1 + z)


def wilson(wins: float, games: float, z: float = Z95) -> tuple[float, float]:
    if games <= 0:
        return 0.0, 1.0
    p = wins / games
    denom = 1 + z * z / games
    centre = p + z * z / (2 * games)
    radius = z * math.sqrt(p * (1 - p) / games + z * z / (4 * games * games))
    return max(0.0, (centre - radius) / denom), min(1.0, (centre + radius) / denom)


def shrink(wins: float, games: float, prior: float, k: float) -> float:
    """Media posterior Beta con ``k`` partidas virtuales al valor ``prior``."""
    total = games + k
    return (wins + k * prior) / total if total > 0 else prior


def log5(pa: float, pb: float) -> float:
    """P(A gana a B) dados sus winrates generales (fórmula log5 de Bill James)."""
    den = pa + pb - 2 * pa * pb
    if den <= 0:
        return 0.5
    return (pa - pa * pb) / den


def posterior_logit_var(wins: float, games: float, prior: float, k: float) -> float:
    """Varianza aproximada (método delta) del logit de la media posterior."""
    a = wins + k * prior
    b = (games - wins) + k * (1 - prior)
    s = a + b
    if s <= 0 or a <= 0 or b <= 0:
        return 1.0
    var_p = (a * b) / (s * s * (s + 1))
    mean = a / s
    return var_p / max((mean * (1 - mean)) ** 2, EPS)


def sample_confidence(games: float) -> dict:
    if games >= 600:
        return {"level": 5, "label": "Muy alta"}
    if games >= 200:
        return {"level": 4, "label": "Alta"}
    if games >= 60:
        return {"level": 3, "label": "Media"}
    if games >= 20:
        return {"level": 2, "label": "Baja"}
    if games > 0:
        return {"level": 1, "label": "Muy baja"}
    return {"level": 0, "label": "Sin datos"}


def round_or_none(value: float | None, digits: int = 1) -> float | None:
    return None if value is None else round(value, digits)


def pct(value: float | None, digits: int = 1) -> float | None:
    return None if value is None else round(value * 100, digits)
