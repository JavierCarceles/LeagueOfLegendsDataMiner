"""Constancia entre partidas (C-11 de ``macro/macro_internet_por_elo.md``): cuánto cambian tus muertes de una partida a
otra y cuántas son en tus peores partidas. Lo mismo para ti (``insights``) y para cada jugador de los OTPs
(``elo_profile``), con sus últimas 20 partidas.

Validado el 25-11-2026 con los jugadores de ``data/otps`` y ``data/partidas_campeon`` con 10 partidas o más (mediana por
jugador, de Hierro-Bronce a Aspirante): la desviación de las muertes baja de 3,1-3,4 a 2,4-2,7 en los cinco roles y las
muertes del 20 % de partidas peores, de 11-12,7 a 8,7-10,3. La del CS/min no sirve (sube con el elo) y la de los
errores graves solo baja en jungla.
"""

from __future__ import annotations

import statistics

RECENT = 20  # últimas partidas de cada jugador
MIN_GAMES = 10
WORST_SHARE = 0.2


def deaths_sd(deaths: list[float]) -> float:
    """Cuánto cambian tus muertes de una partida a otra (desviación típica)."""
    return statistics.pstdev(deaths)


def worst_mean(deaths: list[float], share: float = WORST_SHARE) -> float:
    """Media de muertes de tu ``share`` de partidas peores (al menos una)."""
    worst = sorted(deaths, reverse=True)
    k = max(1, int(len(worst) * share))
    return sum(worst[:k]) / k


def summary(deaths: list[float]) -> dict | None:
    """``deaths_sd`` y ``worst_deaths`` de unas partidas (de la más antigua a la más reciente): las últimas ``RECENT``."""
    recent = deaths[-RECENT:]
    if len(recent) < MIN_GAMES:
        return None
    return {"deaths_sd": round(deaths_sd(recent), 2), "worst_deaths": round(worst_mean(recent), 2)}
