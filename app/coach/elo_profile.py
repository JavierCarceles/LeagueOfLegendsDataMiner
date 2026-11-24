"""Cuántos errores comete cada elo: el mismo análisis de «Mi progreso» aplicado a las partidas de OTPs y de otros
jugadores guardadas en disco (``data/otps`` y ``data/partidas_campeon``), por rol, por elo y por campeón.

Sirve para dos cosas:

* **Compararte con cada elo** en lo mismo que se te mide a ti: errores por partida (todos y por tipo) de Platino,
  Esmeralda, Diamante, Maestro y Aspirante. Si un Aspirante comete un tipo de error tanto como un Platino, ese error no
  es lo que separa los elos y pesa menos en «Errores que repites».
* **Referencias por campeón** para ``champ_checks`` (orden de habilidades, primera compra y ritmo del primer objeto de
  los OTPs de Diamante en adelante).

Leer ~15.000 partidas lleva unos segundos: se hace en segundo plano, se guarda en ``data/.cache/errores_por_elo.json`` y
se rehace sola cuando cambian los ficheros (como mucho cada ``REBUILD_EVERY_S``).
"""

from __future__ import annotations

import gzip
import hashlib
import json
import threading
import time
from collections import Counter, defaultdict
from pathlib import Path

from app.coach import champ_checks, route_macro, rule_metrics
from app.logbus import get_logger

log = get_logger("elo-profile")

VERSION = 6  # 2: medias de movimiento (mv_*) por elo; 3: roams y contraganks (rm_*); 4: reglas medibles (rl_*); 5: destinos de roam y visión; 6: visión y jungla exactas de Riot
CACHE = Path(".cache") / "errores_por_elo.json"
SOURCES = ("otps", "partidas_campeon")
# Con el recolector en marcha los ficheros cambian sin parar: rehacerlo cada media hora (leer ~29.000 partidas) le
# quitaba CPU. Cada 3 h basta (el perfil por elo apenas cambia con unas pocas partidas más).
REBUILD_EVERY_S = 3 * 3600
YIELD_EVERY = 25  # partidas leídas entre pausas cortas: cede la CPU al recolector y a la web
# Grupos de elo que se comparan (de menos a más). Oro o menos cuenta con Platino; Gran Maestro, con Maestro.
GROUPS = ("PLATINUM", "EMERALD", "DIAMOND", "MASTER", "CHALLENGER")
GROUP_OF = {
    "IRON": "PLATINUM", "BRONZE": "PLATINUM", "SILVER": "PLATINUM", "GOLD": "PLATINUM", "PLATINUM": "PLATINUM",
    "EMERALD": "EMERALD", "DIAMOND": "DIAMOND", "MASTER": "MASTER", "GRANDMASTER": "MASTER", "CHALLENGER": "CHALLENGER",
}
GROUP_LABEL = {"PLATINUM": "Platino", "EMERALD": "Esmeralda", "DIAMOND": "Diamante", "MASTER": "Maestro", "CHALLENGER": "Aspirante"}
MIN_GROUP_GAMES = 30  # partidas de un elo (en un rol o campeón) para enseñarlo
MIN_CHAMP_GAMES = 20
COUNTED = ("high", "medium", "low")
MOVE_KEYS = (
    "mv_lane_pre14", "mv_river_pre14", "mv_enemy_jungle_pre14", "mv_base_pct", "mv_grouped_late", "mv_side_alone_late",
) + route_macro.KEYS + rule_metrics.KEYS


# Errores que se detectan desde una versión del análisis sin cambiar ``ANALYSIS_VERSION`` (re-analizar ~35.000 partidas
# frenaría el recolector horas): se cuentan solo en las partidas cuyo análisis trae la métrica que lo marca.
LATER_TYPES = {"death_lens_unused": "deaths_seen_ward", "enemy_pink_ignored": "enemy_pinks_long"}


def game_mistakes(moments: list[dict], role: str | None) -> list[tuple[str, str]]:
    """(tipo, gravedad) de cada error de una partida, con la misma regla que «Errores que repites»."""
    from app.coach.insights import mistake_type  # aquí: evita un import circular

    out = []
    for m in moments or []:
        if m.get("severity") not in COUNTED or m.get("kind") in ("trade", "afk"):
            continue
        mt = mistake_type(m.get("kind"), set(m.get("tags") or []), role)
        if mt is not None:
            out.append((mt["id"], m["severity"]))
    return out


class _Agg:
    """Errores por partida de un grupo de partidas (un elo de un rol o de un campeón)."""

    def __init__(self) -> None:
        self.games = 0
        self.players: set[str] = set()
        self.count: Counter = Counter()
        self.with_type: Counter = Counter()
        self.sev: Counter = Counter()
        self.total = 0
        self.move: dict[str, list[float]] = defaultdict(list)
        self.eligible: Counter = Counter()  # partidas que pueden tener cada tipo de ``LATER_TYPES``

    def add(self, mistakes: list[tuple[str, str]], player: str, metrics: dict | None = None, caps=()) -> None:
        for mid in caps:
            self.eligible[mid] += 1
        for k in MOVE_KEYS:
            v = (metrics or {}).get(k)
            if isinstance(v, (int, float)) and not isinstance(v, bool):
                self.move[k].append(float(v))
        self.games += 1
        self.players.add(player)
        self.total += len(mistakes)
        for mid, sev in mistakes:
            self.count[mid] += 1
            self.sev[sev] += 1
        for mid in {m for m, _ in mistakes}:
            self.with_type[mid] += 1

    def public(self) -> dict:
        n = self.games or 1
        return {
            "games": self.games,
            "players": len(self.players),
            "per_game": round(self.total / n, 3),
            "severity": {s: round(self.sev[s] / n, 3) for s in COUNTED},
            "types": {
                mid: {"per_game": round(c / d, 3), "pct": round(100 * self.with_type[mid] / d, 1)}
                for mid, c in self.count.items()
                if (d := (self.eligible[mid] if mid in LATER_TYPES else n)) >= (MIN_GROUP_GAMES if mid in LATER_TYPES else 1)
            },
            # Medias de movimiento (posiciones reconstruidas; solo partidas analizadas con la versión 14 o más).
            "move": {k: round(sum(v) / len(v), 2) for k, v in self.move.items() if len(v) >= MIN_GROUP_GAMES} or None,
        }


def _files(data_dir: Path) -> list[Path]:
    out = []
    for src in SOURCES:
        base = data_dir / src
        if base.is_dir():
            out += sorted(base.glob("*/*/*/partidas.jsonl.gz"))
    return out


def signature(data_dir: Path) -> str:
    from app.coach import ranking_stats

    h = hashlib.sha1(str(VERSION).encode())
    for f in [*_files(data_dir), Path(data_dir) / "otps" / ranking_stats.FILE]:
        try:
            st = f.stat()
        except OSError:
            continue
        h.update(f"{f}:{st.st_size}:{int(st.st_mtime)}".encode())
    return h.hexdigest()


def _records(data_dir: Path, throttle: bool = False):
    from app.coach.insights import reliable  # aquí: evita un import circular

    read = 0
    for f in _files(data_dir):
        role = f.parent.parent.name
        player = f.parent.name
        try:
            with gzip.open(f, "rt", encoding="utf-8") as fh:
                for line in fh:
                    try:
                        d = json.loads(line)
                    except ValueError:
                        continue  # una línea a medio escribir
                    a = d.get("analysis") or {}
                    if not a.get("has_timeline") or not reliable(a):
                        continue
                    skill_t = [s[0] for s in ((d.get("events") or {}).get("skills") or []) if len(s) >= 3 and s[2] == "NORMAL"]
                    rec = champ_checks.record_from_analysis(a, skill_t or None)
                    rec.update(role=d.get("role") or role, champ=d.get("champ"), tier=d.get("tier"), player=player)
                    rec["mistakes"] = game_mistakes(a.get("moments") or [], rec["role"])
                    read += 1
                    if throttle and read % YIELD_EVERY == 0:
                        time.sleep(0.005)
                    rec["move"] = {k: (a.get("metrics") or {}).get(k) for k in MOVE_KEYS}
                    rec["caps"] = [mid for mid, key in LATER_TYPES.items() if key in (a.get("metrics") or {})]
                    try:
                        rec["move"].update(route_macro.from_dossier(d))
                    except Exception:  # noqa: BLE001  (un dossier raro no puede parar el perfil)
                        pass
                    try:
                        rec["move"].update(rule_metrics.from_dossier(d))
                    except Exception:  # noqa: BLE001
                        pass
                    yield rec
        except (OSError, EOFError, gzip.BadGzipFile):
            log.debug("No se pudo leer %s", f, exc_info=True)


def build(data_dir: Path, catalog=None, champ_name=None, throttle: bool = False) -> dict:
    """Lee todas las partidas guardadas y devuelve el perfil de errores por rol, elo y campeón."""
    t0 = time.monotonic()
    records = list(_records(Path(data_dir), throttle))
    refs: dict[tuple, champ_checks.RefBuilder] = defaultdict(champ_checks.RefBuilder)
    for r in records:
        if r.get("tier") in champ_checks.REF_TIERS and r.get("champ") and r.get("role"):
            refs[(r["champ"], r["role"])].add(r, catalog)
    champ_refs = {k: b.ref() for k, b in refs.items()}

    roles: dict[str, dict[str, _Agg]] = defaultdict(lambda: defaultdict(_Agg))
    champs: dict[tuple, dict[str, _Agg]] = defaultdict(lambda: defaultdict(_Agg))
    for r in records:
        group = GROUP_OF.get((r.get("tier") or "").upper())
        if not group or not r.get("role"):
            continue
        key = (r.get("champ"), r["role"])
        name = (champ_name(r["champ"]) if champ_name and r.get("champ") else None) or "tu campeón"
        extra = champ_checks.check(r, champ_refs.get(key), catalog, name)
        mistakes = r["mistakes"] + game_mistakes(extra, r["role"])
        roles[r["role"]][group].add(mistakes, r["player"], r.get("move"), r.get("caps") or ())
        if r.get("champ"):
            champs[key][group].add(mistakes, r["player"], r.get("move"), r.get("caps") or ())

    def blocks(groups: dict[str, _Agg], min_games: int) -> dict:
        return {g: groups[g].public() for g in GROUPS if g in groups and groups[g].games >= min_games}

    out = {
        "version": VERSION,
        "built_at": int(time.time()),
        "games": len(records),
        "roles": {role: b for role, groups in roles.items() if (b := blocks(groups, MIN_GROUP_GAMES))},
        "champs": {
            f"{c}:{role}": {"tiers": blocks(groups, MIN_CHAMP_GAMES), "ref": champ_refs.get((c, role))}
            for (c, role), groups in champs.items()
            if blocks(groups, MIN_CHAMP_GAMES) or champ_refs.get((c, role))
        },
    }
    _merge_ranking(out, Path(data_dir))
    # Referencias de campeones sin partidas propias en ningún elo con muestra (pero sí de Diamante+).
    for (c, role), ref in champ_refs.items():
        out["champs"].setdefault(f"{c}:{role}", {"tiers": {}, "ref": ref})
    log.info("Perfil de errores por elo: %d partidas en %.1f s.", len(records), time.monotonic() - t0)
    return out


def _merge_ranking(out: dict, data_dir: Path) -> None:
    """Las medias ``rl_*`` (estadísticas exactas de Riot) con las partidas del ranking sin analizar (``ranking_stats``):
    mezcladas con las de las partidas analizadas, cada una pesando sus partidas. Solo en los elos que ya tienen bloque."""
    from app.coach import ranking_stats

    data = ranking_stats.load(data_dir)
    if not data:
        return
    for role, groups in (data.get("roles") or {}).items():
        tiers = (out.get("roles") or {}).get(role) or {}
        for group, metrics in groups.items():
            block = tiers.get(group)
            if block is None:
                continue
            move = block.get("move") or {}
            counts = block.setdefault("move_n", {})
            for key, (n, s, _s2) in metrics.items():
                if not n:
                    continue
                own_n = counts.get(key) or (block.get("games") or 0 if move.get(key) is not None else 0)
                own_sum = (move.get(key) or 0.0) * own_n
                total = own_n + n
                move[key] = round((own_sum + s) / total, 2)
                counts[key] = total
            block["move"] = move


class EloProfile:
    """El perfil en memoria (y en caché en disco), rehecho en segundo plano cuando cambian las partidas guardadas."""

    _instances: dict[str, EloProfile] = {}
    _lock = threading.Lock()

    def __init__(self, svc) -> None:
        self.svc = svc
        self.data_dir = Path(svc.settings.data_dir)
        self.path = self.data_dir / CACHE
        self.data: dict | None = None
        self.sig: str | None = None
        self.checked_at = 0.0
        self.thread: threading.Thread | None = None
        self.lock = threading.Lock()
        self._load()

    @classmethod
    def of(cls, svc) -> EloProfile | None:
        settings = getattr(svc, "settings", None)
        if settings is None or getattr(settings, "data_dir", None) is None:
            return None
        with cls._lock:
            key = str(settings.data_dir)
            inst = cls._instances.get(key)
            if inst is None or inst.svc is not svc:
                inst = cls._instances[key] = cls(svc)
            return inst

    def _load(self) -> None:
        try:
            cached = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return
        if isinstance(cached, dict) and cached.get("version") == VERSION:
            self.data, self.sig = cached.get("profile"), cached.get("signature")

    def get(self, refresh: bool = True) -> dict | None:
        """El perfil ya calculado (o ``None`` la primera vez, mientras se calcula). Nunca bloquea."""
        if refresh:
            self.refresh()
        return self.data

    def refresh(self, force: bool = False) -> None:
        now = time.time()
        built = (self.data or {}).get("built_at") or 0
        if not force and self.data is not None and now - built < REBUILD_EVERY_S:
            return
        if not force and now - self.checked_at < 60:
            return
        self.checked_at = now
        with self.lock:
            if self.thread is not None and self.thread.is_alive():
                return
            self.thread = threading.Thread(target=self._rebuild, args=(force,), name="elo-profile", daemon=True)
            self.thread.start()

    def _rebuild(self, force: bool) -> None:
        try:
            sig = signature(self.data_dir)
            if not force and sig == self.sig and self.data is not None:
                return
            dd = getattr(self.svc, "dd", None)
            catalog = getattr(dd, "item_catalog", None)
            name = (lambda c: dd.name(int(c))) if dd is not None and hasattr(dd, "name") else None
            data = build(self.data_dir, catalog if catalog else None, name, throttle=True)
            self.data, self.sig = data, sig
            self.path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.path.with_suffix(".json.tmp")
            tmp.write_text(json.dumps({"version": VERSION, "signature": sig, "profile": data}, ensure_ascii=False), encoding="utf-8")
            tmp.replace(self.path)
        except Exception:
            log.exception("No se pudo calcular el perfil de errores por elo")

    def champ_ref(self, champ: int | None, role: str | None) -> dict | None:
        if not champ or not role or not self.data:
            return None
        return ((self.data.get("champs") or {}).get(f"{champ}:{role}") or {}).get("ref")
