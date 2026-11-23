"""Errores por campeón frente a los OTPs (habilidades, primera vuelta, ritmo de objetos), de partida entera y perfil
de errores por elo."""

from __future__ import annotations

import gzip
import json

from app.coach import champ_checks, elo_profile, insights
from app.coach.items import ItemCatalog

CATALOG = ItemCatalog(
    {
        1036: {"name": "Espada larga", "cost": 350, "from": [], "into": [3071], "tags": ["Damage"]},
        1001: {"name": "Botas", "cost": 300, "from": [], "into": [], "tags": ["Boots"]},
        1028: {"name": "Cristal de rubí", "cost": 400, "from": [], "into": [3071], "tags": ["Health"]},
        3071: {"name": "Cuchilla negra", "cost": 3000, "from": [1036, 1028], "into": [], "tags": ["Damage"]},
        2003: {"name": "Poción", "cost": 50, "from": [], "into": [], "tags": ["Consumable"], "consumed": True},
        1052: {"name": "Tomo amplificador", "cost": 400, "from": [], "into": [], "tags": ["SpellDamage"]},
        1029: {"name": "Armadura de tela", "cost": 300, "from": [], "into": [], "tags": ["Armor"]},
    }
)


def otp(i: int, skills="QWQEQRQWQWRWWEEREE", back=(1036, 1001), first=10.5, second=16.5, **metrics) -> dict:
    return {
        "champ": 120, "role": "JUNGLE", "skills": skills, "skill_t": [60.0 * (k + 1) for k in range(18)],
        "backs": [{"t": 3, "items": [1102]}, {"t": 240, "items": list(back), "gold": 650}],
        "first_item": 3071, "first_item_t": first * 60, "first_item_min": first, "second_item_min": second,
        "gold_diff10": 0, "gold_diff15": 0, "minutes": 28, "kp": 55, "kills": 5, "assists": 6, "control_wards": 2, "wards_pre14": 5,
        **metrics,
    }


def make_ref(n: int = 100) -> dict:
    b = champ_checks.RefBuilder()
    for i in range(n):
        b.add(otp(i, first=9.5 + (i % 10) * 0.2, second=15 + (i % 10) * 0.3), CATALOG)
    return b.ref()


def test_orden_de_habilidades_raro_se_marca_y_el_habitual_no():
    ref = make_ref()
    assert champ_checks.check(otp(0), ref, CATALOG, "Hecarim") == []
    rare = champ_checks.check(otp(0, skills="QEWQQRQWQWRWWEEREE"), ref, CATALOG, "Hecarim")
    assert [m["tags"] for m in rare] == [["build", "skill_order"]]
    assert rare[0]["title"].startswith("Nivel 2: subiste la E")
    assert rare[0]["t"] == 120.0


def test_variante_comun_de_habilidades_no_es_error():
    b = champ_checks.RefBuilder()
    for i in range(100):  # la mitad sube E a nivel 3: es una variante, no un error
        b.add(otp(i, skills="QWEQQRQWQWRWWEEREE" if i % 2 else "QWQEQRQWQWRWWEEREE"), CATALOG)
    assert champ_checks.check(otp(0, skills="QWEQQRQWQWRWWEEREE"), b.ref(), CATALOG) == []


def test_primera_vuelta_rara_pero_no_defensas_ni_piezas_de_su_build():
    ref = make_ref()
    tomo = champ_checks.check(otp(0, back=(1052,)), ref, CATALOG, "Hecarim")
    assert [m["tags"] for m in tomo] == [["build", "first_back"]] and "Tomo amplificador" in tomo[0]["title"]
    assert champ_checks.check(otp(0, back=(1029,)), ref, CATALOG) == []  # armadura: depende del rival
    assert champ_checks.check(otp(0, back=(1028,)), ref, CATALOG) == []  # rubí: pieza de su primer objeto
    assert champ_checks.check(otp(0, back=(2003, 1036)), ref, CATALOG) == []  # pociones: no cuentan


def test_primer_y_segundo_objeto_tarde_salvo_yendo_por_detras():
    ref = make_ref()
    late = champ_checks.check(otp(0, first=13.5, second=20.5), ref, CATALOG, "Hecarim")
    assert sorted(m["title"].split(" (")[0] for m in late) == ["Primer objeto tarde", "Segundo objeto tarde"]
    assert champ_checks.check(otp(0, first=13.5, gold_diff10=-800), ref, CATALOG) == []


def test_errores_de_partida_entera():
    no_pinks = champ_checks.check(otp(0, control_wards=0), None)
    assert [m["tags"][1] for m in no_pinks] == ["no_pinks"]
    assert champ_checks.check(otp(0, control_wards=0, minutes=18), None) == []  # partida corta
    low_kp = champ_checks.check(otp(0, kp=25, kills=1, assists=2), None)  # 12 kills del equipo, estuvo en 3
    assert [m["tags"][1] for m in low_kp] == ["low_kp"] and low_kp[0]["severity"] == "medium"
    assert champ_checks.check(otp(0, kp=25, kills=1, assists=0), None) == []  # 4 kills en la partida: no dice nada
    assert champ_checks.check(otp(0, role="TOP", kp=25, kills=1, assists=2), None) == []  # en top no separa elos
    assert [m["tags"][1] for m in champ_checks.check(otp(0, wards_pre14=1), None)] == ["few_wards"]
    assert all(insights.mistake_type(m["kind"], set(m["tags"]), "JUNGLE") for m in no_pinks + low_kp)


def test_muerte_en_la_jungla_rival_del_jungla_solo_sin_vision():
    assert insights.mistake_type("death", {"enemy_jungle"}, "JUNGLE") is None
    assert insights.mistake_type("death", {"enemy_jungle", "no_ward"}, "JUNGLE")["id"] == "death_enemy_jungle"
    assert insights.mistake_type("death", {"enemy_jungle"}, "MID")["id"] == "death_enemy_jungle"
    assert insights.mistake_type("death", {"enemy_jungle", "isolated"}, "JUNGLE")["id"] == "death_isolated"


def _dossier(tier: str, errors: int, champ: int = 120) -> dict:
    moments = [{"kind": "death", "severity": "medium", "t": 600 + i, "tags": ["isolated"], "title": "Muerte aislado"} for i in range(errors)]
    moments.append({"kind": "death", "severity": "low", "t": 900, "tags": ["fight_won"], "title": "Caíste, pero ganasteis"})
    return {
        "tier": tier, "champ": champ, "role": "JUNGLE", "queue": 420,
        "events": {"skills": [[60.0 * (k + 1), 1, "NORMAL"] for k in range(18)]},
        "analysis": {"has_timeline": True, "queue": 420, "champ": champ, "role": "JUNGLE", "metrics": {"minutes": 25, "control_wards": 2, "wards_pre14": 5}, "moments": moments, "build": {"skills": "QWQEQRQWQWRWWEEREE"}},
    }


def test_perfil_por_elo_y_tus_errores_frente_a_cada_elo(tmp_path):
    for tier, errors, n in (("PLATINUM", 3, 40), ("CHALLENGER", 1, 40)):
        folder = tmp_path / "otps" / "Hecarim" / "JUNGLE" / f"euw1_{tier}_abcdefgh"
        folder.mkdir(parents=True)
        with gzip.open(folder / "partidas.jsonl.gz", "wt", encoding="utf-8") as fh:
            for _ in range(n):
                fh.write(json.dumps(_dossier(tier, errors)) + "\n")
    profile = elo_profile.build(tmp_path)
    tiers = profile["roles"]["JUNGLE"]
    assert tiers["PLATINUM"]["per_game"] == 3 and tiers["CHALLENGER"]["per_game"] == 1  # la pelea ganada no cuenta
    assert tiers["PLATINUM"]["types"]["death_isolated"]["pct"] == 100
    assert profile["champs"]["120:JUNGLE"]["ref"]["games"] == 40  # referencia: solo Diamante o más

    mine = [
        {"match_id": f"M{i}", "queue": 400, "champ": 120, "role": "JUNGLE", "win": True, "has_timeline": True, "metrics": {},
         "mistakes": [["death", "medium", 600, ["isolated"], "Muerte aislado"]] * 2}
        for i in range(6)
    ]
    e = insights.elo_errors(mine, profile)
    assert e["per_game"] == 2 and e["champ"] == 120 and e["normals"] == 6
    row = next(r for r in e["types"] if r["id"] == "death_isolated")
    # Separa elos (3 por partida frente a 1) aunque ambos lo tengan en todas sus partidas; tú, en el 100 %, como ellos.
    assert row["separates"] is True and row["status"] == "even"
    rec = insights.recurring_mistakes(mine, {"JUNGLE": insights.elo_tiers(profile, "JUNGLE")[0]})
    assert rec["items"][0]["elo"] == {"PLATINUM": 100.0, "CHALLENGER": 100.0} and rec["items"][0]["separates"] is True


def test_tipo_que_no_separa_elos_pesa_la_mitad():
    tiers = {
        "PLATINUM": {"types": {"death_isolated": {"pct": 40, "per_game": 0.5}, "death_solo": {"pct": 40, "per_game": 0.45}}},
        "CHALLENGER": {"types": {"death_isolated": {"pct": 10, "per_game": 0.1}, "death_solo": {"pct": 38, "per_game": 0.42}}},
    }
    assert insights.separation(tiers, "death_isolated")["flat"] is False
    assert insights.separation(tiers, "death_solo")["flat"] is True


def test_old_digests_are_refreshed_from_the_saved_analysis(keyed_svc):
    services = keyed_svc
    """Resúmenes de un formato anterior (sin datos para las comprobaciones por campeón): se rehacen del análisis
    guardado, con los segundos de cada habilidad sacados del timeline; uno dañado no rompe nada."""
    from app.coach import store, tracker

    analysis = {"match_id": "EUW1_1", "me": 1, "has_timeline": True, "role": "JUNGLE", "champ": 120, "metrics": {"first_item_min": 11},
                "build": {"skills": "QWE", "backs": [], "order": [{"t": 660, "item": 3071, "kind": "legendary"}]}, "moments": []}
    timeline = {"info": {"frames": [{"events": [{"type": "SKILL_LEVEL_UP", "participantId": 1, "skillSlot": s, "timestamp": 1000 * (i + 1)} for i, s in enumerate((1, 2, 3))]}]}}
    store.save_game(services.db, {"match_id": "EUW1_1", "puuid": "p", "pid": 1, "queue": 400, "game_start": 1, "duration": 1500, "champ": 120,
                                  "role": "JUNGLE", "win": 1, "status": "ready", "analysis": analysis, "timeline_raw": timeline, "digest": {"m": {}}})
    store.save_game(services.db, {"match_id": "EUW1_2", "puuid": "p", "pid": 1, "queue": 400, "game_start": 2, "duration": 1500, "champ": 120,
                                  "role": "JUNGLE", "win": 1, "status": "ready", "analysis": analysis, "digest": "{roto"})
    assert tracker.refresh_digests(services.db) == 2
    (g,) = [x for x in store.list_games(services.db, ["p"]) if x["match_id"] == "EUW1_1"]
    assert g["checks"]["skills"] == "QWE" and g["checks"]["skill_t"] == [1.0, 2.0, 3.0] and g["checks"]["first_item"] == 3071
    assert tracker.refresh_digests(services.db) == 0  # ya al día


def test_despeje_lento_solo_sin_cambiarlo_por_ganks():
    b = champ_checks.RefBuilder()
    for i in range(100):
        b.add(otp(i, jungle_cs10=60 + i % 30), CATALOG)
    ref = b.ref()
    assert ref["jungle_cs10_p10"] is not None
    slow = champ_checks.check(otp(0, jungle_cs10=50, ganks_pre10=0), ref, CATALOG, "Hecarim")
    assert [m["tags"] for m in slow if m["kind"] == "jungle"] == [["jungle", "slow_clear"]]
    assert not [m for m in champ_checks.check(otp(0, jungle_cs10=50, ganks_pre10=2), ref, CATALOG) if "slow_clear" in m["tags"]]
    assert not [m for m in champ_checks.check(otp(0, jungle_cs10=80, ganks_pre10=0), ref, CATALOG) if "slow_clear" in m["tags"]]


def test_reglas_que_no_separan_elos_en_top_no_cuentan():
    boots = {"kind": "build", "tags": ["build", "boots"]}
    resist = {"kind": "build", "tags": ["build", "resist"]}
    assert insights.dropped(boots, "TOP") and insights.dropped(resist, "TOP") and insights.dropped(boots, "JUNGLE")
    assert not insights.dropped(boots, "MID") and not insights.dropped(resist, "ADC")
    assert insights.mistake_type("build", {"build", "boots"}, "TOP") is None
    assert insights.mistake_type("build", {"build", "boots"}, "MID")["id"] == "no_boots"


def test_reconstruccion_vuelta_a_base_y_teletransporte():
    from app.coach import analyzer as A

    fountain = (400, 420)
    # En la línea de bot al minuto 5 y compra en la fuente a los 5:20: no cruzó el mapa andando, volvió a base.
    rec = A._reconstruct([(300_000, 12_000, 2_000, 0), (320_000, 400, 420, 2), (360_000, 12_000, 2_000, 0)], fountain)
    assert rec[1] == (311_000, 12_000, 2_000, A.ANCHOR_HOLD)
    assert A.interpolate(rec, 310_000) == (12_000, 2_000)  # canalizando en la línea
    assert A.interpolate(rec, 321_000)[0] < 2_000  # ya en la fuente
    # Cerca de la fuente, compra sin vuelta a base.
    near = A._reconstruct([(300_000, 1_200, 1_000, 0), (320_000, 400, 420, 2)], fountain)
    assert all(a[3] != A.ANCHOR_HOLD for a in near)
    # De top a bot en 10 s: imposible andando, teletransporte (se queda y salta).
    tp = A._reconstruct([(600_000, 1_500, 13_000, 0), (610_000, 13_500, 1_500, 1)], fountain)
    assert tp[1][3] == A.ANCHOR_HOLD and A.interpolate(tp, 605_000) == (1_500, 13_000)
