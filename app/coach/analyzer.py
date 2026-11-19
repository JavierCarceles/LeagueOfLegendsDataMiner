"""Análisis de una partida propia a partir de match-v5 y su timeline.

No hace falta grabar la repetición: el timeline de Riot trae la posición de
los 10 jugadores al final de cada minuto y todos los eventos con su
coordenada (kills, objetivos, torres, compras, wards, subidas de nivel).
Con eso se reconstruye por dónde se movió cada uno y se detectan errores
concretos, **teniendo en cuenta el contexto**:

* **Muertes** clasificadas: gank (sobre todo del jungla rival), 1 contra 1,
  adelantado en la línea, sin ward en los 2 minutos previos, con oro que
  podías gastar en tu build, justo antes de un objetivo rival, en la jungla
  rival o aislado. Se mira cómo acabó la pelea (si la ganasteis, no es lo
  mismo) y si tu equipo cogió algo a cambio.
* **Oro**: se reconstruye tu inventario. Solo es «oro parado» el que te daba
  para algo de tu build y con hueco para comprarlo. Con el inventario lleno
  ahorrando para un objeto completo, con la build terminada, en pleno
  objetivo o en el empujón final, no es un error.
* **Objetivos**: si el rival coge uno y estabas lejos, se mide qué sacó tu
  equipo a cambio (kills, torres, placas, su jungla, otro objetivo). Un buen
  cambio es una buena decisión; regalarlo sin sacar nada, no.
* **Build**: orden y minutos de tus objetos, compras por vuelta a base,
  inventario lleno de componentes, la R a tiempo, Heridas graves contra
  curanderos y resistencias frente al daño que recibiste.
* **Tiempo**: minutos vacíos y desconexiones (AFK), que no cuentan como errores
  de juego.
* **Pathing**, **visión**, **objetivos** y **fase de líneas** (CS, oro y
  experiencia frente a tu rival a los 10 y 15 minutos).

La función principal es pura (sin BD ni red):
``analyze_game(match, timeline, pid=…, items=ItemCatalog)``. Sin catálogo de
objetos se usan umbrales de oro fijos. Si aún no hay timeline se calcula lo
que permite el resumen de la partida.
"""

from __future__ import annotations

import bisect
from collections import Counter

from app.coach import geometry as geo
from app.coach.items import Inventory, ItemCatalog
from app.riot.routing import QUEUES, TEAM_POSITION_TO_ROLE

ANALYSIS_VERSION = 2

EARLY_MS = 14 * 60_000
IDLE_FROM_MS = 15 * 60_000
HOARD_GOLD = 1500  # sin catálogo de objetos: umbral fijo
DEATH_GOLD = 1200  # sin catálogo de objetos: umbral fijo
USEFUL_SPEND = 1000  # compra mínima de tu build que justifica volver a base
GENERIC_COMPONENT = 1100  # sin saber tu siguiente objeto: lo que cuesta un componente medio
HOARD_GRACE_MS = 45_000  # margen para acabar la oleada o el campamento y volver
FINAL_PUSH_MS = 90_000
WARD_WINDOW_MS = 120_000
OBJECTIVE_WINDOW_MS = 90_000
TRADE_BEFORE_MS = 75_000
TRADE_AFTER_MS = 40_000
FIGHT_GAP_MS = 20_000
FIGHT_RADIUS = 3200
NEAR_OBJECTIVE = 2600
FAR_FROM_OBJECTIVE = 5500
ISOLATED_RADIUS = 2200
AFK_MIN_FRAMES = 2
SMITE = 11
CONTROL_WARD = 2055
HEAVY_HEAL_MIN = 650  # curación por minuto (propia + aliados) para considerar a un rival «curandero»
R_LATE_S = 30
NO_R_CHECK = {43, 60, 76, 77, 126, 910}  # Karma, Elise, Nidalee, Udyr, Jayce, Hwei: su R no sigue la regla 6/11/16
NO_BOOTS = {69}  # Cassiopeia no puede comprar botas

# Tiempo base de reaparición por nivel (s) y factor por minuto de partida.
BRW = [10, 10, 12, 12, 14, 16, 20, 25, 28, 32.5, 35, 37.5, 40, 42.5, 45, 47.5, 50, 52.5]

MONSTERS = {"DRAGON": "dragon", "BARON_NASHOR": "baron", "RIFTHERALD": "herald", "HORDE": "grubs", "ATAKHAN": "atakhan"}
OBJ_LABEL = {
    "dragon": "el dragón",
    "elder": "el dragón ancestral",
    "baron": "el barón",
    "herald": "el heraldo",
    "grubs": "las larvas",
    "atakhan": "Atakhan",
}
# Valor aproximado en oro de equipo (para comparar cambios de objetivos).
OBJ_VALUE = {"dragon": 1000, "elder": 3000, "baron": 3000, "herald": 900, "grubs": 250, "atakhan": 1500}
BUILDING_VALUE = {"tower": 650, "inhibitor": 900, "plate": 160}
ENEMY_JUNGLE_CS_VALUE = 25
GOOD_TRADE = 0.85
FAIR_TRADE = 0.45
ROLE_LABEL = {"TOP": "top", "JUNGLE": "jungla", "MID": "mid", "ADC": "ADC", "SUPPORT": "support"}
SKILL_KEYS = {1: "Q", 2: "W", 3: "E", 4: "R"}


class NotInMatch(ValueError):
    """El jugador del perfil no aparece en la partida."""


def time_impact(minutes: float) -> float:
    """Aumento del tiempo de muerte según el minuto (0 % antes del 15, tope 50 %)."""
    if minutes < 15:
        return 0.0
    if minutes <= 30:
        return 0.00425 * (minutes - 15) * 2
    if minutes <= 45:
        return 0.1275 + 0.003 * (minutes - 30) * 2
    return min(0.5, 0.2175 + 0.0145 * (minutes - 45) * 2)


def respawn_seconds(level: int, minutes: float) -> float:
    return BRW[max(0, min(17, level - 1))] * (1 + time_impact(minutes))


def duration_seconds(info: dict) -> int:
    duration = int(info.get("gameDuration") or 0)
    if "gameEndTimestamp" not in info and duration > 10_000:  # partidas anteriores a 11.20 (ms)
        duration //= 1000
    return duration


def player_role(p: dict) -> str | None:
    """Rol interno (TOP, JUNGLE, MID, ADC, SUPPORT) según ``teamPosition``."""
    return TEAM_POSITION_TO_ROLE.get((p.get("teamPosition") or "").upper()) or TEAM_POSITION_TO_ROLE.get(
        (p.get("individualPosition") or "").upper()
    )


def _pos(obj: dict | None) -> tuple[int, int] | None:
    if not isinstance(obj, dict):
        return None
    p = obj.get("position")
    if not isinstance(p, dict) or "x" not in p or "y" not in p:
        return None
    return int(p["x"]), int(p["y"])


def _mmss(ms: float) -> str:
    s = int(ms // 1000)
    return f"{s // 60}:{s % 60:02d}"


def _secs(ms: float) -> str:
    s = round(ms / 1000)
    return f"{s} s" if s < 60 else f"{s // 60} min {s % 60:02d} s"


def _int(value: float) -> str:
    return f"{round(value):,}".replace(",", ".")


def _num(value, default=0):
    try:
        return type(default)(value) if value is not None else default
    except (TypeError, ValueError):
        return default


def _plural(n: int, one: str, many: str) -> str:
    return f"{n} {one if n == 1 else many}"


def _sentence(text: str) -> str:
    text = text.strip()
    return (text[:1].upper() + text[1:] + ("" if text.endswith(".") else ".")) if text else ""


def _join(parts: list[str]) -> str:
    parts = [p for p in parts if p]
    if len(parts) <= 1:
        return "".join(parts)
    return ", ".join(parts[:-1]) + " y " + parts[-1]


def find_me(match: dict, *, pid: int | None = None, puuid: str | None = None, game_name: str | None = None, tag_line: str | None = None) -> dict:
    parts = (match.get("info") or {}).get("participants") or []
    if pid:
        found = next((p for p in parts if _num(p.get("participantId")) == pid), None)
        if found:
            return found
    if puuid:
        found = next((p for p in parts if p.get("puuid") == puuid), None)
        if found:
            return found
    if game_name:
        name, tag = game_name.casefold(), (tag_line or "").casefold()
        found = next(
            (p for p in parts if (p.get("riotIdGameName") or "").casefold() == name and (not tag or (p.get("riotIdTagline") or "").casefold() == tag)),
            None,
        )
        if found:
            return found
    raise NotInMatch("El jugador no aparece en esta partida")


class _Game:
    """Datos de la partida indexados para consultas rápidas."""

    def __init__(self, match: dict, timeline: dict | None, me: dict, catalog: ItemCatalog | None = None) -> None:
        info = match.get("info") or {}
        self.info = info
        self.duration = duration_seconds(info)
        self.duration_ms = self.duration * 1000
        self.minutes = max(self.duration, 60) / 60
        self.raw = {}
        self.players: dict[int, dict] = {}
        roles_seen: Counter = Counter()
        me_pid = None
        for index, p in enumerate(info.get("participants") or [], start=1):
            pid = _num(p.get("participantId"), index) or index
            role = player_role(p)
            team = _num(p.get("teamId"))
            if p is me:
                me_pid = pid
            self.raw[pid] = p
            self.players[pid] = {
                "pid": pid,
                "team": team,
                "champ": _num(p.get("championId")),
                "role": role,
                "name": p.get("riotIdGameName") or p.get("summonerName") or "",
                "tag": p.get("riotIdTagline") or "",
                "smite": SMITE in (_num(p.get("summoner1Id")), _num(p.get("summoner2Id"))),
            }
            if role:
                roles_seen[(team, role)] += 1
        for pl in self.players.values():  # rol duplicado en un equipo: no es fiable
            if pl["role"] and roles_seen[(pl["team"], pl["role"])] > 1:
                pl["role"] = None
        if me_pid is None:
            raise NotInMatch("El jugador no aparece en esta partida")
        self.me = self.players[me_pid]
        self.team = self.me["team"]
        self.role = self.me["role"]
        self.won = bool(me.get("win"))
        self.allies = [p for p in self.players if self.players[p]["team"] == self.team and p != self.me["pid"]]
        self.enemies = [p for p in self.players if self.players[p]["team"] != self.team]
        self.opponent = self._by_role(300 - self.team, self.role) if self.role else None
        self.enemy_jungler = self._jungler(300 - self.team)
        self.ally_jungler = self._jungler(self.team)

        tl_info = (timeline or {}).get("info") or {}
        frames = tl_info.get("frames") or []
        self.has_timeline = bool(frames)
        self.ts: list[int] = [_num(f.get("timestamp")) for f in frames]
        self.pf: dict[int, list[dict | None]] = {pid: [] for pid in self.players}
        for f in frames:
            pfs = f.get("participantFrames") or {}
            for pid in self.players:
                self.pf[pid].append(pfs.get(str(pid)))
        events = [e for f in frames for e in (f.get("events") or []) if isinstance(e, dict)]
        events.sort(key=lambda e: _num(e.get("timestamp")))
        self.events = events
        self.catalog = catalog if catalog else None
        self._index_events()
        self._detect_afk()
        self.inv = Inventory(self.catalog, self.item_events[me_pid]) if self.catalog and self.has_timeline else None
        self.gold_rows: dict[int, dict] = {}
        self.hoard_frames: set[int] = set()

    # ----------------------------------------------------------- jugadores
    def _by_role(self, team: int, role: str | None) -> int | None:
        return next((pid for pid, p in self.players.items() if p["team"] == team and p["role"] == role), None)

    def _jungler(self, team: int) -> int | None:
        pid = self._by_role(team, "JUNGLE")
        if pid:
            return pid
        return next((pid for pid, p in self.players.items() if p["team"] == team and p["smite"]), None)

    def natural_opponents(self) -> set[int]:
        """Rivales con los que es normal pelear en la fase de líneas."""
        if self.role in ("ADC", "SUPPORT"):
            return {pid for pid in (self._by_role(300 - self.team, "ADC"), self._by_role(300 - self.team, "SUPPORT")) if pid}
        return {self.opponent} if self.opponent else set()

    # ------------------------------------------------------------- eventos
    def _index_events(self) -> None:
        self.levels: dict[int, list[tuple[int, int]]] = {pid: [] for pid in self.players}
        self.kills: list[dict] = []
        self.objectives: list[dict] = []
        self.buildings: list[dict] = []
        self.wards: dict[int, list[int]] = {pid: [] for pid in self.players}
        self.control_wards_placed: Counter = Counter()
        self.wards_killed: Counter = Counter()
        self.purchases: dict[int, list[tuple[int, int]]] = {pid: [] for pid in self.players}
        self.item_events: dict[int, list[dict]] = {pid: [] for pid in self.players}
        self.skill_ups: dict[int, list[tuple[int, int, str]]] = {pid: [] for pid in self.players}
        for e in self.events:
            kind = e.get("type")
            t = _num(e.get("timestamp"))
            if kind == "LEVEL_UP":
                pid = _num(e.get("participantId"))
                if pid in self.levels:
                    self.levels[pid].append((t, _num(e.get("level"), 1)))
            elif kind == "SKILL_LEVEL_UP":
                pid = _num(e.get("participantId"))
                if pid in self.skill_ups:
                    self.skill_ups[pid].append((t, _num(e.get("skillSlot")), e.get("levelUpType") or "NORMAL"))
            elif kind == "CHAMPION_KILL":
                victim = _num(e.get("victimId"))
                if victim not in self.players:
                    continue
                bounty = _num(e.get("bounty")) + _num(e.get("shutdownBounty"))
                self.kills.append(
                    {
                        "t": t,
                        "killer": _num(e.get("killerId")),
                        "victim": victim,
                        "assists": [_num(a) for a in e.get("assistingParticipantIds") or []],
                        "pos": _pos(e),
                        "value": max(300, min(1000, bounty)) if bounty else 300,
                    }
                )
            elif kind == "ELITE_MONSTER_KILL":
                monster = MONSTERS.get((e.get("monsterType") or "").upper())
                if not monster:
                    continue
                if monster == "dragon" and (e.get("monsterSubType") or "").upper() == "ELDER_DRAGON":
                    monster = "elder"
                killer = _num(e.get("killerId"))
                team = _num(e.get("killerTeamId")) or (self.players[killer]["team"] if killer in self.players else 0)
                if team not in (100, 200):
                    continue
                assists = [_num(a) for a in e.get("assistingParticipantIds") or []]
                last = self.objectives[-1] if self.objectives else None
                if monster == "grubs" and last and last["kind"] == "grubs" and last["team"] == team and t - last["t_end"] <= 120_000:
                    last["t_end"] = t
                    last["count"] += 1
                    last["takers"].update([killer, *assists])
                    continue
                self.objectives.append(
                    {
                        "t": t,
                        "t_end": t,
                        "kind": monster,
                        "team": team,
                        "pos": _pos(e) or (geo.PITS["baron"] if monster in ("baron", "herald", "grubs") else geo.PITS["dragon"]),
                        "takers": {killer, *assists},
                        "sub": e.get("monsterSubType"),
                        "assists_known": "assistingParticipantIds" in e,
                        "count": 1,
                    }
                )
            elif kind in ("BUILDING_KILL", "TURRET_PLATE_DESTROYED"):
                owner = _num(e.get("teamId"))
                self.buildings.append(
                    {
                        "t": t,
                        "owner": owner,
                        "kind": "plate" if kind == "TURRET_PLATE_DESTROYED" else ("tower" if e.get("buildingType") == "TOWER_BUILDING" else "inhibitor"),
                        "lane": (e.get("laneType") or "").replace("_LANE", "").lower(),
                        "takers": {_num(e.get("killerId")), *[_num(a) for a in e.get("assistingParticipantIds") or []]},
                        "pos": _pos(e),
                    }
                )
            elif kind == "WARD_PLACED":
                creator = _num(e.get("creatorId"))
                ward_type = (e.get("wardType") or "").upper()
                if creator in self.wards and ward_type not in ("UNDEFINED", "TEEMO_MUSHROOM", ""):
                    self.wards[creator].append(t)
                    if ward_type == "CONTROL_WARD":
                        self.control_wards_placed[creator] += 1
            elif kind == "WARD_KILL":
                killer = _num(e.get("killerId"))
                if killer in self.players:
                    self.wards_killed[killer] += 1
            elif kind in ("ITEM_PURCHASED", "ITEM_SOLD", "ITEM_UNDO", "ITEM_DESTROYED"):
                pid = _num(e.get("participantId"))
                if pid in self.item_events:
                    self.item_events[pid].append(e)
                    if kind == "ITEM_PURCHASED":
                        self.purchases[pid].append((t, _num(e.get("itemId"))))
        self._deaths()
        self._tracks()

    def level_at(self, pid: int, t: int) -> int:
        level = 1
        for ts, lvl in self.levels.get(pid) or []:
            if ts > t:
                break
            level = lvl
        if level == 1 and not self.levels.get(pid):  # sin eventos LEVEL_UP: el del frame anterior
            i = self.frame_before(t)
            if i is not None:
                level = _num((self.pf[pid][i] or {}).get("level"), 1)
        return level

    def _deaths(self) -> None:
        estimates: dict[int, list[list]] = {pid: [] for pid in self.players}
        for k in self.kills:
            seconds = respawn_seconds(self.level_at(k["victim"], k["t"]), k["t"] / 60_000)
            estimates[k["victim"]].append([k["t"], seconds, k["pos"]])
        self.dead: dict[int, list[tuple[int, int, tuple | None]]] = {}
        for pid, items in estimates.items():
            total = _num(self.raw[pid].get("totalTimeSpentDead"))
            est = sum(i[1] for i in items)
            factor = max(0.5, min(1.8, total / est)) if total and est else 1.0
            intervals = []
            for t, seconds, pos in items:
                end = min(t + int(seconds * factor * 1000), self.duration_ms or t + int(seconds * 1000))
                intervals.append((t, max(end, t + 1000), pos))
            self.dead[pid] = intervals

    def is_dead(self, pid: int, t: int) -> bool:
        return any(start <= t < end for start, end, _ in self.dead.get(pid) or [])

    def dead_overlap(self, pid: int, t0: int, t1: int) -> int:
        return sum(max(0, min(end, t1) - max(start, t0)) for start, end, _ in self.dead.get(pid) or [])

    def _detect_afk(self) -> None:
        """Minutos vivo sin moverse ni ganar experiencia ni hacer nada: desconexión o AFK."""
        self.afk: dict[int, list[tuple[int, int]]] = {pid: [] for pid in self.players}
        for pid in self.players:
            own = sorted([t for t, _ in self.purchases[pid]] + self.wards[pid] + [t for t, *_ in self.skill_ups[pid]])
            still = []
            for i in range(1, len(self.ts)):
                t0, t1 = self.ts[i - 1], self.ts[i]
                if t1 - t0 < 30_000:  # último frame (fin de partida)
                    continue
                a, b = self.frame_pos(pid, i - 1), self.frame_pos(pid, i)
                xp0, xp1 = self.fval(pid, i - 1, "xp"), self.fval(pid, i, "xp")
                if not a or not b or xp0 is None or xp1 is None:
                    continue
                if geo.dist(a, b) > 60 or xp1 - xp0 > 20 or self.dead_overlap(pid, t0, t1) > 0:
                    continue
                j = bisect.bisect_right(own, t0)
                if j < len(own) and own[j] <= t1:
                    continue
                still.append(i)
            for run in _runs(still):
                if len(run) < AFK_MIN_FRAMES:
                    continue
                end = self.ts[run[-1]]
                if run[-1] >= len(self.ts) - 2:  # quieto hasta el final
                    end = max(end, self.duration_ms)
                self.afk[pid].append((self.ts[run[0] - 1], end))

    def is_afk(self, pid: int, t: int) -> bool:
        return any(start <= t < end for start, end in self.afk.get(pid) or [])

    def afk_ms(self, pid: int) -> int:
        return sum(end - start for start, end in self.afk.get(pid) or [])

    def present(self, pid: int, t: int) -> bool:
        return not self.is_dead(pid, t) and not self.is_afk(pid, t)

    def _tracks(self) -> None:
        """Puntos de paso de cada jugador: frames + eventos con posición + tienda y reapariciones."""
        anchors: dict[int, list[tuple[int, int, int, int]]] = {pid: [] for pid in self.players}

        def add(pid, t, p, kind):
            if pid in anchors and p is not None:
                anchors[pid].append((t, int(p[0]), int(p[1]), kind))

        for pid in self.players:
            team = self.players[pid]["team"]
            for i, t in enumerate(self.ts):
                p = _pos(self.pf[pid][i])
                if p and not self.is_dead(pid, t):
                    add(pid, t, p, 0)
            for t, _item in self.purchases[pid]:
                if not self.is_dead(pid, t):
                    add(pid, t, geo.FOUNTAIN[team], 2)
            for start, end, pos in self.dead.get(pid) or []:
                add(pid, start, pos, 3)
                if end < self.duration_ms:
                    add(pid, end, geo.FOUNTAIN[team], 2)
        for k in self.kills:
            for pid in (k["killer"], *k["assists"]):
                if pid in self.players and not self.is_dead(pid, k["t"]):
                    add(pid, k["t"], k["pos"], 1)
        for o in self.objectives:
            if o["assists_known"]:
                for pid in o["takers"]:
                    if pid in self.players and not self.is_dead(pid, o["t"]):
                        add(pid, o["t"], o["pos"], 1)
        for b in self.buildings:
            for pid in b["takers"]:
                if pid in self.players and not self.is_dead(pid, b["t"]):
                    add(pid, b["t"], b["pos"], 1)
        self.anchors = {pid: sorted(a) for pid, a in anchors.items()}
        self._anchor_t = {pid: [a[0] for a in items] for pid, items in self.anchors.items()}

    def pos_at(self, pid: int, t: int) -> tuple[float, float] | None:
        """Posición estimada (interpolada) o ``None`` si estaba muerto o no se sabe."""
        if self.is_dead(pid, t):
            return None
        items = self.anchors.get(pid) or []
        if not items:
            return None
        times = self._anchor_t[pid]
        i = bisect.bisect_right(times, t)
        prev = items[i - 1] if i > 0 else None
        nxt = items[i] if i < len(items) else None
        if prev is not None and prev[3] == 3:  # justo antes de morir
            return prev[1], prev[2]
        if prev is None:
            return (nxt[1], nxt[2]) if nxt else None
        if nxt is None or nxt[0] == prev[0]:
            return prev[1], prev[2]
        f = (t - prev[0]) / (nxt[0] - prev[0])
        return prev[1] + (nxt[1] - prev[1]) * f, prev[2] + (nxt[2] - prev[2]) * f

    # -------------------------------------------------------------- frames
    def frame_before(self, t: int) -> int | None:
        i = bisect.bisect_right(self.ts, t) - 1
        return i if i >= 0 else None

    def frame_at_minute(self, minute: int) -> int | None:
        target = minute * 60_000
        for i, t in enumerate(self.ts):
            if abs(t - target) <= 30_000:
                return i
        return None

    def fval(self, pid: int | None, i: int | None, key: str):
        if pid is None or i is None or pid not in self.pf or i >= len(self.pf[pid]):
            return None
        frame = self.pf[pid][i] or {}
        if key == "cs":
            if "minionsKilled" not in frame and "jungleMinionsKilled" not in frame:
                return None
            return _num(frame.get("minionsKilled")) + _num(frame.get("jungleMinionsKilled"))
        if key == "dmg":
            return _num((frame.get("damageStats") or {}).get("totalDamageDoneToChampions"))
        value = frame.get(key)
        return _num(value) if value is not None else None

    def frame_pos(self, pid: int, i: int) -> tuple[int, int] | None:
        return _pos(self.pf[pid][i]) if pid in self.pf and i < len(self.pf[pid]) else None

    def enemy_jungle_cs(self, pid: int, t0: int, t1: int) -> int:
        """CS de jungla que ``pid`` hizo en la jungla rival entre ``t0`` y ``t1`` (por frames)."""
        enemy = 300 - self.players[pid]["team"]
        total = 0
        for i in range(1, len(self.ts)):
            if self.ts[i] <= t0 or self.ts[i - 1] >= t1:
                continue
            a, b = self.fval(pid, i - 1, "jungleMinionsKilled"), self.fval(pid, i, "jungleMinionsKilled")
            if a is None or b is None or b <= a:
                continue
            spots = [p for p in (self.frame_pos(pid, i - 1), self.frame_pos(pid, i)) if p]
            if any(geo.is_jungle_of(geo.zone(p), enemy) for p in spots):
                total += b - a
        return total


# ====================================================================== análisis
def analyze_game(
    match: dict,
    timeline: dict | None,
    *,
    pid: int | None = None,
    puuid: str | None = None,
    game_name: str | None = None,
    tag_line: str | None = None,
    items: ItemCatalog | None = None,
) -> dict:
    me_raw = find_me(match, pid=pid, puuid=puuid, game_name=game_name, tag_line=tag_line)
    g = _Game(match, timeline, me_raw, items)
    me = g.me["pid"]
    info = g.info
    ch = me_raw.get("challenges") or {}
    team_obj = next((t.get("objectives") or {} for t in info.get("teams") or [] if _num(t.get("teamId")) == g.team), {})

    deaths = [_classify_death(g, k) for k in g.kills if k["victim"] == me]
    moments: list[dict] = [d["moment"] for d in deaths]

    kills = _num(me_raw.get("kills"))
    n_deaths = _num(me_raw.get("deaths"))
    assists = _num(me_raw.get("assists"))
    cs = _num(me_raw.get("totalMinionsKilled")) + _num(me_raw.get("neutralMinionsKilled"))
    team_kills = sum(_num(p.get("kills")) for p in info.get("participants") or [] if _num(p.get("teamId")) == g.team)
    kp = ch.get("killParticipation")
    kp = float(kp) * 100 if kp is not None else ((kills + assists) / team_kills * 100 if team_kills else None)
    dmg_share = ch.get("teamDamagePercentage")
    time_dead = _num(me_raw.get("totalTimeSpentDead"))

    def count(tag: str, early_only: bool = False, unless: tuple[str, ...] = ()) -> int:
        return sum(1 for d in deaths if tag in d["tags"] and (not early_only or d["early"]) and not any(u in d["tags"] for u in unless))

    metrics: dict[str, float | int | None] = {
        "win": int(bool(me_raw.get("win"))),
        "minutes": round(g.minutes, 2),
        "kills": kills,
        "deaths": n_deaths,
        "assists": assists,
        "kda": round((kills + assists) / max(1, n_deaths), 2),
        "kp": round(kp, 1) if kp is not None else None,
        "dmg_share": round(float(dmg_share) * 100, 1) if dmg_share is not None else None,
        "cs_min": round(cs / g.minutes, 2),
        "gold_min": round(_num(me_raw.get("goldEarned")) / g.minutes),
        "vision_min": round(_num(me_raw.get("visionScore")) / g.minutes, 2),
        "wards_min": round(_num(me_raw.get("wardsPlaced")) / g.minutes, 2),
        "control_wards": _num(me_raw.get("visionWardsBoughtInGame")),
        "wards_killed": _num(me_raw.get("wardsKilled")),
        "time_dead_pct": round(time_dead / max(1, g.duration) * 100, 1),
        "solo_kills": _num(ch.get("soloKills")) if "soloKills" in ch else None,
        "plates": _num(ch.get("turretPlatesTaken")) if "turretPlatesTaken" in ch else None,
    }

    # Objetivos (sin timeline, con lo que trae el resumen de la partida).
    team_total = sum(_num((team_obj.get(k) or {}).get("kills")) for k in ("dragon", "baron", "riftHerald"))
    mine_total = _num(ch.get("dragonTakedowns")) + _num(ch.get("baronTakedowns")) + _num(ch.get("riftHeraldTakedowns"))
    metrics["obj_participation"] = round(min(100.0, mine_total / team_total * 100), 1) if team_total and ch else None

    result = {
        "version": ANALYSIS_VERSION,
        "match_id": (match.get("metadata") or {}).get("matchId"),
        "queue": _num(info.get("queueId")),
        "queue_label": QUEUES.get(_num(info.get("queueId")), f"Cola {_num(info.get('queueId'))}"),
        "game_start": _num(info.get("gameStartTimestamp") or info.get("gameCreation")),
        "duration": g.duration,
        "patch": ".".join((info.get("gameVersion") or "").split(".")[:2]),
        "has_timeline": g.has_timeline,
        "items_known": g.inv is not None,
        "me": me,
        "team": g.team,
        "role": g.role,
        "champ": g.me["champ"],
        "win": bool(me_raw.get("win")),
        "opponent": g.opponent,
        "enemy_jungler": g.enemy_jungler,
        "ally_jungler": g.ally_jungler,
        "players": [
            {k: p[k] for k in ("pid", "team", "champ", "role", "name", "tag")}
            | {
                "kills": _num(g.raw[pid].get("kills")),
                "deaths": _num(g.raw[pid].get("deaths")),
                "assists": _num(g.raw[pid].get("assists")),
                "cs": _num(g.raw[pid].get("totalMinionsKilled")) + _num(g.raw[pid].get("neutralMinionsKilled")),
                "gold": _num(g.raw[pid].get("goldEarned")),
                "level": _num(g.raw[pid].get("champLevel")),
                "items": [_num(g.raw[pid].get(f"item{i}")) for i in range(7)],
                "afk_min": round(g.afk_ms(pid) / 60_000, 1),
            }
            for pid, p in sorted(g.players.items())
        ],
        "metrics": metrics,
        "deaths": [{k: v for k, v in d.items() if k != "moment"} for d in deaths],
        "moments": moments,
    }

    if not g.has_timeline:
        for key in ("deaths_pre14", "deaths_solo", "deaths_ganked", "deaths_to_jungler_pre14", "deaths_no_vision_pre14"):
            metrics[key] = None
        return result

    metrics.update(
        {
            "deaths_pre14": sum(1 for d in deaths if d["early"]),
            "deaths_solo": count("solo"),
            "deaths_ganked": count("gank", True),
            "deaths_to_jungler_pre14": count("jungler", True),
            "deaths_no_vision_pre14": count("no_ward", True),
            "deaths_overextended": count("overextended", True),
            "deaths_enemy_jungle": count("enemy_jungle", unless=("fight_won",)),
            "deaths_unspent_gold": count("unspent_gold"),
            "deaths_before_objective": count("before_objective", unless=("fight_won",)),
            "deaths_isolated_late": count("isolated", unless=("traded", "fight_won")),
            "deaths_fight_won": count("fight_won"),
            "wards_pre14": sum(1 for t in g.wards[me] if t < EARLY_MS),
            "control_wards_placed": g.control_wards_placed[me],
            "afk_min": round(g.afk_ms(me) / 60_000, 1),
        }
    )

    _afk_moments(g, moments)
    _lane_phase(g, metrics, moments)
    _objectives(g, metrics, moments)
    _gold_use(g, metrics, moments)
    _time_use(g, metrics, moments)
    if g.role == "JUNGLE":
        result["jungle"] = _jungle(g, metrics, moments)
    else:
        _roaming(g, metrics)
    build = _build(g, metrics, moments)
    if build:
        result["build"] = build

    moments.sort(key=lambda m: m["t"])
    result["afk"] = [{"start": round(s / 1000, 1), "end": round(e / 1000, 1)} for s, e in g.afk[me]]
    result["series"] = _series(g)
    result["replay"] = _replay(g)
    return result


def _moment(t: float, kind: str, severity: str, title: str, detail: str = "", pos=None, tags=None) -> dict:
    return {
        "t": round(t / 1000, 1),
        "kind": kind,
        "severity": severity,
        "title": title,
        "detail": detail,
        "x": int(pos[0]) if pos else None,
        "y": int(pos[1]) if pos else None,
        "tags": tags or [kind],
    }


# ------------------------------------------------------------------ peleas
def _fight(g: _Game, k: dict) -> dict:
    """Kills encadenadas (≤ 20 s y ≤ 3.200 unidades) alrededor de una muerte."""
    cluster = [k]
    changed = True
    while changed:
        changed = False
        for other in g.kills:
            if other in cluster or not other["pos"]:
                continue
            if any(f["pos"] and abs(other["t"] - f["t"]) <= FIGHT_GAP_MS and geo.dist(other["pos"], f["pos"]) <= FIGHT_RADIUS for f in cluster):
                cluster.append(other)
                changed = True
    allies = sum(1 for f in cluster if g.players[f["victim"]]["team"] == g.team)
    return {"allies": allies, "enemies": len(cluster) - allies, "size": len(cluster)}


def _score(fight: dict) -> str:
    return f"{_plural(fight['enemies'], 'kill', 'kills')} y {_plural(fight['allies'], 'caído', 'caídos')}"


def _team_gain(g: _Game, t: int, before: int = 30_000, after: int = 60_000) -> dict | None:
    """Objetivos y estructuras que cogió tu equipo cerca de ``t`` (lo que compensa una muerte)."""
    objectives = [o for o in g.objectives if o["team"] == g.team and t - before <= o["t"] <= t + after]
    towers = [b for b in g.buildings if b["owner"] != g.team and b["kind"] != "plate" and t - before <= b["t"] <= t + after]
    if not objectives and not towers:
        return None
    parts = [OBJ_LABEL[o["kind"]] for o in objectives]
    n_towers = sum(1 for b in towers if b["kind"] == "tower")
    n_inhib = len(towers) - n_towers
    if n_towers:
        parts.append("una torre" if n_towers == 1 else f"{n_towers} torres")
    if n_inhib:
        parts.append("un inhibidor" if n_inhib == 1 else f"{n_inhib} inhibidores")
    value = sum(_obj_value(g, o) for o in objectives) + sum(BUILDING_VALUE[b["kind"]] for b in towers)
    return {"label": _join(parts), "value": value}


# ------------------------------------------------------------------ muertes
def _classify_death(g: _Game, k: dict) -> dict:
    me = g.me["pid"]
    t, pos = k["t"], k["pos"]
    early = t < EARLY_MS
    enemies = {pid for pid in (k["killer"], *k["assists"]) if pid in g.players and g.players[pid]["team"] != g.team}
    tags: list[str] = []
    zone = geo.zone(pos) if pos else None
    lane = geo.ROLE_LANE.get(g.role or "")

    fight = _fight(g, k) if g.has_timeline else {"allies": 1, "enemies": 0, "size": 1}
    if not enemies:
        tags.append("executed")
    if len(enemies) == 1 and fight["allies"] == 1:  # si caen más aliados, es una escaramuza
        tags.append("solo")
    if g.enemy_jungler and g.enemy_jungler in enemies and g.role != "JUNGLE":
        tags.append("jungler")
    if early and g.role and g.role != "JUNGLE" and enemies - g.natural_opponents():
        tags.append("gank")
    if zone and geo.is_jungle_of(zone, 300 - g.team):
        tags.append("enemy_jungle")
    if early and lane and pos and zone == lane and geo.overextended(g.team, lane, pos):
        tags.append("overextended")
    if early and g.has_timeline and not any(t - WARD_WINDOW_MS <= w <= t for w in g.wards[me]):
        tags.append("no_ward")
    gold = _death_gold(g, t)
    if gold:
        tags.append("unspent_gold")
    if fight["enemies"] > fight["allies"]:
        tags.append("fight_won")
    elif fight["enemies"] and fight["enemies"] == fight["allies"]:
        tags.append("fight_even")
    gained = _team_gain(g, t) if g.has_timeline else None
    if gained and gained["value"] >= 600:
        tags.append("traded")
    else:
        gained = None
    lost = next((o for o in g.objectives if o["team"] != g.team and t < o["t"] <= t + OBJECTIVE_WINDOW_MS), None)
    if lost and (not gained or gained["value"] < 0.6 * _obj_value(g, lost)):
        tags.append("before_objective")
    if not early and pos and g.has_timeline and fight["allies"] == 1 and not fight["enemies"]:
        near = [a for a in g.allies if (ap := g.pos_at(a, t)) is not None and geo.dist(ap, pos) <= ISOLATED_RADIUS]
        if not near:
            tags.append("isolated")
    if g.won and t >= g.duration_ms - FINAL_PUSH_MS:
        tags.append("game_end")

    names = [_champ_ref(g, pid) for pid in sorted(enemies)]
    killer_name = _champ_ref(g, k["killer"]) if k["killer"] in g.players else "torre/súbditos"
    if "game_end" in tags:
        title = "Muerte en el empujón final"
    elif "fight_won" in tags:
        title = f"Caíste, pero ganasteis la pelea ({_score(fight)})"
    elif "gank" in tags:
        title = f"Gank de {_champ_ref(g, g.enemy_jungler)} (jungla rival)" if "jungler" in tags else f"Gank ({', '.join(names)})"
    elif "enemy_jungle" in tags:
        title = "Muerte en la jungla rival"
    elif "solo" in tags:
        title = f"Muerte 1 contra 1 contra {killer_name}"
    elif "isolated" in tags:
        title = "Muerte aislado de tu equipo"
    elif "executed" in tags:
        title = "Ejecutado por torre o súbditos"
    else:
        title = f"Muerte en pelea ({_plural(len(enemies), 'rival', 'rivales')})"

    details = []
    if zone:
        details.append(f"en {geo.zone_label(zone, g.team)}")
    if fight["size"] > 1 and "fight_won" not in tags:
        details.append(f"pelea {'igualada' if 'fight_even' in tags else 'perdida'} ({_score(fight)})")
    if "overextended" in tags:
        details.append("estabas adelantado, más cerca de su torre que de la tuya")
    if "no_ward" in tags:
        details.append("sin poner ningún ward en los 2 minutos previos")
    if gold:
        buy = f": {_buy_text(g, gold['item'], gold['cost'])}" if gold.get("cost") else ""
        details.append(f"con {_int(gold['gold'])} de oro sin gastar{buy}")
    if "before_objective" in tags:
        details.append(f"y el rival cogió {OBJ_LABEL[lost['kind']]} a continuación")
    if gained:
        details.append(f"tu equipo cogió {gained['label']} a cambio")
    if "isolated" in tags and "fight_won" not in tags:
        details.append("sin ningún aliado cerca")

    score = 1.0
    score += 1.0 if ("gank" in tags and "no_ward" in tags) else 0
    score += 0.8 if "overextended" in tags else 0
    score += 1.0 if "before_objective" in tags else 0
    score += 0.5 if "unspent_gold" in tags else 0
    score += 0.6 if "enemy_jungle" in tags and "no_ward" in tags else 0
    score -= 1.0 if "fight_won" in tags else 0.5 if "fight_even" in tags else 0
    score -= 0.7 if "traded" in tags else 0
    severity = "high" if score >= 2.5 else "medium" if score >= 1.5 else "low"
    if "game_end" in tags:
        severity = "low"
    entry = {
        "t": round(t / 1000, 1),
        "x": pos[0] if pos else None,
        "y": pos[1] if pos else None,
        "zone": zone,
        "early": early,
        "tags": tags,
        "killer": k["killer"],
        "enemies": sorted(enemies),
        "severity": severity,
        "fight": fight,
    }
    entry["moment"] = {
        "t": entry["t"],
        "kind": "death",
        "severity": severity,
        "title": title,
        "detail": _sentence(", ".join(details)) if details else "",
        "x": entry["x"],
        "y": entry["y"],
        "tags": tags,
    }
    return entry


def _champ_ref(g: _Game, pid: int | None) -> str:
    """Referencia legible a un campeón (el nombre lo pone el frontend: «{champ:ID}»)."""
    if not pid or pid not in g.players:
        return "?"
    return f"{{champ:{g.players[pid]['champ']}}}"


def _item_name(g: _Game, item: int | None) -> str:
    if not item or not g.catalog:
        return "un componente"
    return g.catalog.name(item)


def _buy_text(g: _Game, item: int | None, cost: int | None) -> str:
    """«te llegaba para completar X (1.450)» o «te llegaba para X (1.100)»."""
    if not item or not g.catalog:
        return "te llegaba para un componente"
    verb = "completar " if g.catalog.is_completed(item) and cost is not None and cost < g.catalog.cost(item) else ""
    return f"te llegaba para {verb}{g.catalog.name(item)} ({_int(cost)})"


# ------------------------------------------------------------------- oro
def _targets(g: _Game, t: int) -> list[int]:
    """Lo que compraste después (tu build): el siguiente objeto terminado y lo de antes."""
    cat = g.catalog
    later = [
        p for p in g.inv.bought(since=t)
        if p["cost"] >= 300 and not cat.is_consumable(p["item"]) and not cat.is_starter(p["item"]) and not cat.is_trinket(p["item"])
    ]
    nxt = next((p for p in later if cat.is_completed(p["item"])), None)
    out = [nxt["item"]] if nxt else []
    for p in later:
        if nxt and p["t"] > nxt["t"]:
            break
        if p["item"] not in out:
            out.append(p["item"])
    return out


def _spend(g: _Game, t: int, gold: int) -> dict:
    """Qué podías comprar en ``t`` con ``gold`` para avanzar en tu build (y con hueco)."""
    if g.inv is None:
        return {"known": False, "useful": gold if gold >= HOARD_GOLD else 0, "item": None, "cost": None, "free": None, "full": False, "completed": 0}
    cat = g.catalog
    inv = g.inv.at(t)
    free = g.inv.free_slots(inv)
    targets = _targets(g, t)
    if targets:
        opts = cat.options(targets, inv, free)
    else:  # no volviste a comprar: lo que combine con lo que llevas o, con hueco, un componente
        opts = cat.merges(inv, free)
        if free > 0:
            opts = [*opts, (0, GENERIC_COMPONENT)]
    affordable = [o for o in opts if o[1] <= gold]
    best = max(affordable, key=lambda o: o[1]) if affordable else None
    cheapest = min(opts, key=lambda o: o[1]) if opts else None
    return {
        "known": True,
        "useful": best[1] if best else 0,
        "item": best[0] if best else None,
        "cost": best[1] if best else None,
        "next": cheapest,
        "free": free,
        "full": not opts and free == 0 and not targets,  # build terminada: ya no ibas a comprar nada
        "completed": len(g.inv.completed(inv)),
        "targets": targets,
    }


def _death_gold(g: _Game, t: int) -> dict | None:
    """Oro que ya llevabas 30 s antes de morir y que podías gastar en tu build."""
    me = g.me["pid"]
    if not g.has_timeline or (g.won and t >= g.duration_ms - FINAL_PUSH_MS):
        return None
    i = g.frame_before(t - 30_000)
    if i is None or g.ts[i] < 3 * 60_000 or g.is_dead(me, g.ts[i]):
        return None
    tf = g.ts[i]
    gold = g.fval(me, i, "currentGold")
    if gold is None or any(tf < pt <= t for pt, _ in g.purchases[me]):  # compraste entre medias
        return None
    if g.inv is None:
        return {"gold": gold, "item": None, "cost": None} if gold >= DEATH_GOLD else None
    s = _spend(g, tf, gold)
    if s["useful"] < USEFUL_SPEND:
        return None
    return {"gold": gold, "item": s["item"], "cost": s["cost"]}


def _busy_windows(g: _Game) -> list[tuple[int, int]]:
    """Momentos en los que no tocaba volver a base: peleas, objetivos, torres, final de partida."""
    me = g.me["pid"]
    out = []
    for k in g.kills:
        if me in (k["killer"], k["victim"], *k["assists"]):
            out.append((k["t"] - 25_000, k["t"] + 10_000))
    for o in g.objectives:
        p = g.pos_at(me, o["t"])
        if me in o["takers"] or (p is not None and geo.dist(p, o["pos"]) <= NEAR_OBJECTIVE):
            out.append((o["t"] - 60_000, o["t_end"] + 10_000))
    for b in g.buildings:
        if me in b["takers"]:
            out.append((b["t"] - 30_000, b["t"] + 10_000))
    out.append((g.duration_ms - (FINAL_PUSH_MS if g.won else 60_000), g.duration_ms + 1))
    out.sort()
    merged: list[list[int]] = []
    for s, e in out:
        if merged and s <= merged[-1][1]:
            merged[-1][1] = max(merged[-1][1], e)
        else:
            merged.append([s, e])
    return [(s, e) for s, e in merged]


def _overlap(windows: list[tuple[int, int]], t0: int, t1: int) -> int:
    return sum(max(0, min(e, t1) - max(s, t0)) for s, e in windows)


def _gold_use(g: _Game, metrics: dict, moments: list) -> None:
    """Oro que te daba para algo de tu build, con hueco, y que tardaste en gastar."""
    me = g.me["pid"]
    rows: dict[int, dict] = {}
    flagged: list[int] = []
    for i, t in enumerate(g.ts):
        if t < 3 * 60_000 or not g.present(me, t):
            continue
        gold = g.fval(me, i, "currentGold")
        p = g.frame_pos(me, i)
        if gold is None or (p and geo.in_fountain(g.team, p)):
            continue
        s = _spend(g, t, gold)
        s["gold"] = gold
        rows[i] = s
        if s["useful"] >= USEFUL_SPEND:
            flagged.append(i)
    g.gold_rows = rows

    busy = _busy_windows(g)
    buys = sorted(pt for pt, _ in g.purchases[me])
    respawns = sorted(end for _s, end, _p in g.dead.get(me) or [])
    deaths = sorted(start for start, _e, _p in g.dead.get(me) or [])
    total = 0
    for run in _runs(flagged):
        t_first, t_last = g.ts[run[0]], g.ts[run[-1]]
        start = max([t_first - 30_000, *[b for b in buys if b <= t_first][-1:], *[r for r in respawns if r <= t_first][-1:]])
        ends = [g.duration_ms, t_last + 60_000]
        ends += [b for b in buys if b > t_last][:1] + [d for d in deaths if d > t_last][:1]
        end = min(ends)
        excess = (end - start) - _overlap(busy, start, end) - HOARD_GRACE_MS
        if excess <= 0:
            continue
        total += excess
        g.hoard_frames.update(run)
        best = max((rows[i] for i in run), key=lambda r: r["useful"])
        peak = max(rows[i]["gold"] for i in run)
        if excess < 40_000:
            continue
        if best["known"]:
            what = _buy_text(g, best["item"], best["cost"])
            what += " y tenías hueco" if best["free"] else " con lo que llevabas"
        else:
            what = "te llegaba para un componente"
        moments.append(
            _moment(
                start,
                "gold",
                "medium" if excess >= 120_000 or best["useful"] >= 2500 else "low",
                f"{_secs(end - start)} con oro sin gastar (hasta {_int(peak)})",
                f"Del {_mmss(start)} al {_mmss(end)} {what}. Descontando peleas y objetivos, tardaste {_secs(excess)} de más en volver: "
                "vuelve a base al acabar la oleada o el campamento, ese oro no pelea.",
                tags=["gold"],
            )
        )
    metrics["gold_hoard_min"] = round(total / 60_000, 1)
    # Ahorro legítimo: inventario lleno o build terminada (no cuenta como error).
    saving = [i for i, r in rows.items() if r["known"] and r["gold"] >= HOARD_GOLD and r["useful"] < USEFUL_SPEND]
    metrics["gold_saving_min"] = len(saving)
    full = [i for i, r in sorted(rows.items()) if r["known"] and r["full"] and r["completed"] >= 5 and r["gold"] >= HOARD_GOLD]
    full = [i for i in full if g.ts[i] < g.duration_ms - FINAL_PUSH_MS]
    run = next((r for r in _runs(full) if len(r) >= 2), None)
    if run:
        moments.append(
            _moment(
                g.ts[run[0]],
                "gold",
                "low",
                f"Build completa con {_int(rows[run[-1]]['gold'])} de oro sin uso",
                "Con el inventario lleno de objetos terminados ese oro no es un error, pero puedes convertirlo en elixires (500) y pinks antes de cada pelea u objetivo.",
                tags=["gold", "full_build"],
            )
        )


# --------------------------------------------------------------- AFK
def _afk_moments(g: _Game, moments: list) -> None:
    me = g.me["pid"]
    for start, end in g.afk[me]:
        if end >= g.duration_ms - 1000 and end - start < 150_000:
            continue  # quieto en base esperando el final: no es una desconexión
        moments.append(
            _moment(
                start,
                "afk",
                "low",
                f"Desconexión o AFK: {_secs(end - start)} sin moverte",
                "Esos minutos no cuentan como minutos vacíos ni como oro sin gastar.",
                tags=["afk"],
            )
        )
    allies = [(a, g.afk_ms(a)) for a in g.allies if g.afk_ms(a) >= 120_000]
    for a, ms in allies:
        start = g.afk[a][0][0]
        moments.append(
            _moment(
                start,
                "afk",
                "low",
                f"{_champ_ref(g, a)} (tu equipo) estuvo AFK {_secs(ms)}",
                "Jugasteis en inferioridad: se tiene en cuenta al valorar los objetivos que perdisteis.",
                tags=["afk", "ally"],
            )
        )


# --------------------------------------------------------- fase de líneas
def _lane_phase(g: _Game, metrics: dict, moments: list) -> None:
    me, opp = g.me["pid"], g.opponent
    for minute in (10, 15):
        i = g.frame_at_minute(minute)
        cs = g.fval(me, i, "cs")
        metrics[f"cs{minute}"] = cs
        if opp:
            ocs, gold, ogold = g.fval(opp, i, "cs"), g.fval(me, i, "totalGold"), g.fval(opp, i, "totalGold")
            xp, oxp = g.fval(me, i, "xp"), g.fval(opp, i, "xp")
            metrics[f"cs_diff{minute}"] = cs - ocs if cs is not None and ocs is not None else None
            metrics[f"gold_diff{minute}"] = gold - ogold if gold is not None and ogold is not None else None
            metrics[f"xp_diff{minute}"] = xp - oxp if xp is not None and oxp is not None else None
        else:
            metrics[f"cs_diff{minute}"] = metrics[f"gold_diff{minute}"] = metrics[f"xp_diff{minute}"] = None
    gd = metrics.get("gold_diff15")
    if g.role and g.role not in ("JUNGLE", "SUPPORT") and gd is not None and gd <= -1000:
        moments.append(
            {
                "t": 900.0,
                "kind": "lane",
                "severity": "medium" if gd > -2000 else "high",
                "title": f"Línea perdida: {gd:+,} de oro a los 15".replace(",", "."),
                "detail": f"Frente a {_champ_ref(g, opp)}: {metrics.get('cs_diff15') or 0:+} CS y {metrics.get('xp_diff15') or 0:+} de experiencia.",
                "x": None,
                "y": None,
                "tags": ["lane"],
            }
        )


# --------------------------------------------------------------- objetivos
def _obj_value(g: _Game, o: dict) -> int:
    kind = o["kind"]
    if kind == "grubs":
        return OBJ_VALUE["grubs"] * o.get("count", 1)
    if kind == "dragon":
        prior = sum(1 for x in g.objectives if x["kind"] == "dragon" and x["team"] == o["team"] and x["t"] < o["t"])
        return 2500 if prior >= 3 else 1400 if prior == 2 else OBJ_VALUE["dragon"]
    return OBJ_VALUE.get(kind, 800)


def _obj_label(g: _Game, o: dict) -> str:
    if o["kind"] == "dragon":
        prior = sum(1 for x in g.objectives if x["kind"] == "dragon" and x["team"] == o["team"] and x["t"] < o["t"])
        if prior >= 3:
            return "el alma del dragón"
        if prior == 2:
            return "el tercer dragón (punto de alma)"
    return OBJ_LABEL[o["kind"]]


def _exchange(g: _Game, o: dict) -> dict:
    """Qué ganó y qué perdió tu equipo alrededor de un objetivo del rival."""
    me, team = g.me["pid"], g.team
    t0, t1 = o["t"] - TRADE_BEFORE_MS, o["t"] + TRADE_AFTER_MS
    gain = {"kills": 0, "towers": 0, "inhibitors": 0, "plates": 0, "objectives": [], "jungle_cs": 0}
    mine = {"takedowns": 0, "towers": 0, "plates": 0, "jungle_cs": 0, "objectives": []}
    value_gain = 0
    value_loss = _obj_value(g, o)
    for k in g.kills:
        if not t0 <= k["t"] <= t1:
            continue
        if g.players[k["victim"]]["team"] != team:
            gain["kills"] += 1
            value_gain += k["value"]
            mine["takedowns"] += int(me == k["killer"] or me in k["assists"])
        else:
            value_loss += k["value"]
    for b in g.buildings:
        if not t0 <= b["t"] <= t1:
            continue
        value = BUILDING_VALUE[b["kind"]]
        if b["owner"] == team:
            value_loss += value
            continue
        value_gain += value
        key = {"tower": "towers", "inhibitor": "inhibitors", "plate": "plates"}[b["kind"]]
        gain[key] += 1
        if me in b["takers"]:
            mine["plates" if b["kind"] == "plate" else "towers"] += 1
    for x in g.objectives:
        if x is o or x["team"] != team or not t0 <= x["t"] <= t1:
            continue
        gain["objectives"].append(x["kind"])
        value_gain += _obj_value(g, x)
        if me in x["takers"]:
            mine["objectives"].append(x["kind"])
    for pid in (me, *g.allies):
        cs = g.enemy_jungle_cs(pid, t0, t1 + 30_000)
        gain["jungle_cs"] += cs
        if pid == me:
            mine["jungle_cs"] = cs
    value_gain += gain["jungle_cs"] * ENEMY_JUNGLE_CS_VALUE
    return {"gain": gain, "mine": mine, "value_gain": value_gain, "value_loss": value_loss, "ratio": value_gain / max(1, value_loss)}


def _gain_text(gain: dict) -> str:
    parts = [OBJ_LABEL[k] for k in gain.get("objectives") or []]
    if gain.get("kills") or gain.get("takedowns"):
        n = gain.get("kills", gain.get("takedowns"))
        parts.append(_plural(n, "kill", "kills"))
    if gain.get("towers"):
        parts.append(_plural(gain["towers"], "torre", "torres"))
    if gain.get("inhibitors"):
        parts.append(_plural(gain["inhibitors"], "inhibidor", "inhibidores"))
    if gain.get("plates"):
        parts.append(_plural(gain["plates"], "placa", "placas"))
    if gain.get("jungle_cs", 0) >= 4:
        parts.append(f"{gain['jungle_cs']} CS de su jungla")
    return _join(parts)


def _objectives(g: _Game, metrics: dict, moments: list) -> None:
    me = g.me["pid"]
    taken = participated = lost_dead = conceded = trades = 0
    my_deaths = [k["t"] for k in g.kills if k["victim"] == me]
    for o in g.objectives:
        here = me in o["takers"] if o["assists_known"] else False
        p = g.pos_at(me, o["t"])
        if not here and p is not None and geo.dist(p, o["pos"]) <= NEAR_OBJECTIVE:
            here = True
        if g.is_afk(me, o["t"]):
            continue
        if o["team"] == g.team:
            taken += 1
            participated += int(here)
            continue
        if here:
            continue
        label = _obj_label(g, o)
        big = o["kind"] in ("baron", "elder") or label.startswith("el alma")
        ex = _exchange(g, o)
        got = _gain_text(ex["gain"])
        mine = _gain_text(ex["mine"])
        if g.is_dead(me, o["t"]):
            if ex["ratio"] >= GOOD_TRADE:
                trades += 1
                moments.append(
                    _moment(o["t"], "trade", "low", f"El rival cogió {label} mientras estabas muerto, pero tu equipo lo compensó",
                            f"Mientras tanto sacasteis {got}.", o["pos"], ["objective", "dead", "trade"])
                )
                continue
            lost_dead += 1
            if any(o["t"] - OBJECTIVE_WINDOW_MS <= d <= o["t"] for d in my_deaths):
                continue  # la muerte de antes ya lo cuenta
            moments.append(
                _moment(o["t"], "objective", "high" if o["kind"] in ("baron", "elder", "dragon") else "medium",
                        f"El rival cogió {label} mientras estabas muerto", "Una muerte antes de un objetivo se paga doble: no puedes disputarlo.",
                        o["pos"], ["objective", "dead"])
            )
            continue
        if p is None or geo.dist(p, o["pos"]) < FAR_FROM_OBJECTIVE:
            continue
        where = geo.zone_label(geo.zone(p), g.team)
        you = f"; tú: {mine}" if mine else ""
        if ex["ratio"] >= GOOD_TRADE:
            trades += 1
            moments.append(
                _moment(o["t"], "trade", "good", f"Buen cambio: {label} por {got}",
                        f"Estabas en {where}{you}. Si no se puede disputar un objetivo, cambiarlo por algo al otro lado es la decisión correcta.",
                        p, ["objective", "trade"])
            )
            continue
        if ex["ratio"] >= FAIR_TRADE and got:
            trades += 1
            moments.append(
                _moment(o["t"], "trade", "low", f"Cambio justo: {label} por {got}",
                        f"Estabas en {where}{you}. Valía algo menos que el objetivo: si llegabais, era mejor disputarlo.", p, ["objective", "trade"])
            )
            continue
        allies_up = sum(1 for a in (me, *g.allies) if g.present(a, o["t"]))
        enemies_up = sum(1 for e in g.enemies if g.present(e, o["t"]))
        if enemies_up - allies_up >= 2:
            moments.append(
                _moment(o["t"], "objective", "low", f"El rival cogió {label} con ventaja numérica ({allies_up} contra {enemies_up})",
                        "No se podía disputar: lo que costó el objetivo fueron las muertes de antes.", p, ["objective", "outnumbered"])
            )
            continue
        conceded += 1
        if g.role in ("JUNGLE", "SUPPORT", "MID") or big:
            recalled = any(o["t"] - 60_000 <= pt <= o["t"] and not g.is_dead(me, pt) for pt, _ in g.purchases[me])
            if recalled:
                detail = "Volviste a base justo antes del objetivo"
            else:
                detail = f"Estabas en {where}, a {_int(geo.dist(p, o['pos']))} unidades"
            detail += f"; {('solo sacasteis ' + got) if got else 'tu equipo no sacó nada a cambio'}"
            detail += ". Si no vas a disputarlo, cámbialo: invade su jungla al otro lado, presiona una torre o haz un dive."
            free = ex["value_gain"] < 0.25 * _obj_value(g, o)
            title = f"El rival cogió {label} gratis y estabas lejos" if free else f"El rival cogió {label} y no lo compensasteis"
            moments.append(_moment(o["t"], "objective", "high" if big else "medium", title, detail, p, ["objective", "far"]))
    metrics["objectives_team"] = taken
    if taken:
        metrics["obj_participation"] = round(participated / taken * 100, 1)
    metrics["obj_lost_dead"] = lost_dead
    metrics["obj_lost_far"] = conceded
    metrics["obj_trades"] = trades


# ---------------------------------------------------------- uso del tiempo
def _time_use(g: _Game, metrics: dict, moments: list) -> None:
    me = g.me["pid"]
    # Minutos vacíos desde el 15: sin farmear, pelear, wardear, comprar ni hacer objetivos.
    my_events = sorted(
        [k["t"] for k in g.kills if me == k["killer"] or me in k["assists"]]
        + [o["t"] for o in g.objectives if me in o["takers"]]
        + [b["t"] for b in g.buildings if me in b["takers"]]
        + list(g.wards[me])
        + [t for t, _ in g.purchases[me]]
    )
    eligible = 0
    idle: list[int] = []
    for i in range(1, len(g.ts)):
        t0, t1 = g.ts[i - 1], g.ts[i]
        if t0 < IDLE_FROM_MS or t1 - t0 < 40_000:
            continue
        if g.dead_overlap(me, t0, t1) > 15_000 or any(min(e, t1) - max(s, t0) > 15_000 for s, e in g.afk[me]):
            continue
        eligible += 1
        cs0, cs1 = g.fval(me, i - 1, "cs"), g.fval(me, i, "cs")
        d0, d1 = g.fval(me, i - 1, "dmg"), g.fval(me, i, "dmg")
        busy = (cs0 is not None and cs1 is not None and cs1 - cs0 >= 3) or (d0 is not None and d1 is not None and d1 - d0 >= 400)
        if not busy:
            j = bisect.bisect_left(my_events, t0)
            busy = j < len(my_events) and my_events[j] <= t1
        if not busy:
            idle.append(i)
    metrics["idle_min"] = len(idle)
    metrics["idle_pct"] = round(len(idle) / eligible * 100, 1) if eligible else None
    for run in _runs(idle):
        if len(run) >= 2:
            p = g.frame_pos(me, run[0])
            moments.append(
                {
                    "t": round(g.ts[run[0] - 1] / 1000, 1),
                    "kind": "idle",
                    "severity": "medium" if len(run) >= 3 else "low",
                    "title": f"{len(run)} minutos sin farmear ni pelear",
                    "detail": f"Del minuto {round(g.ts[run[0] - 1] / 60_000)} al {round(g.ts[run[-1]] / 60_000)} no hiciste CS, daño, wards ni objetivos. Si no hay jugada, farmea una línea o la jungla.",
                    "x": p[0] if p else None,
                    "y": p[1] if p else None,
                    "tags": ["idle"],
                }
            )

    # Viajes a base (compras estando vivo, sin contar la compra inicial).
    trips: list[int] = []
    for t, _item in g.purchases[me]:
        if t < 90_000 or g.is_dead(me, t):
            continue
        if not trips or t - trips[-1] > 40_000:
            trips.append(t)
    metrics["recalls"] = len(trips)
    metrics["first_recall_min"] = round(trips[0] / 60_000, 1) if trips else None
    metrics["control_wards_bought"] = sum(1 for _t, item in g.purchases[me] if item == CONTROL_WARD)


def _runs(indices: list[int]) -> list[list[int]]:
    runs: list[list[int]] = []
    for i in indices:
        if runs and i == runs[-1][-1] + 1:
            runs[-1].append(i)
        else:
            runs.append([i])
    return runs


# ------------------------------------------------------------------ jungla
def _jungle(g: _Game, metrics: dict, moments: list) -> dict:
    me = g.me["pid"]
    team = g.team
    path = []
    for i in range(1, min(len(g.ts), 9)):
        p = g.frame_pos(me, i)
        if not p:
            continue
        camp = geo.nearest_camp(p, 1500)
        z = geo.zone(p)
        path.append(
            {
                "minute": round(g.ts[i] / 60_000),
                "x": p[0],
                "y": p[1],
                "zone": z,
                "camp": camp[1] if camp else None,
                "camp_team": camp[0] if camp else None,
            }
        )
    start = next((s for s in path if s["camp"] and s["camp_team"] == team), None)
    presence: Counter = Counter()
    invade = 0
    mirror_same = mirror_n = 0
    for i, t in enumerate(g.ts):
        if t < 2 * 60_000 or t > EARLY_MS:
            continue
        p = g.frame_pos(me, i)
        if not p or g.is_dead(me, t):
            continue
        z = geo.zone(p)
        if geo.is_lane(z):
            presence[z] += 1
        elif geo.is_jungle_of(z, 300 - team):
            invade += 1
        elif z == "river":
            presence["river"] += 1
        ej = g.enemy_jungler
        q = g.frame_pos(ej, i) if ej else None
        if q and not g.is_dead(ej, t):
            mirror_n += 1
            mirror_same += int(geo.half_of(p) == geo.half_of(q))

    takedowns = [k for k in g.kills if (me == k["killer"] or me in k["assists"]) and k["pos"]]
    ganks = [k for k in takedowns if k["t"] < 10 * 60_000 and geo.is_lane(geo.zone(k["pos"]))]
    gank_events = []
    for k in ganks:
        if not gank_events or k["t"] - gank_events[-1]["t"] > 30_000:
            gank_events.append(k)
    first_gank = next((k for k in takedowns if k["t"] < EARLY_MS and geo.is_lane(geo.zone(k["pos"]))), None)

    # Impacto antes del 10: kills en cualquier zona, objetivos e invasiones.
    early_takedowns = [k for k in g.kills if k["t"] < 10 * 60_000 and (me == k["killer"] or me in k["assists"])]
    early_objectives = [o for o in g.objectives if o["team"] == team and o["t"] < 10 * 60_000 and me in o["takers"]]
    invade_cs = g.enemy_jungle_cs(me, 0, 10 * 60_000)
    impact = len(early_takedowns) + len(early_objectives) + (1 if invade_cs >= 8 else 0)

    # Ganks del jungla rival mientras estabas en la otra mitad del mapa.
    ej = g.enemy_jungler
    enemy_ganks = far = 0
    for k in g.kills:
        if not ej or k["t"] >= EARLY_MS or not k["pos"] or g.players[k["victim"]]["team"] != team:
            continue
        if ej != k["killer"] and ej not in k["assists"]:
            continue
        if not geo.is_lane(geo.zone(k["pos"])):
            continue
        enemy_ganks += 1
        p = g.pos_at(me, k["t"])
        if p is not None and geo.half_of(p) != geo.half_of(k["pos"]):
            far += 1

    i10 = g.frame_at_minute(10)
    jcs10 = g.fval(me, i10, "jungleMinionsKilled")
    ejcs10 = g.fval(ej, i10, "jungleMinionsKilled") if ej else None
    metrics.update(
        {
            "ganks_pre10": len(gank_events),
            "impact_pre10": impact,
            "invade_cs_pre10": invade_cs,
            "first_gank_min": round(first_gank["t"] / 60_000, 1) if first_gank else None,
            "invade_min": invade,
            "jungle_cs10": jcs10,
            "jungle_cs_diff10": jcs10 - ejcs10 if jcs10 is not None and ejcs10 is not None else None,
            "enemy_ganks_far": far,
            "mirror_pct": round(mirror_same / mirror_n * 100) if mirror_n else None,
            "scuttles": _num((g.raw[me].get("challenges") or {}).get("scuttleCrabKills")) if "scuttleCrabKills" in (g.raw[me].get("challenges") or {}) else None,
            "enemy_jungle_cs": _num(g.raw[me].get("totalEnemyJungleMinionsKilled")),
        }
    )
    if not impact and g.duration_ms >= 12 * 60_000:
        moments.append(
            _moment(
                600_000,
                "jungle",
                "medium",
                "Early sin impacto: ni kills, ni objetivos, ni invasiones antes del 10",
                "Tras la primera limpieza busca una jugada: gank a la línea con empuje y control de masas, larvas o dragón con prioridad, o invadir donde no está su jungla.",
                tags=["jungle"],
            )
        )
    if far >= 2:
        moments.append(
            {
                "t": 840.0,
                "kind": "jungle",
                "severity": "medium",
                "title": f"El jungla rival ganqueó {far} veces en la otra mitad del mapa",
                "detail": "Si sabes dónde está el jungla rival, juega en espejo: contragank en su lado o invade/objetivo en el contrario.",
                "x": None,
                "y": None,
                "tags": ["jungle", "tracking"],
            }
        )
    return {
        "start_camp": start["camp"] if start else None,
        "start_side": geo.half_of((start["x"], start["y"])) if start else None,
        "path": path,
        "presence": dict(presence),
        "ganks": [{"t": round(k["t"] / 1000, 1), "x": k["pos"][0], "y": k["pos"][1], "zone": geo.zone(k["pos"])} for k in gank_events],
        "enemy_ganks": enemy_ganks,
    }


def _roaming(g: _Game, metrics: dict) -> None:
    me = g.me["pid"]
    lane = geo.ROLE_LANE.get(g.role or "")
    if not lane:
        return
    roam = 0
    for i, t in enumerate(g.ts):
        if t < 3 * 60_000 or t > EARLY_MS or g.is_dead(me, t):
            continue
        p = g.frame_pos(me, i)
        if not p:
            continue
        z = geo.zone(p)
        if z != lane and not z.startswith("base_"):
            roam += 1
    metrics["roam_min"] = roam
    metrics["roam_takedowns"] = sum(
        1
        for k in g.kills
        if (me == k["killer"] or me in k["assists"]) and k["t"] < EARLY_MS and k["pos"] and geo.zone(k["pos"]) not in (lane,)
    )


# ------------------------------------------------------------------- build
def _build(g: _Game, metrics: dict, moments: list) -> dict | None:
    """Orden de compra, minutos de cada objeto y errores de build."""
    if g.inv is None:
        return None
    cat, inv = g.catalog, g.inv
    me = g.me["pid"]
    raw = g.raw[me]
    bought = inv.bought()
    completed = [p for p in bought if cat.is_completed(p["item"])]
    legendaries = [p for p in completed if cat.is_legendary(p["item"])]
    boots = next((p for p in completed if cat.is_boots(p["item"])), None)
    order = [{"t": round(p["t"] / 1000, 1), "item": p["item"], "kind": "boots" if cat.is_boots(p["item"]) else "legendary"} for p in completed]

    backs: list[dict] = []
    for p in bought:
        spent = p["cost"] - sum(cat.cost(c) for c in p["consumed"])
        if backs and p["t"] - backs[-1]["_last"] <= 40_000:
            backs[-1]["items"].append(p["item"])
            backs[-1]["gold"] += spent
            backs[-1]["_last"] = p["t"]
        else:
            backs.append({"t": round(p["t"] / 1000, 1), "items": [p["item"]], "gold": spent, "_last": p["t"], "dead": g.is_dead(me, p["t"])})
    for b in backs:
        b.pop("_last")

    def minute(i: int) -> float | None:
        return round(legendaries[i]["t"] / 60_000, 1) if len(legendaries) > i else None

    metrics["first_item_min"] = minute(0)
    metrics["second_item_min"] = minute(1)
    metrics["boots_min"] = round(boots["t"] / 60_000, 1) if boots else None
    metrics["legendaries"] = len(legendaries)

    final_inv = inv.at(g.duration_ms + 1)
    final_resists = set().union(*[cat.resist(i) for i in final_inv]) if final_inv else set()
    notes: list[dict] = []

    # 1. Inventario lleno con componentes de otros objetos: por el orden de compra no podías gastar.
    #    (Llevar los componentes de tu siguiente objeto y ahorrar para combinarlos no es un error.)
    clogged: list[tuple[int, list[int]]] = []
    for i, r in sorted(g.gold_rows.items()):
        if not r.get("known") or r["free"] or r["gold"] < USEFUL_SPEND or r["useful"] >= USEFUL_SPEND or g.ts[i] < 8 * 60_000:
            continue
        snap = inv.at(g.ts[i])
        planned = Counter()
        for target in r.get("targets") or []:
            planned.update(cat.tree(target))
            planned[target] += 1
        strays = []
        for c in snap.elements():
            if not cat.uses_slot(c) or cat.is_completed(c) or cat.is_starter(c) or cat.is_consumable(c):
                continue
            if planned[c] > 0:
                planned[c] -= 1
            else:
                strays.append(c)
        if strays and r.get("targets"):
            clogged.append((i, strays))
    metrics["inv_clogged_min"] = len(clogged)
    if clogged:
        i, strays = clogged[0]
        names = _join([cat.name(c) for c in strays[:3]])
        nxt = cat.name(g.gold_rows[i]["targets"][0])
        moments.append(
            _moment(
                g.ts[i], "build", "medium" if len(clogged) >= 2 else "low",
                "Inventario lleno con componentes de otros objetos",
                f"Con {_int(g.gold_rows[i]['gold'])} de oro no podías avanzar hacia {nxt}: {names} ocupaban hueco sin ser parte de ese objeto. Termina un objeto antes de empezar el siguiente.",
                tags=["build", "clogged"],
            )
        )

    # 2. Objetos terminados vendidos (se pierde ~30 % de su valor).
    sold = []
    for s in inv.sold:
        if cat.is_legendary(s["item"]):
            loss = cat.cost(s["item"]) - cat.get(s["item"]).get("sell", 0)
            sold.append({"t": round(s["t"] / 1000, 1), "item": s["item"], "loss": loss})
    metrics["sold_legendaries"] = len(sold)
    for s in sold:
        moments.append(
            _moment(s["t"] * 1000, "build", "low", f"Vendiste {cat.name(s['item'])}",
                    f"Al venderlo pierdes {_int(s['loss'])} de oro. Si cambias de objeto, que sea por una amenaza concreta.", tags=["build", "sold"])
        )

    # 3. La R a tiempo (niveles 6, 11 y 16).
    skills = [(t, slot) for t, slot, kind in g.skill_ups[me] if kind == "NORMAL" and slot in SKILL_KEYS]
    r_delays = []
    if skills and g.me["champ"] not in NO_R_CHECK:
        reached = {lvl: t for t, lvl in g.levels[me]}
        r_ups = [t for t, slot in skills if slot == 4]
        for lvl in (6, 11, 16):
            if lvl not in reached:
                continue
            tl = reached[lvl]
            tr = next((t for t in r_ups if t >= tl - 1000), None)
            delay = (tr if tr is not None else g.duration_ms) - tl
            if delay > R_LATE_S * 1000 and (tr is not None or g.duration_ms - tl > 60_000):
                r_delays.append({"level": lvl, "t": round(tl / 1000, 1), "delay": round(delay / 1000)})
    metrics["r_late"] = len(r_delays)
    for d in r_delays:
        moments.append(
            _moment(d["t"] * 1000, "build", "medium" if d["level"] == 6 else "low", f"Subiste la R {d['delay']} s tarde (nivel {d['level']})",
                    "Sube la R en cuanto llegues a 6, 11 y 16: es tu mayor pico de poder y el rival lo sabe.", tags=["build", "skills"])
        )
    letters = "".join(SKILL_KEYS[slot] for _t, slot in skills)
    rank: Counter = Counter()
    maxed: list[str] = []
    for ch in letters:
        rank[ch] += 1
        if ch in "QWE" and rank[ch] == 5:
            maxed.append(ch)
    maxed += [k for k, _ in sorted(((k, -rank[k]) for k in "QWE" if k not in maxed), key=lambda x: x[1])]

    # 4. Heridas graves contra curanderos.
    minutes = max(g.minutes, 1)
    healers = []
    for e in g.enemies:
        heal = (_num(g.raw[e].get("totalHeal")) + _num(g.raw[e].get("totalHealsOnTeammates"))) / minutes
        if heal >= HEAVY_HEAL_MIN:
            healers.append({"pid": e, "champ": g.players[e]["champ"], "per_min": round(heal)})
    healers.sort(key=lambda h: h["per_min"], reverse=True)
    has_gw = any(cat.anti_heal(p["item"]) for p in bought)
    phys = _num(raw.get("physicalDamageDealtToChampions"))
    magic = _num(raw.get("magicDamageDealtToChampions"))
    style = "ad" if phys >= magic else "ap"
    suggest = _anti_heal_item(cat, style)
    anti = {"healers": healers, "has": has_gw, "suggest": suggest}
    if healers and not has_gw and g.role in ("TOP", "JUNGLE", "MID", "ADC") and len(legendaries) >= 2 and (healers[0]["per_min"] >= 900 or len(healers) >= 2):
        who = _join([_champ_ref(g, h["pid"]) for h in healers[:2]])
        tool = f"{cat.name(suggest)} ({_int(cat.cost(suggest))})" if suggest else "Un objeto con Heridas graves"
        moments.append(
            _moment(min(g.duration_ms, 15 * 60_000), "build", "low", f"Sin Heridas graves contra {who}",
                    f"Se curaba{'n' if len(healers) > 1 else ''} hasta {_int(healers[0]['per_min'])} por minuto. {tool} corta esa curación un 40 %.",
                    tags=["build", "anti_heal"])
        )
    notes.append({"kind": "anti_heal", **anti})

    # 5. Resistencias frente al daño que recibiste.
    taken = {k: _num(raw.get(f"{k}DamageTaken")) for k in ("physical", "magic", "true")}
    total_taken = sum(taken.values()) or 1
    shares = {k: round(v / total_taken * 100) for k, v in taken.items()}
    need = "armor" if shares["physical"] >= 65 else "mr" if shares["magic"] >= 65 else None
    if need and need not in final_resists and _num(raw.get("deaths")) >= 6 and len(legendaries) >= 2:
        boots_id = 3047 if need == "armor" else 3111
        kind = "físico" if need == "armor" else "mágico"
        res = "armadura" if need == "armor" else "resistencia mágica"
        moments.append(
            _moment(min(g.duration_ms, 20 * 60_000), "build", "low", f"El {shares['physical' if need == 'armor' else 'magic']} % del daño que recibiste fue {kind}",
                    f"Moriste {_num(raw.get('deaths'))} veces sin ningún objeto con {res}. Unas {cat.name(boots_id)} o un objeto defensivo de tu build te habrían dado más margen.",
                    tags=["build", "resist"])
        )

    # 6. Botas.
    if not boots and g.duration_ms >= 22 * 60_000 and g.me["champ"] not in NO_BOOTS:
        moments.append(
            _moment(20 * 60_000, "build", "low", "Sin botas mejoradas al minuto 20",
                    "Las botas de nivel 2 cuestan poco (900-1.250) y dan mucha movilidad y estadísticas: suelen ir entre el primer y el segundo objeto.", tags=["build", "boots"])
        )

    return {
        "order": order,
        "backs": backs,
        "final": [_num(raw.get(f"item{i}")) for i in range(6)],
        "first_item_min": metrics["first_item_min"],
        "second_item_min": metrics["second_item_min"],
        "boots_min": metrics["boots_min"],
        "skills": letters,
        "skill_max": maxed[:3],
        "r_delays": r_delays,
        "sold": sold,
        "anti_heal": anti,
        "damage_taken": shares,
        "resists": sorted(final_resists),
        "damage_style": style,
    }


def _anti_heal_item(cat: ItemCatalog, style: str) -> int | None:
    """El objeto con Heridas graves más barato para tu tipo de daño."""
    want = "SpellDamage" if style == "ap" else "Damage"
    options = [
        (cat.cost(i), i)
        for i, it in cat.items.items()
        if cat.anti_heal(i) and it.get("buyable") and want in it["tags"] and not it.get("champion") and cat.cost(i) <= 1500
    ]
    return min(options)[1] if options else None


# -------------------------------------------------------- series y replay
def _series(g: _Game) -> dict:
    me, opp = g.me["pid"], g.opponent
    keys = ("minute", "gold", "opp_gold", "cs", "opp_cs", "xp", "opp_xp", "current_gold", "level", "team_gold_diff", "useful_gold", "hoard")
    out: dict[str, list] = {k: [] for k in keys}
    for i, t in enumerate(g.ts):
        out["minute"].append(round(t / 60_000, 2))
        out["gold"].append(g.fval(me, i, "totalGold"))
        out["cs"].append(g.fval(me, i, "cs"))
        out["xp"].append(g.fval(me, i, "xp"))
        out["current_gold"].append(g.fval(me, i, "currentGold"))
        out["level"].append(g.fval(me, i, "level"))
        out["opp_gold"].append(g.fval(opp, i, "totalGold") if opp else None)
        out["opp_cs"].append(g.fval(opp, i, "cs") if opp else None)
        out["opp_xp"].append(g.fval(opp, i, "xp") if opp else None)
        row = g.gold_rows.get(i)
        out["useful_gold"].append(row["useful"] if row else None)
        out["hoard"].append(1 if i in g.hoard_frames else 0)
        diff = 0
        for pid, p in g.players.items():
            value = g.fval(pid, i, "totalGold") or 0
            diff += value if p["team"] == g.team else -value
        out["team_gold_diff"].append(diff)
    return out


def _replay(g: _Game) -> dict:
    """Datos compactos para la «repetición» 2D del frontend (tiempos en segundos)."""
    tracks = {}
    for pid in g.players:
        tracks[str(pid)] = {
            "a": [[round(t / 1000, 1), x, y, kind] for t, x, y, kind in g.anchors[pid]],
            "dead": [
                [round(s / 1000, 1), round(e / 1000, 1), p[0] if p else None, p[1] if p else None]
                for s, e, p in g.dead.get(pid) or []
            ],
        }
    return {
        "tracks": tracks,
        "kills": [[round(k["t"] / 1000, 1), k["killer"], k["victim"], *(k["pos"] or (None, None)), k["assists"]] for k in g.kills],
        "objectives": [[round(o["t"] / 1000, 1), o["kind"], o["team"], int(o["pos"][0]), int(o["pos"][1])] for o in g.objectives],
        "buildings": [
            [round(b["t"] / 1000, 1), b["owner"], b["kind"], b["lane"], *(b["pos"] or (None, None))] for b in g.buildings if b["kind"] != "plate"
        ],
    }
