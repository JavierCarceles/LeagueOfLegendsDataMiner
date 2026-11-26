"""Precargador en segundo plano: que cada vista abra con los datos ya descargados.

Cuando entras en *Draft*, *Matchups* o *Campeones* los datos externos ya están
en caché porque este módulo los ha ido bajando antes, con calma:

* **Carriles** (uno por web): ``opgg-web``, ``opgg-mcp``, ``lolalytics`` y
  ``riot-web`` (notas de parche). Cada uno con un solo hilo y el ritmo educado
  de su fuente, así que en paralelo avanzan rápido sin martillear a nadie.
  ``demand`` ejecuta lo que una petición de la web no pudo esperar.
* **Prioridades**: primero tus campeones y matchups, después lo esencial con
  los filtros que usas (tier lists y counters del parche actual y el anterior)
  y al final los extras (sinergias, guías de matchups populares, regiones…).
* **Ámbitos**: además de los filtros por defecto, recuerda los que usas en la
  web (elo, región y parche) y los mantiene al día.
* **Sin molestar**: mientras estás en partida (cliente de LoL abierto en este
  ordenador) se pausa para no quitarte ancho de banda ni CPU, y lo caducado se
  refresca solo (6 h el parche en curso, 7 días el anterior).
"""

from __future__ import annotations

import itertools
import json
import queue
import threading
import time
from collections import deque

from app import budget
from app.external.base import P_BACKGROUND, P_CORE, P_DEMAND, P_EXTRA, P_USER, inline_refresh
from app.logbus import get_logger

log = get_logger("precarga")

LANES = {"opgg-web": 2, "opgg-mcp": 2, "lolalytics": 1, "riot-web": 1, "demand": 3}
LANE_LABELS = {
    "opgg-web": "OP.GG (web)",
    "opgg-mcp": "OP.GG (API oficial)",
    "lolalytics": "Lolalytics",
    "riot-web": "Notas de parche (Riot)",
    "demand": "Peticiones de la web",
}
ROLES = ("TOP", "JUNGLE", "MID", "ADC", "SUPPORT")
REPLAN_S = 60 * 60  # revisar qué falta, una vez por hora (pedido el 26-11-2026; antes cada 10 min y se veía «Preparando datos» a menudo)
LAST_FULL_KEY = "warmer.last_full"  # cuándo acabó la última revisión completa: al reiniciar la app no se repite antes de la hora
SCOPES_KEY = "warmer.scopes"
DEFAULT_SCOPE = {"tier": "emerald_plus", "platform": "all", "patch": "all"}
MAX_SCOPES = 4
SCOPE_TTL_S = 3 * 86400
MIN_ROLE_RATE = 0.08  # campeones que de verdad se juegan en ese rol (fracción de sus partidas)
ESSENTIAL = P_CORE  # lo de prioridad <= P_CORE cuenta como «esencial» en el progreso


WARM_GAP_WHILE_CRAWLING_S = 10.0  # pausa entre tareas de precarga mientras recoge el recolector


def _enabled(source) -> bool:
    """¿Fuente externa (OP.GG, Lolalytics, notas de parche) presente y activada?"""
    return source is not None and source.enabled


def _scope_patches(scopes: list):
    """(ámbito, parche) de cada parche de cada ámbito, en orden."""
    return ((scope, patch) for scope in scopes for patch in scope.patches)


class Lane:
    def __init__(self, name: str, workers: int) -> None:
        self.name = name
        self.workers = workers
        self.queue: queue.PriorityQueue = queue.PriorityQueue()
        self.best: dict = {}  # clave -> (prioridad, fn, seq, etiqueta, plan)
        self.lock = threading.Lock()
        self.done = 0
        self.errors = 0
        self.current: str | None = None
        self.recent: deque = deque(maxlen=120)
        self.threads: list[threading.Thread] = []

    def pending(self) -> int:
        with self.lock:
            return len(self.best)

    def rate_per_min(self) -> float:
        now = time.monotonic()
        recent = [t for t in self.recent if now - t <= 60]
        return float(len(recent))


class Warmer:
    def __init__(self, svc, enabled: bool = True) -> None:
        self.svc = svc
        self.enabled = enabled
        self.lanes = {name: Lane(name, workers) for name, workers in LANES.items()}
        self.stop_event = threading.Event()
        self.replan_event = threading.Event()
        self.started = False
        self.manual_pause = False
        self._seq = itertools.count()
        self._plan_id = 0
        self._plan_lock = threading.Lock()
        self.plan: dict = {"id": 0, "total": 0, "done": 0, "essential_total": 0, "essential_done": 0, "started_at": None, "finished_at": None, "phase": None}
        self._scopes: dict[str, dict] = {}
        self._scopes_loaded = False
        self._scopes_dirty = False
        self._last_scope_save = 0.0
        self._planner: threading.Thread | None = None

    # ---------------------------------------------------------------- control
    def start(self) -> None:
        if self.started or not self.enabled:
            return
        self.started = True
        self.stop_event.clear()
        for lane in self.lanes.values():
            for i in range(lane.workers):
                thread = threading.Thread(target=self._work, args=(lane,), name=f"warm-{lane.name}-{i}", daemon=True)
                lane.threads.append(thread)
                thread.start()
        self._planner = threading.Thread(target=self._plan_loop, name="warm-planner", daemon=True)
        self._planner.start()

    def shutdown(self) -> None:
        self.stop_event.set()
        self.replan_event.set()
        self._save_scopes(force=True)

    def replan(self) -> None:
        """Vuelve a revisar qué falta (al guardar tu perfil, cambiar de parche, etc.)."""
        self.replan_event.set()

    def set_paused(self, paused: bool) -> None:
        self.manual_pause = bool(paused)
        log.info("Precarga %s.", "en pausa" if paused else "reanudada")

    def paused_reason(self) -> str | None:
        if self.manual_pause:
            return "manual"
        svc = self.svc
        if getattr(svc.settings, "pause_in_game", True):
            tracker = getattr(svc, "tracker", None)
            if tracker is not None and (tracker.state.get("busy") or tracker.state.get("in_game")):
                return "in_game"
        return None

    # ----------------------------------------------------------------- colas
    def submit(self, lane_name: str, key, fn, priority: int, label: str | None = None, plan: int | None = None) -> bool:
        """Encola ``fn`` (idempotente) sin duplicar: si ya estaba, se queda con la mejor prioridad."""
        if not self.enabled:
            return False
        lane = self.lanes.get(lane_name) or self.lanes["demand"]
        with lane.lock:
            existing = lane.best.get(key)
            if existing is not None:
                prio = min(existing[0], priority)
                plan_id = plan if plan is not None else existing[4]
                if prio == existing[0]:
                    lane.best[key] = (existing[0], fn, existing[2], label or existing[3], plan_id)
                    return True
            else:
                prio = priority
                plan_id = plan
            seq = next(self._seq)
            lane.best[key] = (prio, fn, seq, label, plan_id)
            lane.queue.put((prio, seq, key))
        return True

    def defer(self, fn, label: str = "petición pendiente") -> bool:
        """Ejecuta en segundo plano algo que una petición de la web no pudo esperar."""
        return self.submit("demand", ("defer", next(self._seq)), fn, P_DEMAND, label=label)

    def _work(self, lane: Lane) -> None:
        while not self.stop_event.is_set():
            if lane.name != "demand" and self.paused_reason():
                self.stop_event.wait(2.0)
                continue
            try:
                prio, seq, key = lane.queue.get(timeout=1.0)
            except queue.Empty:
                continue
            with lane.lock:
                entry = lane.best.get(key)
                if entry is None or entry[0] != prio or entry[2] != seq:
                    continue  # sustituida por otra con mejor prioridad
                del lane.best[key]
            _, fn, _, label, plan_id = entry
            lane.current = label
            try:
                budget.run_detached(self._run_inline, fn)
                lane.done += 1
            except Exception:
                lane.errors += 1
                log.debug("Precarga: fallo en %s", label, exc_info=True)
            finally:
                lane.current = None
                lane.recent.append(time.monotonic())
                self._tick(plan_id, prio)
            if lane.name != "demand" and self._crawler_running():
                # Con el recolector en marcha la precarga cede el paso: sus escrituras (~3,5 por segundo entre todas las
                # colas) competían por la base de datos con las partidas. Lo que pides desde la web («demand») no espera.
                self.stop_event.wait(WARM_GAP_WHILE_CRAWLING_S)

    def _crawler_running(self) -> bool:
        crawler = getattr(self.svc, "crawler", None)
        try:
            return bool(crawler is not None and crawler.running)
        except Exception:  # noqa: BLE001
            return False

    @staticmethod
    def _run_inline(fn):
        inline_refresh.set(True)
        return fn()

    def _tick(self, plan_id: int | None, prio: int) -> None:
        if plan_id is None:
            return
        with self._plan_lock:
            if plan_id != self.plan["id"]:
                return
            self.plan["done"] += 1
            if prio <= ESSENTIAL:
                self.plan["essential_done"] += 1
            if self.plan["done"] >= self.plan["total"] and not self.plan["finished_at"]:
                self.plan["finished_at"] = time.time()
                self.plan["phase"] = None
                log.info("Precarga completa: %d tareas al día.", self.plan["total"])
                try:
                    self.svc.db.meta_set(LAST_FULL_KEY, str(self.plan["finished_at"]))
                except Exception:  # noqa: BLE001 - sin guardarlo, la próxima vez se revisa al arrancar (como antes)
                    log.debug("No se pudo guardar la hora de la precarga", exc_info=True)

    # ---------------------------------------------------------------- ámbitos
    def _load_scopes(self) -> None:
        if self._scopes_loaded:
            return
        self._scopes_loaded = True
        try:
            raw = json.loads(self.svc.db.meta_get(SCOPES_KEY) or "[]")
        except (ValueError, TypeError):
            raw = []
        now = time.time()
        for item in raw if isinstance(raw, list) else []:
            if not isinstance(item, dict):
                continue
            scope = {k: str(item.get(k) or "all") for k in ("tier", "platform", "patch")}
            if now - float(item.get("last_used") or 0) <= SCOPE_TTL_S:
                self._scopes[self._scope_key(scope)] = {**scope, "last_used": float(item.get("last_used") or now)}

    @staticmethod
    def _scope_key(scope: dict) -> str:
        return f"{scope['tier']}|{scope['platform']}|{scope['patch']}"

    def note_scope(self, tier: str, platform: str, patch: str) -> None:
        """La web ha usado estos filtros: se mantendrán precargados unos días."""
        if not self.enabled:
            return
        scope = {"tier": (tier or "all").strip() or "all", "platform": (platform or "all").strip().lower() or "all", "patch": (patch or "all").strip() or "all"}
        if scope["patch"] == "legacy":
            return
        self._load_scopes()
        key = self._scope_key(scope)
        is_new = key not in self._scopes and key != self._scope_key(DEFAULT_SCOPE)
        self._scopes[key] = {**scope, "last_used": time.time()}
        if len(self._scopes) > MAX_SCOPES:
            oldest = sorted(self._scopes, key=lambda k: self._scopes[k]["last_used"])
            for k in oldest[: len(self._scopes) - MAX_SCOPES]:
                self._scopes.pop(k, None)
        self._scopes_dirty = True
        if is_new:
            self._save_scopes(force=True)
            self.replan()
        else:
            self._save_scopes()

    def _save_scopes(self, force: bool = False) -> None:
        if not self._scopes_dirty or (not force and time.monotonic() - self._last_scope_save < 120):
            return
        try:
            self.svc.db.meta_set(SCOPES_KEY, json.dumps(list(self._scopes.values())))
            self._scopes_dirty = False
            self._last_scope_save = time.monotonic()
        except Exception:
            log.debug("No se pudieron guardar los ámbitos de precarga", exc_info=True)

    def scopes(self) -> list[dict]:
        """Ámbitos a precargar: el de por defecto y los usados recientemente."""
        self._load_scopes()
        now = time.time()
        items = [s for s in self._scopes.values() if now - s["last_used"] <= SCOPE_TTL_S]
        items.sort(key=lambda s: -s["last_used"])
        out = [dict(DEFAULT_SCOPE)]
        seen = {self._scope_key(DEFAULT_SCOPE)}
        for s in items:
            key = self._scope_key(s)
            if key not in seen:
                seen.add(key)
                out.append({k: s[k] for k in ("tier", "platform", "patch")})
        return out[:MAX_SCOPES]

    # ------------------------------------------------------------ planificar
    def _plan_loop(self) -> None:
        # Espera a que Data Dragon y la importación inicial estén listos.
        self.svc.startup_done.wait(timeout=600)
        first = True
        # Recién reiniciada (cada versión nueva): si la última revisión completa fue hace menos de una hora, lo bajado
        # sigue en disco y se espera a que toque. Un cambio de filtros o de fuentes (``replan``) la adelanta.
        wait = self._until_due()
        if wait > 0 and getattr(self.svc.dd, "champions", None):
            self.replan_event.wait(wait)
            self.replan_event.clear()
        while not self.stop_event.is_set():
            if self.paused_reason():
                self.stop_event.wait(5)
                continue
            try:
                self._make_plan(first)
                first = False
            except Exception:
                log.exception("Fallo planificando la precarga")
            # Sin Data Dragon (primer arranque sin internet) se reintenta cada minuto; si no, cada hora.
            self.replan_event.wait(REPLAN_S if getattr(self.svc.dd, "champions", None) else 60)
            self.replan_event.clear()
            self._save_scopes()

    def _until_due(self) -> float:
        """Segundos que faltan para la siguiente revisión (0 si ya toca o no se sabe cuándo fue la última)."""
        try:
            last = float(self.svc.db.meta_get(LAST_FULL_KEY) or 0)
        except (TypeError, ValueError, AttributeError):
            return 0.0
        except Exception:  # noqa: BLE001 - base de datos ocupada o sin la tabla: se revisa ya
            return 0.0
        return max(0.0, min(REPLAN_S, last + REPLAN_S - time.time()))

    def _make_plan(self, first: bool) -> None:
        tasks: list[tuple] = []  # (carril, clave, fn, prioridad, etiqueta)
        with budget_free():
            self._plan_sources(tasks)
        # La misma tarea puede salir en varios bloques (p. ej. tus counters también son «esenciales»):
        # se queda una sola, con la mejor prioridad, para que el progreso llegue al 100 %.
        best: dict = {}
        for task in tasks:
            key = (task[0], tuple(task[1]))
            if key not in best or task[3] < best[key][3]:
                best[key] = task
        tasks = list(best.values())
        if not tasks:
            return
        with self._plan_lock:
            self._plan_id += 1
            plan_id = self._plan_id
            essential = sum(1 for t in tasks if t[3] <= ESSENTIAL)
            self.plan = {
                "id": plan_id,
                "total": len(tasks),
                "done": 0,
                "essential_total": essential,
                "essential_done": 0,
                "started_at": time.time(),
                "finished_at": None,
                "phase": "Revisando qué falta…",
            }
        for lane, key, fn, prio, label in tasks:
            if not self.submit(lane, ("plan",) + tuple(key), fn, prio, label=label, plan=plan_id):
                self._tick(plan_id, prio)
        self._phase(None)
        if first:
            log.info("Precarga en marcha: %d tareas (%d esenciales). Las vistas se abren al instante con lo que ya hay.", len(tasks), essential)

    def _plan_sources(self, tasks: list) -> None:
        svc = self.svc
        opgg = getattr(svc, "opgg", None)
        lola = getattr(svc, "lolalytics", None)
        notes = getattr(svc, "patchnotes", None)
        if not self._ddragon_ready():
            return

        # Notas de parche: índice y las tres últimas (en el carril de Riot).
        if _enabled(notes):
            tasks.append(("riot-web", ("notes",), lambda: notes.recent(3), P_EXTRA, "Notas de parche"))

        # Ámbitos externos (OP.GG/Lolalytics) a partir de los filtros usados.
        scopes: list = []
        user = self._user_champions()
        if (_enabled(opgg) or _enabled(lola)) and not self._warm_scopes(scopes, user, opgg, lola):
            return  # en pausa o cerrando: en esta vuelta no se planifica nada más

        primary = scopes[0] if scopes else None

        # ----- 1) Tú: tus campeones, tus builds y tus matchups.
        for champ, role in user:
            self._plan_user_champion(tasks, champ, role, scopes, primary, opgg, lola)

        # ----- 2) Lo esencial de cada ámbito: tier lists y counters.
        self._plan_core(tasks, scopes, opgg, lola)

        # ----- 3) Extras: tipo de daño y builds (draft y tu build), sinergias y guías populares.
        if _enabled(opgg) and primary is not None:
            patch = primary.patches[0]
            for role in ROLES:
                self._plan_role_extras(tasks, primary, role, patch, opgg)

    def _ddragon_ready(self) -> bool:
        """¿Hay campeones de Data Dragon? Si no (primer arranque sin internet), lo reintenta en cada ciclo."""
        svc = self.svc
        if getattr(svc.dd, "champions", None):
            return True
        # Primer arranque sin internet: se reintenta Data Dragon en cada ciclo hasta que haya conexión.
        try:
            if svc.dd.load():
                log.info("Data Dragon cargado: ya hay nombres de campeones y objetos.")
                svc.priors.invalidate()
        except Exception:
            log.debug("Data Dragon sigue sin estar disponible", exc_info=True)
        return bool(getattr(svc.dd, "champions", None))

    def _warm_scopes(self, scopes: list, user: list, opgg, lola) -> bool:
        """Rellena ``scopes`` y baja ya lo que hace falta para planificar el resto; False si hay que parar (pausa o cierre)."""
        token = inline_refresh.set(True)
        try:
            self._phase("Comprobando el parche actual…")
            if _enabled(opgg):
                opgg.versions()
            scopes.extend(self._ext_scopes())
            # Lo que hace falta para planificar el resto se baja ya (son pocas páginas):
            # tier lists de cada ámbito y los counters de tus campeones.
            if not self._fetch_tier_lists(scopes, opgg, lola):
                return False
            if scopes and _enabled(opgg):
                for champ, role in user:
                    self._phase(f"Tus counters: {self.svc.dd.name(champ)}")
                    opgg.counters(champ, role, scopes[0].tier, scopes[0].region, scopes[0].patches[0])
        finally:
            inline_refresh.reset(token)
        return True

    def _ext_scopes(self) -> list:
        """Ámbitos de OP.GG/Lolalytics de los filtros a precargar, sin repetir (uno que ya no vale se descarta)."""
        from app.analytics.evidence import ext_scope
        from app.analytics.filters import Filters

        svc = self.svc
        scopes: list = []
        for raw in self.scopes():
            try:
                f = Filters.parse(svc.db, patch=raw["patch"], tier=raw["tier"], platform=raw["platform"])
                scope = ext_scope(svc, f)
            # un filtro guardado que ya no vale no para la precarga
            except Exception as exc:  # noqa: BLE001
                log.warning("Ámbito de precarga %s descartado: %s", raw, exc)
                scope = None
            if scope is not None and scope not in scopes:
                scopes.append(scope)
        return scopes

    def _fetch_tier_lists(self, scopes: list, opgg, lola) -> bool:
        """Baja ya las tier lists de cada ámbito (OP.GG o, sin él, Lolalytics); False si hay que parar (pausa o cierre)."""
        for scope, patch in _scope_patches(scopes):
            for role in ROLES:
                if self.stop_event.is_set() or self.paused_reason():
                    return False
                self._phase(f"Tier list {role.lower()} · parche {patch}")
                if _enabled(opgg):
                    opgg.tier_list(role, scope.tier, scope.region, patch)
                elif _enabled(lola):
                    lola.tier_list(role, scope.tier, scope.region, patch)
        return True

    def _plan_user_champion(self, tasks: list, champ: int, role: str, scopes: list, primary, opgg, lola) -> None:
        """Tareas de uno de tus campeones: su build, sus counters (dos primeros ámbitos) y las guías de sus matchups."""
        name = self.svc.dd.name(champ)
        if _enabled(opgg):
            tasks.append(("opgg-mcp", ("build", champ, role), lambda c=champ, r=role: opgg.champion_build(c, r), P_USER, f"Build de {name}"))
            for scope, patch in _scope_patches(scopes[:2]):
                tasks.append(
                    ("opgg-web", ("counters", champ, role, scope.tier, scope.region, patch),
                     lambda c=champ, r=role, s=scope, p=patch: opgg.counters(c, r, s.tier, s.region, p), P_USER, f"Counters de {name}")
                )
            if primary is not None:
                for opp in self._top_opponents(champ, role, primary, limit=8):
                    tasks.append(
                        ("opgg-mcp", ("guide", champ, opp, role),
                         lambda c=champ, o=opp, r=role: opgg.matchup_guide(c, o, r), P_USER, f"Guía {name} vs {self.svc.dd.name(opp)}")
                    )
        if _enabled(lola) and primary is not None:
            tasks.append(
                ("lolalytics", ("counters", champ, role, primary.tier, primary.region, primary.patches[0]),
                 lambda c=champ, r=role, s=primary: lola.counters(c, r, s.tier, s.region, s.patches[0]), P_USER, f"Lolalytics {name}")
            )

    def _plan_core(self, tasks: list, scopes: list, opgg, lola) -> None:
        """Lo esencial de cada ámbito: tier lists y counters (los del primer ámbito, los más urgentes)."""
        opgg_on = _enabled(opgg)
        for index, scope in enumerate(scopes):
            prio = P_CORE if index == 0 else P_EXTRA
            if opgg_on:
                self._plan_opgg_scope(tasks, scope, prio, opgg)
            if _enabled(lola):
                self._plan_lola_scope(tasks, scope, prio, lola)

    def _plan_opgg_scope(self, tasks: list, scope, prio: int, opgg) -> None:
        """Tier lists y counters de OP.GG de un ámbito (los counters de cada parche siguiente, un punto menos urgentes)."""
        for patch in scope.patches:
            for role in ROLES:
                tasks.append(
                    ("opgg-web", ("tier", role, scope.tier, scope.region, patch),
                     lambda r=role, s=scope, p=patch: opgg.tier_list(r, s.tier, s.region, p), prio - 1, f"Tier list {role} {patch}")
                )
        for patch_index, patch in enumerate(scope.patches):
            for role in ROLES:
                for champ in self._role_champions(scope, role, patch):
                    tasks.append(
                        ("opgg-web", ("counters", champ, role, scope.tier, scope.region, patch),
                         lambda c=champ, r=role, s=scope, p=patch: opgg.counters(c, r, s.tier, s.region, p),
                         prio + patch_index, f"Counters {self.svc.dd.name(champ)} {role} {patch}")
                    )

    def _plan_lola_scope(self, tasks: list, scope, prio: int, lola) -> None:
        """Tier lists y counters de Lolalytics de un ámbito (solo su primer parche)."""
        patch = scope.patches[0]
        for role in ROLES:
            tasks.append(
                ("lolalytics", ("tier", role, scope.tier, scope.region, patch),
                 lambda r=role, s=scope, p=patch: lola.tier_list(r, s.tier, s.region, p), prio, f"Lolalytics tier list {role}")
            )
            for champ in self._role_champions(scope, role, patch):
                tasks.append(
                    ("lolalytics", ("counters", champ, role, scope.tier, scope.region, patch),
                     lambda c=champ, r=role, s=scope, p=patch: lola.counters(c, r, s.tier, s.region, p),
                     prio + 5, f"Lolalytics {self.svc.dd.name(champ)} {role}")
                )

    def _plan_role_extras(self, tasks: list, primary, role: str, patch: str, opgg) -> None:
        """Extras de OP.GG de un rol en el ámbito principal: tipo de daño, builds, sinergias y guías de matchups."""
        svc = self.svc
        ranked = self._role_champions(primary, role, patch)
        for champ in ranked:
            tasks.append(("opgg-mcp", ("champion", champ, role), lambda c=champ, r=role: opgg.champion_info(c, r), P_EXTRA, f"Tipo de daño {svc.dd.name(champ)}"))
            tasks.append(("opgg-mcp", ("build", champ, role), lambda c=champ, r=role: opgg.champion_build(c, r), P_EXTRA + 2, f"Build {svc.dd.name(champ)}"))
        for champ in ranked[:25]:
            for partner in ROLES:
                if partner != role:
                    tasks.append(
                        ("opgg-mcp", ("synergy", champ, role, partner),
                         lambda c=champ, r=role, p=partner: opgg.synergies(c, r, p), P_BACKGROUND, f"Sinergias {svc.dd.name(champ)}")
                    )
        for champ in ranked[:12]:
            for opp in self._top_opponents(champ, role, primary, limit=4):
                tasks.append(
                    ("opgg-mcp", ("guide", champ, opp, role),
                     lambda c=champ, o=opp, r=role: opgg.matchup_guide(c, o, r), P_BACKGROUND + 2, f"Guía {svc.dd.name(champ)} vs {svc.dd.name(opp)}")
                )

    # --------------------------------------------------------------- helpers
    def _phase(self, text: str | None) -> None:
        with self._plan_lock:
            self.plan["phase"] = text

    def _user_champions(self) -> list[tuple[int, str]]:
        """Tus campeones más jugados con su rol (hasta 6), según tus partidas analizadas."""
        try:
            from app.coach import store

            profile = store.get_profile(self.svc.db)
            if not profile:
                return []
            pool = store.champion_pool(self.svc.db, store.known_puuids(profile))
        # sin tus campeones, la precarga sigue con lo esencial
        except Exception as exc:  # noqa: BLE001
            log.warning("No se pudieron leer tus campeones para la precarga: %s", exc)
            return []
        out = []
        for row in pool:
            if row.get("role") in ROLES and row.get("champ"):
                out.append((int(row["champ"]), row["role"]))
            if len(out) >= 6:
                break
        return out

    def _role_champions(self, scope, role: str, patch: str) -> list[int]:
        """Campeones de un rol (de más a menos jugados) según la tier list en caché (OP.GG o, si no, Lolalytics)."""
        data = {}
        opgg = getattr(self.svc, "opgg", None)
        if opgg is not None and opgg.enabled:
            data = opgg.tier_list(role, scope.tier, scope.region, patch, allow_fetch=False) or {}
        if not data:
            lola = getattr(self.svc, "lolalytics", None)
            if lola is not None and lola.enabled:
                data = lola.tier_list(role, scope.tier, scope.region, patch, allow_fetch=False) or {}
        rows = [(champ, row) for champ, row in data.items() if (row.get("role_rate") or 0) >= MIN_ROLE_RATE]
        rows.sort(key=lambda item: -(item[1].get("pick") or 0))
        return [champ for champ, _ in rows]

    def _top_opponents(self, champ: int, role: str, scope, limit: int) -> list[int]:
        opgg = getattr(self.svc, "opgg", None)
        if opgg is None:
            return []
        data = opgg.counters(champ, role, scope.tier, scope.region, scope.patches[0], allow_fetch=False) or {}
        ranked = sorted(data.items(), key=lambda item: -item[1][0])
        return [opp for opp, _ in ranked[:limit]]

    # ----------------------------------------------------------------- estado
    def status(self) -> dict:
        reason = self.paused_reason()
        with self._plan_lock:
            plan = dict(self.plan)
        lanes = {}
        for name, lane in self.lanes.items():
            lanes[name] = {
                "label": LANE_LABELS.get(name, name),
                "pending": lane.pending(),
                "done": lane.done,
                "errors": lane.errors,
                "current": lane.current,
                "rate_per_min": lane.rate_per_min(),
            }
        total = plan.get("essential_total") or 0
        essential_pct = 100 if not total else round(100 * min(1.0, (plan.get("essential_done") or 0) / total))
        all_pct = 100 if not plan.get("total") else round(100 * min(1.0, (plan.get("done") or 0) / plan["total"]))
        return {
            "enabled": self.enabled,
            "running": self.started,
            "paused": bool(reason),
            "pause_reason": reason,
            "plan": plan,
            "essential_pct": essential_pct,
            "all_pct": all_pct,
            "lanes": lanes,
            "scopes": self.scopes(),
        }


class budget_free:
    """Contexto sin presupuesto de petición (el planificador corre en su propio hilo)."""

    def __enter__(self):
        self._token = budget._current.set(None)
        return self

    def __exit__(self, *_):
        budget._current.reset(self._token)
        return False
