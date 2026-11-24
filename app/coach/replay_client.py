"""Repeticiones reales (.rofl) desde el cliente de LoL abierto en este ordenador.

Flujo de «Ver en el cliente de LoL» (en segundo plano, con mensajes de estado):

1. Se pregunta al cliente por la repetición de la partida (``lol-replays``).
2. Si hace falta, se descarga (Riot limita a 50 descargas al día: solo se baja
   la que pides) y se espera a que esté lista.
3. Se abre en el cliente.
4. Con ``EnableReplayApi=1`` en game.cfg, se espera a que el juego cargue la
   repetición y se salta al segundo pedido (unos segundos antes del error).

Nunca se descarga nada por iniciativa propia ni se toca game.cfg sin que lo
pidas desde la web.
"""

from __future__ import annotations

import threading
import time
from itertools import takewhile

from app import lcu
from app.logbus import get_logger

log = get_logger("repeticiones")

NO_CLIENT = "no_client"
DOWNLOAD_TIMEOUT_S = 240
LOAD_TIMEOUT_S = 180
STATE_TEXT = {
    "incompatible": "Esa repetición es de otro parche: el cliente solo reproduce las del parche actual.",
    "missingOrExpired": "Riot ya no tiene esa repetición (caducada o no disponible).",
    "lost": "Riot ya no tiene esa repetición.",
    "unsupported": "El cliente no permite repeticiones de este modo de juego.",
    "error": "El cliente dio un error con esta repetición. Prueba a descargarla de nuevo desde su historial.",
}


def game_id(match_id: str) -> int:
    """``EUW1_7412345678`` -> ``7412345678``: los dígitos del final (un salto de línea al final no cuenta).

    Sin expresión regular: la de antes (``(\\d+)$`` con ``re.search``) volvía a probar desde cada posición. Mismo
    resultado: cualquier dígito que Python lea como número (``str.isdecimal``, como ``\\d``)."""
    text = (match_id or "").removesuffix("\n")
    digits = "".join(takewhile(str.isdecimal, reversed(text)))[::-1]
    if not digits:
        raise ValueError(f"Identificador de partida no válido: {match_id}")
    return int(digits)


def _downloading_text(progress) -> str:
    """Mensaje de «descargando» con el porcentaje cuando el cliente lo da."""
    return f"Descargando la repetición… {int(progress)} %" if isinstance(progress, (int, float)) else "Descargando la repetición…"


class ClientReplays:
    def __init__(self) -> None:
        self.job: dict | None = None
        self._lock = threading.Lock()

    # ----------------------------------------------------------------- estado
    def status(self, match_id: str) -> dict:
        gid = game_id(match_id)
        out = {"match_id": match_id, "game_id": gid, "client_open": False, "state": NO_CLIENT, "progress": None, "detail": None,
               "replay_api": None, "job": self.job}
        try:
            meta = lcu.replay_metadata(gid)
        except lcu.LCUError as exc:
            text = str(exc)
            if "No se encontró el cliente" in text or "no responde" in text:
                out["detail"] = "Abre el cliente de League of Legends en este ordenador para usar las repeticiones reales."
                return out
            out.update(client_open=True, state="error", detail=text)
            return out
        out["client_open"] = True
        out["state"] = meta.get("state") or "checking"
        progress = meta.get("downloadProgress")
        out["progress"] = progress if isinstance(progress, (int, float)) and 0 < progress < 100 else None
        out["detail"] = STATE_TEXT.get(out["state"])
        try:
            out["replay_api"] = lcu.replay_api_enabled()
        # solo informa de si está activada; no debe impedir ver el estado
        except Exception as exc:  # noqa: BLE001
            log.debug("No se pudo saber si la Replay API está activada: %s", exc)
            out["replay_api"] = None
        return out

    def _set(self, match_id: str, status: str, message: str) -> None:
        self.job = {"match_id": match_id, "status": status, "message": message, "at": time.time()}

    # ----------------------------------------------------------------- acciones
    def download(self, match_id: str) -> dict:
        gid = game_id(match_id)
        meta = lcu.replay_metadata(gid)
        state = meta.get("state")
        if state == "watch":
            return {"message": "Ya está descargada: pulsa «Ver en el cliente de LoL»."}
        if state not in ("download", "retryDownload"):
            raise lcu.LCUError(STATE_TEXT.get(state) or f"No se puede descargar ahora ({state})")
        lcu.replay_download(gid)
        return {"message": "Descargando la repetición en el cliente…"}

    def watch(self, match_id: str, seconds: float = 0.0) -> dict:
        gid = game_id(match_id)
        meta = lcu.replay_metadata(gid)  # falla rápido si el cliente no está abierto
        state = meta.get("state")
        if state in STATE_TEXT:
            raise lcu.LCUError(STATE_TEXT[state])
        with self._lock:
            if self.job and self.job.get("status") == "running":
                raise lcu.LCUError("Ya estoy abriendo otra repetición: espera a que termine.")
            self._set(match_id, "running", "Preparando la repetición…")
        threading.Thread(target=self._watch, args=(match_id, gid, max(0.0, float(seconds))), name="replay-watch", daemon=True).start()
        when = f" y salto al {int(seconds) // 60}:{int(seconds) % 60:02d}" if seconds else ""
        return {"message": f"Abriendo la repetición en el cliente de LoL{when}. El juego tarda unos segundos en cargarla."}

    def _watch(self, match_id: str, gid: int, seconds: float) -> None:
        try:
            self._wait_downloaded(match_id, gid)
            self._set(match_id, "running", "Abriendo la repetición en el juego…")
            lcu.replay_watch(gid)
            if seconds <= 0:
                self._set(match_id, "done", "Repetición abierta en el cliente de LoL.")
                return
            if lcu.replay_api_enabled() is False:
                self._set(match_id, "done", f"Repetición abierta. Ve a mano al {int(seconds) // 60}:{int(seconds) % 60:02d} (activa el salto automático para que lo haga yo).")
                return
            self._seek_when_loaded(match_id, seconds)
        except lcu.LCUError as exc:
            self._set(match_id, "error", str(exc))
            log.warning("Repetición %s: %s", match_id, exc)
        except Exception as exc:  # nunca dejar el trabajo «en curso» para siempre
            self._set(match_id, "error", f"Error inesperado: {exc}")
            log.exception("Fallo abriendo la repetición %s", match_id)

    def _wait_downloaded(self, match_id: str, gid: int) -> None:
        """Descarga la repetición si hace falta y espera a que se pueda ver; ``LCUError`` si no se puede o tarda demasiado."""
        deadline = time.monotonic() + DOWNLOAD_TIMEOUT_S
        started = False
        while True:
            meta = lcu.replay_metadata(gid)
            state = meta.get("state")
            if state == "watch":
                return
            if state in STATE_TEXT:
                raise lcu.LCUError(STATE_TEXT[state])
            if state in ("download", "retryDownload") and not started:
                self._set(match_id, "running", "Descargando la repetición (cuenta para el límite de 50 al día)…")
                lcu.replay_download(gid)
                started = True
            elif state == "downloading":
                self._set(match_id, "running", _downloading_text(meta.get("downloadProgress")))
            if time.monotonic() > deadline:
                raise lcu.LCUError("La descarga está tardando demasiado. Revisa el cliente de LoL.")
            time.sleep(2)

    def _seek_when_loaded(self, match_id: str, seconds: float) -> None:
        """Espera a que el juego cargue la repetición y salta al segundo pedido (o avisa de que hay que ir a mano)."""
        self._set(match_id, "running", "Esperando a que el juego cargue la repetición…")
        deadline = time.monotonic() + LOAD_TIMEOUT_S
        while time.monotonic() < deadline:
            playback = lcu.replay_playback()
            if playback and (playback.get("length") or 0) > 0:
                target = min(seconds, max(0.0, float(playback["length"]) - 1))
                lcu.replay_seek(target)
                self._set(match_id, "done", f"Listo: repetición en el {int(target) // 60}:{int(target) % 60:02d}.")
                log.info("Repetición %s abierta en el cliente en el segundo %d.", match_id, int(target))
                return
            time.sleep(2)
        self._set(match_id, "done", "Repetición abierta, pero el juego no respondió a tiempo para saltar al momento: búscalo a mano.")

    def seek(self, seconds: float) -> dict:
        playback = lcu.replay_playback()
        if not playback:
            raise lcu.LCUError("No hay ninguna repetición abierta en el juego (o falta EnableReplayApi=1).")
        lcu.replay_seek(seconds)
        return {"message": f"Repetición en el {int(seconds) // 60}:{int(seconds) % 60:02d}."}
