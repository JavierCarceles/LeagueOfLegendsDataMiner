"""Subida automática de los datos a GitHub: cada N partidas nuevas del recolector, un commit «datos» y push.

El recolector escribe cada partida en ``data/otps`` o ``data/partidas_campeon``. Al llegar a ``AUTO_PUSH_GAMES``
partidas nuevas (500 por defecto; ``0`` lo desactiva) se hace, en segundo plano y sin tocar nada fuera de ``data/``:

1. ``git add -- data`` y ``git commit -m datos -- data`` (solo la carpeta de datos: si tienes código a medias, no entra);
2. ``git pull --rebase --autostash`` de la rama actual (trabajas desde dos ordenadores: el remoto puede ir por delante).
   Si tienes código modificado sin commit fuera de ``data/``, no se hace pull (podría mezclar tus cambios con los del
   remoto): solo se intenta el push y, si el remoto va por delante, se avisa en «Datos y API» y se reintenta luego;
3. ``git push`` normal. **Nunca se fuerza**: si el rebase choca, se aborta, el commit queda en local y se reintenta
   más tarde.

Mientras juegas se espera a que acabe la partida. La cuenta de partidas pendientes se guarda en
``data/.autopush.json`` (ignorado por git), así que sobrevive a cerrar la app.

También se puede lanzar a mano: ``python -m app.autopush`` (sube ya lo que haya, sin esperar a las 500).
"""

from __future__ import annotations

import json
import re
import os
import subprocess
import sys
import threading
import time
from pathlib import Path

from app.config import BASE_DIR, env
from app.logbus import get_logger

log = get_logger("autopush")

DEFAULT_EVERY = 500
STATE_FILE = ".autopush.json"
COMMIT_MESSAGE = "datos"
GIT_TIMEOUT_S = 600  # un push grande con una conexión lenta
RETRY_AFTER_S = 15 * 60  # tras un fallo (sin red, rebase con conflictos…), no se reintenta antes
IN_GAME_WAIT_S = 30


def every() -> int:
    """Partidas nuevas entre una subida y la siguiente (``AUTO_PUSH_GAMES``; 0 = desactivado)."""
    raw = env("AUTO_PUSH_GAMES")
    try:
        return max(0, int(raw)) if raw not in (None, "") else DEFAULT_EVERY
    except ValueError:
        return DEFAULT_EVERY


class GitError(RuntimeError):
    pass


def _git(repo: Path, *args: str, check: bool = True, timeout: int = GIT_TIMEOUT_S) -> subprocess.CompletedProcess:
    env_vars = {**os.environ, "GIT_TERMINAL_PROMPT": "0"}  # nunca quedarse esperando una contraseña en consola
    flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)  # Windows: sin ventanas de consola que aparecen y desaparecen
    try:
        proc = subprocess.run(
            ["git", *args], cwd=repo, capture_output=True, text=True, encoding="utf-8", errors="replace",
            timeout=timeout, env=env_vars, creationflags=flags,
        )
    except FileNotFoundError as exc:
        raise GitError("git no está instalado o no está en el PATH") from exc
    except subprocess.TimeoutExpired as exc:
        raise GitError(f"git {args[0]} tardó más de {timeout} s") from exc
    if check and proc.returncode != 0:
        raise GitError(f"git {' '.join(args)}: {(proc.stderr or proc.stdout).strip()[:500]}")
    return proc


# Fases de una subida (lo que enseña «Datos y API» mientras sube: antes solo decía «subiendo los datos ahora…»).
STEPS = {
    "game": "esperando a que acabe tu partida (no gasta tu conexión mientras juegas)",
    "report": "poniendo al día el estado de la búsqueda y el registro",
    "locks": "esperando a que se termine de escribir la partida en curso",
    "add": "añadiendo los ficheros nuevos (git add)",
    "commit": "guardando el commit «datos»",
    "pull": "bajando lo nuevo de GitHub (git pull)",
    "push": "subiendo a GitHub (git push)",
}
STEP_ORDER = ("report", "locks", "add", "commit", "pull", "push")
_PROGRESS = re.compile(r"(Counting|Compressing|Writing) objects:\s+(\d+)%\s*\((\d+)/(\d+)\)(?:,\s*([\d.,]+\s*[KMG]i?B))?")
_PROGRESS_LABEL = {"Counting": "contando", "Compressing": "comprimiendo", "Writing": "enviando"}


def _push_streaming(repo: Path, remote: str, ref: str, on_progress, timeout: int = GIT_TIMEOUT_S) -> subprocess.CompletedProcess:
    """``git push --progress`` leyendo su progreso (``Writing objects: 45% (12/27), 1.2 MiB``) mientras sube."""
    env_vars = {**os.environ, "GIT_TERMINAL_PROMPT": "0"}
    flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    args = ["git", "push", "--progress", remote, ref]
    try:
        proc = subprocess.Popen(
            args, cwd=repo, stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=env_vars, creationflags=flags,
        )
    except FileNotFoundError as exc:
        raise GitError("git no está instalado o no está en el PATH") from exc
    timer = threading.Timer(timeout, proc.kill)
    timer.start()
    err = bytearray()
    line = bytearray()
    try:
        while True:
            ch = proc.stderr.read(1)
            if not ch:
                break
            err += ch
            if ch in (b"\r", b"\n"):
                m = _PROGRESS.search(line.decode("utf-8", "replace"))
                if m:
                    size = f", {m.group(5).strip()}" if m.group(5) else ""
                    on_progress(f"{_PROGRESS_LABEL[m.group(1)]} {m.group(2)} % ({m.group(3)}/{m.group(4)} objetos{size})")
                line.clear()
            else:
                line += ch
        out = proc.stdout.read()
        proc.wait()
    finally:
        timer.cancel()
    if proc.returncode is None or proc.returncode < 0:
        raise GitError(f"git push tardó más de {timeout} s")
    return subprocess.CompletedProcess(args, proc.returncode, out.decode("utf-8", "replace"), err.decode("utf-8", "replace"))


def push_data(repo: Path, data_dir: Path, locks=(), progress=None) -> dict:
    """Commit «datos» con lo que haya en ``data_dir`` y push de la rama actual. Devuelve qué se hizo.

    ``locks``: cerrojos de los escritores de partidas, para no hacer el commit con un fichero a medio escribir.
    ``progress(fase, detalle=None)``: en qué va (``STEPS``).
    """
    progress = progress or (lambda step, detail=None: None)
    if _git(repo, "rev-parse", "--is-inside-work-tree", check=False).stdout.strip() != "true":
        raise GitError(f"{repo} no es un repositorio git")
    branch = _git(repo, "rev-parse", "--abbrev-ref", "HEAD").stdout.strip()
    if branch == "HEAD":
        raise GitError("el repositorio no está en ninguna rama (HEAD separado)")
    try:
        rel = data_dir.resolve().relative_to(repo.resolve()).as_posix()
    except ValueError as exc:
        raise GitError(f"la carpeta de datos ({data_dir}) no está dentro del repositorio") from exc
    if _git(repo, "rev-parse", "--verify", "--quiet", "REBASE_HEAD", check=False).returncode == 0:
        raise GitError("hay un rebase a medias en el repositorio: termínalo o abórtalo antes")
    if _git(repo, "rev-parse", "--verify", "--quiet", "MERGE_HEAD", check=False).returncode == 0:
        raise GitError("hay un merge a medias en el repositorio: termínalo o abórtalo antes")

    # Los escritores de partidas esperan hasta después del pull: así ``data/`` no cambia entre el commit y el merge y
    # el autostash nunca tiene que mezclar partidas a medio escribir.
    progress("locks")
    for lock in locks:
        lock.acquire()
    try:
        progress("add")
        _git(repo, "add", "--", rel)
        files = _git(repo, "diff", "--cached", "--name-only", "--", rel, check=False).stdout.split("\n")
        n_files = sum(1 for f in files if f.strip())
        staged = n_files > 0
        if staged:
            progress("commit", f"{n_files} ficheros")
            _git(repo, "commit", "-m", COMMIT_MESSAGE, "--", rel)
        upstream = _git(repo, "rev-parse", "--abbrev-ref", "--symbolic-full-name", "@{u}", check=False).stdout.strip()
        remote, _, remote_branch = upstream.partition("/") if upstream else ("origin", "", branch)
        ahead = _git(repo, "rev-list", "--count", f"{upstream}..HEAD", check=False).stdout.strip() if upstream else "1"
        if not staged and ahead == "0":
            return {"committed": False, "pushed": False, "branch": branch}
        # Código modificado sin commit: un rebase con autostash podría dejar marcas de conflicto en tus ficheros si el
        # remoto cambió los mismos. En ese caso no se toca nada: se intenta el push tal cual y, si el remoto va por
        # delante, se avisa y se reintenta más tarde.
        code_dirty = _git(repo, "status", "--porcelain", "--untracked-files=no", "--", ".", f":(exclude){rel}").stdout.strip()
        if not code_dirty:
            # Merge, no rebase: el rebase reescribía en disco los ficheros de data/ que la app tiene abiertos y en Windows
            # fallaba («git pull --rebase falló»). El merge solo toca lo que cambió en GitHub.
            progress("pull")
            pull = _git(repo, "pull", "--no-rebase", "--no-edit", "--autostash", remote, remote_branch, check=False)
            conflicts = _git(repo, "diff", "--name-only", "--diff-filter=U", check=False).stdout.strip()
            if pull.returncode != 0 or conflicts:
                _git(repo, "merge", "--abort", check=False)
                raise GitError(f"git pull falló (el commit queda en local): {(pull.stderr or pull.stdout).strip()[:500]}")
    finally:
        for lock in reversed(locks):
            lock.release()

    progress("push")
    report = lambda detail: progress("push", detail)  # noqa: E731
    if code_dirty:
        pushed = _push_streaming(repo, remote, f"HEAD:{remote_branch}", report)
        if pushed.returncode != 0:
            raise GitError(
                "GitHub tiene commits nuevos y hay cambios de código sin commit fuera de data/ "
                f"({len(code_dirty.splitlines())} ficheros): haz commit o descártalos y se subirá solo (el commit «datos» queda en local)"
            )
        return {"committed": staged, "pushed": True, "branch": branch}
    pushed = _push_streaming(repo, remote, f"HEAD:{remote_branch}", report)
    if pushed.returncode != 0:
        raise GitError(f"git push: {(pushed.stderr or pushed.stdout).strip()[-500:]}")
    return {"committed": staged, "pushed": True, "branch": branch}


class AutoPush:
    """Cuenta las partidas nuevas del recolector y, cada ``every()``, sube los datos a GitHub en segundo plano."""

    _instance: AutoPush | None = None
    _instance_lock = threading.Lock()

    def __init__(self, svc, repo: Path = BASE_DIR) -> None:
        self.svc = svc
        self.repo = Path(repo)
        self.data_dir = Path(svc.settings.data_dir)
        self.path = self.data_dir / STATE_FILE
        self.lock = threading.Lock()
        self.thread: threading.Thread | None = None
        self.state = self._load()

    @classmethod
    def of(cls, svc) -> AutoPush | None:
        settings = getattr(svc, "settings", None)
        if settings is None or getattr(settings, "data_dir", None) is None:
            return None
        with cls._instance_lock:
            if cls._instance is None or cls._instance.svc is not svc:
                cls._instance = cls(svc)
            return cls._instance

    def _load(self) -> dict:
        try:
            state = json.loads(self.path.read_text(encoding="utf-8"))
            if isinstance(state, dict):
                return {"pending": int(state.get("pending") or 0), **{k: v for k, v in state.items() if k != "pending"}}
        except (OSError, ValueError, TypeError):
            pass
        return {"pending": 0}

    def _save(self) -> None:
        try:
            self.data_dir.mkdir(parents=True, exist_ok=True)
            tmp = self.path.with_suffix(".json.tmp")
            tmp.write_text(json.dumps(self.state, ensure_ascii=False, indent=1), encoding="utf-8")
            tmp.replace(self.path)
        except OSError:
            log.debug("No se pudo guardar %s", self.path, exc_info=True)

    def public(self) -> dict:
        n = every()
        return {"enabled": n > 0, "every": n, "running": bool(self.thread and self.thread.is_alive()), **self.state}

    def note_game(self, tier: str | None = None, analyzed: bool = False) -> None:
        """Una partida nueva guardada por el recolector (``tier``: el elo, para la gráfica de «Datos y API»; vuelve a
        0 con cada subida a GitHub)."""
        n = every()
        with self.lock:
            kind = "analizadas" if analyzed else "guardadas"
            tiers = self.state.setdefault("tiers", {}).setdefault(kind, {})
            key = (tier or "?").upper()
            tiers[key] = int(tiers.get(key) or 0) + 1
            if n <= 0:
                return
            self.state["pending"] = pending = int(self.state.get("pending") or 0) + 1
            self._save()
            due = pending >= n and time.time() - float(self.state.get("last_error_at") or 0) >= RETRY_AFTER_S
        if pending % 100 == 0 and pending < n:
            log.info("Subida a GitHub: %d de %d partidas nuevas.", pending, n)
        if due:
            self.start()

    def check(self) -> dict:
        """Al arrancar: ¿git está instalado y esto es un repositorio con rama? Así un problema se ve en «Datos y API»
        desde el primer momento, no a las 500 partidas. Si ya tocaba subir (la app se cerró antes), se sube."""
        try:
            if _git(self.repo, "rev-parse", "--is-inside-work-tree", check=False, timeout=30).stdout.strip() != "true":
                raise GitError(f"{self.repo} no es un repositorio git (¿descargaste el proyecto como .zip en vez de clonarlo?)")
            branch = _git(self.repo, "rev-parse", "--abbrev-ref", "HEAD", timeout=30).stdout.strip()
            remote = _git(self.repo, "remote", check=False, timeout=30).stdout.split()
            if not remote:
                raise GitError("el repositorio no tiene ningún remoto (GitHub) al que subir")
            self.state.update(git=f"git listo (rama {branch}, remoto {remote[0]})", git_ok=True)
        except GitError as exc:
            self.state.update(git=str(exc), git_ok=False)
            log.warning("Subida automática a GitHub: %s", exc)
        with self.lock:
            self._save()
        if self.state.get("git_ok") and every() > 0 and int(self.state.get("pending") or 0) >= every():
            self.start()
        return self.public()

    def start(self) -> bool:
        with self.lock:
            if self.thread is not None and self.thread.is_alive():
                return False
            self.thread = threading.Thread(target=self._run, name="autopush", daemon=True)
            self.thread.start()
            return True

    def _fresh_funnel(self) -> None:
        """El estado de la búsqueda (``data/otps/_busqueda.json``) al momento: si no, se sube uno de hasta 5 min antes."""
        crawler = getattr(self.svc, "crawler", None)
        if crawler is None or not getattr(crawler, "running", False):
            return
        try:
            crawler.write_funnel(force=True)
            thread = getattr(crawler, "_funnel_thread", None)
            if thread is not None:
                thread.join(60)
        except Exception:  # noqa: BLE001  (es un informe: la subida sigue sin él)
            log.debug("No se pudo poner al día el estado de la búsqueda antes de subir", exc_info=True)

    def _snapshot_log(self) -> None:
        """Copia el final del registro vivo (``app_vivo.log``, abierto por la app y fuera de GitHub) a ``app.log``."""
        from app.logbus import LIVE_LOG, LOG_SNAPSHOT_BYTES

        folder = self.data_dir / "registro"
        live = folder / LIVE_LOG
        try:
            if not live.exists():
                return
            tail, total = b"", 0
            # El vivo y, si acaba de rotar (es corto), el final del anterior: siempre ~LOG_SNAPSHOT_BYTES seguidos.
            for path in (live.with_name(live.name + ".1"), live):
                if not path.exists():
                    continue
                size = path.stat().st_size
                total += size
                with open(path, "rb") as fh:
                    fh.seek(max(0, size - LOG_SNAPSHOT_BYTES))
                    tail += fh.read()
            tail = tail[-LOG_SNAPSHOT_BYTES:]
            if total > len(tail):
                tail = tail.split(b"\n", 1)[-1]  # desde una línea entera
            tmp = folder / "app.log.tmp"
            tmp.write_bytes(tail)
            tmp.replace(folder / "app.log")
        except OSError:
            log.debug("No se pudo copiar el registro para GitHub", exc_info=True)

    def _write_log(self) -> None:
        """``data/otps/_registro.json``: lo último del registro de la app (avisos, errores con dónde fallaron) y qué está
        haciendo cada hilo ahora mismo. Así se ve desde GitHub por qué el recolector va lento o se ha quedado parado."""
        import traceback

        from app.logbus import bus

        try:
            # Solo acompaña a datos nuevos: por sí solo no es motivo para subir nada.
            if not _git(self.repo, "status", "--porcelain", "--", "data", check=False, timeout=120).stdout.strip():
                return
            names = {t.ident: t.name for t in threading.enumerate()}
            threads = {}
            for ident, frame in sys._current_frames().items():
                name = names.get(ident, str(ident))
                if name in threads:  # varios hilos con el mismo nombre (los de la web): todos, no solo el último
                    name = f"{name} #{ident}"
                stack = traceback.format_stack(frame)[-6:]
                threads[name] = [line.strip().replace("\n", " | ") for line in stack]
            entries = [
                {**e, "hora": time.strftime("%H:%M:%S", time.localtime(e.get("ts", 0)))} for e in bus.recent(300)
            ]
            from app import diag

            out = {"at": int(time.time()), "diagnostico": diag.sampler.snapshot(), "registro": entries, "hilos": threads}
            folder = self.data_dir / "otps"
            folder.mkdir(parents=True, exist_ok=True)
            tmp = folder / "_registro.json.tmp"
            tmp.write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
            tmp.replace(folder / "_registro.json")
        except Exception:  # noqa: BLE001  (es un informe: la subida sigue sin él)
            log.debug("No se pudo escribir el registro para GitHub", exc_info=True)

    def _step(self, key: str, detail: str | None = None) -> None:
        """Fase de la subida en curso (``public()`` la enseña; ``None`` al acabar)."""
        with self.lock:
            prev = self.state.get("step") or {}
            n = STEP_ORDER.index(key) + 1 if key in STEP_ORDER else 0
            at = prev.get("at") if prev.get("key") == key else time.time()
            self.state["step"] = {"key": key, "label": STEPS[key], "n": n or 1, "of": len(STEP_ORDER), "detail": detail, "at": at}

    def _in_game(self) -> bool:
        tracker = getattr(self.svc, "tracker", None)
        return bool(tracker is not None and getattr(tracker, "state", {}).get("in_game"))

    def _run(self) -> None:
        stop = getattr(self.svc, "shutdown_event", None)
        t0 = time.time()
        try:
            self._run_steps(stop, t0)
        finally:
            with self.lock:
                self.state["step"] = None
                self._save()

    def _run_steps(self, stop, t0: float) -> None:
        while self._in_game():  # el ancho de banda, para la partida
            self._step("game")
            if stop is not None and stop.wait(IN_GAME_WAIT_S):
                return
            if stop is None:
                time.sleep(IN_GAME_WAIT_S)
        with self.lock:
            counted = int(self.state.get("pending") or 0)
            counted_tiers = {k: dict(v) for k, v in (self.state.get("tiers") or {}).items()}
        self._step("report")
        try:
            from app.coach import ranking_stats

            stats = ranking_stats.of(self.svc)
            if stats is not None:
                stats.flush()  # las medias del ranking, al día en la subida
        except Exception:  # noqa: BLE001
            log.debug("No se pudieron volcar las medias del ranking", exc_info=True)
        self._fresh_funnel()
        self._snapshot_log()
        self._write_log()
        from app.ingest.otp_details import KIND_DIRS, Files  # aquí: evita un import circular

        locks = [f.lock for f in (Files.of(self.svc, k) for k in KIND_DIRS) if f is not None]
        try:
            result = push_data(self.repo, self.data_dir, locks, progress=self._step)
        except GitError as exc:
            log.warning("No se pudieron subir los datos a GitHub: %s. Se reintentará en %d min.", exc, RETRY_AFTER_S // 60)
            with self.lock:
                self.state.update(last_error=str(exc), last_error_at=time.time())
                self._save()
            return
        except Exception:
            log.exception("Fallo inesperado subiendo los datos a GitHub")
            with self.lock:
                self.state.update(last_error="fallo inesperado (ver el log)", last_error_at=time.time())
                self._save()
            return
        with self.lock:
            # Las partidas que llegaron mientras se subía cuentan para la siguiente.
            self.state["pending"] = max(0, int(self.state.get("pending") or 0) - counted)
            # La gráfica por elo vuelve a 0 (lo que llegó mientras se subía, cuenta para la siguiente).
            for kind, done in counted_tiers.items():
                now = self.state.setdefault("tiers", {}).setdefault(kind, {})
                for tier, n_done in done.items():
                    left = int(now.get(tier) or 0) - int(n_done)
                    if left > 0:
                        now[tier] = left
                    else:
                        now.pop(tier, None)
            self.state.update(last_error=None, last_error_at=None, last_check_at=time.time(), last_push_s=round(time.time() - t0))
            if result["pushed"]:
                self.state.update(last_push_at=time.time(), last_push_games=counted, last_result="pushed")
            else:
                # Recogiendo todo el ranking (sin campeones) las partidas solo van a la base de datos (data/lol.db, que no
                # se sube): en data/ no había nada nuevo.
                self.state.update(last_result="nothing")
            self._save()
        if result["pushed"]:
            log.info("Datos subidos a GitHub (%d partidas nuevas, rama %s).", counted, result["branch"])
        else:
            log.info("Subida a GitHub: %d partidas nuevas, pero nada nuevo en data/ (sin campeones, solo se guardan en la base de datos).", counted)


_warned = False


def note_game(svc, tier: str | None = None, analyzed: bool = False) -> None:
    """Para el recolector: cuenta una partida nueva (no falla nunca: subir datos no puede parar la recogida)."""
    global _warned
    try:
        auto = AutoPush.of(svc)
        if auto is not None:
            auto.note_game(tier, analyzed)
    except Exception:
        if not _warned:
            _warned = True
            log.exception("Subida automática a GitHub: no se pudo contar la partida")


def start(svc) -> None:
    """Al arrancar la app: comprueba git en segundo plano (y sube si quedó pendiente)."""
    auto = AutoPush.of(svc)
    if auto is not None and every() > 0:
        threading.Thread(target=auto.check, name="autopush-check", daemon=True).start()


def main() -> int:
    """``python -m app.autopush``: commit «datos» y push ahora mismo."""
    from app.config import settings

    try:
        result = push_data(BASE_DIR, settings.data_dir)
    except GitError as exc:
        print(f"No se pudo subir: {exc}", file=sys.stderr)
        return 1
    if not result["pushed"]:
        print("No hay datos nuevos que subir.")
    else:
        print(f"Datos subidos a la rama {result['branch']}.")
        try:  # la cuenta de partidas pendientes vuelve a empezar
            path = Path(settings.data_dir) / STATE_FILE
            state = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
            state.update(pending=0, last_push_at=time.time())
            path.write_text(json.dumps(state, ensure_ascii=False, indent=1), encoding="utf-8")
        except (OSError, ValueError):
            pass
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
