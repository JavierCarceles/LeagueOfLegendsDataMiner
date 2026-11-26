"""Cifras de los OTPs del programa (``data/otps``) en cada ficha de campeón: el bloque ``datos_otp``.

Lo que las guías de internet no dan con números: a qué minuto vuelve a base y compra cada objeto, con qué orden sube
las habilidades, por qué campamento empieza y cuándo gankea, cuánto CS de jungla lleva a los 3:30, cuánto tiempo
pasa en otras líneas antes del 14 (roameos) o solo en una lateral al final (split push), y cómo gana según lo que dura
la partida (su curva de fuerza). De OTPs de Diamante en adelante (si hay pocos, de todos los elos, y lo dice).

``python -m app.coach.fichas_datos`` rehace el bloque en todas las fichas (se puede repetir cuando haya más partidas).
"""

from __future__ import annotations

import gzip
import json
import os
import statistics
import sys
import time
from collections import Counter, defaultdict
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

from app.coach import fichas as F

OTP_DIR = F.ROOT / "data" / "otps"
OTHER_DIR = F.ROOT / "data" / "partidas_campeon"  # jugadores que no son OTP: de reserva (Ziggs no tiene OTPs)
HIGH = frozenset({"DIAMOND", "MASTER", "GRANDMASTER", "CHALLENGER"})
MIN_HIGH = 40  # con menos partidas de Diamante+ se usan todos los elos
MIN_ROLE = 20  # menos partidas en un rol: no se resume
LENGTH_BINS = ((0, 20), (20, 25), (25, 30), (30, 35), (35, 99))
SKILL_PREFIX = 9  # orden de las 9 primeras subidas


def _q(values: list[float], digits: int = 1) -> dict | None:
    """Percentiles 25, 50 y 75 (``None`` sin datos)."""
    vals = sorted(v for v in values if v is not None)
    if not vals:
        return None
    if len(vals) == 1:
        return {"p25": round(vals[0], digits), "mediana": round(vals[0], digits), "p75": round(vals[0], digits)}
    q = statistics.quantiles(vals, n=4, method="inclusive")
    return {"p25": round(q[0], digits), "mediana": round(q[1], digits), "p75": round(q[2], digits)}


def _shares(counter: Counter, total: int, top: int = 4) -> list:
    return [[k, round(v / total, 2)] for k, v in counter.most_common(top)] if total else []


def _item_names() -> dict[int, str]:
    versions = sorted((p for p in F.DD_DIR.glob("*") if (p / "item.json").exists()),
                      key=lambda p: [int(x) if x.isdigit() else 0 for x in p.name.split(".")])
    if not versions:
        return {}
    data = json.loads((versions[-1] / "item.json").read_text(encoding="utf-8"))["data"]
    return {int(k): v["name"] for k, v in data.items()}


def _jungle_cs_at(frames: list, minute: float) -> float | None:
    """CS de jungla interpolado a ese minuto (los frames son de minuto en minuto)."""
    by_min = {round(f.get("min", -1)): f.get("jungle_cs") for f in frames or []}
    lo, hi = int(minute), int(minute) + 1
    if by_min.get(lo) is None or by_min.get(hi) is None:
        return None
    return by_min[lo] + (by_min[hi] - by_min[lo]) * (minute - lo)


def _game_rows(path: Path) -> list[dict]:
    """Lo que hace falta de cada partida de un fichero ``partidas.jsonl.gz`` (lo pesado se descarta enseguida)."""
    rows = []
    try:
        with gzip.open(path, "rt", encoding="utf-8") as fh:
            for line in fh:
                try:
                    g = json.loads(line)
                except ValueError:
                    continue
                a = g.get("analysis") or {}
                m = a.get("metrics") or {}
                build = a.get("build") or {}
                jungle = a.get("jungle") or {}
                me = a.get("me")
                move = ((a.get("movement") or {}).get(str(me)) or {}).get("early") or {}
                first_leg = next((o.get("item") for o in build.get("order") or [] if o.get("kind") == "legendary"), None)
                ganks = [k.get("zone") for k in jungle.get("ganks") or [] if (k.get("t") or 0) <= 600]
                rows.append({
                    "champ": g.get("champ"), "role": g.get("role"), "tier": g.get("tier"), "patch": g.get("patch"),
                    "win": bool(g.get("win")), "min": (g.get("duration") or 0) / 60,
                    "recall": m.get("first_recall_min"), "item1": m.get("first_item_min"),
                    "item2": m.get("second_item_min"), "first_leg": first_leg,
                    "skills": (build.get("skills") or "")[:SKILL_PREFIX], "max": "".join(build.get("skill_max") or []),
                    "kp": m.get("kp"), "dmg": m.get("dmg_share"), "vision": m.get("vision_min"),
                    "deaths14": m.get("deaths_pre14"), "solo": m.get("solo_kills"), "cs10": m.get("cs10"),
                    "csd10": m.get("cs_diff10"), "gd10": m.get("gold_diff10"), "plates": m.get("plates"),
                    "side": m.get("mv_side_alone_late"), "grouped": m.get("mv_grouped_late"),
                    "other_lane": move.get("other_lane", 0.0) if move else None, "river": move.get("river", 0.0) if move else None,
                    "wards14": m.get("wards_pre14"), "pinks": m.get("control_wards"),
                    "start": jungle.get("start_camp"), "jcs330": _jungle_cs_at(g.get("frames"), 3.5),
                    "jcs10": m.get("jungle_cs10"), "gank1": m.get("first_gank_min"), "ganks10": m.get("ganks_pre10"),
                    "gank_zones": ganks, "invade": m.get("invade_cs_pre10"), "crabs": m.get("crabs_pre10"),
                })
    except (OSError, EOFError) as exc:
        print(f"  {path}: {exc}", file=sys.stderr)
    return rows


def _folder_rows(job: tuple[str, str]) -> list[dict]:
    root, folder = job
    out = []
    base = Path(root) / folder
    if not base.is_dir():
        return out
    for role in os.listdir(base):
        role_dir = base / role
        if role not in F.ROLES or not role_dir.is_dir():
            continue
        for player in os.listdir(role_dir):
            path = role_dir / player / "partidas.jsonl.gz"
            if path.exists():
                out.extend({**r, "otp": root == str(OTP_DIR)} for r in _game_rows(path))
    return out


def _summary(rows: list[dict], role: str, items: dict[int, str]) -> dict:
    n = len(rows)
    out: dict = {"partidas": n}
    wins_by_len = []
    for lo, hi in LENGTH_BINS:
        part = [r for r in rows if lo <= r["min"] < hi]
        if len(part) >= 30:  # con menos, el % sale demasiado al azar
            wins_by_len.append({"minutos": f"{lo}-{hi}" if hi < 99 else f"{lo}+", "partidas": len(part),
                                "victorias": round(sum(r["win"] for r in part) / len(part), 3)})
    out["victorias"] = round(sum(r["win"] for r in rows) / n, 3)
    out["victorias_por_duracion"] = wins_by_len
    out["primera_vuelta_min"] = _q([r["recall"] for r in rows])
    out["primer_objeto_min"] = _q([r["item1"] for r in rows])
    out["segundo_objeto_min"] = _q([r["item2"] for r in rows])
    firsts = Counter(items.get(r["first_leg"], str(r["first_leg"])) for r in rows if r["first_leg"])
    out["primer_objeto"] = _shares(firsts, sum(firsts.values()))
    skills = Counter(r["skills"] for r in rows if len(r["skills"]) == SKILL_PREFIX)
    out["orden_9_primeras_subidas"] = _shares(skills, sum(skills.values()), 3)
    maxing = Counter(r["max"] for r in rows if r["max"])
    out["maximiza"] = _shares(maxing, sum(maxing.values()), 3)
    out["participacion_kills_pct"] = _q([r["kp"] for r in rows])
    out["parte_del_dano_pct"] = _q([r["dmg"] for r in rows])
    out["vision_por_min"] = _q([r["vision"] for r in rows], 2)
    out["muertes_antes_14"] = _q([r["deaths14"] for r in rows])
    out["solo_kills_media"] = round(statistics.fmean([r["solo"] or 0 for r in rows]), 2)
    out["tiempo_solo_en_lateral_tarde_pct"] = _q([r["side"] for r in rows])
    out["tiempo_agrupado_tarde_pct"] = _q([r["grouped"] for r in rows])
    if role == "JUNGLE":
        starts = Counter(r["start"] for r in rows if r["start"])
        zones = Counter(z for r in rows for z in r["gank_zones"] if z)
        out["campamento_inicial"] = _shares(starts, sum(starts.values()), 6)
        out["cs_jungla_3_30"] = _q([r["jcs330"] for r in rows])
        out["cs_jungla_10"] = _q([r["jcs10"] for r in rows])
        out["primer_gank_min"] = _q([r["gank1"] for r in rows])
        out["ganks_antes_10"] = _q([r["ganks10"] for r in rows])
        out["ganks_antes_10_por_zona"] = _shares(zones, sum(zones.values()), 5)
        out["cs_robado_antes_10"] = _q([r["invade"] for r in rows])
        out["escuttles_antes_10"] = _q([r["crabs"] for r in rows])
    else:
        out["cs_10"] = _q([r["cs10"] for r in rows])
        out["dif_cs_10"] = _q([r["csd10"] for r in rows])
        out["dif_oro_10"] = _q([r["gd10"] for r in rows], 0)
        out["placas"] = _q([r["plates"] for r in rows])
        out["tiempo_en_otra_linea_antes_14_pct"] = _q([r["other_lane"] for r in rows])
        out["tiempo_en_rio_antes_14_pct"] = _q([r["river"] for r in rows])
        if role == "SUPPORT":
            out["wards_antes_14"] = _q([r["wards14"] for r in rows])
            out["pinks"] = _q([r["pinks"] for r in rows])
    return out


def compute(folders: list[str] | None = None, workers: int | None = None) -> dict[str, dict]:
    """``{clave: datos_otp}`` de todos los campeones con partidas de OTPs."""
    jobs = []
    for root in (OTP_DIR, OTHER_DIR):
        names = folders or (sorted(d for d in os.listdir(root) if (root / d).is_dir()) if root.is_dir() else [])
        jobs += [(str(root), name) for name in names]
    ids = {info["id"]: key for key, info in F.champions().items()}
    items = _item_names()
    by_key: dict[str, list[dict]] = defaultdict(list)
    with ProcessPoolExecutor(max_workers=workers or max(1, (os.cpu_count() or 2) - 1)) as pool:
        for rows in pool.map(_folder_rows, jobs):
            for r in rows:
                key = ids.get(r["champ"])
                if key:
                    by_key[key].append(r)
    out = {}
    for key, rows in by_key.items():
        roles: dict[str, dict] = {}
        tiers_used = set()
        for role in F.ROLES:
            otp = [r for r in rows if r["role"] == role and r["otp"]]
            others = len(otp) < MIN_ROLE
            mine = [r for r in rows if r["role"] == role and r["otp"] != others]
            high = [r for r in mine if r["tier"] in HIGH]
            pool_ = high if len(high) >= MIN_HIGH else mine
            use, patches = _recent(pool_)
            if len(use) < MIN_ROLE:
                continue
            block = _summary(use, role, items)
            block["elo"] = "Diamante+" if pool_ is high else "todos los elos (pocas de Diamante+)"
            block["jugadores"] = "no OTP (no hay bastantes OTPs)" if others else "OTPs"
            block["parches"] = f"{patches[0]}-{patches[-1]}" if len(patches) > 1 else patches[0]
            roles[role] = block
        if not roles:
            continue
        out[key] = {
            "fuente": "partidas guardadas por el programa (data/otps; si no hay bastantes OTPs, data/partidas_campeon): "
                      "de Diamante en adelante y de los parches más recientes que dan bastantes partidas (cada rol lo dice)",
            "actualizado": time.strftime("%Y-%m-%d"),
            "por_rol": roles,
        }
    return out


RECENT_MIN = 300  # partidas que se buscan juntando parches desde el más nuevo hacia atrás


def _patch_key(p: str) -> list[int]:
    return [int(x) if x.isdigit() else 0 for x in str(p).split(".")]


def _recent(rows: list[dict]) -> tuple[list[dict], list[str]]:
    """Las partidas de los parches más nuevos que juntan ``RECENT_MIN`` (o todas, si no llegan)."""
    by_patch: dict[str, list[dict]] = defaultdict(list)
    for r in rows:
        if r["patch"]:
            by_patch[r["patch"]].append(r)
    chosen: list[str] = []
    picked: list[dict] = []
    for patch in sorted(by_patch, key=_patch_key, reverse=True):
        chosen.append(patch)
        picked.extend(by_patch[patch])
        if len(picked) >= RECENT_MIN:
            break
    return picked, sorted(chosen, key=_patch_key)


def main(argv: list[str]) -> int:
    t0 = time.time()
    data = compute()
    dry = "--ver" in argv
    done = 0
    for key, block in sorted(data.items()):
        path = F.DIR / f"{key}.json"
        if dry:
            if key in argv:
                print(json.dumps(block, ensure_ascii=False, indent=1))
            continue
        if not path.exists():
            continue
        card = json.loads(path.read_text(encoding="utf-8"))
        card["datos_otp"] = block
        path.write_text(json.dumps(card, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
        done += 1
    print(f"{len(data)} campeones con datos de OTPs; {done} fichas actualizadas en {time.time() - t0:.0f} s.")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
