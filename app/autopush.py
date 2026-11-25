"""Subida automática de los datos a GitHub: un commit «datos» y push cuando hay bastante nuevo.

Disparadores (lo primero que llegue; ``AUTO_PUSH_GAMES=0`` lo desactiva todo):

* ``AUTO_PUSH_GAMES`` partidas nuevas del recolector (2.000 por defecto);
* ``AUTO_PUSH_PLAYERS`` jugadores con su historial revisado (2.000);
* ``AUTO_PUSH_VIDEOS`` vídeos recogidos (5; y siempre al vaciar la cola de vídeos);
* y, con algo pendiente, como mucho cada 6 h aunque no se llegue a ninguno (``push_if_stale``, desde ``updater``).

Al subir, en segundo plano y sin tocar nada fuera de ``data/``:

1. Lo que ``.gitignore`` ya excluye pero seguía en el índice (los ``resumen.json`` de cada campeón, que se rehacen solos
   y eran un tercio de lo que crecía el repositorio) se quita del índice sin borrarlo del disco;
2. ``git add -- data`` y ``git commit -m datos -- data`` (solo la carpeta de datos: si tienes código a medias, no entra);
3. ``git pull`` (merge) de la rama actual: así llega el código nuevo que subas desde otro ordenador (``updater`` reinicia
   la app con él). Si tienes código modificado sin commit fuera de ``data/``, no se hace pull: solo se intenta el push y,
   si el remoto va por delante, se avisa en «Datos y API» y se reintenta luego;
4. ``git push`` normal. **Nunca se fuerza**: si el pull choca, se aborta, el commit queda en local y se reintenta
   más tarde.

Mientras juegas se espera a que acabe la partida. Lo pendiente se guarda en ``data/.autopush.json`` (ignorado por git),
así que sobrevive a cerrar o reiniciar la app. Con el repositorio por encima de 4 GB se avisa en «Datos y API» (GitHub
recomienda no pasar de 5 GB).

También se puede lanzar a mano: ``python -m app.autopush`` (sube ya lo que haya, sin esperar a ningún umbral).
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

DEFAULT_EVERY = 2000  # 2.000 partidas nuevas entre subidas (antes 500; con ~90 partidas/min, una cada ~20 min)
# Lo pendiente de cada disparador: (clave en el estado, variable de entorno, umbral por defecto). «pending» son las
# partidas (el nombre de siempre en ``.autopush.json``).
TRIGGERS = (("pending", "AUTO_PUSH_GAMES", DEFAULT_EVERY), ("players_pending", "AUTO_PUSH_PLAYERS", 2000), ("videos_pending", "AUTO_PUSH_VIDEOS", 5))
STALE_PUSH_S = 6 * 3600  # con algo pendiente, como mucho tanto entre subidas
REPO_WARN_MB = 4096  # GitHub recomienda no pasar de 5 GB por repositorio
STATE_FILE = ".autopush.json"
COMMIT_MESSAGE = "datos"
GIT_TIMEOUT_S = 600  # un push grande con una conexión lenta
UNTRACK_CHUNK = 200
RETRY_AFTER_S = 15 * 60  # tras un fallo (sin red, rebase con conflictos…), no se reintenta antes
IN_GAME_WAIT_S = 30


def _env_int(name: str, default: int) -> int:
    raw = env(name)
    try:
        return max(0, int(raw)) if raw not in (None, "") else default
    except ValueError:
        return default


def every() -> int:
    """Partidas nuevas entre una subida y la siguiente (``AUTO_PUSH_GAMES``; 0 = subida automática desactivada)."""
    return _env_int("AUTO_PUSH_GAMES", DEFAULT_EVERY)


def threshold(key: str) -> int:
    """Umbral de un disparador de ``TRIGGERS`` (0: ese disparador no sube nada)."""
    if key == "pending":
        return every()
    for k, name, default in TRIGGERS:
        if k == key:
            return _env_int(name, default)
    raise KeyError(key)


class GitError(RuntimeError):
    def __init__(self, message: str, committed_at: float | None = None) -> None:
        super().__init__(message)
        self.committed_at = committed_at  # el commit «datos» se hizo (y queda en local) aunque falle el push


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
    "summaries": "esperando a que se rehagan los resúmenes por campeón (para subirlos ya hechos)",
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


def _report_progress(text: str, on_progress) -> None:
    m = _PROGRESS.search(text)
    if m:
        size = f", {m.group(5).strip()}" if m.group(5) else ""
        on_progress(f"{_PROGRESS_LABEL[m.group(1)]} {m.group(2)} % ({m.group(3)}/{m.group(4)} objetos{size})")


def _read_progress(stream, on_progress) -> bytearray:
    """La salida de error de git, carácter a carácter (el progreso se reescribe con «\\r»), avisando de cada línea."""
    err = bytearray()
    line = bytearray()
    while True:
        ch = stream.read(1)
        if not ch:
            return err
        err += ch
        if ch in (b"\r", b"\n"):
            _report_progress(line.decode("utf-8", "replace"), on_progress)
            line.clear()
        else:
            line += ch


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
    try:
        err = _read_progress(proc.stderr, on_progress)
        out = proc.stdout.read()
        proc.wait()
    finally:
        timer.cancel()
    if proc.returncode is None or proc.returncode < 0:
        raise GitError(f"git push tardó más de {timeout} s")
    return subprocess.CompletedProcess(args, proc.returncode, out.decode("utf-8", "replace"), err.decode("utf-8", "replace"))


def _check_repo(repo: Path, data_dir: Path) -> tuple[str, str]:
    """(rama, carpeta de datos relativa al repositorio), o ``GitError`` si no se puede subir."""
    if _git(repo, "rev-parse", "--is-inside-work-tree", check=False).stdout.strip() != "true":
        raise GitError(f"{repo} no es un repositorio git")
    branch = _git(repo, "rev-parse", "--abbrev-ref", "HEAD").stdout.strip()
    if branch == "HEAD":
        raise GitError("el repositorio no está en ninguna rama (HEAD separado)")
    try:
        rel = data_dir.resolve().relative_to(repo.resolve()).as_posix()
    except ValueError as exc:
        raise GitError(f"la carpeta de datos ({data_dir}) no está dentro del repositorio") from exc
    for ref, what in (("REBASE_HEAD", "rebase"), ("MERGE_HEAD", "merge")):
        if _git(repo, "rev-parse", "--verify", "--quiet", ref, check=False).returncode == 0:
            raise GitError(f"hay un {what} a medias en el repositorio: termínalo o abórtalo antes")
    return branch, rel


REVERT_PREFIX = "Vuelta atrás automática"  # commits de ``updater`` y ``run.py`` al volver a la versión anterior


def _code(rel: str) -> tuple[str, ...]:
    """Pathspec de todo lo que no son datos."""
    return ("--", ".", f":(exclude){rel}")


def _only_reverts(repo: Path, rel: str, ref: str) -> bool:
    """¿Lo único de código que este ordenador tiene y GitHub no son vueltas atrás automáticas? (código propio: no)"""
    own = _git(repo, "log", "--no-merges", "--format=%s", f"{ref}..HEAD", *_code(rel), check=False).stdout.splitlines()
    return bool(own) and all(line.startswith(REVERT_PREFIX) for line in own)


def _take_remote_code(repo: Path, rel: str, merging: bool) -> None:
    """El código queda exactamente como en GitHub (``FETCH_HEAD``); los datos, como estaban (ya fusionados)."""
    _git(repo, "restore", "--source=FETCH_HEAD", "--staged", "--worktree", *_code(rel))
    if merging:
        _git(repo, "commit", "-q", "--no-edit")
    elif _git(repo, "diff", "--cached", "--quiet", check=False).returncode != 0:
        _git(repo, "commit", "-q", "-m", "Código igual que en GitHub (el arreglo sustituye a la vuelta atrás automática)")


def _pull(repo: Path, remote: str, remote_branch: str, progress, rel: str = "data") -> None:
    """Pull (merge) de GitHub. Los datos se fusionan y nunca se pisan: si chocan, se aborta. En el código manda GitHub
    cuando lo único propio de este ordenador son vueltas atrás automáticas: si mientras se subía la vuelta atrás llegó
    un arreglo hecho sin ella, el código queda como el del arreglo (si no, quedaría a medias entre los dos)."""
    # Merge, no rebase: el rebase reescribía en disco los ficheros de data/ que la app tiene abiertos y en Windows fallaba
    # («git pull --rebase falló»). El merge solo toca lo que cambió en GitHub.
    progress("pull")
    before = _git(repo, "rev-parse", "HEAD", check=False).stdout.strip()
    pull = _git(repo, "pull", "--no-rebase", "--no-edit", "--autostash", remote, remote_branch, check=False)
    # -z: sin las comillas con las que git escribe las rutas con letras no inglesas (si no, «data/…» no parecía datos)
    conflicts = [c for c in _git(repo, "diff", "--name-only", "-z", "--diff-filter=U", check=False).stdout.split("\0") if c]
    in_data = any(c == rel or c.startswith(f"{rel}/") for c in conflicts)
    if conflicts and not in_data and _only_reverts(repo, rel, "FETCH_HEAD"):
        _take_remote_code(repo, rel, merging=True)
        return
    if pull.returncode != 0 or conflicts:
        _git(repo, "merge", "--abort", check=False)
        raise GitError(f"git pull falló (el commit queda en local): {(pull.stderr or pull.stdout).strip()[:500]}")
    brought_code = _git(repo, "diff", "--name-only", f"{before}...FETCH_HEAD", *_code(rel), check=False).stdout.strip()
    if before and brought_code and _only_reverts(repo, rel, "FETCH_HEAD"):
        _take_remote_code(repo, rel, merging=False)


def _untrack_ignored(repo: Path, rel: str) -> int:
    """Ficheros de ``rel`` que ``.gitignore`` ya excluye pero siguen en el índice: fuera del índice (no del disco)."""
    listed = _git(repo, "ls-files", "-ci", "--exclude-standard", "-z", "--", rel, check=False).stdout
    files = [f for f in listed.split("\0") if f]
    for i in range(0, len(files), UNTRACK_CHUNK):  # en tandas: la línea de órdenes de Windows tiene un límite
        _git(repo, "rm", "--cached", "--quiet", "--ignore-unmatch", "--", *files[i : i + UNTRACK_CHUNK])
    return len(files)


def _commit_data(repo: Path, rel: str) -> None:
    """Commit «datos» de lo preparado en ``rel``. Si tienes código preparado (``git add``) fuera de ahí, el commit se
    limita a ``rel`` para no llevárselo (y entonces los ficheros que se quitaron del índice esperan a la siguiente)."""
    others = _git(repo, "diff", "--cached", "--name-only", "--", ".", f":(exclude){rel}", check=False).stdout.strip()
    if not others:
        _git(repo, "commit", "-m", COMMIT_MESSAGE)
        return
    done = _git(repo, "commit", "-m", COMMIT_MESSAGE, "--", rel, check=False)
    if done.returncode != 0 and "nothing" not in (done.stdout + done.stderr):
        raise GitError(f"git commit: {(done.stderr or done.stdout).strip()[:500]}")


def _commit_and_pull(repo: Path, rel: str, branch: str, progress) -> dict | None:
    """Commit «datos» y, con el código limpio, pull. ``None`` si no hay nada que subir; si no, lo que necesita el push."""
    progress("add")
    _untrack_ignored(repo, rel)
    _git(repo, "add", "--", rel)
    files = _git(repo, "diff", "--cached", "--name-only", "--", rel, check=False).stdout.split("\n")
    n_files = sum(1 for f in files if f.strip())
    committed_at = None
    if n_files:
        progress("commit", f"{n_files} ficheros")
        committed_at = time.time()  # antes del commit: lo que se escriba después ya no va en él
        _commit_data(repo, rel)
    upstream = _git(repo, "rev-parse", "--abbrev-ref", "--symbolic-full-name", "@{u}", check=False).stdout.strip()
    remote, _, remote_branch = upstream.partition("/") if upstream else ("origin", "", branch)
    ahead = _git(repo, "rev-list", "--count", f"{upstream}..HEAD", check=False).stdout.strip() if upstream else "1"
    if not n_files and ahead == "0":
        return None
    # Código modificado sin commit: un rebase con autostash podría dejar marcas de conflicto en tus ficheros si el remoto
    # cambió los mismos. En ese caso no se toca nada: se intenta el push tal cual y, si el remoto va por delante, se
    # avisa y se reintenta más tarde.
    code_dirty = _git(repo, "status", "--porcelain", "--untracked-files=no", "--", ".", f":(exclude){rel}").stdout.strip()
    if not code_dirty:
        _pull(repo, remote, remote_branch, progress, rel)
    return {"staged": n_files > 0, "remote": remote, "remote_branch": remote_branch, "code_dirty": code_dirty, "committed_at": committed_at}


def _push_error(pushed: subprocess.CompletedProcess, code_dirty: str) -> str:
    if code_dirty:
        return (
            "GitHub tiene commits nuevos y hay cambios de código sin commit fuera de data/ "
            f"({len(code_dirty.splitlines())} ficheros): haz commit o descártalos y se subirá solo (el commit «datos» queda en local)"
        )
    return f"git push: {(pushed.stderr or pushed.stdout).strip()[-500:]}"


def push_data(repo: Path, data_dir: Path, locks=(), progress=None) -> dict:
    """Commit «datos» con lo que haya en ``data_dir`` y push de la rama actual. Devuelve qué se hizo.

    ``locks``: cerrojos de los escritores de partidas, para no hacer el commit con un fichero a medio escribir.
    ``progress(fase, detalle=None)``: en qué va (``STEPS``).
    """
    progress = progress or (lambda *_: None)
    branch, rel = _check_repo(repo, data_dir)
    # Los escritores de partidas esperan hasta después del pull: así ``data/`` no cambia entre el commit y el merge y el
    # autostash nunca tiene que mezclar partidas a medio escribir.
    progress("locks")
    for lock in locks:
        lock.acquire()
    try:
        state = _commit_and_pull(repo, rel, branch, progress)
    finally:
        for lock in reversed(locks):
            lock.release()
    if state is None:
        return {"committed": False, "pushed": False, "branch": branch, "committed_at": None}
    progress("push")
    pushed = _push_streaming(repo, state["remote"], f"HEAD:{state['remote_branch']}", lambda detail: progress("push", detail))
    if pushed.returncode != 0:
        raise GitError(_push_error(pushed, state["code_dirty"]), state["committed_at"])
    return {"committed": state["staged"], "pushed": True, "branch": branch, "committed_at": state["committed_at"]}


def pull_code(repo: Path, data_dir: Path, locks=()) -> bool:
    """Solo el pull (merge, sin commit ni push): para ``updater`` cuando no había datos que subir. ``False`` si no se
    pudo (sin red, conflicto o código tuyo modificado sin commit: entonces no se toca nada)."""
    try:
        branch, rel = _check_repo(repo, data_dir)
    except GitError:
        return False
    if _git(repo, "status", "--porcelain", "--untracked-files=no", "--", ".", f":(exclude){rel}", check=False).stdout.strip():
        return False
    upstream = _git(repo, "rev-parse", "--abbrev-ref", "--symbolic-full-name", "@{u}", check=False).stdout.strip()
    remote, _, remote_branch = upstream.partition("/") if upstream else ("origin", "", branch)
    for lock in locks:
        lock.acquire()
    try:
        _pull(repo, remote, remote_branch, lambda *_: None, rel)
        return True
    except GitError as exc:
        log.warning("No se pudo bajar el código nuevo de GitHub: %s", exc)
        return False
    finally:
        for lock in reversed(locks):
            lock.release()


def repo_mb(repo: Path) -> float | None:
    """Lo que ocupa el historial del repositorio (MB, como lo cuenta git en este ordenador)."""
    try:
        out = _git(repo, "count-objects", "-v", check=False, timeout=60).stdout
    except GitError:
        return None
    sizes = dict(line.split(": ", 1) for line in out.splitlines() if ": " in line)
    try:
        return round((int(sizes.get("size-pack", 0)) + int(sizes.get("size", 0))) / 1024, 1)
    except ValueError:
        return None


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
        self.busy = False  # una subida en marcha (o la de la cola a punto de empezar)
        self.queued = False  # se pidió otra mientras subía: sale al terminar la actual
        self.queued_wait = False
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
        mb = self.state.get("repo_mb")
        return {
            "enabled": n > 0, "every": n, "running": self.busy, "queued": self.queued,
            "every_players": threshold("players_pending"), "every_videos": threshold("videos_pending"),
            "repo_warning": bool(mb and mb >= REPO_WARN_MB), **self.state,
        }

    def _due(self, key: str) -> bool:
        """¿Toca subir por ese disparador? (con la cerradura cogida; nunca justo después de un fallo)."""
        n = threshold(key)
        return 0 < n <= int(self.state.get(key) or 0) and time.time() - float(self.state.get("last_error_at") or 0) >= RETRY_AFTER_S

    def _note(self, key: str) -> int | None:
        """Suma uno a lo pendiente de ``key``; si toca, sube. Devuelve lo pendiente (``None`` si la subida está apagada)."""
        if every() <= 0:
            return None
        with self.lock:
            self.state[key] = pending = int(self.state.get(key) or 0) + 1
            self._save()
            due = self._due(key)
        if due:
            self.start()
        return pending

    def note_game(self, tier: str | None = None, analyzed: bool = False) -> None:
        """Una partida nueva guardada por el recolector (``tier``: el elo, para la gráfica de «Datos y API»; vuelve a
        0 con cada subida a GitHub)."""
        with self.lock:
            kind = "analizadas" if analyzed else "guardadas"
            tiers = self.state.setdefault("tiers", {}).setdefault(kind, {})
            key = (tier or "?").upper()
            tiers[key] = int(tiers.get(key) or 0) + 1
        pending = self._note("pending")
        if pending is not None and pending % 100 == 0 and pending < every():
            log.info("Subida a GitHub: %d de %d partidas nuevas.", pending, every())

    def note_player(self) -> None:
        """Un jugador con su historial revisado entero (``AUTO_PUSH_PLAYERS`` de ellos también disparan la subida)."""
        self._note("players_pending")

    def note_video(self) -> None:
        """Un vídeo recogido (``AUTO_PUSH_VIDEOS`` de ellos disparan la subida)."""
        self._note("videos_pending")

    def pending_any(self) -> bool:
        return any(int(self.state.get(k) or 0) > 0 for k, _, _ in TRIGGERS)

    def push_if_stale(self) -> bool:
        """Con algo pendiente y sin subir desde hace ``STALE_PUSH_S``, sube aunque no se llegue a ningún umbral."""
        if every() <= 0 or self.busy or self.state.get("git_ok") is False or not self.pending_any():
            return False
        last = max(float(self.state.get("last_push_at") or 0), float(self.state.get("last_error_at") or 0))
        if last and time.time() - last < STALE_PUSH_S:
            return False
        if not last:  # primera vez con este fichero de estado: empieza a contar ahora
            with self.lock:
                self.state["last_push_at"] = time.time()
                self._save()
            return False
        log.info("Subida a GitHub: hay datos pendientes desde hace más de %d h; los subo.", STALE_PUSH_S // 3600)
        return self.start()

    def committed_since(self) -> float:
        """Hora del último commit «datos» (lo escrito antes ya va en un commit: no se reescribe; ver ``otp_details``)."""
        return float(self.state.get("last_commit_at") or 0)

    def check(self) -> dict:
        """Al arrancar: ¿git está instalado y esto es un repositorio con rama? Así un problema se ve en «Datos y API»
        desde el primer momento, no a las 2.000 partidas. Si ya tocaba subir (la app se cerró antes), se sube."""
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
        if self.state.get("git_ok"):
            self.state["repo_mb"] = repo_mb(self.repo)
        with self.lock:
            self._save()
            due = any(self._due(k) for k, _, _ in TRIGGERS)
        if self.state.get("git_ok") and every() > 0 and due:
            self.start()
        return self.public()

    def start(self, wait_summaries: bool = False) -> bool:
        """Sube en segundo plano (``True``). Si ya hay una subida en marcha, deja otra en cola que sale en cuanto acabe
        (``False``): al parar el recolector y el de vídeos seguidos, se sube lo de los dos. ``wait_summaries``: antes,
        que terminen los resúmenes por campeón (al detener el recolector se rehacen todos; si no, se escribían después
        del commit y quedaban 40-50 ficheros sin subir)."""
        with self.lock:
            if self.busy:
                self.queued = True
                self.queued_wait = self.queued_wait or wait_summaries
                return False
            self.busy = True
            self.thread = threading.Thread(target=self._loop, args=(wait_summaries,), name="autopush", daemon=True)
            self.thread.start()
            return True

    def _loop(self, wait_summaries: bool) -> None:
        """La subida pedida y, si mientras tanto se pidió otra, también esa (con lo nuevo que haya)."""
        again = True
        while again:
            try:
                self._run(wait_summaries)
            finally:
                again, wait_summaries = self._next_round()
            if again:
                log.info("Subida a GitHub: había otra en cola; subo también lo nuevo.")

    def _next_round(self) -> tuple[bool, bool]:
        """¿Otra subida en cola? (no si se está cerrando la app). Si no, se libera para la siguiente."""
        stop = getattr(self.svc, "shutdown_event", None)
        with self.lock:
            again = self.queued and not (stop is not None and stop.is_set())
            wait = self.queued_wait
            self.queued = self.queued_wait = False
            if not again:
                self.busy = False
            return again, wait

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
        # es un informe: la subida sigue sin él
        except Exception:  # noqa: BLE001
            log.debug("No se pudo poner al día el estado de la búsqueda antes de subir", exc_info=True)

    def _snapshot_log(self) -> None:
        """Copia el final de los registros vivos (abiertos por la app y fuera de GitHub) a los que se suben: ``app.log`` y
        ``videos.log`` (el detalle del recolector de vídeos)."""
        from app.logbus import LIVE_LOG, VIDEOS_LIVE_LOG, VIDEOS_LOG, copy_tail

        folder = self.data_dir / "registro"
        for live, target in ((LIVE_LOG, "app.log"), (VIDEOS_LIVE_LOG, VIDEOS_LOG)):
            try:
                copy_tail(folder / live, folder / target)
            except OSError:
                log.debug("No se pudo copiar %s para GitHub", live, exc_info=True)

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
        # es un informe: la subida sigue sin él
        except Exception:  # noqa: BLE001
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

    def _run(self, wait_summaries: bool = False) -> None:
        stop = getattr(self.svc, "shutdown_event", None)
        t0 = time.time()
        try:
            if wait_summaries:
                self._step("summaries")
                from app.ingest import otp_details  # aquí: evita un import circular

                otp_details.wait_summaries(self.svc)
            self._run_steps(stop, t0)
        finally:
            with self.lock:
                self.state["step"] = None
                self._save()

    def _wait_game_end(self, stop) -> bool:
        """Mientras juegas, el ancho de banda es para la partida: espera. ``False`` si se paró la app mientras."""
        while self._in_game():
            self._step("game")
            if stop is None:
                time.sleep(IN_GAME_WAIT_S)
            elif stop.wait(IN_GAME_WAIT_S):
                return False
        return True

    def _prepare_report(self) -> None:
        """Lo que va con la subida: las medias del ranking al día, el embudo y el registro."""
        self._step("report")
        try:
            from app.coach import ranking_stats

            stats = ranking_stats.of(self.svc)
            if stats is not None:
                stats.flush()
        except Exception:  # noqa: BLE001
            log.debug("No se pudieron volcar las medias del ranking", exc_info=True)
        self._fresh_funnel()
        self._snapshot_log()
        self._write_log()

    def writer_locks(self) -> list:
        """Cerrojos de los escritores de partidas: con ellos cogidos, ``data/`` no cambia (commit y pull sin partidas a
        medio escribir)."""
        from app.ingest.otp_details import KIND_DIRS, Files  # aquí: evita un import circular

        return [f.lock for f in (Files.of(self.svc, k) for k in KIND_DIRS) if f is not None]

    def _poke_updater(self) -> None:
        try:
            from app.updater import Updater  # aquí: evita un import circular

            updater = Updater.of(self.svc)
            if updater is not None:
                updater.poke()
        except Exception:  # noqa: BLE001 - avisar al actualizador no puede estropear una subida
            log.debug("No se pudo avisar al actualizador", exc_info=True)

    def run_now(self) -> bool:
        """Sube ya y espera a que acabe (``updater``: su pull trae el código nuevo). ``False`` si había otra en marcha."""
        with self.lock:
            if self.busy:
                return False
            self.busy = True
        try:
            self._run()
        finally:
            with self.lock:
                self.busy = False
        return True

    def _push_now(self) -> dict | None:
        """``push_data`` con los cerrojos de los escritores; si falla, lo apunta (y se reintenta más tarde) y da ``None``."""
        locks = self.writer_locks()
        try:
            return push_data(self.repo, self.data_dir, locks, progress=self._step)
        except GitError as exc:
            log.warning("No se pudieron subir los datos a GitHub: %s. Se reintentará en %d min.", exc, RETRY_AFTER_S // 60)
            error = str(exc)
            if exc.committed_at:
                self.state["last_commit_at"] = exc.committed_at
        except Exception:
            log.exception("Fallo inesperado subiendo los datos a GitHub")
            error = "fallo inesperado (ver el log)"
        with self.lock:
            self.state.update(last_error=error, last_error_at=time.time())
            self._save()
        return None

    def _discount_tiers(self, counted_tiers: dict) -> None:
        """La gráfica por elo vuelve a 0 (lo que llegó mientras se subía, cuenta para la siguiente)."""
        for kind, done in counted_tiers.items():
            now = self.state.setdefault("tiers", {}).setdefault(kind, {})
            for tier, n_done in done.items():
                left = int(now.get(tier) or 0) - int(n_done)
                if left > 0:
                    now[tier] = left
                else:
                    now.pop(tier, None)

    def _run_steps(self, stop, t0: float) -> None:
        if not self._wait_game_end(stop):
            return
        with self.lock:
            counted = {k: int(self.state.get(k) or 0) for k, _, _ in TRIGGERS}
            counted_tiers = {k: dict(v) for k, v in (self.state.get("tiers") or {}).items()}
        self._prepare_report()
        result = self._push_now()
        if result is None:
            return
        with self.lock:
            # Lo que llegó mientras se subía cuenta para la siguiente.
            for key, n in counted.items():
                self.state[key] = max(0, int(self.state.get(key) or 0) - n)
            self._discount_tiers(counted_tiers)
            self.state.update(last_error=None, last_error_at=None, last_check_at=time.time(), last_push_s=round(time.time() - t0))
            if result.get("committed_at"):
                self.state["last_commit_at"] = result["committed_at"]
            if result["pushed"]:
                self.state.update(
                    last_push_at=time.time(), last_push_games=counted["pending"], last_push_players=counted["players_pending"],
                    last_push_videos=counted["videos_pending"], last_result="pushed", repo_mb=repo_mb(self.repo),
                )
            else:
                # Recogiendo todo el ranking (sin campeones) las partidas solo van a la base de datos (data/lol.db, que no
                # se sube): en data/ no había nada nuevo.
                self.state.update(last_result="nothing")
            self._save()
        self._poke_updater()  # el pull pudo traer código nuevo: que la app se reinicie con él ya, sin esperar
        if result["pushed"]:
            log.info("Datos subidos a GitHub (%d partidas, %d jugadores y %d vídeos nuevos; rama %s).", counted["pending"],
                     counted["players_pending"], counted["videos_pending"], result["branch"])
        else:
            log.info("Subida a GitHub: %d partidas nuevas, pero nada nuevo en data/ (sin campeones, solo se guardan en la base de datos).", counted["pending"])


_warned = False


def _safe_note(svc, what: str, call) -> None:
    """Cuenta algo nuevo sin fallar nunca: subir datos no puede parar la recogida."""
    global _warned
    try:
        auto = AutoPush.of(svc)
        if auto is not None:
            call(auto)
    except Exception:
        if not _warned:
            _warned = True
            log.exception("Subida automática a GitHub: no se pudo contar %s", what)


def note_game(svc, tier: str | None = None, analyzed: bool = False) -> None:
    """Para el recolector: cuenta una partida nueva."""
    _safe_note(svc, "la partida", lambda auto: auto.note_game(tier, analyzed))


def note_player(svc) -> None:
    """Para el recolector: un jugador con su historial revisado entero."""
    _safe_note(svc, "el jugador", lambda auto: auto.note_player())


def note_video(svc) -> None:
    """Para el recolector de vídeos: un vídeo recogido."""
    _safe_note(svc, "el vídeo", lambda auto: auto.note_video())


def committed_since(svc) -> float:
    """Hora del último commit «datos» (0 si no hay subida automática): lo escrito antes ya no se reescribe."""
    try:
        auto = AutoPush.of(svc)
        return auto.committed_since() if auto is not None and every() > 0 else 0.0
    except Exception:  # noqa: BLE001 - sin estado, como si nunca se hubiera subido
        return 0.0


class PushUnavailable(RuntimeError):
    """No se puede subir (desactivado o git no está listo): el mensaje es para la web."""


def push_now(svc) -> dict:
    """«Subir a GitHub» de arriba: ya, o en cola si hay otra subida en marcha (sale al terminar)."""
    auto = AutoPush.of(svc)
    if auto is None or every() <= 0:
        raise PushUnavailable("La subida automática está desactivada (AUTO_PUSH_GAMES=0).")
    if auto.state.get("git_ok") is False:
        raise PushUnavailable(f"No se puede subir: {auto.state.get('git')}")
    started = auto.start()
    return {"started": started, "queued": not started, "autopush": auto.public()}


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
        try:  # lo pendiente vuelve a empezar
            path = Path(settings.data_dir) / STATE_FILE
            state = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
            state.update({k: 0 for k, _, _ in TRIGGERS}, last_push_at=time.time())
            if result.get("committed_at"):
                state["last_commit_at"] = result["committed_at"]
            path.write_text(json.dumps(state, ensure_ascii=False, indent=1), encoding="utf-8")
        except (OSError, ValueError):
            pass
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
