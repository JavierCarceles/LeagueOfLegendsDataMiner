"""Por qué va lento: un muestreo de dónde está cada hilo, y consultas lentas a la base de datos.

Cada ``SAMPLE_S`` mira en qué línea del proyecto está cada hilo (y, si está esperando dentro de otra librería, en
cuál: la red, un candado, una cola) y lo suma por grupo de hilos (cada región del recolector, las búsquedas, la web, la
precarga…). ``snapshot()`` da los últimos ``WINDOW_MIN`` minutos en %: va en el embudo (``data/otps/_busqueda.json``,
``diagnostico``) y en el registro cada 5 min, que se suben a GitHub. Cuesta muy poco (unos 60 hilos dos veces por segundo).
"""

from __future__ import annotations

import linecache
import re
import sys
import threading
import time
from collections import Counter, deque

from app.logbus import get_logger

log = get_logger("diag")

SAMPLE_S = 0.5
WINDOW_MIN = 5
TOP_LINES = 6

# Qué está haciendo, por la línea del proyecto y la librería en la que está esperando.
CATEGORY_LABEL = {
    "riot_limite": "esperando turno de la key de Riot",
    "red": "esperando a la red (Riot, OP.GG, GitHub)",
    "candado": "esperando un candado (base de datos, ficheros)",
    "bd": "consultando o escribiendo la base de datos",
    "analizar": "analizando partidas",
    "ficheros": "comprimiendo o escribiendo ficheros",
    "git": "subiendo a GitHub",
    "sin_trabajo": "parado esperando trabajo",
    "otro": "otro",
}
_NET = ("urllib3", "http/client", "ssl.py", "socket.py", "requests", "httpx", "httpcore", "selectors.py")
_ANALYZE = ("analyzer.py", "navgrid.py", "route_macro.py", "track_check.py", "geometry.py", "rule_metrics.py", "champ_checks.py")
_FILES = ("gzip.py", "json/", "zlib", "shutil.py", "pathlib")


def group_of(name: str) -> str:
    """Grupo de un hilo: las búsquedas de cada plataforma juntas, los de la web juntos, sin números de hilo."""
    if name.endswith("-otps"):
        return "busquedas"
    if name.startswith("AnyIO worker"):
        return "web"
    name = name.rsplit("_", 1)[0]
    name = re.sub(r"[-_ ]\d+$", "", name)
    name = re.sub(r" \(.*\)$", "", name)
    return name


def _norm(path: str) -> str:
    return path.replace("\\", "/")


def _app_file(path: str) -> str | None:
    p = _norm(path)
    i = p.rfind("/app/")
    return p[i + 5 :] if i >= 0 else None


DB_FILE = "db.py"
_WAIT_FILES = ("threading.py", "queue.py")
_DB_CALLS = ("execute(", "executemany(", ".query(", ".one(", ".scalar(", "fetchall(")


def _app_frames(frame) -> list[tuple]:
    """La línea del proyecto más interior y, si es de la base de datos, la de quién la llama: [(fichero, línea, función,
    ruta)]."""
    apps: list[tuple] = []
    f = frame
    while f is not None and len(apps) < 2:
        rel = _app_file(f.f_code.co_filename)
        if rel is not None and (not apps or not rel.endswith(DB_FILE)):
            apps.append((rel, f.f_lineno, f.f_code.co_name, f.f_code.co_filename))
            if not rel.endswith(DB_FILE):
                break
        f = f.f_back
    return apps


def _where(apps: list[tuple], inner_file: str, inner_name: str) -> str:
    app = apps[0]
    where = f"{app[0]}:{app[1]} {app[2]}"
    if len(apps) > 1:  # la base de datos: quién la llama
        where = f"{apps[1][0]}:{apps[1][1]} {apps[1][2]} → {app[0].rsplit('/', 1)[-1]}:{app[2]}"
    if _app_file(inner_file) is None:
        where += f" → {inner_file.rsplit('/', 1)[-1]}:{inner_name}"
    return where


def _lock_category(rel: str, text: str, where: str) -> str:
    """Esperando en un candado o una cola: de la base de datos o de un candado nuestro, «candado»; si no, sin trabajo."""
    lock = "lock" in text.lower() or "lock" in where.lower()
    return "candado" if rel.endswith(DB_FILE) or lock else "sin_trabajo"


def _category(rel: str, inner_file: str, text: str, where: str) -> str:
    if rel.endswith("ratelimit.py"):
        return "riot_limite"
    if any(n in inner_file for n in _NET):
        return "red"
    if rel.endswith("autopush.py") and "subprocess" in inner_file:
        return "git"
    if inner_file.endswith(_WAIT_FILES) or "lock" in text.lower() or ".acquire(" in text:
        return _lock_category(rel, text, where)
    if rel.endswith(DB_FILE) or any(k in text for k in _DB_CALLS):
        return "bd"
    if rel.endswith(_ANALYZE):
        return "analizar"
    if any(n in inner_file for n in _FILES):
        return "ficheros"
    return "otro"


def classify(frame) -> tuple[str, str]:
    """(categoría, dónde): la línea del proyecto más interior (si es la base de datos, quién la llama) y, si espera
    dentro de otra librería, en cuál. Las esperas en C (un candado, una consulta de SQLite) no tienen línea propia: se
    reconocen por el texto de la línea del proyecto."""
    inner_file = _norm(frame.f_code.co_filename)
    inner_name = frame.f_code.co_name
    apps = _app_frames(frame)
    if not apps:
        idle = inner_file.endswith((*_WAIT_FILES, "selectors.py"))
        return ("sin_trabajo" if idle else "otro"), f"{inner_file.rsplit('/', 1)[-1]} {inner_name}"
    app = apps[0]
    where = _where(apps, inner_file, inner_name)
    text = linecache.getline(app[3], app[1]).strip() if _app_file(inner_file) is not None else ""
    return _category(app[0], inner_file, text, where), where


class Sampler:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        # Un bloque por minuto: {grupo: [muestras, Counter(categoría), Counter(dónde)]}.
        self._minutes: deque[tuple[int, dict]] = deque(maxlen=WINDOW_MIN)
        self._thread: threading.Thread | None = None

    def sample(self) -> None:
        names = {t.ident: t.name for t in threading.enumerate()}
        me = threading.get_ident()
        minute = int(time.time() // 60)
        with self._lock:
            if not self._minutes or self._minutes[-1][0] != minute:
                self._minutes.append((minute, {}))
            block = self._minutes[-1][1]
            for ident, frame in sys._current_frames().items():
                if ident == me:
                    continue
                group = group_of(names.get(ident, str(ident)))
                cat, where = classify(frame)
                entry = block.setdefault(group, [0, Counter(), Counter()])
                entry[0] += 1
                entry[1][cat] += 1
                entry[2][where] += 1

    def snapshot(self) -> dict:
        """Por grupo de hilos (de más a menos ocupado): % de tiempo en cada cosa y las líneas donde más está."""
        merged: dict[str, list] = {}
        with self._lock:
            for _minute, block in self._minutes:
                for group, (n, cats, wheres) in block.items():
                    m = merged.setdefault(group, [0, Counter(), Counter()])
                    m[0] += n
                    m[1].update(cats)
                    m[2].update(wheres)
        out = {}
        for group, (n, cats, wheres) in sorted(merged.items(), key=lambda kv: -(kv[1][0] - kv[1][1]["sin_trabajo"])):
            out[group] = {
                "muestras": n,
                "en": {CATEGORY_LABEL[c]: round(100 * k / n) for c, k in cats.most_common() if round(100 * k / n) >= 1},
                "lineas": {w: round(100 * k / n) for w, k in wheres.most_common(TOP_LINES)},
            }
        return out

    def summary(self, groups=None) -> str:
        """Una línea por grupo para el registro: «crawler-europe: esperando turno de la key 45 %, …»."""
        lines = []
        for group, s in self.snapshot().items():
            if groups is not None and not any(group.startswith(g) for g in groups):
                continue
            parts = ", ".join(f"{k} {v} %" for k, v in list(s["en"].items())[:4])
            top = next(iter(s["lineas"]), "")
            lines.append(f"{group}: {parts} (más en {top})")
        return " | ".join(lines)

    def start(self, stop: threading.Event | None = None) -> None:
        if self._thread is not None and self._thread.is_alive():
            return

        def run() -> None:
            while not (stop.wait(SAMPLE_S) if stop is not None else time.sleep(SAMPLE_S)):
                try:
                    self.sample()
                # un diagnóstico nunca puede tumbar nada
                except Exception:  # noqa: BLE001
                    log.debug("Fallo muestreando los hilos", exc_info=True)

        self._thread = threading.Thread(target=run, name="diag-sampler", daemon=True)
        self._thread.start()


sampler = Sampler()
