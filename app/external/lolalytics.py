"""Datos de Lolalytics: segunda fuente estadística, independiente de OP.GG.

Se usa la API JSON pública que alimenta su propia web (``a1.lolalytics.com``),
con el ``robots.txt`` permitiendo clientes genéricos (``User-agent: *``,
``Allow: /``; solo prohíbe entrenar IA con sus datos). Sin saltarse ninguna
protección y a ritmo tranquilo (≈1 petición/s), con caché en SQLite.

* **Tier list** por línea: WR, pick, ban, partidas y % de partidas en esa línea.
* **Counters** de un campeón en su línea: WR contra cada rival y nº de partidas.

Las muestras de OP.GG y Lolalytics salen de la misma población (partidas
clasificatorias de Riot) y se solapan, así que no se suman a ciegas: al
combinarlas se toma el WR ponderado y como tamaño de muestra el mayor de los
dos (estimación conservadora). Ver :mod:`app.analytics.evidence`.
"""

from __future__ import annotations

import threading

import requests

from app import __version__
from app.data.db import Database
from app.ddragon import DDragon
from app.external.base import CachedSource, Throttle
from app.logbus import get_logger

log = get_logger("lolalytics")

API = "https://a1.lolalytics.com/mega/"
USER_AGENT = f"TempuDrafter/{__version__} (personal stats tool)"
LANES = {"TOP": "top", "JUNGLE": "jungle", "MID": "middle", "ADC": "bottom", "SUPPORT": "support"}
# Ámbitos de OP.GG -> Lolalytics (los *_plus coinciden).
TIERS = {
    "all": "all", "iron": "iron", "bronze": "bronze", "silver": "silver", "gold": "gold", "gold_plus": "gold_plus",
    "platinum": "platinum", "platinum_plus": "platinum_plus", "emerald": "emerald", "emerald_plus": "emerald_plus",
    "diamond": "diamond", "diamond_plus": "diamond_plus", "master": "master", "master_plus": "master_plus",
    "grandmaster": "grandmaster", "challenger": "challenger",
}
REGIONS = {
    "global": "all", "euw": "euw", "eune": "eune", "na": "na", "kr": "kr", "jp": "jp", "br": "br", "lan": "lan",
    "las": "las", "oce": "oce", "tr": "tr", "ru": "ru", "me": "me", "sg": "sg", "tw": "tw", "vn": "vn",
}
SLUG_EXCEPTIONS = {"MonkeyKing": "wukong"}
TTL_LATEST = 6 * 3600
TTL_OLD = 7 * 86400


class LolalyticsError(Exception):
    pass


def game_version(patch: str) -> str:
    """Parche como lo usa Lolalytics (versión del juego): ``26.23`` -> ``16.23``; ``16.23`` se queda igual."""
    try:
        major, minor = str(patch).split(".")[:2]
        major_i = int(major)
        int(minor)
    except (ValueError, AttributeError):
        return str(patch)
    if major_i >= 25:  # desde 2025 el parche se llama por el año y la versión del juego va 10 por detrás
        major_i -= 10
    return f"{major_i}.{int(minor)}"


class Lolalytics(CachedSource):
    name = "lolalytics"
    default_lane = "lolalytics"

    def __init__(self, db: Database, dd: DDragon, enabled: bool = True, warmer=None, latest_patch=None) -> None:
        super().__init__(db, enabled=enabled, warmer=warmer)
        self.dd = dd
        self._local = threading.local()
        self._throttle = Throttle(1.5)
        self._latest_patch = latest_patch  # callable -> parche en curso (para el TTL)

    def _http(self) -> requests.Session:
        session = getattr(self._local, "session", None)
        if session is None:
            session = requests.Session()
            session.headers["User-Agent"] = USER_AGENT
            session.headers["Accept"] = "application/json"
            self._local.session = session
        return session

    def slug(self, champ: int) -> str:
        key = self.dd.key(champ) or ""
        return SLUG_EXCEPTIONS.get(key, key.lower())

    def _ttl(self, patch: str) -> int:
        latest = None
        try:
            latest = self._latest_patch() if self._latest_patch else None
        except Exception:
            latest = None
        return TTL_LATEST if not latest or game_version(patch) == game_version(latest) else TTL_OLD

    def _get(self, params: dict) -> dict:
        self._throttle.wait()
        response = self._http().get(API, params={**params, "v": 1, "queue": 420}, timeout=20)
        if response.status_code != 200:
            raise LolalyticsError(f"HTTP {response.status_code}")
        try:
            data = response.json()
        except ValueError as exc:
            raise LolalyticsError("respuesta no válida") from exc
        if not isinstance(data, dict) or data.get("status") or (isinstance(data.get("response"), dict) and data["response"].get("valid") is False):
            raise LolalyticsError("sin datos para esos filtros")
        return data

    @staticmethod
    def _scope(tier: str, region: str) -> tuple[str, str] | None:
        t = TIERS.get((tier or "all").lower())
        r = REGIONS.get((region or "global").lower())
        if t is None or r is None:
            return None
        return t, r

    def tier_list(self, role: str, tier: str, region: str, patch: str, allow_fetch: bool = True) -> dict[int, dict] | None:
        """{campeón: {wr, pick, ban, games, role_rate, tier, rank}} de una línea."""
        scope = self._scope(tier, region)
        if scope is None or role not in LANES:
            return None
        lane = LANES[role]
        version = game_version(patch)
        key = f"{lane}|{scope[0]}|{scope[1]}|{version}"

        def fetch():
            data = self._get({"ep": "list", "patch": version, "lane": lane, "tier": scope[0], "region": scope[1]})
            rows = data.get("cid")
            if not isinstance(rows, dict):
                return None
            out = {}
            for cid, row in rows.items():
                if not isinstance(row, dict) or not str(cid).isdigit():
                    continue
                games = int(row.get("games") or 0)
                if games <= 0:
                    continue
                out[str(int(cid))] = {
                    "wr": _float(row.get("wr")),
                    "pick": _float(row.get("pr")),
                    "ban": _float(row.get("br")),
                    "games": games,
                    "role_rate": round((_float(row.get("pctLane")) or 0) / 100, 4),
                    "tier": row.get("tier"),
                    "rank": row.get("rank"),
                }
            return out or None

        data = self._cached("lola_tier", key, self._ttl(patch), fetch, allow_fetch)
        return {int(k): v for k, v in data.items()} if data else None

    def counters(self, champ: int, role: str, tier: str, region: str, patch: str, allow_fetch: bool = True) -> dict[int, tuple[int, int]] | None:
        """{rival: (partidas, victorias de ``champ``)} en la misma línea."""
        scope = self._scope(tier, region)
        slug = self.slug(champ)
        if scope is None or role not in LANES or not slug:
            return None
        lane = LANES[role]
        version = game_version(patch)
        key = f"{champ}|{lane}|{scope[0]}|{scope[1]}|{version}"

        def fetch():
            data = self._get({"ep": "counter", "patch": version, "c": slug, "lane": lane, "tier": scope[0], "region": scope[1]})
            stats = data.get("stats") or {}
            if stats.get("cid") not in (None, champ):
                raise LolalyticsError(f"campeón inesperado ({stats.get('cid')} en vez de {champ})")
            out = {}
            for row in data.get("counters") or []:
                try:
                    opp = int(row["cid"])
                    games = int(row.get("n") or 0)
                    wr = float(row.get("vsWr"))
                except (KeyError, TypeError, ValueError):
                    continue
                if opp == champ or games <= 0 or not 0 <= wr <= 100:
                    continue
                out[str(opp)] = [games, int(round(games * wr / 100))]
            return out  # {} es un resultado válido (nadie lo juega en esa línea)

        data = self._cached("lola_counters", key, self._ttl(patch), fetch, allow_fetch)
        if data is None:
            return None
        return {int(k): (v[0], v[1]) for k, v in data.items()}


def _float(value) -> float | None:
    try:
        return round(float(value), 3)
    except (TypeError, ValueError):
        return None
