"""Configuración central del proyecto.

Todo se lee del fichero ``.env`` de la raíz (ver ``.env.example``) con
fallback a variables de entorno. La API key de Riot NO se lee aquí: la
gestiona :mod:`app.riot.keys` para poder recargarla en caliente.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import dotenv_values

BASE_DIR = Path(__file__).resolve().parent.parent
ENV_PATH = BASE_DIR / ".env"
WEB_DIR = BASE_DIR / "web"


def _env_values() -> dict[str, str]:
    if ENV_PATH.exists():
        return {k: v for k, v in dotenv_values(ENV_PATH).items() if v is not None}
    return {}


def env(name: str, default: str | None = None) -> str | None:
    """Lee una variable priorizando .env y después el entorno del sistema."""
    value = _env_values().get(name)
    if value in (None, ""):
        value = os.environ.get(name, default)
    return value


def _split(value: str | None) -> list[str]:
    return [item.strip() for item in (value or "").split(",") if item.strip()]


def _int(value: str | None, default: int) -> int:
    try:
        return int(value) if value not in (None, "") else default
    except ValueError:
        return default


@dataclass
class Settings:
    host: str = "127.0.0.1"
    port: int = 8000
    data_dir: Path = BASE_DIR / "data"
    db_path: Path = BASE_DIR / "data" / "lol.db"
    legacy_dir: Path = BASE_DIR / "data" / "legacy"
    coachings_dir: Path = BASE_DIR / "data" / "coachings"
    ddragon_dir: Path = BASE_DIR / "data" / "ddragon"
    ddragon_lang: str = "es_ES"
    gemini_keys: list[str] = field(default_factory=list)
    gemini_models: list[str] = field(default_factory=list)
    # Colas que alimentan las estadísticas agregadas (420 = SoloQ, 440 = Flex).
    stats_queues: tuple[int, ...] = (420, 440)
    # Datos de OP.GG (counters, tier list, sinergias, guías de matchup).
    opgg_enabled: bool = True

    @classmethod
    def load(cls) -> "Settings":
        data_dir = Path(env("DATA_DIR") or (BASE_DIR / "data"))
        if not data_dir.is_absolute():
            data_dir = BASE_DIR / data_dir

        # Compatibilidad: si existe la carpeta antigua ./coachings la seguimos usando.
        coachings = env("COACHINGS_DIR")
        if coachings:
            coachings_dir = Path(coachings)
            if not coachings_dir.is_absolute():
                coachings_dir = BASE_DIR / coachings_dir
        elif (BASE_DIR / "coachings").is_dir() and not (data_dir / "coachings").is_dir():
            coachings_dir = BASE_DIR / "coachings"
        else:
            coachings_dir = data_dir / "coachings"

        gemini_keys = _split(env("GEMINI_KEYS"))
        for name in ("GEMINI_KEY_1", "GEMINI_KEY_2", "GEMINI_KEY_3"):
            value = env(name)
            if value and value not in gemini_keys:
                gemini_keys.append(value)

        queues = tuple(int(q) for q in _split(env("STATS_QUEUES", "420,440")) if q.isdigit())

        return cls(
            host=env("HOST", "127.0.0.1") or "127.0.0.1",
            port=_int(env("PORT"), 8000),
            data_dir=data_dir,
            db_path=data_dir / "lol.db",
            legacy_dir=data_dir / "legacy",
            coachings_dir=coachings_dir,
            ddragon_dir=data_dir / "ddragon",
            ddragon_lang=env("DDRAGON_LANG", "es_ES") or "es_ES",
            gemini_keys=gemini_keys,
            gemini_models=_split(env("GEMINI_MODELS", "gemini-2.5-flash,gemini-2.5-flash-lite")),
            stats_queues=queues or (420, 440),
            opgg_enabled=(env("OPGG_ENABLED", "1") or "1").strip().lower() not in ("0", "false", "no", "off"),
        )


settings = Settings.load()
