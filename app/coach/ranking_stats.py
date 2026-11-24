"""Medias por elo de las estadísticas exactas de Riot con todas las partidas que guarda el recolector, también sin análisis.

En «todo el ranking» (sin campeones ni líneas) solo se baja la partida, sin timeline: no se puede reconstruir el
movimiento ni los errores, pero la partida trae los retos y estadísticas de Riot de los 10 jugadores (wards quitados,
pinks, visión, campamentos rivales…: ``rule_metrics``), justo lo que mejor separa elos. Cada partida nueva suma las métricas
``rl_*`` de sus 10 jugadores (por rol, con el elo del jugador por el que se encontró: el emparejamiento junta elos
parecidos) a un acumulado que se vuelca cada ``FLUSH_EVERY_S`` a ``data/otps/_medias_ranking.json`` (se sube a GitHub;
pesa poco: solo sumas). El perfil por elo (``elo_profile``) lo mezcla con lo de las partidas analizadas.
"""

from __future__ import annotations

import json
import threading
import time
from pathlib import Path

from app.coach import rule_metrics
from app.logbus import get_logger

log = get_logger("ranking-stats")

FILE = "_medias_ranking.json"
FLUSH_EVERY_S = 60
GROUP_OF = {
    "IRON": "BRONZE", "BRONZE": "BRONZE", "SILVER": "GOLD", "GOLD": "GOLD", "PLATINUM": "PLATINUM",
    "EMERALD": "EMERALD", "DIAMOND": "DIAMOND", "MASTER": "MASTER", "GRANDMASTER": "MASTER", "CHALLENGER": "CHALLENGER",
}
ROLE_OF = {"TOP": "TOP", "JUNGLE": "JUNGLE", "MIDDLE": "MID", "BOTTOM": "ADC", "UTILITY": "SUPPORT"}
# 2: Hierro-Bronce y Plata-Oro aparte de Platino. Lo sumado con la 1 en «PLATINUM» mezcla Hierro a Platino: se guarda
# aparte (``MIXED_KEY``, no se borra) y no se usa.
VERSION = 2
MIXED_KEY = "hierro_a_platino_v1"


def _upgrade(data: dict) -> dict:
    if int(data.get("version") or 1) >= VERSION:
        return data
    mixed = data.setdefault(MIXED_KEY, {})
    for role, groups in (data.get("roles") or {}).items():
        if "PLATINUM" in groups:
            mixed[role] = groups.pop("PLATINUM")
    data["version"] = VERSION
    return data


def participant_metrics(match: dict) -> list[tuple[str, dict]]:
    """(rol, métricas ``rl_*``) de cada jugador de la partida (las que no necesitan el timeline)."""
    info = (match or {}).get("info") or {}
    duration = info.get("gameDuration") or 0
    if duration > 100_000:  # partidas antiguas: en ms
        duration /= 1000
    minutes = duration / 60
    out = []
    for p in info.get("participants") or []:
        role = ROLE_OF.get(p.get("teamPosition") or "")
        if not role:
            continue
        m = rule_metrics.metrics(rule_metrics.raw_fields(p), None, minutes, role)
        if m:
            out.append((role, m))
    return out


class RankingStats:
    """Acumulado en memoria (por rol, elo y métrica: [partidas, suma, suma de cuadrados]) que se vuelca al fichero."""

    def __init__(self, data_dir: Path) -> None:
        self.path = Path(data_dir) / "otps" / FILE
        self._lock = threading.Lock()
        self._pending: dict = {}
        self._games = 0
        self._last_flush = time.monotonic()

    def add(self, match: dict, tier: str | None) -> None:
        group = GROUP_OF.get((tier or "").upper())
        if not group:
            return
        rows = participant_metrics(match)
        if not rows:
            return
        with self._lock:
            for role, metrics in rows:
                block = self._pending.setdefault(role, {}).setdefault(group, {})
                for key, v in metrics.items():
                    acc = block.setdefault(key, [0, 0.0, 0.0])
                    acc[0] += 1
                    acc[1] += v
                    acc[2] += v * v
            self._games += 1
            due = time.monotonic() - self._last_flush >= FLUSH_EVERY_S
        if due:
            self.flush()

    def flush(self) -> None:
        """Suma lo pendiente al fichero (lee, suma y reemplaza de una vez)."""
        with self._lock:
            pending, games = self._pending, self._games
            self._pending, self._games = {}, 0
            self._last_flush = time.monotonic()
        if not pending:
            return
        try:
            data = _upgrade(load(self.path.parent.parent) or {"version": VERSION, "games": 0, "roles": {}})
            data["games"] = int(data.get("games") or 0) + games
            for role, groups in pending.items():
                for group, metrics in groups.items():
                    block = data["roles"].setdefault(role, {}).setdefault(group, {})
                    for key, (n, s, s2) in metrics.items():
                        acc = block.setdefault(key, [0, 0.0, 0.0])
                        acc[0] += n
                        acc[1] = round(acc[1] + s, 4)
                        acc[2] = round(acc[2] + s2, 4)
            data["updated_at"] = int(time.time())
            self.path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.path.with_suffix(".json.tmp")
            tmp.write_text(json.dumps(data, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
            tmp.replace(self.path)
        except OSError:
            log.debug("No se pudieron guardar las medias del ranking", exc_info=True)
            with self._lock:  # se reintenta en el siguiente volcado
                for role, groups in pending.items():
                    for group, metrics in groups.items():
                        block = self._pending.setdefault(role, {}).setdefault(group, {})
                        for key, (n, s, s2) in metrics.items():
                            acc = block.setdefault(key, [0, 0.0, 0.0])
                            acc[0] += n
                            acc[1] += s
                            acc[2] += s2
                self._games += games


def load(data_dir: Path) -> dict | None:
    path = Path(data_dir) / "otps" / FILE
    try:
        return _upgrade(json.loads(path.read_text(encoding="utf-8")))
    except (OSError, ValueError):
        return None


_instances: dict[str, RankingStats] = {}
_instances_lock = threading.Lock()


def of(svc) -> RankingStats | None:
    data_dir = getattr(getattr(svc, "settings", None), "data_dir", None)
    if data_dir is None:
        return None
    key = str(Path(data_dir).resolve())
    with _instances_lock:
        inst = _instances.get(key)
        if inst is None:
            inst = _instances[key] = RankingStats(Path(data_dir))
        return inst
