"""Gestión de la API key de Riot (manual, sin bots de renovación).

* Se lee de ``RIOT_API_KEY`` en ``.env`` y se **recarga en caliente**: si
  pegas una key nueva en el fichero (o desde la web) no hace falta reiniciar.
* Se marca como inválida ante un 401/403 confirmado y los procesos en segundo
  plano se pausan hasta que cambie la key.
* La edad de la key se cuenta desde que se vio por primera vez (``state_path``
  guarda una huella, nunca la key), no desde que arrancó la app: si reinicias
  con una development key de 20 h, la cuenta atrás sigue en ~4 h.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import threading
import time
from pathlib import Path

from dotenv import dotenv_values, set_key

KEY_PATTERN = re.compile(r"^RGAPI-[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$")
DEV_KEY_LIFETIME_H = 24


def _fingerprint(key: str) -> str:
    return hashlib.sha256(key.encode("utf-8")).hexdigest()[:16]


class KeyStore:
    def __init__(self, env_path: Path, state_path: Path | None = None) -> None:
        self.env_path = env_path
        self.state_path = state_path
        self._lock = threading.Lock()
        self._changed = threading.Condition(self._lock)
        self._key = ""
        self._mtime: float | None = None
        self._last_poll = 0.0
        self.version = 0
        self.status = "missing"  # missing | unknown | valid | invalid
        self.detail = ""
        self.loaded_at: float | None = None
        self.checked_at: float | None = None
        # Recomprobación automática de keys recién generadas (Riot puede tardar en activarlas).
        self.rechecking = False
        self.next_check_at: float | None = None
        with self._lock:
            self._reload(force=True)

    # ------------------------------------------------------------------ lectura
    def _read_file_key(self) -> str:
        if not self.env_path.exists():
            return ""
        return (dotenv_values(self.env_path).get("RIOT_API_KEY") or "").strip().strip("'\"")

    def _reload(self, force: bool = False) -> None:
        now = time.monotonic()
        if not force and now - self._last_poll < 2.0:
            return
        self._last_poll = now
        try:
            mtime = self.env_path.stat().st_mtime
        except FileNotFoundError:
            mtime = None
        if not force and mtime == self._mtime:
            return
        self._mtime = mtime
        file_key = self._read_file_key()
        key = file_key or os.environ.get("RIOT_API_KEY", "").strip()
        if key != self._key:
            self._apply(key, file_mtime=mtime if file_key else None)

    def _apply(self, key: str, file_mtime: float | None = None) -> None:
        self._key = key
        self.version += 1
        self.status = "unknown" if key else "missing"
        self.detail = ""
        self.loaded_at = self._first_seen(key, file_mtime) if key else None
        self.checked_at = None
        self._changed.notify_all()

    def _first_seen(self, key: str, file_mtime: float | None) -> float:
        """Cuándo apareció esta key (no cuándo arrancó la app).

        Si ya se había visto, se usa ese momento. Si es nueva y viene del
        ``.env``, el fichero se escribió como tarde cuando se pegó: se toma su
        fecha de modificación. Si se pega desde la web, es ahora.
        """
        now = time.time()
        fingerprint = _fingerprint(key)
        state = self._read_state()
        first_seen = state.get("first_seen")
        if state.get("fingerprint") == fingerprint and isinstance(first_seen, (int, float)) and 0 < first_seen <= now:
            return float(first_seen)
        seen = min(now, file_mtime) if file_mtime else now
        self._write_state({"fingerprint": fingerprint, "first_seen": seen})
        return seen

    def _read_state(self) -> dict:
        if self.state_path is None:
            return {}
        try:
            data = json.loads(self.state_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}
        return data if isinstance(data, dict) else {}

    def _write_state(self, data: dict) -> None:
        if self.state_path is None:
            return
        try:
            self.state_path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.state_path.with_name(self.state_path.name + ".tmp")
            tmp.write_text(json.dumps(data), encoding="utf-8")
            os.replace(tmp, self.state_path)
        except OSError:
            pass  # sin poder guardarlo, la edad se cuenta desde ahora (como antes)

    def get(self) -> str:
        with self._lock:
            self._reload()
            return self._key

    # ---------------------------------------------------------------- escritura
    def set(self, key: str) -> None:
        key = (key or "").strip()
        if not KEY_PATTERN.match(key):
            raise ValueError("Formato no válido. Debe ser RGAPI-xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx")
        self.env_path.touch(exist_ok=True)
        set_key(str(self.env_path), "RIOT_API_KEY", key, quote_mode="never")
        os.environ["RIOT_API_KEY"] = key
        with self._lock:
            self._mtime = self.env_path.stat().st_mtime
            self._apply(key)

    # ------------------------------------------------------------------- estado
    def mark_valid(self) -> None:
        self.checked_at = time.time()
        if self.status != "valid":
            with self._lock:
                self.status = "valid"
                self.detail = ""
                self._changed.notify_all()

    def mark_invalid(self, detail: str) -> None:
        with self._lock:
            self.status = "invalid"
            self.detail = detail
            self.checked_at = time.time()

    def wait_for_change(self, version: int, stop: threading.Event, poll: float = 2.0) -> bool:
        """Bloquea hasta que haya una key nueva o se revalide la actual (True) o se pida parar (False)."""
        while not stop.is_set():
            with self._lock:
                self._reload(force=True)
                if self._key and (self.version != version or self.status == "valid"):
                    return True
                self._changed.wait(timeout=poll)
        return False

    def public(self) -> dict:
        key = self.get()
        age_h = (time.time() - self.loaded_at) / 3600 if self.loaded_at else None
        return {
            "configured": bool(key),
            "status": self.status,
            "detail": self.detail,
            "masked": f"RGAPI-…{key[-4:]}" if key else "",
            "loaded_at": self.loaded_at,
            "checked_at": self.checked_at,
            "age_hours": round(age_h, 2) if age_h is not None else None,
            "dev_key_lifetime_hours": DEV_KEY_LIFETIME_H,
            "rechecking": self.rechecking,
            "next_check_in": max(0, round(self.next_check_at - time.time())) if self.rechecking and self.next_check_at else None,
        }
