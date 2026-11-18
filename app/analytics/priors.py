"""Bayes empírico: cuánto hay que "encoger" cada winrate según tus datos.

La fuerza del prior ``k`` (partidas virtuales) sale de comparar la varianza
observada de los winrates con la que produciría el puro azar:

    τ² = E[(wr_obs − esperado)²] − E[p(1−p)/n]     (varianza real entre matchups)
    k  = 0.25 / τ²

Si los matchups reales apenas se diferencian entre sí (τ² pequeño), un 60 %
con 40 partidas es casi todo ruido y ``k`` sale grande. Se recalcula cada
cierto tiempo a medida que crece la base de datos.
"""

from __future__ import annotations

import threading
import time

from app.analytics.aggregates import Aggregates
from app.analytics.filters import Filters
from app.analytics.stats import clamp, log5, logit, shrink, sigmoid
from app.riot.routing import ROLES

# Sinergias: efectos reales pequeños (~±1 pp) y datos de OP.GG solo del parche
# en curso, así que el prior por defecto es fuerte.
DEFAULTS = {"champ": 400.0, "matchup": 250.0, "synergy": 1500.0}
BOUNDS = {"champ": (80.0, 3000.0), "matchup": (40.0, 1500.0), "synergy": (300.0, 4000.0)}
REFRESH_SECONDS = 1800


def _estimate_k(samples: list[tuple[float, float, float]], kind: str, min_samples: int = 40) -> tuple[float, int]:
    """samples: (observado, esperado, n). Devuelve (k, nº de muestras usadas)."""
    if len(samples) < min_samples:
        return DEFAULTS[kind], len(samples)
    num = 0.0
    den = 0.0
    for observed, expected, n in samples:
        num += n * ((observed - expected) ** 2 - expected * (1 - expected) / n)
        den += n
    tau2 = num / den if den else 0.0
    lo, hi = BOUNDS[kind]
    if tau2 <= 1e-6:
        return hi, len(samples)
    return clamp(0.25 / tau2, lo, hi), len(samples)


def _external_matchup_samples(opgg) -> list[tuple[float, float, float]]:
    """Residuos de los counters de OP.GG cacheados frente a su esperado log5."""
    if opgg is None:
        return []
    tiers = {key: data for key, data in opgg.cached_entries("tier")}
    samples = []
    for key, data in opgg.cached_entries("counters"):
        try:
            _champ, position, tier, region, patch = key.split("|")
        except ValueError:
            continue
        table = tiers.get(f"{position}|{tier}|{region}|{patch}")
        if not table or not data:
            continue
        games_a = sum(v[0] for v in data.values())
        wins_a = sum(v[1] for v in data.values())
        if games_a < 1000:
            continue
        base_a = wins_a / games_a
        for opp, (games, wins) in data.items():
            row = table.get(str(opp))
            if games < 200 or not row or row.get("wr") is None:
                continue
            samples.append((wins / games, log5(base_a, row["wr"] / 100), games))
    return samples


class Priors:
    def __init__(self, aggregates: Aggregates, opgg=None) -> None:
        self.agg = aggregates
        self.opgg = opgg
        self._lock = threading.Lock()
        self._value: dict | None = None
        self._computed_at = 0.0
        self._version = -1

    def get(self) -> dict:
        stale = self._value is None or (
            time.monotonic() - self._computed_at > REFRESH_SECONDS and self._version != self.agg.version
        )
        if stale:
            with self._lock:
                if self._value is None or time.monotonic() - self._computed_at > REFRESH_SECONDS:
                    self._value = self._compute()
                    self._computed_at = time.monotonic()
                    self._version = self.agg.version
        return self._value  # type: ignore[return-value]

    def invalidate(self) -> None:
        with self._lock:
            self._value = None

    def _compute(self) -> dict:
        all_data = Filters()
        champ_samples: list[tuple[float, float, float]] = []
        base: dict[tuple[str, int], float] = {}
        k_champ_guess = DEFAULTS["champ"]
        role_maps = {role: self.agg.role_champs(all_data, role) for role in ROLES}
        for champs in role_maps.values():
            for row in champs.values():
                games, wins = row["games"] or 0, row["wins"] or 0
                if games >= 100:
                    champ_samples.append((wins / games, 0.5, games))
        k_champ, n_champ = _estimate_k(champ_samples, "champ", min_samples=20)
        for role, champs in role_maps.items():
            for champ, row in champs.items():
                base[(role, champ)] = shrink(row["wins"] or 0, row["games"] or 0, 0.5, k_champ or k_champ_guess)

        matchup_samples: list[tuple[float, float, float]] = []
        for role in ROLES:
            for champ, opp, games, wins in self.agg.all_pairs(role):
                if games < 50:
                    continue
                expected = log5(base.get((role, champ), 0.5), base.get((role, opp), 0.5))
                matchup_samples.append((wins / games, expected, games))
        external = _external_matchup_samples(self.opgg)
        k_matchup, n_matchup = _estimate_k(matchup_samples + external, "matchup")

        synergy_samples: list[tuple[float, float, float]] = []
        for r1, c1, r2, c2, games, wins in self.agg.all_synergies():
            if games < 50:
                continue
            expected = sigmoid(logit(base.get((r1, c1), 0.5)) + logit(base.get((r2, c2), 0.5)))
            synergy_samples.append((wins / games, expected, games))
        k_synergy, n_synergy = _estimate_k(synergy_samples, "synergy")

        return {
            "champ": round(k_champ, 1),
            "matchup": round(k_matchup, 1),
            "synergy": round(k_synergy, 1),
            "samples": {"champ": n_champ, "matchup": n_matchup, "matchup_opgg": len(external), "synergy": n_synergy},
            "computed_at": int(time.time()),
        }
