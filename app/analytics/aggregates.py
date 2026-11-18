"""Capa de consulta sobre las tablas ``agg_*`` con caché por versión de datos."""

from __future__ import annotations

import threading
import time
from collections import OrderedDict

from app.analytics.filters import Filters
from app.data.db import Database
from app.data.repository import CHAMP_COLS, MATCHUP_COLS
from app.riot.routing import ROLE_INDEX

CACHE_TTL = 60.0
CACHE_SIZE = 256


class _Cache:
    def __init__(self) -> None:
        self._data: OrderedDict = OrderedDict()
        self._lock = threading.Lock()

    def get(self, key, version):
        with self._lock:
            item = self._data.get(key)
            if item is None:
                return None
            stored_version, stored_at, value = item
            if stored_version != version or time.monotonic() - stored_at > CACHE_TTL:
                self._data.pop(key, None)
                return None
            self._data.move_to_end(key)
            return value

    def put(self, key, version, value) -> None:
        with self._lock:
            self._data[key] = (version, time.monotonic(), value)
            self._data.move_to_end(key)
            while len(self._data) > CACHE_SIZE:
                self._data.popitem(last=False)


def _sums(cols: list[str]) -> str:
    return ", ".join(f"SUM({c}) AS {c}" for c in cols)


class Aggregates:
    """Todas las sumas que necesita la capa de análisis, con filtros."""

    def __init__(self, db: Database) -> None:
        self.db = db
        self.version = 0
        self._cache = _Cache()

    def bump(self) -> None:
        """Invalida la caché (llamar tras ingerir datos)."""
        self.version += 1

    def _cached(self, key, fn):
        value = self._cache.get(key, self.version)
        if value is None:
            value = fn()
            self._cache.put(key, self.version, value)
        return value

    # ---------------------------------------------------------------- totales
    def totals(self, f: Filters) -> dict:
        def run():
            where, params = f.clause()
            row = self.db.one(
                f"SELECT SUM(matches) AS matches, SUM(dmatches) AS dmatches, SUM(blue_wins) AS blue_wins, "
                f"SUM(minutes) AS minutes FROM agg_totals WHERE {where}",
                params,
            )
            return {k: (row[k] or 0) for k in ("matches", "dmatches", "blue_wins", "minutes")}

        return self._cached(("totals", f.cache_key()), run)

    def bans(self, f: Filters) -> dict[int, int]:
        def run():
            where, params = f.clause()
            rows = self.db.query(f"SELECT champ, SUM(bans) AS bans FROM agg_bans WHERE {where} GROUP BY champ", params)
            return {r["champ"]: r["bans"] for r in rows}

        return self._cached(("bans", f.cache_key()), run)

    # -------------------------------------------------------------- campeones
    def role_champs(self, f: Filters, role: str) -> dict[int, dict]:
        """Stats de todos los campeones en un rol: {champ: {...sumas}}."""

        def run():
            where, params = f.clause()
            rows = self.db.query(
                f"SELECT champ, {_sums(CHAMP_COLS)} FROM agg_champ WHERE role = ? AND {where} GROUP BY champ",
                [role, *params],
            )
            return {r["champ"]: dict(r) for r in rows}

        return self._cached(("role_champs", role, f.cache_key()), run)

    def champ_roles(self, f: Filters, champ: int) -> dict[str, dict]:
        def run():
            where, params = f.clause()
            rows = self.db.query(
                f"SELECT role, {_sums(CHAMP_COLS)} FROM agg_champ WHERE champ = ? AND {where} GROUP BY role",
                [champ, *params],
            )
            return {r["role"]: dict(r) for r in rows}

        return self._cached(("champ_roles", champ, f.cache_key()), run)

    def role_totals(self, f: Filters) -> dict[str, dict]:
        """Sumas por rol (para medias de referencia del rol)."""

        def run():
            where, params = f.clause()
            rows = self.db.query(f"SELECT role, {_sums(CHAMP_COLS)} FROM agg_champ WHERE {where} GROUP BY role", params)
            return {r["role"]: dict(r) for r in rows}

        return self._cached(("role_totals", f.cache_key()), run)

    def champ_breakdown(self, f: Filters, role: str, champ: int, dim: str) -> list[dict]:
        assert dim in ("tier", "patch", "platform")
        where, params = f.clause()
        rows = self.db.query(
            f"SELECT {dim} AS dim, SUM(games) AS games, SUM(wins) AS wins FROM agg_champ "
            f"WHERE role = ? AND champ = ? AND {where} GROUP BY {dim}",
            [role, champ, *params],
        )
        return [dict(r) for r in rows]

    # --------------------------------------------------------------- matchups
    def matchups_of(self, f: Filters, role: str, champ: int) -> dict[int, dict]:
        """{rival: sumas} desde el punto de vista de ``champ``."""

        def run():
            where, params = f.clause()
            rows = self.db.query(
                f"SELECT opp, {_sums(MATCHUP_COLS)} FROM agg_matchup WHERE role = ? AND champ = ? AND {where} GROUP BY opp",
                [role, champ, *params],
            )
            return {r["opp"]: dict(r) for r in rows}

        return self._cached(("matchups_of", role, champ, f.cache_key()), run)

    def matchups_against(self, f: Filters, role: str, opp: int) -> dict[int, dict]:
        """{candidato: sumas} de todos los campeones que se han enfrentado a ``opp``."""

        def run():
            where, params = f.clause()
            rows = self.db.query(
                f"SELECT champ, {_sums(MATCHUP_COLS)} FROM agg_matchup WHERE role = ? AND opp = ? AND {where} GROUP BY champ",
                [role, opp, *params],
            )
            return {r["champ"]: dict(r) for r in rows}

        return self._cached(("matchups_against", role, opp, f.cache_key()), run)

    def matchup(self, f: Filters, role: str, champ: int, opp: int) -> dict | None:
        return self.matchups_of(f, role, champ).get(opp)

    def matchup_breakdown(self, f: Filters, role: str, champ: int, opp: int, dim: str) -> list[dict]:
        assert dim in ("tier", "patch", "platform")
        where, params = f.clause()
        rows = self.db.query(
            f"SELECT {dim} AS dim, {_sums(MATCHUP_COLS)} FROM agg_matchup "
            f"WHERE role = ? AND champ = ? AND opp = ? AND {where} GROUP BY {dim}",
            [role, champ, opp, *params],
        )
        return [dict(r) for r in rows]

    # -------------------------------------------------------------- sinergias
    @staticmethod
    def _ordered(role_a: str, champ_a: int, role_b: str, champ_b: int):
        if ROLE_INDEX[role_a] <= ROLE_INDEX[role_b]:
            return role_a, champ_a, role_b, champ_b
        return role_b, champ_b, role_a, champ_a

    def synergy(self, f: Filters, role_a: str, champ_a: int, role_b: str, champ_b: int) -> tuple[int, int]:
        r1, c1, r2, c2 = self._ordered(role_a, champ_a, role_b, champ_b)

        def run():
            where, params = f.clause()
            row = self.db.one(
                f"SELECT SUM(games) AS games, SUM(wins) AS wins FROM agg_synergy "
                f"WHERE role1 = ? AND champ1 = ? AND role2 = ? AND champ2 = ? AND {where}",
                [r1, c1, r2, c2, *params],
            )
            return (row["games"] or 0, row["wins"] or 0) if row else (0, 0)

        return self._cached(("synergy", r1, c1, r2, c2, f.cache_key()), run)

    def partners(self, f: Filters, role: str, champ: int, partner_role: str) -> dict[int, tuple[int, int]]:
        """{compañero: (partidas, victorias)} para ``champ`` en ``role`` con aliados en ``partner_role``."""

        def run():
            where, params = f.clause()
            if ROLE_INDEX[role] < ROLE_INDEX[partner_role]:
                sql = (
                    f"SELECT champ2 AS partner, SUM(games) AS games, SUM(wins) AS wins FROM agg_synergy "
                    f"WHERE role1 = ? AND champ1 = ? AND role2 = ? AND {where} GROUP BY champ2"
                )
            else:
                sql = (
                    f"SELECT champ1 AS partner, SUM(games) AS games, SUM(wins) AS wins FROM agg_synergy "
                    f"WHERE role2 = ? AND champ2 = ? AND role1 = ? AND {where} GROUP BY champ1"
                )
            rows = self.db.query(sql, [role, champ, partner_role, *params])
            return {r["partner"]: (r["games"], r["wins"]) for r in rows}

        return self._cached(("partners", role, champ, partner_role, f.cache_key()), run)

    # ----------------------------------------------------------- todo el rol
    def all_pairs(self, role: str) -> list[tuple[int, int, int, int]]:
        """(champ, opp, games, wins) sin filtros, para estimar priors."""
        rows = self.db.query(
            "SELECT champ, opp, SUM(games), SUM(wins) FROM agg_matchup WHERE role = ? AND champ < opp GROUP BY champ, opp",
            (role,),
        )
        return [tuple(r) for r in rows]

    def all_synergies(self) -> list[tuple[str, int, str, int, int, int]]:
        rows = self.db.query(
            "SELECT role1, champ1, role2, champ2, SUM(games), SUM(wins) FROM agg_synergy "
            "GROUP BY role1, champ1, role2, champ2 HAVING SUM(games) >= 30"
        )
        return [tuple(r) for r in rows]
