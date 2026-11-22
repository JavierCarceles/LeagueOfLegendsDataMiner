"""Repeticiones (.rofl) guardadas en este ordenador.

Al arrancar la app se busca la carpeta de repeticiones del cliente de LoL y se recuerda **por sistema operativo**
(``meta`` ``replays_dir:<windows|darwin|linux>``). Orden:

1. La carpeta ya guardada (si sigue existiendo).
2. La que dice el cliente de LoL si está abierto (``/lol-replays/v1/rofls/path``: la de sus ajustes).
3. Las rutas por defecto (``Documentos/League of Legends/Replays``, también con Documentos en OneDrive o movido).
4. Si no aparece, se busca por todo el disco en segundo plano (primero tu carpeta de usuario, luego cada unidad)
   hasta encontrar una carpeta con ``.rofl``.

Encontrada, se indexa cada ``.rofl`` (tabla ``local_replays``) y se vuelve a mirar cada pocos minutos por si
descargas más. De cada fichero se lee solo la **cabecera**: parche, duración y las estadísticas finales de los 10
jugadores. El resto (los paquetes de la partida) va cifrado y cambia cada parche: solo el cliente del juego lo
reproduce, así que no se intenta decodificar.
"""

from __future__ import annotations

import json
import os
import platform
import re
import string
import struct
import threading
import time
from collections import deque
from pathlib import Path

from app.coach.store import pack, unpack
from app.data.db import now_s
from app.logbus import get_logger

log = get_logger("rofl")

ROFL_SUFFIX = ".rofl"
RESCAN_S = 180  # cada cuánto se mira si hay repeticiones nuevas en la carpeta
NOT_FOUND_RETRY_S = 12 * 3600  # una búsqueda completa sin resultado no se repite antes de 12 h (salvo que la pidas)
HEADER_READ = 1 << 20  # bytes del final del fichero donde buscar la cabecera (formato nuevo)
# Carpetas que no se recorren en la búsqueda por todo el disco (sistema, papeleras, cachés enormes).
SKIP_NAMES = {
    "windows", "$recycle.bin", "system volume information", "$windows.~bt", "$windows.~ws", "recovery",
    "programdata", "appdata", "node_modules", ".git", ".cache", ".venv", "venv", "__pycache__", "library", "system",
    "proc", "sys", "dev", "private", "volumes", "cores", "snap", "tmp", "var", "usr", "bin", "sbin", "opt", "etc",
    "boot", "lib", "lib64", "run", "msocache", "perflogs", "intel", "amd", "nvidia", "steamapps",
}


def os_key() -> str:
    return platform.system().lower() or "unknown"


def match_id_of(path: Path) -> str | None:
    """``EUW1-7412345678.rofl`` → ``EUW1_7412345678``."""
    m = re.fullmatch(r"([A-Za-z]+\d*)-(\d+)", path.stem)
    return f"{m.group(1).upper()}_{m.group(2)}" if m else None


def has_rofl(folder: Path) -> bool:
    try:
        with os.scandir(folder) as it:
            return any(e.is_file() and e.name.lower().endswith(ROFL_SUFFIX) for e in it)
    except OSError:
        return False


# ------------------------------------------------------------------ dónde buscar
def _documents_dirs() -> list[Path]:
    home = Path.home()
    out = [home / "Documents", home / "Documentos"]
    if os.name == "nt":
        for env in ("OneDrive", "OneDriveConsumer", "OneDriveCommercial"):
            if os.environ.get(env):
                out += [Path(os.environ[env]) / "Documents", Path(os.environ[env]) / "Documentos"]
        try:  # Documentos movido a otra carpeta o unidad (Propiedades → Ubicación)
            import winreg

            key = winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Software\Microsoft\Windows\CurrentVersion\Explorer\User Shell Folders")
            value, _ = winreg.QueryValueEx(key, "Personal")
            out.insert(0, Path(os.path.expandvars(value)))
        except OSError:
            pass
    return list(dict.fromkeys(out))


def default_dirs() -> list[Path]:
    """Rutas donde el cliente guarda las repeticiones por defecto en este sistema."""
    out = [d / "League of Legends" / "Replays" for d in _documents_dirs()]
    if os_key() == "linux":  # Wine / Lutris
        home = Path.home()
        for prefix in (home / "Games" / "league-of-legends", home / ".wine"):
            users = prefix / "drive_c" / "users"
            if users.is_dir():
                out += [u / "Documents" / "League of Legends" / "Replays" for u in users.iterdir() if u.is_dir()]
    return out


def client_dir() -> Path | None:
    """La carpeta que tiene configurada el cliente de LoL (solo con el cliente abierto)."""
    from app import lcu

    for path in ("/lol-replays/v1/rofls/path", "/lol-replays/v1/rofls/path/default"):
        try:
            value = lcu._get(path, timeout=2.0)
        except Exception:  # noqa: BLE001, S112  (cliente cerrado o sin esa ruta: se sigue buscando)
            continue
        if isinstance(value, str) and value.strip():
            return Path(value.strip())
    return None


def scan_roots() -> list[Path]:
    """Primero tu carpeta de usuario (lo más probable) y después cada unidad o la raíz."""
    roots = [Path.home()]
    if os.name == "nt":
        roots += [Path(f"{d}:\\") for d in string.ascii_uppercase if os.path.exists(f"{d}:\\")]
    else:
        roots += [Path("/Volumes")] if Path("/Volumes").is_dir() else []
        roots.append(Path("/"))
    return list(dict.fromkeys(roots))


def full_scan(roots: list[Path], stop: threading.Event | None = None, progress=None) -> Path | None:
    """Recorre las carpetas (en anchura, sin seguir enlaces) hasta dar con una que tenga ``.rofl``.

    Entre varias, gana la primera llamada ``Replays``; si no hay ninguna así, la primera con ``.rofl``.
    """
    seen: set[str] = set()
    fallback: Path | None = None
    visited = 0
    for root in roots:
        queue = deque([root])
        while queue:
            if stop is not None and stop.is_set():
                return fallback
            folder = queue.popleft()
            key = os.path.normcase(str(folder))
            if key in seen:
                continue
            seen.add(key)
            visited += 1
            if progress and visited % 500 == 0:
                progress(visited, str(folder))
            found_here = False
            try:
                with os.scandir(folder) as it:
                    for entry in it:
                        try:
                            if entry.is_symlink():
                                continue
                            if entry.is_dir(follow_symlinks=False):
                                if entry.name.lower() not in SKIP_NAMES and not entry.name.startswith("."):
                                    queue.append(Path(entry.path))
                            elif not found_here and entry.name.lower().endswith(ROFL_SUFFIX):
                                found_here = True
                        except OSError:
                            continue
            except OSError:
                continue
            if found_here:
                if folder.name.lower() == "replays":
                    return folder
                fallback = fallback or folder
        if fallback is not None:
            return fallback
    return fallback


# ------------------------------------------------------------------ cabecera del .rofl
def parse_rofl(path: Path) -> dict | None:
    """Cabecera de una repetición: ``{game_length, game_version, players: [...]}`` o ``None`` si no se entiende.

    Formato clásico: ``RIOT`` + firma de 256 bytes + tamaños; la cabecera JSON está en ``metadata_offset``. En el formato
    nuevo la cabecera JSON va al final del fichero; se busca ahí.
    """
    try:
        with open(path, "rb") as fh:
            head = fh.read(288)
            if not head.startswith(b"RIOT"):
                return None
            meta = None
            if len(head) >= 288:
                try:
                    _hl, _fl, meta_off, meta_len = struct.unpack_from("<HIII", head, 262)
                    if 0 < meta_len < 50_000_000:
                        fh.seek(meta_off)
                        meta = json.loads(fh.read(meta_len).decode("utf-8"))
                except (struct.error, ValueError, UnicodeDecodeError, OSError):
                    meta = None
            if not isinstance(meta, dict) or "gameLength" not in meta:
                size = fh.seek(0, os.SEEK_END)
                fh.seek(max(0, size - HEADER_READ))
                tail = fh.read()
                start = tail.rfind(b'{"gameLength"')
                if start < 0:
                    return None
                end = tail.rfind(b"}") + 1
                meta = json.loads(tail[start:end].decode("utf-8", errors="replace"))
    except (OSError, ValueError):
        return None
    try:
        stats = json.loads(meta.get("statsJson") or "[]")
    except ValueError:
        stats = []
    return {
        "game_length": int(meta.get("gameLength") or 0),  # ms
        "game_version": meta.get("gameVersion"),
        "players": stats if isinstance(stats, list) else [],
    }


# ------------------------------------------------------------------ servicio
class LocalReplays:
    """Encuentra la carpeta (una vez por sistema operativo), la indexa al arrancar y la vigila mientras la app está abierta."""

    def __init__(self, svc) -> None:
        self.svc = svc
        self.stop_event = threading.Event()
        self.wake = threading.Event()
        self.thread: threading.Thread | None = None
        self.force_scan = False
        self.state: dict = {"status": "starting", "os": os_key(), "path": None, "found_by": None, "files": 0}

    @property
    def meta_key(self) -> str:
        return f"replays_dir:{os_key()}"

    def saved(self) -> dict | None:
        raw = self.svc.db.meta_get(self.meta_key)
        try:
            return json.loads(raw) if raw else None
        except ValueError:
            return None

    def _remember(self, path: Path | None, found_by: str, **extra) -> None:
        value = {"path": str(path) if path else None, "found_by": found_by, "at": now_s(), **extra}
        self.svc.db.meta_set(self.meta_key, json.dumps(value))

    def start(self) -> None:
        if self.thread is not None and self.thread.is_alive():
            return
        self.stop_event.clear()
        self.thread = threading.Thread(target=self._run, name="rofl", daemon=True)
        self.thread.start()

    def stop(self) -> None:
        self.stop_event.set()
        self.wake.set()

    def rescan(self, path: str | None = None) -> dict:
        """Busca de nuevo (o usa la carpeta que indiques) y reindexa."""
        if path:
            folder = Path(path).expanduser()
            if not folder.is_dir():
                raise ValueError(f"No existe la carpeta: {folder}")
            self._remember(folder, "manual")
        else:
            self.svc.db.meta_set(self.meta_key, "")
            self.force_scan = True
        self.start()
        self.wake.set()
        return self.state

    # ---------------------------------------------------------- bucle
    def _run(self) -> None:
        while not self.stop_event.is_set():
            try:
                folder = self.locate()
                if folder is not None:
                    self.index(folder)
                    self.state["status"] = "watching"
            except Exception as exc:  # que no se caiga el hilo; se reintenta
                log.exception("Error revisando las repeticiones locales")
                self.state.update(status="error", error=str(exc))
            self.wake.wait(RESCAN_S)
            self.wake.clear()

    def locate(self) -> Path | None:
        """La carpeta de repeticiones de este sistema (la guardada, la del cliente, la de por defecto o buscándola)."""
        saved = self.saved() or {}
        if saved.get("path") and Path(saved["path"]).is_dir():
            folder = Path(saved["path"])
            client = client_dir() if saved.get("found_by") != "manual" else None
            if client is not None and client.is_dir() and client != folder:
                self._remember(client, "client")  # cambiaste la carpeta en los ajustes del cliente
                folder = client
            self.state.update(path=str(folder), found_by=(self.saved() or saved).get("found_by"))
            return folder
        client = client_dir()
        if client is not None and client.is_dir():
            self._remember(client, "client")
            self.state.update(path=str(client), found_by="client")
            return client
        defaults = [d for d in default_dirs() if d.is_dir()]
        best = next((d for d in defaults if has_rofl(d)), None)
        if best is not None:
            self._remember(best, "default")
            self.state.update(path=str(best), found_by="default")
            return best
        # La carpeta por defecto existe pero vacía: puede que las tengas en otra; si no aparecen, se usa esa.
        empty_default = defaults[0] if defaults else None
        if not self.force_scan and saved.get("not_found_at") and now_s() - int(saved["not_found_at"]) < NOT_FOUND_RETRY_S:
            if empty_default is not None:
                self.state.update(path=str(empty_default), found_by="default")
                return empty_default
            self.state.update(status="not_found", path=None)
            return None
        self.force_scan = False
        self.state.update(status="scanning", path=None, scanned=0, at=None)
        started = time.time()
        found = full_scan(scan_roots(), self.stop_event, lambda n, where: self.state.update(scanned=n, at=where))
        if found is None:
            if self.stop_event.is_set():
                return None
            log.info("No se encontró ninguna repetición (.rofl) en este ordenador (%.0f s buscando).", time.time() - started)
            # Sin ninguna repetición en el disco: se usa la carpeta por defecto si existe (ahí caerán las que bajes).
            self._remember(empty_default, "default" if empty_default else "scan", not_found_at=now_s())
            if empty_default is not None:
                self.state.update(path=str(empty_default), found_by="default")
                return empty_default
            self.state.update(status="not_found", path=None)
            return None
        self._remember(found, "scan")
        self.state.update(path=str(found), found_by="scan")
        log.info("Carpeta de repeticiones encontrada: %s (%.0f s buscando).", found, time.time() - started)
        return found

    def index(self, folder: Path) -> int:
        """Añade/actualiza las repeticiones nuevas o cambiadas y quita las que ya no están. Devuelve cuántas hay."""
        db = self.svc.db
        known = {r["path"]: (r["size"], r["mtime"]) for r in db.query("SELECT path, size, mtime FROM local_replays")}
        present: set[str] = set()
        added = 0
        try:
            entries = [e for e in os.scandir(folder) if e.is_file() and e.name.lower().endswith(ROFL_SUFFIX)]
        except OSError:
            entries = []
        for entry in entries:
            path = Path(entry.path)
            present.add(str(path))
            try:
                st = entry.stat()
            except OSError:
                continue
            if known.get(str(path)) == (st.st_size, int(st.st_mtime)):
                continue
            header = parse_rofl(path)
            with db.write() as conn:
                conn.execute(
                    "INSERT INTO local_replays (path, match_id, size, mtime, game_version, game_length, players, readable, indexed_at) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?) ON CONFLICT(path) DO UPDATE SET match_id = excluded.match_id, "
                    "size = excluded.size, mtime = excluded.mtime, game_version = excluded.game_version, "
                    "game_length = excluded.game_length, players = excluded.players, readable = excluded.readable, "
                    "indexed_at = excluded.indexed_at",
                    (
                        str(path), match_id_of(path), st.st_size, int(st.st_mtime),
                        (header or {}).get("game_version"), (header or {}).get("game_length"),
                        pack((header or {}).get("players")) if header else None, int(header is not None), now_s(),
                    ),
                )
            added += 1
        gone = [p for p in known if p not in present]  # borradas o de otra carpeta anterior
        if gone:
            with db.write() as conn:
                conn.executemany("DELETE FROM local_replays WHERE path = ?", [(p,) for p in gone])
        total = int(db.scalar("SELECT COUNT(*) FROM local_replays") or 0)
        self.state.update(files=total, indexed_at=now_s(), error=None)
        if added:
            log.info("Repeticiones locales: %d nuevas (%d en total) en %s.", added, total, folder)
        return total

    # ---------------------------------------------------------- consultas
    def summary(self, limit: int = 20) -> dict:
        rows = self.svc.db.query(
            "SELECT match_id, path, size, mtime, game_version, game_length, readable FROM local_replays ORDER BY mtime DESC LIMIT ?",
            (limit,),
        )
        ids = [r[0] for r in self.svc.db.query("SELECT match_id FROM local_replays WHERE match_id IS NOT NULL")]
        return {**self.state, "saved": self.saved(), "recent": [dict(r) for r in rows], "match_ids": ids}

    def players(self, match_id: str) -> list[dict] | None:
        row = self.svc.db.one("SELECT players FROM local_replays WHERE match_id = ?", (match_id,))
        return unpack(row["players"]) if row and row["players"] else None
