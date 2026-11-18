"""Combina todas las fuentes de un enfrentamiento en una única estimación.

Para cada matchup A vs B (mismo rol) se juntan:

1. **OP.GG**: partidas y victorias de A contra B (página de counters, con el
   elo/región/parche elegidos). Muestras grandes: decenas de miles.
2. **Tu base de datos**: lo que haya recogido el recolector + el histórico.
3. **Estadísticas de campeón**: WR general de A y de B en ese rol. Con log5
   dan el resultado *esperado* si no hubiera ventaja específica.

Estimación final (bayesiana): ``(victorias + k·esperado) / (partidas + k)``.
Con pocas partidas manda lo esperado por la fuerza de los campeones; con
muchas, mandan los datos del enfrentamiento.
"""

from __future__ import annotations

from dataclasses import dataclass

from app.analytics.filters import Filters
from app.analytics.stats import log5, logit, posterior_logit_var, shrink, sigmoid
from app.external.opgg import PLATFORM_TO_REGION, TIERS as OPGG_TIERS
from app.riot.routing import ROLES

SOURCES = ("all", "opgg", "own")
TIERLIST_PSEUDO_GAMES = 2000  # peso de un WR de tier list sin nº de partidas conocido
LIGHT_PRIOR = 50.0  # encogimiento mínimo para el "esperado" de las sinergias


@dataclass(frozen=True)
class ExtScope:
    tier: str
    region: str
    patches: tuple[str, ...]

    def public(self) -> dict:
        return {"tier": self.tier, "region": self.region, "patches": list(self.patches)}


def opgg_tier(label: str) -> str:
    label = (label or "all").lower()
    if label in OPGG_TIERS:
        return label
    parts = [p.strip() for p in label.split(",") if p.strip()]
    if len(parts) == 1 and parts[0] in OPGG_TIERS:
        return parts[0]
    return "all"


def ext_scope(svc, f: Filters) -> ExtScope | None:
    if not getattr(svc, "opgg", None) or not svc.opgg.enabled:
        return None
    versions = svc.opgg.versions()
    if not versions:
        return None
    label = (f.label_patch or "all").lower()
    if label == "legacy":
        return None
    if label == "latest":
        patches = tuple(versions[:1])
    elif f.patches:
        patches = tuple(p for p in f.patches if p in versions)
        if not patches:
            return None
    else:
        patches = tuple(versions[:2])
    region = "global"
    if f.platforms and len(f.platforms) == 1:
        region = PLATFORM_TO_REGION.get(f.platforms[0], "global")
    return ExtScope(opgg_tier(f.label_tier), region, patches)


class Evidence:
    def __init__(self, svc, f: Filters, sources: str = "all", fetch: bool = True) -> None:
        self.svc = svc
        self.f = f
        self.sources = sources if sources in SOURCES else "all"
        self.use_own = self.sources in ("all", "own")
        self.scope = ext_scope(svc, f) if self.sources in ("all", "opgg") else None
        self.fetch = fetch
        self.priors = svc.priors.get()
        self.kc = self.priors["champ"]
        self.km = self.priors["matchup"]
        self.ks = self.priors["synergy"]
        self._own: dict[str, dict] = {}
        self._tier: dict[str, dict | None] = {}
        self._counters: dict[tuple[str, int], dict | None] = {}
        self._ref_patch: dict[str, str | None] = {}

    # ------------------------------------------------------------ fuentes
    def own_role(self, role: str) -> dict:
        if not self.use_own:
            return {}
        if role not in self._own:
            self._own[role] = self.svc.agg.role_champs(self.f, role)
        return self._own[role]

    def counters(self, role: str, champ: int, fetch: bool | None = None) -> dict | None:
        """Counters de OP.GG sumados en los parches del alcance."""
        if not self.scope:
            return None
        key = (role, champ)
        allow = self.fetch if fetch is None else fetch
        cached = self._counters.get(key)
        if cached is not None or (key in self._counters and not allow):
            return cached
        merged: dict[int, list[int]] = {}
        found = False
        for patch in self.scope.patches:
            data = self.svc.opgg.counters(champ, role, self.scope.tier, self.scope.region, patch, allow_fetch=allow)
            if data is None:
                continue
            found = True
            for opp, (games, wins) in data.items():
                row = merged.setdefault(opp, [0, 0])
                row[0] += games
                row[1] += wins
        result = {opp: (g, w) for opp, (g, w) in merged.items()} if found else None
        self._counters[key] = result
        return result

    def counters_by_patch(self, role: str, champ: int, opp: int) -> list[dict]:
        out = []
        if not self.scope:
            return out
        for patch in self.scope.patches:
            data = self.svc.opgg.counters(champ, role, self.scope.tier, self.scope.region, patch, allow_fetch=False) or {}
            if opp in data:
                games, wins = data[opp]
                out.append({"patch": patch, "games": games, "wins": wins})
        return out

    def ref_patch(self, role: str) -> str | None:
        """Parche de referencia para la tier list: el actual si ya tiene muestra suficiente."""
        if not self.scope:
            return None
        if role in self._ref_patch:
            return self._ref_patch[role]
        patches = self.scope.patches
        choice = patches[0]
        if len(patches) > 1:
            latest = self.svc.opgg.tier_list(role, self.scope.tier, self.scope.region, patches[0], allow_fetch=self.fetch) or {}
            if latest:
                popular = max(latest, key=lambda c: latest[c].get("pick") or 0)
                sizes = []
                for patch in patches[:2]:
                    data = self.svc.opgg.counters(popular, role, self.scope.tier, self.scope.region, patch, allow_fetch=self.fetch) or {}
                    sizes.append(sum(g for g, _ in data.values()))
                if sizes[0] < 0.5 * sizes[1]:
                    choice = patches[1]
            else:
                choice = patches[1]
        self._ref_patch[role] = choice
        return choice

    def tier_list(self, role: str) -> dict:
        if not self.scope:
            return {}
        if role not in self._tier:
            patch = self.ref_patch(role)
            data = self.svc.opgg.tier_list(role, self.scope.tier, self.scope.region, patch, allow_fetch=self.fetch) if patch else None
            if not data:  # si falta el de referencia, cualquiera del alcance
                for other in self.scope.patches:
                    data = self.svc.opgg.tier_list(role, self.scope.tier, self.scope.region, other, allow_fetch=False)
                    if data:
                        break
            self._tier[role] = data or {}
        return self._tier[role]

    def ext_role_matches(self, role: str) -> float | None:
        """Partidas totales de OP.GG en el rol (alcance actual), estimadas con
        los campeones cuyos counters ya están descargados: partidas / pick rate."""
        if not self.scope:
            return None
        cache = getattr(self, "_role_matches", None)
        if cache is None:
            cache = self._role_matches = {}
        if role in cache:
            return cache[role]
        ratios = []
        for champ, row in self.tier_list(role).items():
            pick = row.get("pick") or 0
            counters = self.counters(role, champ, fetch=False) if pick >= 0.5 else None
            if counters:
                ratios.append(sum(g for g, _ in counters.values()) / (pick / 100))
        cache[role] = sorted(ratios)[len(ratios) // 2] if ratios else None
        return cache[role]

    # ------------------------------------------------------- estimaciones
    def base_counts(self, role: str, champ: int) -> dict:
        """Partidas/victorias generales del campeón en el rol, por fuente."""
        own = self.own_role(role).get(champ) or {}
        n_o, w_o = own.get("games") or 0, own.get("wins") or 0
        n_e = w_e = 0.0
        ext_kind = None
        counters = self.counters(role, champ, fetch=False)
        if counters:
            n_e = float(sum(g for g, _ in counters.values()))
            w_e = float(sum(w for _, w in counters.values()))
            ext_kind = "counters"
        else:
            row = self.tier_list(role).get(champ)
            if row and row.get("wr") is not None:
                # Sin counters descargados: partidas ≈ pick rate × partidas del rol.
                total = self.ext_role_matches(role)
                n_e = float(total * (row.get("pick") or 0) / 100) if total else float(TIERLIST_PSEUDO_GAMES)
                n_e = max(n_e, 200.0)
                w_e = n_e * row["wr"] / 100
                ext_kind = "tierlist"
        return {"own": (n_o, w_o), "ext": (n_e, w_e), "ext_kind": ext_kind}

    def base(self, role: str, champ: int) -> tuple[float, float, float]:
        """(WR ajustado, varianza logit, partidas totales)."""
        c = self.base_counts(role, champ)
        games = c["own"][0] + c["ext"][0]
        wins = c["own"][1] + c["ext"][1]
        return shrink(wins, games, 0.5, self.kc), posterior_logit_var(wins, games, 0.5, self.kc), games

    def pair_counts(self, role: str, a: int, b: int, fetch: bool | None = None) -> dict:
        own_row = self.svc.agg.matchup(self.f, role, a, b) if self.use_own else None
        own_row = own_row or {}
        n_o, w_o = own_row.get("games") or 0, own_row.get("wins") or 0
        n_e = w_e = 0
        counters_a = self.counters(role, a, fetch=fetch)
        if counters_a and b in counters_a:
            n_e, w_e = counters_a[b]
        else:
            counters_b = self.counters(role, b, fetch=False)
            if counters_b and a in counters_b:
                games, wins_b = counters_b[a]
                n_e, w_e = games, games - wins_b
        return {"own": (n_o, w_o), "ext": (n_e, w_e), "own_row": own_row}

    def pair(self, role: str, a: int, b: int, fetch: bool | None = None) -> dict:
        counts = self.pair_counts(role, a, b, fetch)
        pa, _, ga = self.base(role, a)
        pb, _, gb = self.base(role, b)
        games = counts["own"][0] + counts["ext"][0]
        wins = counts["own"][1] + counts["ext"][1]
        expected = log5(pa, pb)
        adjusted = shrink(wins, games, expected, self.km)
        return {
            "games": games,
            "wins": wins,
            "own": counts["own"],
            "ext": counts["ext"],
            "own_row": counts["own_row"],
            "pa": pa,
            "pb": pb,
            "expected": expected,
            "adjusted": adjusted,
            "logit": logit(adjusted),
            "var": posterior_logit_var(wins, games, expected, self.km),
        }

    def synergy_counts(self, r1: str, c1: int, r2: str, c2: int, fetch: bool = False) -> dict:
        own = self.svc.agg.synergy(self.f, r1, c1, r2, c2) if self.use_own else (0, 0)
        ext = (0, 0)
        if self.scope:
            data = self.svc.opgg.synergies(c1, r1, r2, allow_fetch=fetch)
            if data and c2 in data:
                ext = data[c2]
            else:
                data = self.svc.opgg.synergies(c2, r2, r1, allow_fetch=False)
                if data and c1 in data:
                    ext = data[c1]
        return {"own": own, "ext": ext}

    def observed_base(self, role: str, champ: int) -> float:
        """WR general apenas encogido (k=50): lo que de verdad rinde en estos datos.

        Se usa como esperado de las sinergias. Si se usara el WR muy encogido,
        la fuerza "sobrante" de un campeón fuerte se contaría otra vez en cada
        una de sus parejas.
        """
        c = self.base_counts(role, champ)
        return shrink(c["own"][1] + c["ext"][1], c["own"][0] + c["ext"][0], 0.5, LIGHT_PRIOR)

    def synergy(self, r1: str, c1: int, r2: str, c2: int, fetch: bool = False) -> dict:
        counts = self.synergy_counts(r1, c1, r2, c2, fetch)
        games = counts["own"][0] + counts["ext"][0]
        wins = counts["own"][1] + counts["ext"][1]
        expected = sigmoid(logit(self.observed_base(r1, c1)) + logit(self.observed_base(r2, c2)))
        adjusted = shrink(wins, games, expected, self.ks)
        return {
            "games": games,
            "wins": wins,
            "own": counts["own"],
            "ext": counts["ext"],
            "expected": expected,
            "adjusted": adjusted,
            "logit": logit(adjusted) - logit(expected),
            "var": posterior_logit_var(wins, games, expected, self.ks) if games else 0.0,
        }

    def champions_in_role(self, role: str, min_games: int = 0) -> list[int]:
        champs = set(self.tier_list(role))
        champs |= {c for c, row in self.own_role(role).items() if (row.get("games") or 0) >= min_games}
        return sorted(champs)

    def public(self) -> dict:
        return {
            "sources": self.sources,
            "opgg": self.scope.public() if self.scope else None,
            "own": self.use_own,
            "ref_patches": {role: self._ref_patch.get(role) for role in ROLES if role in self._ref_patch},
        }
