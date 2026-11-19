"""Cliente HTTP de la API de Riot con reintentos, prioridades y rate limiting."""

from __future__ import annotations

import threading
import time
from urllib.parse import quote

import requests

from app import __version__
from app.logbus import get_logger
from app.riot.keys import KeyStore
from app.riot.ratelimit import Priority, RateLimiter
from app.riot.routing import LEAGUE_QUEUE, QUEUE_SOLO, account_route, region_of

log = get_logger("riot")


class RiotAPIError(Exception):
    def __init__(self, status: int, message: str) -> None:
        super().__init__(message)
        self.status = status
        self.message = message


class RiotKeyError(RiotAPIError):
    """La API key falta, ha caducado o no es válida."""


class Cancelled(Exception):
    """La operación se canceló (parada del recolector o apagado)."""


class RiotClient:
    def __init__(self, keys: KeyStore, limiter: RateLimiter) -> None:
        self.keys = keys
        self.limiter = limiter
        self._local = threading.local()
        self._validate_lock = threading.Lock()

    # ------------------------------------------------------------------ núcleo
    def _session(self) -> requests.Session:
        session = getattr(self._local, "session", None)
        if session is None:
            session = requests.Session()
            session.headers["User-Agent"] = f"TempuDrafter/{__version__}"
            self._local.session = session
        return session

    def request(
        self,
        host: str,
        path: str,
        *,
        method: str,
        params: dict | None = None,
        priority: int = Priority.HIGH,
        stop: threading.Event | None = None,
        retries: int = 3,
        timeout: float = 15.0,
        validate_on_403: bool = True,
    ):
        key = self.keys.get()
        if not key:
            raise RiotKeyError(0, "No hay RIOT_API_KEY configurada")
        url = f"https://{host}.api.riotgames.com{path}"
        failures = 0
        throttles = 0
        while True:
            if not self.limiter.acquire(host, method, priority, stop):
                raise Cancelled()
            try:
                response = self._session().get(url, params=params, headers={"X-Riot-Token": key}, timeout=timeout)
            except requests.RequestException as exc:
                failures += 1
                if failures > retries:
                    raise RiotAPIError(0, f"Error de red con {host}: {exc}") from exc
                self._sleep(min(2**failures, 15), stop)
                continue

            status = response.status_code
            self.limiter.update(host, method, status, response.headers)

            if status == 200:
                self.keys.mark_valid()
                return response.json()
            if status == 404:
                return None
            if status == 429:
                throttles += 1
                if throttles > 8:
                    raise RiotAPIError(429, "Demasiados 429 seguidos")
                log.warning("429 en %s (%s). Esperando lo que indica Riot…", host, method)
                continue
            if status == 401:
                detail = f"401 {self._riot_message(response) or 'Unauthorized'}"
                self.keys.mark_invalid(detail)
                raise RiotKeyError(401, f"Riot no reconoce la API key ({detail})")
            if status == 403:
                # 403 suele ser key caducada, pero también endpoint no permitido:
                # se confirma con un endpoint barato antes de invalidar la key.
                if validate_on_403 and self.validate_key():
                    raise RiotAPIError(403, f"Acceso denegado a {method}")
                detail = f"403 {self._riot_message(response) or 'Forbidden'}"
                raise RiotKeyError(403, f"Riot rechaza la API key ({detail}): caducada, mal copiada o aún sin activar")
            if status >= 500:
                failures += 1
                if failures > retries:
                    raise RiotAPIError(status, f"Riot devolvió {status} en {method}")
                self._sleep(min(2**failures, 15), stop)
                continue
            raise RiotAPIError(status, f"HTTP {status} en {method}: {response.text[:160]}")

    @staticmethod
    def _sleep(seconds: float, stop: threading.Event | None) -> None:
        if stop is not None:
            if stop.wait(seconds):
                raise Cancelled()
        else:
            time.sleep(seconds)

    @staticmethod
    def _riot_message(response) -> str:
        """Mensaje de error de Riot (``{"status": {"message": ...}}``) si lo hay."""
        try:
            return str((response.json().get("status") or {}).get("message") or "")[:120]
        except ValueError:
            return ""

    def check_key(self, platform: str = "euw1") -> dict:
        """Comprueba la key con status-v4 (1 petición, host de plataforma).

        ``valid`` es True (Riot la acepta), False (Riot la rechaza: 401/403) o
        None (no se pudo comprobar: red caída o Riot con errores 5xx).
        """
        with self._validate_lock:
            try:
                self.request(
                    platform,
                    "/lol/status/v4/platform-data",
                    method="status-v4.platform",
                    retries=1,
                    validate_on_403=False,
                )
                return {"valid": True, "status": 200, "reason": "Riot acepta la key"}
            except RiotKeyError as exc:
                self.keys.mark_invalid(exc.message)
                return {"valid": False, "status": exc.status, "reason": exc.message}
            except RiotAPIError as exc:
                return {"valid": None, "status": exc.status, "reason": exc.message}

    def validate_key(self, platform: str = "euw1") -> bool:
        result = self.check_key(platform)
        if result["valid"] is None:  # sin respuesta de Riot: se mantiene lo que se sabía
            return self.keys.status == "valid"
        return result["valid"]

    # -------------------------------------------------------------- account-v1
    def account_by_riot_id(self, game_name: str, tag_line: str, platform: str, **kw):
        path = f"/riot/account/v1/accounts/by-riot-id/{quote(game_name.strip(), safe='')}/{quote(tag_line.strip(), safe='')}"
        return self.request(account_route(platform), path, method="account-v1.by-riot-id", **kw)

    def account_by_puuid(self, puuid: str, platform: str, **kw):
        return self.request(account_route(platform), f"/riot/account/v1/accounts/by-puuid/{puuid}", method="account-v1.by-puuid", **kw)

    def active_platform(self, puuid: str, platform_hint: str = "euw1", **kw) -> str | None:
        data = self.request(
            account_route(platform_hint),
            f"/riot/account/v1/region/by-game/lol/by-puuid/{puuid}",
            method="account-v1.region",
            **kw,
        )
        if isinstance(data, dict) and data.get("region"):
            return str(data["region"]).lower()
        return None

    # ------------------------------------------------------- summoner / league
    def summoner_by_puuid(self, platform: str, puuid: str, **kw):
        return self.request(platform, f"/lol/summoner/v4/summoners/by-puuid/{puuid}", method="summoner-v4.by-puuid", **kw)

    def league_entries_by_puuid(self, platform: str, puuid: str, **kw) -> list[dict]:
        return self.request(platform, f"/lol/league/v4/entries/by-puuid/{puuid}", method="league-v4.by-puuid", **kw) or []

    def league_entries(self, platform: str, tier: str, division: str, page: int = 1, queue: int = QUEUE_SOLO, **kw) -> list[dict]:
        path = f"/lol/league/v4/entries/{LEAGUE_QUEUE[queue]}/{tier}/{division}"
        return self.request(platform, path, method="league-v4.entries", params={"page": page}, **kw) or []

    def apex_league(self, platform: str, tier: str, queue: int = QUEUE_SOLO, **kw) -> dict | None:
        endpoint = {"CHALLENGER": "challengerleagues", "GRANDMASTER": "grandmasterleagues", "MASTER": "masterleagues"}[tier]
        path = f"/lol/league/v4/{endpoint}/by-queue/{LEAGUE_QUEUE[queue]}"
        return self.request(platform, path, method=f"league-v4.{endpoint}", **kw)

    # ------------------------------------------------------------ maestría
    def top_masteries(self, platform: str, puuid: str, count: int = 3, **kw) -> list[dict]:
        """Campeones con más puntos de maestría de un jugador (champion-mastery-v4)."""
        path = f"/lol/champion-mastery/v4/champion-masteries/by-puuid/{puuid}/top"
        data = self.request(platform, path, method="champion-mastery-v4.top", params={"count": max(1, min(count, 10))}, **kw)
        return data if isinstance(data, list) else []

    # ---------------------------------------------------------------- match-v5
    def match_ids(
        self,
        platform: str,
        puuid: str,
        *,
        queue: int | None = None,
        ranked_only: bool = False,
        start_time: int | None = None,
        start: int = 0,
        count: int = 20,
        **kw,
    ) -> list[str]:
        params: dict = {"start": start, "count": max(1, min(count, 100))}
        if queue is not None:
            params["queue"] = queue
        if ranked_only:
            params["type"] = "ranked"
        if start_time:
            params["startTime"] = int(start_time)
        path = f"/lol/match/v5/matches/by-puuid/{puuid}/ids"
        return self.request(region_of(platform), path, method="match-v5.ids", params=params, **kw) or []

    def match(self, match_id: str, **kw) -> dict | None:
        platform = match_id.split("_", 1)[0].lower()
        return self.request(region_of(platform), f"/lol/match/v5/matches/{match_id}", method="match-v5.match", **kw)

    def timeline(self, match_id: str, **kw) -> dict | None:
        platform = match_id.split("_", 1)[0].lower()
        return self.request(region_of(platform), f"/lol/match/v5/matches/{match_id}/timeline", method="match-v5.timeline", **kw)
