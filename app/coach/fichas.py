"""Fichas de campeón (``macro/campeones/<clave>.json``): lo que hay que saber de cada campeón en su rol principal (o
sus dos roles, si juega mucho los dos), sacado de internet campeón a campeón (26-11-2026, parche 16.23).

Sirven al reconstructor de partidas para interpretar lo que se ve en el timeline de Riot: picos de poder (¿pelea en
su momento fuerte o en el del rival?), rutas de jungla, movilidad global (Twisted Fate, Shen o Pantheon «saltan» de
una punta a otra con la R), saltos de muros, invisibilidad, cuándo roamea un mid o un support, etc. El formato está
en ``macro/campeones/FORMATO.md``; ``comprobar`` lo valida (``python -m app.coach.fichas --comprobar``).
"""

from __future__ import annotations

import json
import re
import sys
from functools import lru_cache
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
DIR = ROOT / "macro" / "campeones"
DD_DIR = ROOT / "data" / "ddragon"
ROLES = ("TOP", "JUNGLE", "MID", "ADC", "SUPPORT")
CAMPS = ("blue", "gromp", "wolves", "raptors", "red", "krugs", "scuttle_top", "scuttle_bot")
KEYS = ("P", "Q", "W", "E", "R")
REACH = ("cuerpo_a_cuerpo", "distancia")
WALLS = ("no", "si", "con_condiciones")
GLOBAL_KINDS = ("teletransporte", "daño", "escudo", "curacion", "vision", "control", "otro")
REQUIRED = (
    "clave", "nombre", "parche", "revisado", "roles", "arquetipo", "alcance", "fuerza_por_fase", "picos_de_poder",
    "habilidades", "orden_habilidades", "movilidad", "unico", "combos", "build", "por_rol", "peleas", "macro",
    "debilidades", "contrarrestado_por", "bueno_contra", "sinergias", "errores_comunes", "senales_reconstructor",
    "fuentes",
)
# Lo mínimo de cada rol (se puede añadir más: cuenta todo lo útil).
ROLE_TEXT = {
    "JUNGLE": ("inicio", "ganks", "objetivos", "invasion", "plan"),
    "TOP": ("roameos", "fase_media"),
    "MID": ("roameos", "fase_media"),
    "ADC": ("fase_media",),
    "SUPPORT": ("roameos", "vision", "fase_media"),
}
LANE_TEXT = ("niveles_1_3", "tradeos", "oleadas", "all_in", "jugar_seguro")
DUO_TEXT = ("nivel_2", "tradeos", "oleadas")


def _norm(text) -> str:
    return re.sub(r"[^a-z0-9]", "", str(text or "").lower())


@lru_cache(maxsize=1)
def champions() -> dict[str, dict]:
    """Los campeones de la caché de Data Dragon más nueva: ``{clave: {"nombre", "id"}}`` (vacío sin caché)."""
    versions = sorted((p for p in DD_DIR.glob("*") if (p / "champion.json").exists()),
                      key=lambda p: [int(x) if x.isdigit() else 0 for x in p.name.split(".")])
    if not versions:
        return {}
    data = json.loads((versions[-1] / "champion.json").read_text(encoding="utf-8"))["data"]
    return {k: {"nombre": v["name"], "id": int(v["key"])} for k, v in data.items()}


@lru_cache(maxsize=1)
def _aliases() -> dict[str, str]:
    from app.ddragon import EXTRA_ALIASES

    out = {_norm(alias): key for alias, key in EXTRA_ALIASES.items()}
    for key, info in champions().items():
        out[_norm(key)] = key
        out[_norm(info["nombre"])] = key
    out.update({"maestroyi": "MasterYi", "nunuywillump": "Nunu", "bardo": "Bard", "wukong": "MonkeyKing"})
    return out


def resolve(name: str) -> str | None:
    """Clave de Data Dragon de un nombre de campeón (inglés, español, clave o apodo habitual)."""
    return _aliases().get(_norm(name))


def load(key: str) -> dict | None:
    path = DIR / f"{key}.json"
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


@lru_cache(maxsize=1)
def all_cards() -> dict[str, dict]:
    """Todas las fichas por clave de Data Dragon."""
    out = {}
    for path in sorted(DIR.glob("*.json")):
        try:
            card = json.loads(path.read_text(encoding="utf-8"))
        except ValueError:
            continue
        out[card.get("clave") or path.stem] = card
    return out


def by_id(champ_id: int) -> dict | None:
    """La ficha de un campeón por su id numérico (el de las partidas de Riot)."""
    for key, info in champions().items():
        if info["id"] == int(champ_id):
            return all_cards().get(key)
    return None


SLOTS = {"Q": 0, "W": 1, "E": 2, "R": 3}  # ``spellSlot`` de Riot en el daño de cada kill
FAR_KINDS = ("daño", "curacion", "escudo")  # efectos que llegan sin que quien los lanza se mueva (no saltos ni arrastres)
FAR_REACH = 2000  # alcance desde el que lanzarla no dice dónde estaba quien la lanzó


def _reach(text) -> float | None:
    """Unidades de un alcance escrito («global», «5000 unidades», «2.500-3.500 u»): el mayor número o infinito."""
    s = str(text or "").lower()
    if "global" in s or "todo el mapa" in s:
        return float("inf")
    nums = [float(n.replace(".", "").replace(",", "")) for n in re.findall(r"\d[\d.,]*\d|\d", s)]
    return max(nums) if nums else None


@lru_cache(maxsize=1)
def far_reach() -> dict[int, dict[int, str]]:
    """``{id del campeón: {ranura 0-3 (Q-R): tipo}}`` de las habilidades que dañan, curan o protegen a ``FAR_REACH`` o
    más (R de Jinx, Ezreal, Karthus o Senna, R de Soraka o de Shen…), sacado de ``movilidad.global`` de las fichas."""
    ids = {key: info["id"] for key, info in champions().items()}
    out: dict[int, dict[int, str]] = {}
    for key, card in all_cards().items():
        cid = ids.get(key)
        for eff in ((card.get("movilidad") or {}).get("global") or []) if cid is not None else ():
            slot = SLOTS.get(eff.get("habilidad"))
            reach = _reach(eff.get("alcance"))
            if slot is not None and eff.get("tipo") in FAR_KINDS and reach is not None and reach >= FAR_REACH:
                out.setdefault(cid, {})[slot] = eff["tipo"]
    return out


# ------------------------------------------------------------------ validación
def _text(errors: list, where: str, value, min_len: int = 3) -> None:
    if not isinstance(value, str) or len(value.strip()) < min_len:
        errors.append(f"{where}: falta el texto")


def _texts(errors: list, where: str, value, minimum: int) -> None:
    if not isinstance(value, list) or len([v for v in value if isinstance(v, str) and v.strip()]) < minimum:
        errors.append(f"{where}: hacen falta al menos {minimum} textos")


def _names(errors: list, where: str, value, minimum: int = 0) -> None:
    if not isinstance(value, list) or len(value) < minimum:
        errors.append(f"{where}: hace falta una lista de campeones (al menos {minimum})")
        return
    if not champions():
        return
    for name in value:
        if resolve(name) is None:
            errors.append(f"{where}: «{name}» no es un campeón (usa el nombre en inglés de Data Dragon)")


def _route(errors: list, where: str, route) -> None:
    if not isinstance(route, dict):
        errors.append(f"{where}: cada ruta es un objeto")
        return
    _text(errors, f"{where}.nombre", route.get("nombre"))
    order = route.get("orden")
    if not isinstance(order, list) or len(order) < 2 or any(c not in CAMPS for c in order):
        errors.append(f"{where}.orden: lista de campamentos de {CAMPS}")
    _text(errors, f"{where}.despues", route.get("despues"))


def _role(errors: list, role: str, block) -> None:
    where = f"por_rol.{role}"
    if not isinstance(block, dict):
        errors.append(f"{where}: falta el bloque del rol")
        return
    for key in ROLE_TEXT[role]:
        _text(errors, f"{where}.{key}", block.get(key))
    if role == "JUNGLE":
        routes = block.get("rutas")
        if not isinstance(routes, list) or not routes:
            errors.append(f"{where}.rutas: al menos una ruta")
        for i, route in enumerate(routes or []):
            _route(errors, f"{where}.rutas[{i}]", route)
        return
    if role in ("TOP", "MID"):
        lane = block.get("linea")
        if not isinstance(lane, dict):
            errors.append(f"{where}.linea: falta")
        else:
            for key in LANE_TEXT:
                _text(errors, f"{where}.linea.{key}", lane.get(key))
    else:
        duo = block.get("linea_2v2")
        if not isinstance(duo, dict):
            errors.append(f"{where}.linea_2v2: falta")
        else:
            for key in DUO_TEXT:
                _text(errors, f"{where}.linea_2v2.{key}", duo.get(key))
            partner = "con_que_supports" if role == "ADC" else "con_que_adcs"
            _names(errors, f"{where}.linea_2v2.{partner}", duo.get(partner), 2)
            _names(errors, f"{where}.linea_2v2.sufre_contra", duo.get("sufre_contra"), 1)
    _names(errors, f"{where}.matchups_dificiles", block.get("matchups_dificiles"), 1)
    _names(errors, f"{where}.matchups_faciles", block.get("matchups_faciles"), 1)


def validate(card: dict, key: str | None = None) -> list[str]:
    """Errores de formato de una ficha (lista vacía = bien)."""
    errors: list[str] = []
    if not isinstance(card, dict):
        return ["la ficha no es un objeto JSON"]
    for field in REQUIRED:
        if field not in card:
            errors.append(f"falta «{field}»")
    if errors:
        return errors
    known = champions()
    if key and card["clave"] != key:
        errors.append(f"clave «{card['clave']}» distinta del fichero ({key})")
    if known and card["clave"] not in known:
        errors.append(f"clave «{card['clave']}» no está en Data Dragon")
    roles = card["roles"]
    names = [r.get("rol") for r in roles] if isinstance(roles, list) and all(isinstance(r, dict) for r in roles) else []
    if not 1 <= len(names) <= 3 or len(set(names)) != len(names) or any(r not in ROLES for r in names):
        errors.append(f"roles: 1 a 3 roles distintos de {ROLES}")
    elif sum(1 for r in roles if r.get("principal")) != 1:
        errors.append("roles: exactamente uno con «principal»: true")
    if card["alcance"] not in REACH:
        errors.append(f"alcance: uno de {REACH}")
    phases = card["fuerza_por_fase"]
    if not isinstance(phases, dict) or any(not isinstance(phases.get(p), int) or not 1 <= phases[p] <= 5
                                           for p in ("temprana", "media", "tardia")):
        errors.append("fuerza_por_fase: temprana, media y tardia de 1 a 5")
    spikes = card["picos_de_poder"]
    if not isinstance(spikes, list) or len(spikes) < 2:
        errors.append("picos_de_poder: al menos 2")
    for i, spike in enumerate(spikes if isinstance(spikes, list) else []):
        if not isinstance(spike, dict):
            errors.append(f"picos_de_poder[{i}]: objeto")
            continue
        _text(errors, f"picos_de_poder[{i}].cuando", spike.get("cuando"))
        _text(errors, f"picos_de_poder[{i}].por_que", spike.get("por_que"))
        level, items = spike.get("nivel"), spike.get("objetos")
        if level is not None and (not isinstance(level, int) or not 1 <= level <= 18):
            errors.append(f"picos_de_poder[{i}].nivel: 1-18 o null")
        if items is not None and (not isinstance(items, int) or not 0 <= items <= 6):
            errors.append(f"picos_de_poder[{i}].objetos: 0-6 o null")
    skills = card["habilidades"]
    if not isinstance(skills, dict):
        errors.append("habilidades: objeto")
    else:
        for k in ("pasiva", "Q", "W", "E", "R"):
            _text(errors, f"habilidades.{k}", skills.get(k), 10)
    order = card["orden_habilidades"]
    if not isinstance(order, dict):
        errors.append("orden_habilidades: objeto")
    else:
        maxing = order.get("maximizar")
        if not isinstance(maxing, list) or not 3 <= len(maxing) <= 4 or len(set(maxing)) != len(maxing) \
                or any(k not in KEYS[1:] for k in maxing):
            errors.append("orden_habilidades.maximizar: Q, W y E (y R si se sube como básica) en orden")
        first = order.get("niveles_1_3")
        if not isinstance(first, list) or len(first) != 3 or any(k not in KEYS[1:] for k in first):
            errors.append("orden_habilidades.niveles_1_3: las 3 primeras subidas")
    move = card["movilidad"]
    if not isinstance(move, dict):
        errors.append("movilidad: objeto")
    else:
        _text(errors, "movilidad.desplazamientos", move.get("desplazamientos"))
        if move.get("salta_muros") not in WALLS:
            errors.append(f"movilidad.salta_muros: uno de {WALLS}")
        elif move["salta_muros"] != "no":
            _text(errors, "movilidad.como_salta_muros", move.get("como_salta_muros"))
        if move.get("invisibilidad") is not None:
            _text(errors, "movilidad.invisibilidad", move.get("invisibilidad"))
        effects = move.get("global")
        if not isinstance(effects, list):
            errors.append("movilidad.global: lista (vacía si no tiene nada de largo alcance)")
        for i, eff in enumerate(effects or []):
            if not isinstance(eff, dict) or eff.get("habilidad") not in KEYS or eff.get("tipo") not in GLOBAL_KINDS \
                    or not isinstance(eff.get("desde_nivel"), int):
                errors.append(f"movilidad.global[{i}]: habilidad {KEYS}, tipo {GLOBAL_KINDS} y desde_nivel")
            else:
                _text(errors, f"movilidad.global[{i}].alcance", eff.get("alcance"))
    _texts(errors, "unico", card["unico"], 3)
    _texts(errors, "combos", card["combos"], 1)
    build = card["build"]
    if not isinstance(build, dict):
        errors.append("build: objeto")
    else:
        _texts(errors, "build.objetos_clave", build.get("objetos_clave"), 1)
        _text(errors, "build.runas", build.get("runas"))
        _texts(errors, "build.hechizos", build.get("hechizos"), 1)
    blocks = card["por_rol"]
    if not isinstance(blocks, dict):
        errors.append("por_rol: objeto")
    else:
        for role in names:
            if role in ROLES:
                _role(errors, role, blocks.get(role))
    for field, keys in (("peleas", ("papel", "como_entrar", "objetivo", "posicion")),
                        ("macro", ("split_push", "objetivos", "plan_de_partida"))):
        block = card[field]
        if not isinstance(block, dict):
            errors.append(f"{field}: objeto")
            continue
        for k in keys:
            _text(errors, f"{field}.{k}", block.get(k))
    _texts(errors, "debilidades", card["debilidades"], 2)
    _names(errors, "contrarrestado_por", card["contrarrestado_por"], 1)
    _names(errors, "bueno_contra", card["bueno_contra"], 1)
    _names(errors, "sinergias", card["sinergias"], 1)
    _texts(errors, "errores_comunes", card["errores_comunes"], 2)
    _texts(errors, "senales_reconstructor", card["senales_reconstructor"], 2)
    sources = card["fuentes"]
    if not isinstance(sources, list) or len([s for s in sources if str(s).startswith("http")]) < 2:
        errors.append("fuentes: al menos 2 enlaces")
    return errors


def check(paths: list[Path] | None = None) -> dict[str, list[str]]:
    """``{fichero: errores}`` de las fichas dadas (o de todas)."""
    out = {}
    for path in paths or sorted(DIR.glob("*.json")):
        try:
            card = json.loads(Path(path).read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            out[str(path)] = [f"JSON ilegible: {exc}"]
            continue
        errors = validate(card, Path(path).stem)
        if errors:
            out[str(path)] = errors
    return out


def missing() -> list[str]:
    """Campeones de Data Dragon sin ficha."""
    return sorted(k for k in champions() if not (DIR / f"{k}.json").exists())


def main(argv: list[str]) -> int:
    if argv and argv[0] == "--faltan":
        rest = missing()
        print(f"{len(champions())} campeones en Data Dragon, {len(champions()) - len(rest)} con ficha.")
        print("Sin ficha:", ", ".join(rest) or "ninguno")
        return 0
    paths = [Path(p) for p in argv[1:]] if argv and argv[0] == "--comprobar" and len(argv) > 1 else None
    bad = check(paths)
    for path, errors in bad.items():
        print(path)
        for e in errors:
            print("  -", e)
    print("Todo bien." if not bad else f"{len(bad)} fichas con errores.")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
