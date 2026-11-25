"""Cola de vídeos y datos recogidos (``data/videos``), compartidos por la web, el recolector y la línea de órdenes.

* ``_cola.json``: las fuentes añadidas (vídeo, lista, canal o búsqueda), cada vídeo con su estado y lo que se recogió,
  y si el recolector está en pausa.
* ``<id>.json``: lo recogido de cada vídeo (ver :mod:`app.videos`).

Escrituras atómicas: se escribe ``*.tmp`` y se renombra (los ``.tmp`` no entran en git), así una subida a GitHub a mitad
de escritura nunca se lleva un fichero cortado.
"""

from __future__ import annotations

import json
import threading
import time
import uuid
from pathlib import Path

QUEUE_FILE = "_cola.json"

PENDING = "pendiente"
WORKING = "procesando"
DONE = "hecho"
FAILED = "error"
SKIPPED = "descartado"
STATES = (PENDING, WORKING, DONE, FAILED, SKIPPED)
# Ritmo de la recogida: con los últimos vídeos acabados seguidos (sin un hueco de más de media hora entre uno y el
# siguiente: app cerrada o en pausa), cuántos salen por hora y cuánto le queda a la cola a ese ritmo.
PACE_SAMPLE = 10
PACE_MIN = 3
PACE_MAX_GAP_S = 1800
MAX_TRIES = 3  # después de 3 fallos un vídeo queda en «error» (se puede reintentar desde la web)
RETRY_DELAY_S = 20 * 60  # un vídeo que falló no se vuelve a intentar antes


def write_json(path: Path, data, indent: int | None = 1) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=indent), encoding="utf-8")
    for attempt in range(5):
        try:
            tmp.replace(path)
            return
        except PermissionError:  # Windows: alguien (git, antivirus) lo tiene abierto un instante
            if attempt == 4:
                raise
            time.sleep(0.3 * (attempt + 1))


def _now() -> float:
    return round(time.time(), 1)


class VideoStore:
    """Estado del recolector en disco. Todos los métodos son seguros entre hilos."""

    def __init__(self, root: Path) -> None:
        self.root = Path(root)
        self.lock = threading.RLock()
        self._cache: dict | None = None

    # ------------------------------------------------------------------ disco
    @property
    def queue_path(self) -> Path:
        return self.root / QUEUE_FILE

    def _data(self) -> dict:
        if self._cache is None:
            try:
                data = json.loads(self.queue_path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                data = {}
            if not isinstance(data, dict):
                data = {}
            data.setdefault("version", 1)
            data.setdefault("pausado", False)
            data.setdefault("fuentes", [])
            data.setdefault("videos", {})
            self._cache = data
        return self._cache

    def _flush(self) -> None:
        write_json(self.queue_path, self._data())

    def reload(self) -> None:
        """Vuelve a leer la cola del disco (p. ej. tras un ``git pull`` que la trajo de otro ordenador)."""
        with self.lock:
            self._cache = None

    # ------------------------------------------------------------------ pausa
    @property
    def paused(self) -> bool:
        with self.lock:
            return bool(self._data().get("pausado"))

    def set_paused(self, paused: bool) -> None:
        with self.lock:
            self._data()["pausado"] = bool(paused)
            self._flush()

    # ------------------------------------------------------------------ fuentes
    def add_source(self, kind: str, value: str, options: dict | None = None) -> dict:
        """Una fuente nueva (``video``, ``lista``, ``canal`` o ``busqueda``) que el recolector convierte en vídeos."""
        source = {
            "id": uuid.uuid4().hex[:10],
            "tipo": kind,
            "valor": value,
            "opciones": options or {},
            "estado": PENDING,
            "alta": _now(),
            "videos": 0,
            "nuevos": 0,
        }
        with self.lock:
            self._data()["fuentes"].append(source)
            self._flush()
        return dict(source)

    def next_source(self) -> dict | None:
        with self.lock:
            for source in self._data()["fuentes"]:
                if source.get("estado") == PENDING:
                    return dict(source)
        return None

    def finish_source(self, source_id: str, found: int = 0, added: int = 0, error: str | None = None) -> None:
        with self.lock:
            for source in self._data()["fuentes"]:
                if source.get("id") == source_id:
                    source.update(estado=FAILED if error else DONE, videos=found, nuevos=added, error=error, fin=_now())
            self._flush()

    def sources(self) -> list[dict]:
        with self.lock:
            return [dict(s) for s in reversed(self._data()["fuentes"])]

    # ------------------------------------------------------------------ vídeos
    def add_videos(self, entries: list[dict], source: dict | None = None) -> int:
        """Añade los vídeos que no estaban ya (``id`` de YouTube); devuelve cuántos son nuevos."""
        added = 0
        with self.lock:
            videos = self._data()["videos"]
            for entry in entries:
                vid = entry.get("id")
                if not vid or vid in videos:
                    continue
                videos[vid] = {
                    "id": vid,
                    "titulo": entry.get("titulo") or entry.get("title") or "",
                    "canal": entry.get("canal") or entry.get("channel") or entry.get("uploader") or "",
                    "duracion": entry.get("duracion") or entry.get("duration"),
                    "fuente": (source or {}).get("id"),
                    "estado": PENDING,
                    "intentos": 0,
                    "alta": _now(),
                }
                added += 1
            if added:
                self._flush()
        return added

    def next_video(self, now: float | None = None) -> dict | None:
        """El vídeo pendiente más antiguo (los que fallaron esperan ``RETRY_DELAY_S`` antes de reintentarse)."""
        now = time.time() if now is None else now
        with self.lock:
            ready = [
                v for v in self._data()["videos"].values()
                if v.get("estado") == PENDING and now - float(v.get("fallo") or 0) >= RETRY_DELAY_S
            ]
            if not ready:
                return None
            return dict(min(ready, key=lambda v: (v.get("intentos") or 0, v.get("alta") or 0)))

    def update(self, vid: str, **fields) -> None:
        with self.lock:
            video = self._data()["videos"].get(vid)
            if video is None:
                return
            video.update(fields)
            self._flush()

    def start(self, vid: str) -> None:
        self.update(vid, estado=WORKING, inicio=_now())

    def done(self, vid: str, summary: dict) -> None:
        self.update(vid, estado=DONE, fin=_now(), error=None, resumen=summary)

    def fail(self, vid: str, error: str, final: bool = False) -> None:
        """Un fallo: vuelve a la cola hasta ``MAX_TRIES`` (o se descarta si ``final``: privado, borrado…)."""
        with self.lock:
            video = self._data()["videos"].get(vid)
            if video is None:
                return
            tries = int(video.get("intentos") or 0) + 1
            if final:
                state = SKIPPED
            else:
                state = FAILED if tries >= MAX_TRIES else PENDING
            video.update(estado=state, intentos=tries, error=error[:500], fallo=_now())
            self._flush()

    def requeue(self, vids: list[str]) -> None:
        """Vuelven a la cola para rehacerlos (lo recogido se sobrescribe al terminar)."""
        with self.lock:
            videos = self._data()["videos"]
            for vid in vids:
                if vid in videos:
                    videos[vid].update(estado=PENDING, intentos=0, error=None, fallo=None)
            self._flush()

    def retry(self, vid: str) -> bool:
        with self.lock:
            video = self._data()["videos"].get(vid)
            if video is None:
                return False
            video.update(estado=PENDING, intentos=0, error=None, fallo=None)
            self._flush()
            return True

    def remove(self, vid: str) -> bool:
        """Quita un vídeo de la cola (y lo recogido de él)."""
        with self.lock:
            removed = self._data()["videos"].pop(vid, None) is not None
            if removed:
                self._flush()
        if removed:
            self.result_path(vid).unlink(missing_ok=True)
        return removed

    def reset_working(self) -> int:
        """Al arrancar: lo que se quedó «procesando» (app cerrada a mitad) vuelve a la cola."""
        with self.lock:
            stuck = [v for v in self._data()["videos"].values() if v.get("estado") == WORKING]
            for video in stuck:
                video["estado"] = PENDING
            if stuck:
                self._flush()
            return len(stuck)

    def counts(self) -> dict:
        with self.lock:
            out = dict.fromkeys(STATES, 0)
            for video in self._data()["videos"].values():
                state = video.get("estado") or PENDING
                out[state] = out.get(state, 0) + 1
            out["total"] = len(self._data()["videos"])
            out["fuentes_pendientes"] = sum(1 for s in self._data()["fuentes"] if s.get("estado") == PENDING)
            return out

    def pace(self) -> dict | None:
        """``{"por_hora", "horas", "muestra"}`` con los últimos vídeos recogidos seguidos; ``None`` sin muestra."""
        with self.lock:
            videos = [dict(v) for v in self._data()["videos"].values()]
        done = sorted((v for v in videos if v.get("estado") == DONE and v.get("inicio") and v.get("fin")), key=lambda v: v["fin"])
        if not done:
            return None
        chain = [done[-1]]
        for v in reversed(done[:-1]):
            if len(chain) >= PACE_SAMPLE or chain[-1]["inicio"] - v["fin"] > PACE_MAX_GAP_S:
                break
            chain.append(v)
        span = chain[0]["fin"] - chain[-1]["inicio"]
        if len(chain) < PACE_MIN or span <= 0:
            return None
        per_hour = len(chain) * 3600 / span
        pending = sum(1 for v in videos if (v.get("estado") or PENDING) in (PENDING, WORKING))
        return {"por_hora": round(per_hour, 1), "horas": round(pending / per_hour, 1), "muestra": len(chain)}

    def videos(self, state: str | None = None, limit: int = 300) -> list[dict]:
        """Los vídeos más recientes primero (los que se están procesando, arriba)."""
        with self.lock:
            items = [dict(v) for v in self._data()["videos"].values() if state in (None, "", v.get("estado"))]
        items.sort(key=lambda v: (v.get("estado") != WORKING, -(v.get("fin") or v.get("alta") or 0)))
        return items[: max(1, limit)]

    def get(self, vid: str) -> dict | None:
        with self.lock:
            video = self._data()["videos"].get(vid)
            return dict(video) if video else None

    # ------------------------------------------------------------------ datos recogidos
    def result_path(self, vid: str) -> Path:
        return self.root / f"{vid}.json"

    def save_result(self, vid: str, data: dict) -> Path:
        path = self.result_path(vid)
        write_json(path, data, indent=None)
        return path

    def load_result(self, vid: str) -> dict | None:
        try:
            data = json.loads(self.result_path(vid).read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None
        return data if isinstance(data, dict) else None
