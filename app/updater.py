"""Actualización automática desde GitHub sin perder lo que estaba haciendo la app.

Cada ``CHECK_EVERY_S`` se mira (``git ls-remote``, sin bajar nada) si la rama de GitHub tiene commits que este ordenador
no tiene. Si traen código (algo fuera de ``data/``, ``docs/``…):

1. se espera a que dejen de llegar commits (``SETTLE_S``: desde el otro ordenador se suben varios seguidos) y a un buen
   momento: sin partida ni selección de campeones en curso (no se reinicia nunca mientras juegas);
2. se suben los datos como siempre (``autopush``: commit «datos», pull y push); el pull (merge) trae el código nuevo sin
   tocar los datos de este ordenador;
3. se comprueba que el código nuevo carga (``python -c "import app.main"``); si no, vuelta atrás al momento (commit
   «Vuelta atrás automática», que se sube con los datos y el error en ``data/registro/actualizacion.log``);
4. se apunta en ``data/.update.json`` de qué versión a cuál se pasa y se reinicia la app (``restart``). El supervisor de
   ``run.py`` la vuelve a lanzar y, si la versión nueva no llega a responder, vuelve a la anterior él solo.

Nada se pierde al reiniciar: el recolector sigue por el progreso guardado de cada jugador (``otp_progress``), los
vídeos por su carpeta de trabajo (``app.videos.collector``), la subida pendiente por ``data/.autopush.json`` y el cupo
de la API por ``data/.ratelimit.json``. Sin ``run.py`` (``uvicorn`` a mano) no hay quien relance la app: solo se avisa.
Con ``AUTO_UPDATE=0`` (o la subida automática apagada, ``AUTO_PUSH_GAMES=0``) no se hace nada.
"""

from __future__ import annotations

import json
import subprocess
import sys
import threading
import time
from pathlib import Path

from app import restart
from app.config import BASE_DIR, env
from app.logbus import get_logger

log = get_logger("updater")

CHECK_EVERY_S = 90
SETTLE_S = 180
RETRY_S = 15 * 60  # tras un fallo (sin red, conflicto…), no se reintenta antes
IMPORT_TIMEOUT_S = 180
# Comprobación del código nuevo antes de reiniciar: que todos los ficheros compilen y que carguen la app y lo que usa
# run.py (en otro proceso: este sigue con el código de antes).
CHECK_CODE = (
    "import compileall, sys\n"
    "if not compileall.compile_dir('app', quiet=1) or not compileall.compile_file('run.py', quiet=1):\n"
    "    sys.exit(1)\n"
    "import app.main, app.restart, app.updater, app.autopush\n"
)
UPDATE_FILE = ".update.json"  # en data/ (ignorado por git); lo lee también run.py
# Lo que no necesita reiniciar la app (llega con la siguiente subida de datos): datos, documentación y tests.
NO_RESTART = ("data/", "docs/", "macro/", "tests/", ".problems/", ".claude/", ".vscode/")
NO_RESTART_FILES = ("README.md", ".gitignore")
REQUIREMENTS = ("requirements.txt", "requirements-videos.txt")


def enabled() -> bool:
    from app import autopush

    return env("AUTO_UPDATE") not in ("0", "false", "no") and autopush.every() > 0


def needs_restart(paths: list[str]) -> bool:
    """¿Alguno de los ficheros que cambian es código de la app?"""
    return any(p and not p.startswith(NO_RESTART) and p not in NO_RESTART_FILES for p in paths)


def _names(out: str) -> list[str]:
    """Rutas de ``git … --name-only -z``: sin las comillas y los códigos con los que git escribe las rutas con letras no
    inglesas (los nombres coreanos o vietnamitas de los jugadores): con ellas, ``data/…`` parecía código y la app se
    reiniciaba en cada subida de datos."""
    return [p for p in out.split("\0") if p]


def _git(repo: Path, *args: str, timeout: int = 120) -> subprocess.CompletedProcess:
    from app.autopush import _git as git  # mismo git que la subida (sin ventanas en Windows, sin pedir contraseña)

    return git(repo, *args, check=False, timeout=timeout)


class Updater:
    """El hilo que vigila GitHub y aplica el código nuevo (uno por app)."""

    _instance: Updater | None = None
    _instance_lock = threading.Lock()

    def __init__(self, svc, repo: Path = BASE_DIR) -> None:
        self.svc = svc
        self.repo = Path(repo)
        self.data_dir = Path(svc.settings.data_dir)
        self.thread: threading.Thread | None = None
        self.seen: tuple[str, float] | None = None  # (commit remoto nuevo, desde cuándo se ve)
        self.failed: tuple[str, float] | None = None  # (commit remoto que no se pudo aplicar, cuándo): no antes de RETRY_S
        self.loaded: str | None = None  # el commit del código que cargó esta app al arrancar
        self.wake = threading.Event()  # una subida de datos acaba de hacer pull: mirar ya, sin esperar a la vuelta
        self.state: dict = {"status": "idle", "detail": None, "remote": None, "at": None}

    @classmethod
    def of(cls, svc) -> Updater | None:
        if getattr(getattr(svc, "settings", None), "data_dir", None) is None:
            return None
        with cls._instance_lock:
            if cls._instance is None or cls._instance.svc is not svc:
                cls._instance = cls(svc)
            return cls._instance

    # ------------------------------------------------------------------ estado
    def _set(self, status: str, detail: str | None = None, **extra) -> None:
        self.state.update(status=status, detail=detail, at=time.time(), **extra)

    def public(self) -> dict:
        return {**self.state, "enabled": enabled(), "restart": restart.available(), "last": self.last_update()}

    def last_update(self) -> dict | None:
        try:
            info = json.loads((self.data_dir / UPDATE_FILE).read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None
        return info if isinstance(info, dict) else None

    # ------------------------------------------------------------------ bucle
    def start(self) -> None:
        if not enabled():
            return
        info = self.last_update() or {}
        if info.get("status") in ("installed", "ok") and time.time() - float(info.get("at") or 0) < 600:
            log.info("Actualizado a la versión %s de GitHub.", str(info.get("to") or "?")[:9])
        self.loaded = self._head()
        self.thread = threading.Thread(target=self._loop, name="updater", daemon=True)
        self.thread.start()

    def poke(self) -> None:
        """Mirar ya (lo llama la subida de datos tras su pull: puede haber traído código nuevo)."""
        self.wake.set()

    def _loop(self) -> None:
        stop = self.svc.shutdown_event
        while not stop.is_set():
            self.wake.wait(CHECK_EVERY_S)
            self.wake.clear()
            if stop.is_set():
                return
            try:
                self._confirm_update()
                if self.tick() == "restarting":
                    return  # la app se está cerrando: otra vuelta (el aviso de la subida de datos) pedía otro reinicio
            except Exception:  # vigilar GitHub no puede tumbar la app
                log.exception("Actualización automática: fallo inesperado")

    def _confirm_update(self) -> None:
        """La versión instalada lleva un rato funcionando: queda dada por buena (si algo falla después, no es por ella y
        el supervisor ya no vuelve atrás). Lo marca también el supervisor de run.py en cuanto la app responde. Si el
        supervisor tuvo que volver atrás, se sube ya (el commit de la vuelta atrás y el error) para verlo desde GitHub."""
        info = self.last_update()
        if not info:
            return
        if info.get("status") == "installed":
            self._write_update({**info, "status": "ok", "ok_at": time.time()})
        elif info.get("status") == "rolled_back" and not info.get("reported"):
            from app import autopush

            auto = autopush.AutoPush.of(self.svc)
            if auto is not None and auto.state.get("git_ok") and auto.start():
                self._write_update({**info, "reported": time.time()})

    def tick(self) -> str:
        """Una vuelta: ¿hay código nuevo en GitHub y es buen momento para aplicarlo? Devuelve qué pasó."""
        from app import autopush

        if self.state["status"] == "restarting":
            return "restarting"  # ya pedido: la app se está cerrando

        auto = autopush.AutoPush.of(self.svc)
        if auto is None or not enabled() or auto.state.get("git_ok") is False:
            return "off"
        auto.push_if_stale()
        head = self._head()
        remote = self._remote_head()
        if not head or not remote:
            return "offline"
        if remote == head or self._contains(head, remote):
            self.seen = None
            return self._loaded_changed(head)
        return self._new_remote(head, remote)

    def _loaded_changed(self, head: str) -> str:
        """Lo de GitHub ya está en este ordenador, pero el código en disco puede no ser el que cargó la app: lo trae el
        pull de cualquier subida de datos (o un pull a mano). Entonces se reinicia igual que con una versión nueva."""
        if not self.loaded or head == self.loaded:
            if self.state["status"] not in ("idle", "failed"):
                self._set("idle")
            return "same"
        paths = self._diff(self.loaded, head)
        if not needs_restart(paths):
            self.loaded = head  # solo datos o documentación: nada que recargar
            return "same"
        return self._gate(head, time.time()) or self._install(self.loaded, head, head, paths)

    def _new_remote(self, head: str, remote: str) -> str:
        """Hay commits nuevos en GitHub: esperar a que dejen de llegar, ver si son código y aplicarlos."""
        now = time.time()
        if self.seen is None or self.seen[0] != remote:
            self.seen = (remote, now)
            self._fetch()
            self._set("settling", "hay commits nuevos en GitHub: espero a que dejen de llegar", remote=remote)
            return "seen"
        if now - self.seen[1] < SETTLE_S:
            return "settling"
        paths = self._changed(head)
        if not needs_restart(paths):
            self._set("idle", "lo nuevo de GitHub no es código: llega con la siguiente subida de datos", remote=remote)
            return "no_code"
        return self._gate(remote, now) or self.apply(head, remote, paths)

    def _gate(self, key: str, now: float) -> str | None:
        """Por qué aún no se aplica una versión (``backoff``, ``manual`` o ``waiting``); ``None`` si ya se puede."""
        if self.failed is not None and self.failed[0] == key and now - self.failed[1] < RETRY_S:
            return "backoff"  # la misma que acaba de fallar; una nueva (un arreglo) se prueba ya
        if not restart.available():
            self._set("manual", "hay una versión nueva: cierra y vuelve a abrir la app (run.py) para usarla", remote=key)
            return "manual"
        busy = self._busy()
        if busy:
            self._set("waiting", busy, remote=key)
            return "waiting"
        return None

    # ------------------------------------------------------------------ git
    def _head(self) -> str | None:
        out = _git(self.repo, "rev-parse", "HEAD")
        return out.stdout.strip() if out.returncode == 0 else None

    def _upstream(self) -> tuple[str, str]:
        up = _git(self.repo, "rev-parse", "--abbrev-ref", "--symbolic-full-name", "@{u}").stdout.strip()
        if "/" in up:
            remote, _, branch = up.partition("/")
            return remote, branch
        return "origin", _git(self.repo, "rev-parse", "--abbrev-ref", "HEAD").stdout.strip() or "main"

    def _remote_head(self) -> str | None:
        remote, branch = self._upstream()
        out = _git(self.repo, "ls-remote", remote, f"refs/heads/{branch}", timeout=60)
        parts = out.stdout.split()
        return parts[0] if out.returncode == 0 and parts else None

    def _contains(self, head: str, sha: str) -> bool:
        """¿``sha`` ya está en este ordenador (es antecesor de ``head``)?"""
        return _git(self.repo, "merge-base", "--is-ancestor", sha, head).returncode == 0

    def _fetch(self) -> None:
        remote, branch = self._upstream()
        _git(self.repo, "fetch", "-q", remote, branch, timeout=600)

    def _diff(self, a: str, b: str) -> list[str]:
        return _names(_git(self.repo, "diff", "--name-only", "-z", a, b).stdout)

    def _changed(self, head: str) -> list[str]:
        """Ficheros que cambian en GitHub desde lo último común (lo de este ordenador no cuenta)."""
        remote, branch = self._upstream()
        self._fetch()
        return _names(_git(self.repo, "diff", "--name-only", "-z", f"{head}...{remote}/{branch}").stdout)

    # ------------------------------------------------------------------ cuándo
    def _busy(self) -> str | None:
        """Por qué no es buen momento para reiniciar (``None`` si lo es)."""
        tracker = getattr(self.svc, "tracker", None)
        if tracker is not None and (getattr(tracker, "state", {}) or {}).get("in_game"):
            return "versión nueva lista: se aplica al acabar tu partida"
        draft = getattr(self.svc, "draft_live", None)
        if draft is not None and (getattr(draft, "state", {}) or {}).get("active"):
            return "versión nueva lista: se aplica al acabar la selección de campeones"
        from app import autopush

        auto = autopush.AutoPush.of(self.svc)
        if auto is not None and auto.busy:
            return "versión nueva lista: se aplica al acabar la subida de datos en curso"
        return None

    # ------------------------------------------------------------------ aplicar
    def apply(self, head: str, remote: str, paths: list[str]) -> str:
        """Baja el código nuevo (con la subida de datos), comprueba que carga y reinicia la app."""
        from app import autopush

        self._set("applying", "subiendo los datos y bajando la versión nueva", remote=remote)
        log.info("Actualización automática: versión nueva en GitHub (%s); subo los datos y la bajo.", remote[:9])
        auto = autopush.AutoPush.of(self.svc)
        auto.run_now()
        new_head = self._head() or head
        if not self._contains(new_head, remote):
            autopush.pull_code(self.repo, self.data_dir, auto.writer_locks())
            new_head = self._head() or head
        if not self._contains(new_head, remote):
            return self._failed(remote, f"no se pudo bajar la versión nueva: {auto.state.get('last_error') or 'git pull falló'}")
        return self._install(self.loaded or head, new_head, remote, paths)

    def _install(self, old: str, new: str, key: str, paths: list[str]) -> str:
        """El código ``new`` ya está en disco (antes corría ``old``): si carga, se reinicia la app con él; si no, vuelta
        atrás."""
        problem = None if any(p in REQUIREMENTS for p in paths) else self._import_error()
        if problem:
            return self._revert(old, new, key, problem)
        self._write_update({"status": "installed", "from": old, "to": new, "remote": key, "at": time.time()})
        _append_log(self.data_dir, f"Versión {new[:9]} instalada (antes {old[:9]}); reinicio la app.")
        self._set("restarting", "versión nueva instalada: reiniciando la app", remote=key)
        restart.request(f"versión nueva de GitHub ({new[:9]})")
        return "restarting"

    def _failed(self, remote: str, why: str) -> str:
        self.failed = (remote, time.time())
        log.warning("Actualización automática: %s. Se reintenta en %d min.", why, RETRY_S // 60)
        self._set("failed", why)
        return "failed"

    def _import_error(self) -> str | None:
        """¿Carga el código nuevo? (en otro proceso: este sigue con el de antes). El error, si no carga."""
        flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        try:
            out = subprocess.run(
                [sys.executable, "-c", CHECK_CODE], cwd=self.repo, capture_output=True, text=True, encoding="utf-8",
                errors="replace", timeout=IMPORT_TIMEOUT_S, creationflags=flags, check=False,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            return f"no se pudo comprobar el código nuevo: {exc}"
        if out.returncode == 0:
            return None
        lines = [x for x in (out.stderr or out.stdout).strip().splitlines() if x.strip()]
        return " | ".join(lines[-3:])[:400] or f"código de salida {out.returncode}"

    def _revert(self, head: str, new_head: str, remote: str, problem: str) -> str:
        """El código nuevo no carga: vuelve al de antes con un commit que se sube con los datos (y el error)."""
        restored = _git(self.repo, "restore", f"--source={head}", "--staged", "--worktree", "--", ".", ":(exclude)data")
        if restored.returncode == 0:
            _git(self.repo, "commit", "-q", "-m", f"Vuelta atrás automática: la versión {new_head[:9]} no carga en este ordenador\n\n{problem}")
        _append_log(self.data_dir, f"La versión {new_head[:9]} no carga ({problem}); vuelvo a {head[:9]}.")
        self._write_update({"status": "rolled_back", "from": head, "to": new_head, "error": problem, "at": time.time(), "reported": time.time()})
        from app import autopush

        auto = autopush.AutoPush.of(self.svc)
        if auto is not None:
            auto.start()  # la vuelta atrás y el error, a GitHub
        return self._failed(remote, f"la versión nueva no carga ({problem[:200]}); se ha vuelto a la anterior")

    def _write_update(self, info: dict) -> None:
        path = self.data_dir / UPDATE_FILE
        try:
            tmp = path.with_name(path.name + ".tmp")
            tmp.write_text(json.dumps(info, ensure_ascii=False, indent=1), encoding="utf-8")
            tmp.replace(path)
        except OSError:
            log.warning("No se pudo guardar %s", path, exc_info=True)


def _append_log(data_dir: Path, text: str) -> None:
    """Una línea en ``data/registro/actualizacion.log`` (se sube con los datos: así se ve desde el otro ordenador)."""
    path = Path(data_dir) / "registro" / "actualizacion.log"
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "a", encoding="utf-8") as fh:
            fh.write(f"{time.strftime('%Y-%m-%d %H:%M:%S')} {text}\n")
    except OSError:
        log.debug("No se pudo escribir %s", path, exc_info=True)


def start(svc) -> None:
    """Al arrancar la app (después de comprobar git en ``autopush``)."""
    updater = Updater.of(svc)
    if updater is not None:
        updater.start()
