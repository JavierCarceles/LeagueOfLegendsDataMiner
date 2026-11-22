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
REPLAN_S = 10 * 60
SCOPES_KEY = "warmer.scopes"
DEFAULT_SCOPE = {"tier": "emerald_plus", "platform": "all", "patch": "all"}
MAX_SCOPES = 4
SCOPE_TTL_S = 3 * 86400
MIN_ROLE_RATE = 0.08  # campeones que de verdad se juegan en ese rol (fracción de sus partidas)
ESSENTIAL = P_CORE  # lo de prioridad <= P_CORE cuenta como «esencial» en el progreso


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
            if tracker is not None and tracker.state.get("in_game"):
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
        while not self.stop_event.is_set():
            if self.paused_reason():
                self.stop_event.wait(5)
                continue
            try:
                self._make_plan(first)
                first = False
            except Exception:
                log.exception("Fallo planificando la precarga")
            # Sin Data Dragon (primer arranque sin internet) se reintenta cada minuto; si no, cada 10.
            self.replan_event.wait(REPLAN_S if getattr(self.svc.dd, "champions", None) else 60)
            self.replan_event.clear()
            self._save_scopes()

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
        from app.analytics.evidence import ext_scope
        from app.analytics.filters import Filters

        opgg = getattr(svc, "opgg", None)
        lola = getattr(svc, "lolalytics", None)
        notes = getattr(svc, "patchnotes", None)
        champs_known = bool(getattr(svc.dd, "champions", None))
        if not champs_known:
            # Primer arranque sin internet: se reintenta Data Dragon en cada ciclo hasta que haya conexión.
            try:
                if svc.dd.load():
                    log.info("Data Dragon cargado: ya hay nombres de campeones y objetos.")
                    svc.priors.invalidate()
            except Exception:
                log.debug("Data Dragon sigue sin estar disponible", exc_info=True)
            if not getattr(svc.dd, "champions", None):
                return

        # Notas de parche: índice y las tres últimas (en el carril de Riot).
        if notes is not None and notes.enabled:
            tasks.append(("riot-web", ("notes",), lambda: notes.recent(3), P_EXTRA, "Notas de parche"))

        # Ámbitos externos (OP.GG/Lolalytics) a partir de los filtros usados.
        scopes = []
        user = self._user_champions()
        web_on = (opgg is not None and opgg.enabled) or (lola is not None and lola.enabled)
        if web_on:
            token = inline_refresh.set(True)
            try:
                self._phase("Comprobando el parche actual…")
                if opgg is not None and opgg.enabled:
                    opgg.versions()
                for raw in self.scopes():
                    try:
                        f = Filters.parse(svc.db, patch=raw["patch"], tier=raw["tier"], platform=raw["platform"])
                        scope = ext_scope(svc, f)
                    except Exception as exc:  # noqa: BLE001  (un filtro guardado que ya no vale no para la precarga)
                        log.warning("Ámbito de precarga %s descartado: %s", raw, exc)
                        scope = None
                    if scope is not None and scope not in scopes:
                        scopes.append(scope)
                # Lo que hace falta para planificar el resto se baja ya (son pocas páginas):
                # tier lists de cada ámbito y los counters de tus campeones.
                for scope in scopes:
                    for patch in scope.patches:
                        for role in ROLES:
                            if self.stop_event.is_set() or self.paused_reason():
                                return
                            self._phase(f"Tier list {role.lower()} · parche {patch}")
                            if opgg is not None and opgg.enabled:
                                opgg.tier_list(role, scope.tier, scope.region, patch)
                            elif lola is not None and lola.enabled:
                                lola.tier_list(role, scope.tier, scope.region, patch)
                if scopes and opgg is not None and opgg.enabled:
                    for champ, role in user:
                        self._phase(f"Tus counters: {svc.dd.name(champ)}")
                        opgg.counters(champ, role, scopes[0].tier, scopes[0].region, scopes[0].patches[0])
            finally:
                inline_refresh.reset(token)

        primary = scopes[0] if scopes else None

        # ----- 1) Tú: tus campeones, tus builds y tus matchups.
        for champ, role in user:
            name = svc.dd.name(champ)
            if opgg is not None and opgg.enabled:
                tasks.append(("opgg-mcp", ("build", champ, role), lambda c=champ, r=role: opgg.champion_build(c, r), P_USER, f"Build de {name}"))
                for scope in scopes[:2]:
                    for patch in scope.patches:
                        tasks.append(
                            ("opgg-web", ("counters", champ, role, scope.tier, scope.region, patch),
                             lambda c=champ, r=role, s=scope, p=patch: opgg.counters(c, r, s.tier, s.region, p), P_USER, f"Counters de {name}")
                        )
                if primary is not None:
                    for opp in self._top_opponents(champ, role, primary, limit=8):
                        tasks.append(
                            ("opgg-mcp", ("guide", champ, opp, role),
                             lambda c=champ, o=opp, r=role: opgg.matchup_guide(c, o, r), P_USER, f"Guía {name} vs {svc.dd.name(opp)}")
                        )
            if lola is not None and lola.enabled and primary is not None:
                tasks.append(
                    ("lolalytics", ("counters", champ, role, primary.tier, primary.region, primary.patches[0]),
                     lambda c=champ, r=role, s=primary: lola.counters(c, r, s.tier, s.region, s.patches[0]), P_USER, f"Lolalytics {name}")
                )

        # ----- 2) Lo esencial de cada ámbito: tier lists y counters.
        opgg_on = opgg is not None and opgg.enabled
        for index, scope in enumerate(scopes):
            prio = P_CORE if index == 0 else P_EXTRA
            for patch in scope.patches if opgg_on else ():
                for role in ROLES:
                    tasks.append(
                        ("opgg-web", ("tier", role, scope.tier, scope.region, patch),
                         lambda r=role, s=scope, p=patch: opgg.tier_list(r, s.tier, s.region, p), prio - 1, f"Tier list {role} {patch}")
                    )
            for patch_index, patch in enumerate(scope.patches if opgg_on else ()):
                for role in ROLES:
                    for champ in self._role_champions(scope, role, patch):
                        tasks.append(
                            ("opgg-web", ("counters", champ, role, scope.tier, scope.region, patch),
                             lambda c=champ, r=role, s=scope, p=patch: opgg.counters(c, r, s.tier, s.region, p),
                             prio + patch_index, f"Counters {svc.dd.name(champ)} {role} {patch}")
                        )
            if lola is not None and lola.enabled:
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
                             prio + 5, f"Lolalytics {svc.dd.name(champ)} {role}")
                        )

        # ----- 3) Extras: tipo de daño y builds (draft y tu build), sinergias y guías populares.
        if opgg is not None and opgg.enabled and primary is not None:
            patch = primary.patches[0]
            for role in ROLES:
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
        except Exception as exc:  # noqa: BLE001  (sin tus campeones, la precarga sigue con lo esencial)
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

    def __exit__(self, *exc):
        budget._current.reset(self._token)
        return False
