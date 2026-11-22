"""Draft en vivo con el cliente de LoL.

Mientras estás en la selección de campeones, cada segundo se lee la sesión del cliente (LCU) y se convierte en un
draft ``{rol: campeón}`` para los dos equipos. La web lo pinta y lo analiza en cuanto cambia. Los roles de tu equipo
vienen del cliente (en las colas con posiciones); los del rival, y los tuyos en las colas sin posiciones, se deducen
con el reparto de roles más probable según en qué línea se juega cada campeón (y quién lleva Aplastar).

Cuando están elegidos los 10 (o, en las colas a ciegas, cuando llega la fase final con tu campeón fijado) se importa
la build del Draft para tu campeón: runas, hechizos (con Aplastar —Castigo, en inglés Smite— en la tecla que prefieras) y un conjunto de
objetos. Una vez por partida y campeón: si cambias de campeón en la fase final, se vuelve a importar.

Runas: siempre en **la página con «TD» en el nombre** (TempuDrafter), que se sobrescribe en su mismo hueco y se renombra
«Campeón TD». Si no hay ninguna y queda un hueco libre, se crea ahí. Tus otras páginas nunca se tocan.
"""

from __future__ import annotations

import itertools
import json
import math
import re
import threading
import time
import uuid

from app import lcu
from app.logbus import get_logger

log = get_logger("draft")

ROLES = ("TOP", "JUNGLE", "MID", "ADC", "SUPPORT")
POSITION_ROLE = {"top": "TOP", "jungle": "JUNGLE", "middle": "MID", "mid": "MID", "bottom": "ADC", "utility": "SUPPORT", "support": "SUPPORT"}
SMITE = 11
FLASH = 4
PAGE_PREFIX = "Tempu"  # las de versiones anteriores («Tempu · Hecarim»): también son de la app
SET_PREFIX = "Tempu"
TD_MARK = re.compile(r"(?:^|[^A-Za-z0-9])TD(?:$|[^A-Za-z0-9])")


def is_app_page(name) -> bool:
    """¿Es la página (o el conjunto) de la app? Lleva «TD» como palabra en el nombre, o es una antigua «Tempu …»."""
    name = str(name or "")
    return bool(TD_MARK.search(name)) or name.startswith(PAGE_PREFIX)


class NoRuneSlot(Exception):
    pass


def write_rune_page(client, page: dict) -> str:
    """Escribe la página en la de la app («TD»): ``updated`` (sobrescrita en su hueco) o ``created`` (en un hueco libre)."""
    pages = [p for p in client.rune_pages() if isinstance(p, dict)]
    editable = [p for p in pages if p.get("isEditable", True) and p.get("isDeletable", True)]
    ours = sorted((p for p in editable if is_app_page(p.get("name"))), key=lambda p: (not TD_MARK.search(str(p.get("name") or "")), p.get("id", 0)))
    if ours:
        target = ours[0]
        try:
            client.update_rune_page(target["id"], page)
        except lcu.LCUError:  # algunas versiones del cliente no dejan editar: se rehace en el mismo hueco
            client.delete_rune_page(target["id"])
            target = client.create_rune_page(page) or target
        _make_current(client, target.get("id"))
        return "updated"
    slots = client.rune_page_slots()
    if slots is not None and len(editable) >= slots:
        raise NoRuneSlot
    created = client.create_rune_page(page) or {}
    _make_current(client, created.get("id"))
    return "created"


def _make_current(client, page_id) -> None:
    if not page_id:
        return
    try:
        client.set_current_rune_page(page_id)
    except lcu.LCUError:
        log.debug("No se pudo marcar la página de runas como la actual", exc_info=True)
POLL_IN_SELECT_S = 1.0
POLL_IDLE_S = 3.0
DEFAULT_SHARDS = [5008, 5008, 5011]  # adaptativo, adaptativo, vida: lo más jugado cuando OP.GG no los da
PREFS_KEY = "draft_live_prefs"
DEFAULT_PREFS = {"enabled": True, "auto_import": True, "runes": True, "spells": True, "items": True, "smite_key": "F"}


# ------------------------------------------------------------------ preferencias
def get_prefs(db) -> dict:
    try:
        saved = json.loads(db.meta_get(PREFS_KEY) or "{}")
    except ValueError:
        saved = {}
    prefs = {**DEFAULT_PREFS, **{k: v for k, v in saved.items() if k in DEFAULT_PREFS}}
    if prefs["smite_key"] not in ("D", "F"):
        prefs["smite_key"] = DEFAULT_PREFS["smite_key"]
    return prefs


def save_prefs(db, changes: dict) -> dict:
    prefs = get_prefs(db)
    for key, value in changes.items():
        if key not in DEFAULT_PREFS or value is None:
            continue
        if key == "smite_key":
            value = str(value).upper()
            if value not in ("D", "F"):
                continue
        else:
            value = bool(value)
        prefs[key] = value
    db.meta_set(PREFS_KEY, json.dumps(prefs))
    return prefs


def order_spells(spells: list[int], smite_key: str) -> list[int] | None:
    """Los dos hechizos en el orden D, F con Aplastar (el 11) en la tecla elegida (el otro, en la contraria)."""
    spells = [int(s) for s in spells if s][:2]
    if len(spells) != 2 or spells[0] == spells[1]:
        return None
    if SMITE in spells:
        other = spells[0] if spells[1] == SMITE else spells[1]
        return [SMITE, other] if smite_key == "D" else [other, SMITE]
    return spells


# ------------------------------------------------------------------ roles
def assign_roles(champs: list[int], share, fixed: dict[int, str] | None = None, smite: set[int] | None = None) -> dict[str, int]:
    """Reparto de roles más probable para unos campeones (≤ 5) del mismo equipo.

    ``share(champ, role)``: fracción de las partidas del campeón que se juegan en ese rol (``None`` si no se sabe).
    ``fixed``: roles ya conocidos (los del cliente). Quien lleva Aplastar va a la jungla.
    """
    fixed = dict(fixed or {})
    smite = smite or set()
    out = {role: champ for champ, role in fixed.items() if role in ROLES}
    free_roles = [r for r in ROLES if r not in out]
    free = [c for c in champs if c not in fixed]
    if not free:
        return out

    def weight(champ: int, role: str) -> float:
        if champ in smite:
            return 0.0 if role == "JUNGLE" else -6.0
        value = share(champ, role)
        if value is None:
            return math.log(0.05)
        return math.log(max(float(value), 0.002))

    best, best_score = None, -math.inf
    for roles in itertools.permutations(free_roles, len(free)):
        score = sum(weight(c, r) for c, r in zip(free, roles))
        if score > best_score:
            best, best_score = roles, score
    for champ, role in zip(free, best or ()):
        out[role] = champ
    return out


# ------------------------------------------------------------------ sesión
def parse_session(session: dict, share) -> dict:
    """Sesión del cliente -> draft: ``ally`` / ``enemy`` ``{rol: campeón}``, tú, baneos y si ya está completo."""
    my_cell = session.get("localPlayerCellId")
    locked: dict[int, int] = {}
    for turn in session.get("actions") or []:
        for action in turn if isinstance(turn, list) else [turn]:
            if action.get("type") == "pick" and action.get("completed") and action.get("championId"):
                locked[action.get("actorCellId")] = int(action["championId"])
    bans = {"ally": [], "enemy": []}
    for turn in session.get("actions") or []:
        for action in turn if isinstance(turn, list) else [turn]:
            if action.get("type") == "ban" and action.get("completed") and action.get("championId"):
                bans["ally" if action.get("isAllyAction") else "enemy"].append(int(action["championId"]))

    def team(members: list[dict], ally: bool) -> tuple[dict[str, int], list[dict]]:
        rows = []
        for m in members or []:
            cell = m.get("cellId")
            champ = int(m.get("championId") or 0) or locked.get(cell, 0)
            hover = 0
            if not champ and ally:
                hover = int(m.get("championPickIntent") or 0)
            role = POSITION_ROLE.get(str(m.get("assignedPosition") or "").lower())
            # Tu equipo: fijado si su acción de elegir está completada. Del rival solo se ve lo ya fijado.
            is_locked = (cell in locked) if ally else bool(champ)
            rows.append({"cell": cell, "champ": champ or hover, "locked": is_locked, "hover": bool(hover), "role": role,
                         "spells": [int(m.get("spell1Id") or 0), int(m.get("spell2Id") or 0)]})
        picked = [r for r in rows if r["champ"]]
        known = {r["champ"]: r["role"] for r in picked if r["role"]}
        if len(known) != len(set(known.values())):  # posiciones repetidas (modos raros): se deducen
            known = {}
        smite = {r["champ"] for r in picked if SMITE in r["spells"]}
        roles = assign_roles([r["champ"] for r in picked], share, fixed=known, smite=smite)
        by_champ = {c: role for role, c in roles.items()}
        for r in rows:
            r["role"] = by_champ.get(r["champ"], r["role"])
        return roles, rows

    ally, ally_rows = team(session.get("myTeam") or [], True)
    enemy, enemy_rows = team(session.get("theirTeam") or [], False)
    me = next((r for r in ally_rows if r["cell"] == my_cell), None)
    phase = ((session.get("timer") or {}).get("phase") or "").upper()
    all_locked = len(ally_rows) == 5 and len(enemy_rows) == 5 and all(r["champ"] and r["locked"] for r in ally_rows + enemy_rows)
    my_locked = bool(me and me["champ"] and me["locked"])
    complete = my_locked and (all_locked or phase in ("FINALIZATION", "GAME_STARTING"))
    return {
        "ally": ally,
        "enemy": enemy,
        "hovers": [r["champ"] for r in ally_rows if r["hover"]],
        "me": {"role": me["role"], "champ": me["champ"], "locked": my_locked, "cell": my_cell} if me else None,
        "bans": bans,
        "phase": phase,
        "complete": complete,
        "blind": not enemy_rows,  # a ciegas el cliente no manda al equipo rival (en las colas con draft, sí: vacío al principio)
        "game_id": session.get("gameId"),
    }


# ------------------------------------------------------------------ importar
def _item_set(champ: int, title: str, rec: dict) -> dict:
    def block(name: str, items: list[int]) -> dict | None:
        seen, rows = set(), []
        for i in items:
            if i and i not in seen:
                seen.add(i)
                rows.append({"id": str(int(i)), "count": 1})
        return {"type": name, "items": rows} if rows else None

    build = rec.get("build") or []
    blocks = [
        block("Inicio", rec.get("starter") or []),
        block("Núcleo", [b["item"] for b in build if b.get("kind") == "core"]),
        block("Botas", [(rec.get("boots") or {}).get("item")] if rec.get("boots") else []),
        block("Resto de la build", [b["item"] for b in build if b.get("kind") != "core"]),
        block("De equipo (support)", [t["item"] for t in rec.get("team_items") or []]),
        block("Alternativas", [a["item"] for a in rec.get("alternatives") or []]),
    ]
    return {
        "associatedChampions": [int(champ)],
        "associatedMaps": [11],
        "blocks": [b for b in blocks if b],
        "map": "SR",
        "mode": "any",
        "preferredItemSlots": [],
        "sortrank": 0,
        "startedFrom": "blank",
        "title": title,
        "type": "custom",
        "uid": str(uuid.uuid4()),
    }


def _rune_page(rec_runes: dict | None, fallback: dict | None, name: str) -> dict | None:
    runes = rec_runes or fallback
    if not runes or not runes.get("primary") or not runes.get("secondary") or len(runes.get("ids") or []) != 6:
        return None
    shards = runes.get("shards") or []
    if len(shards) != 3 and fallback and fallback.get("keystone") == runes.get("keystone"):
        shards = fallback.get("shards") or []
    if len(shards) != 3:
        shards = DEFAULT_SHARDS
    return {"name": name, "primaryStyleId": int(runes["primary"]), "subStyleId": int(runes["secondary"]),
            "selectedPerkIds": [int(i) for i in runes["ids"]] + [int(s) for s in shards], "current": True}


SPELL_TRIES = 3


def set_spells_checked(client, spells: list[int]) -> list[int] | None:
    """Pone los hechizos y comprueba en la selección que se han aplicado (si no, lo reintenta). ``None`` si no entran."""
    for attempt in range(SPELL_TRIES):
        client.set_summoner_spells(*spells)
        reader = getattr(client, "my_spells", None)
        if reader is None:
            return spells  # sin forma de leerlos: se da por bueno
        try:
            current = reader()
        except lcu.LCUError:
            return spells
        if current is None or list(current) == list(spells):
            return spells
        log.info("El cliente no aplicó los hechizos %s (tiene %s): reintento %d", spells, current, attempt + 1)
        time.sleep(0.4)
    return None


def spells_for(svc, rec: dict, champ: int, role: str) -> tuple[list[int], str | None]:
    """Hechizos para importar: los de la build; si faltan (OP.GG aún no había respondido), OP.GG sin límite de tiempo;
    si tampoco, tus hechizos de siempre con ese campeón. Devuelve ``(hechizos, fuente)``."""
    spells = [int(x) for x in rec.get("spells") or [] if x]
    if len(spells) == 2:
        return spells, "build"
    opgg = getattr(svc, "opgg", None)
    if opgg is not None and getattr(opgg, "enabled", False):
        try:
            general = opgg.champion_build(champ, role, allow_fetch=True) or {}
        except Exception:
            log.debug("Hechizos de OP.GG", exc_info=True)
            general = {}
        if len(general.get("spells") or []) == 2:
            return [int(x) for x in general["spells"]], "opgg"
    from app.coach import store

    mine = store.usual_spells(svc.db, champ)
    if mine:
        return mine, "tuyos"
    return [], None


def apply_import(client, rec: dict, champ_name: str, opponent_name: str | None, prefs: dict, extra_runes: dict | None = None) -> dict:
    """Escribe en el cliente lo que toque según ``prefs``. ``client``: el módulo ``lcu`` (o un doble en los tests)."""
    done: dict = {"runes": None, "spells": None, "items": None, "errors": []}
    title = f"{champ_name} TD" + (f" vs {opponent_name}" if opponent_name else "")
    if prefs.get("runes"):
        page = _rune_page(rec.get("runes"), extra_runes, f"{champ_name[:22]} TD")  # el cliente admite 25 caracteres
        if page is None:
            done["errors"].append("Sin página de runas recomendada para este campeón")
        else:
            try:
                done["rune_page"] = write_rune_page(client, page)
                done["runes"] = page["selectedPerkIds"]
            except NoRuneSlot:
                done["errors"].append("Runas: no hay ninguna página con «TD» en el nombre ni huecos libres. Pon «TD» en el nombre de una de "
                                      "tus páginas y la app escribirá siempre ahí, sin tocar las demás.")
            except lcu.LCUError as exc:
                done["errors"].append(f"Runas: {exc}")
    if prefs.get("spells"):
        spells = order_spells(rec.get("spells") or [], prefs.get("smite_key", "F"))
        if spells is None:
            done["errors"].append("Sin hechizos recomendados para este campeón")
        else:
            try:
                done["spells"] = set_spells_checked(client, spells)
                if done["spells"] is None:
                    done["errors"].append("Hechizos: el cliente no los aceptó (pruébalo con «Importar build ya»)")
            except lcu.LCUError as exc:
                done["errors"].append(f"Hechizos: {exc}")
    if prefs.get("items"):
        try:
            sid = client.summoner_id()
            payload = client.item_sets(sid) or {}
            sets = [s for s in payload.get("itemSets") or [] if not is_app_page(s.get("title"))]
            new = _item_set(rec["champ"], title[:75], rec)
            sets.append(new)
            client.save_item_sets(sid, {**payload, "accountId": payload.get("accountId", sid), "itemSets": sets, "timestamp": int(time.time() * 1000)})
            done["items"] = sum(len(b["items"]) for b in new["blocks"])
        except lcu.LCUError as exc:
            done["errors"].append(f"Objetos: {exc}")
    return done


# ------------------------------------------------------------------ perfil
AUTO_PROFILE_KEY = "profile_autodetect"  # "0" si borraste tu perfil a propósito: no se vuelve a crear solo
AUTO_PROFILE_EVERY_S = 60


def ensure_profile(svc, client=lcu) -> dict | None:
    """Sin perfil guardado y con el cliente abierto, guarda solo tu cuenta (la del cliente). Devuelve el perfil nuevo."""
    from app.coach import store
    from app.coach.benchmarks import DEFAULT_TARGET
    from app.coach.tracker import ProfileNotFound, resolve_account
    from app.riot.client import RiotAPIError, RiotKeyError

    if store.get_profile(svc.db) or svc.db.meta_get(AUTO_PROFILE_KEY) == "0":
        return None
    account = client.current_summoner()
    if not account.get("platform"):
        return None
    resolved: dict = {}
    if svc.keys.get() and svc.keys.status != "invalid":
        try:
            resolved = resolve_account(svc, account["game_name"], account["tag_line"], account["platform"])
        except (ProfileNotFound, RiotKeyError, RiotAPIError):
            resolved = {}  # se vincula solo cuando Riot responda (como al guardarlo a mano)
    profile = store.save_profile(
        svc.db,
        game_name=resolved.get("game_name") or account["game_name"],
        tag_line=resolved.get("tag_line") or account["tag_line"],
        platform=resolved.get("platform") or account["platform"],
        puuid=resolved.get("puuid"),
        profile_icon=resolved.get("profile_icon") or account.get("profile_icon"),
        summoner_level=resolved.get("summoner_level") or account.get("summoner_level"),
        target_tier=DEFAULT_TARGET,
    )
    tracker = getattr(svc, "tracker", None)
    if tracker is not None:
        tracker.trigger()
    log.info("Perfil detectado en el cliente de LoL: %s#%s (%s).", profile["game_name"], profile["tag_line"], profile["platform"])
    return profile


# ------------------------------------------------------------------ vigilante
class DraftLive:
    def __init__(self, svc) -> None:
        self.svc = svc
        self.stop_event = threading.Event()
        self._thread: threading.Thread | None = None
        self._lock = threading.Lock()
        self._imported: tuple | None = None
        self._share_for: tuple | None = None  # (partida, función): una consulta de roles por selección, no por segundo
        self._next_profile = 0.0
        self.state: dict = {"active": False, "client": False, "draft": None, "import": None, "updated": None, "error": None}

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self.stop_event = threading.Event()
        self._thread = threading.Thread(target=self._run, name="draft-live", daemon=True)
        self._thread.start()

    def shutdown(self) -> None:
        self.stop_event.set()

    def status(self) -> dict:
        return {**self.state, "prefs": get_prefs(self.svc.db)}

    def _run(self) -> None:
        while not self.stop_event.is_set():
            wait = POLL_IDLE_S
            try:
                if get_prefs(self.svc.db)["enabled"] and lcu.available():
                    wait = self.poll()
                elif self.state["active"]:
                    self.state.update(active=False, draft=None)
            except Exception as exc:  # el cliente es opcional: nunca tumbar el hilo
                self.state.update(error=str(exc))
                log.debug("Draft en vivo: %s", exc, exc_info=True)
            self.stop_event.wait(wait)

    def _share(self):
        from app.analytics.evidence import Evidence

        ev = Evidence(self.svc, _filters(self.svc), fetch=False)
        cache: dict = {}

        def share(champ: int, role: str) -> float | None:
            key = (champ, role)
            if key not in cache:
                try:
                    cache[key] = ev.presence(role, champ).get("share")
                except Exception:  # noqa: BLE001  (sin datos de ese campeón: el reparto lo trata como «no se sabe»)
                    cache[key] = None
            return cache[key]

        return share

    def poll(self) -> float:
        phase = lcu.gameflow_phase()
        self.state["client"] = phase is not None
        if phase is not None and time.time() >= self._next_profile:
            self._next_profile = time.time() + AUTO_PROFILE_EVERY_S
            try:
                if ensure_profile(self.svc):
                    self.state["profile_detected"] = time.time()
            except lcu.LCUError:
                log.debug("El cliente aún no ha cargado tu cuenta", exc_info=True)
        if phase != "ChampSelect":
            if self.state["active"]:
                self.state.update(active=False)
            return POLL_IDLE_S
        session = lcu.champ_select_session()
        if not session:
            return POLL_IN_SELECT_S
        if not self._share_for or self._share_for[0] != session.get("gameId"):
            self._share_for = (session.get("gameId"), self._share())
        draft = parse_session(session, self._share_for[1])
        changed = draft != self.state.get("draft")
        self.state.update(active=True, draft=draft, error=None)
        if changed:
            self.state["updated"] = time.time()
        prefs = get_prefs(self.svc.db)
        me = draft.get("me") or {}
        key = (draft.get("game_id"), me.get("champ"), tuple(sorted(draft["enemy"].items())))
        if draft["complete"] and prefs["auto_import"] and key != self._imported and me.get("role"):
            self._imported = key
            self.import_now(draft)
        return POLL_IN_SELECT_S

    def import_now(self, draft: dict | None = None, f=None) -> dict:
        """Importa la build del Draft para tu campeón (también a mano, desde la web)."""
        from app.analytics import builds

        with self._lock:
            draft = draft or self.state.get("draft")
            me = (draft or {}).get("me") or {}
            if not draft or not me.get("champ") or not me.get("role"):
                result = {"status": "error", "errors": ["Aún no hay campeón tuyo en la selección"], "at": time.time()}
                self.state["import"] = result
                return result
            svc = self.svc
            ally, enemy = dict(draft["ally"]), dict(draft["enemy"])
            ally[me["role"]] = me["champ"]
            rec = next((p for p in builds.recommend(svc, f or _filters(svc), ally, enemy)["ally"] if p["champ"] == me["champ"]), None)
            if rec is None:
                result = {"status": "error", "errors": ["No hay build para tu campeón"], "at": time.time()}
                self.state["import"] = result
                return result
            extra = None
            opgg = getattr(svc, "opgg", None)
            if opgg is not None and getattr(opgg, "enabled", False):
                try:
                    extra = opgg.rune_page(me["champ"], me["role"])
                except Exception:
                    log.debug("Página de runas de OP.GG", exc_info=True)
                    extra = None
            spells, source = spells_for(svc, rec, me["champ"], me["role"])
            rec = {**rec, "spells": spells}
            name = svc.dd.name(me["champ"])
            opp = rec.get("opponent")
            done = apply_import(lcu, rec, name, svc.dd.name(opp) if opp else None, get_prefs(svc.db), extra)
            ok = any(done[k] is not None for k in ("runes", "spells", "items"))
            result = {"status": "ok" if ok and not done["errors"] else "partial" if ok else "error", "champ": me["champ"], "role": me["role"],
                      "opponent": opp, "spells_source": source, **done, "at": time.time()}
            self.state["import"] = result
            log.info("Build de %s importada al cliente (%s): runas %s, hechizos %s (%s), %s objetos%s.", name, result["status"],
                     "sí" if done["runes"] else "no", done["spells"], source, done["items"],
                     f"; avisos: {' · '.join(done['errors'])}" if done["errors"] else "")
            return result


def _filters(svc):
    """Los filtros que usaste por última vez en la web (elo, región, parche); si no, los de por defecto."""
    from app.analytics.filters import Filters

    warmer = getattr(svc, "warmer", None)
    scopes = getattr(warmer, "_scopes", None) or {}
    if scopes:
        last = max(scopes.values(), key=lambda s: s.get("last_used") or 0)
        try:
            return Filters.parse(svc.db, patch=last.get("patch", "all"), tier=last.get("tier", "all"), platform=last.get("platform", "all"))
        except (ValueError, TypeError):  # filtros guardados que ya no valen: los de por defecto
            log.debug("Filtros guardados no válidos: %s", last, exc_info=True)
    return Filters.parse(svc.db, patch="all", tier="all", platform="all")
