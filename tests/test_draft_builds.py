"""Draft: build completa de cada jugador contra todo el equipo rival y coordinada con su equipo."""

from __future__ import annotations

import json
from typing import ClassVar

from app.analytics import builds
from app.analytics.filters import Filters
from app.coach.items import ItemCatalog
from tests.factories import AATROX, AHRI, DARIUS, ITEMS, JINX, LEE_SIN, ORIANNA, SYNDRA, THRESH, VIEGO, ZED, FakeOpgg, install_opgg

EXTRA = {  # objetos completos que el catálogo de pruebas no trae (Heridas graves, armadura, resistencia, defensivos)
    "3033": {"name": "Recordatorio mortal", "gold": {"base": 800, "total": 3000, "sell": 2100, "purchasable": True}, "tags": ["Damage", "ArmorPenetration"], "maps": {"11": True}, "from": ["3123", "1037"], "into": [], "description": "Aplica Heridas graves"},
    "3075": {"name": "Malla de espinas", "gold": {"base": 800, "total": 2450, "sell": 1700, "purchasable": True}, "tags": ["Armor", "Health"], "maps": {"11": True}, "from": ["1029", "1028"], "into": [], "description": "Heridas graves"},
    "3065": {"name": "Semblante espiritual", "gold": {"base": 800, "total": 2700, "sell": 1900, "purchasable": True}, "tags": ["Health", "SpellBlock"], "maps": {"11": True}, "from": ["1033", "1028"], "into": []},
    "3026": {"name": "Ángel de la guarda", "gold": {"base": 800, "total": 3200, "sell": 2200, "purchasable": True}, "tags": ["Armor", "Damage"], "maps": {"11": True}, "from": ["1029", "1037"], "into": []},
    "3156": {"name": "Fauces de Malmortius", "gold": {"base": 800, "total": 2800, "sell": 1900, "purchasable": True}, "tags": ["Damage", "SpellBlock"], "maps": {"11": True}, "from": ["1033", "1037"], "into": []},
}
CORE = [3071, 3053, 3161]
GENERAL = {"core": CORE, "boots": [3047], "popular": [{"id": 3071, "pick_rate": 0.6}, {"id": 3053, "pick_rate": 0.5}],
           "fourth": [{"id": 6694, "pick_rate": 0.2}, {"id": 3065, "pick_rate": 0.1}], "fifth": [{"id": 3075, "pick_rate": 0.1}, {"id": 3156, "pick_rate": 0.08}],
           "sixth": [{"id": 3026, "pick_rate": 0.05}, {"id": 3033, "pick_rate": 0.05}], "skill_max": ["Q", "W", "E"]}
GUIDE = {
    "core_items": [{"ids": CORE, "games": 900, "wins": 480}],
    "boots": [{"ids": [3111]}], "starter_items": [{"ids": [1055, 2003]}], "spells": [{"ids": [4, 12]}],
    "skills": [{"order": "QEWQQR"}],
    "runes": [{"keystone": 8010, "primary": 8000, "secondary": 8400, "ids": [8010, 9111], "names": ["Conquistador", "Triunfo"]}],
}


def _svc(svc, guides=None, generals=None):
    svc.dd.item_catalog = ItemCatalog.from_ddragon({"data": {**ITEMS, **EXTRA}})

    class Opgg(FakeOpgg):
        def matchup_guide(self, champ, opp, role, lang="es_ES", allow_fetch=True):
            return (guides or {}).get((champ, opp))

    return install_opgg(svc, Opgg(builds=generals or {}))


def items_of(p):
    return [b["item"] for b in p["build"]]


def test_full_build_starts_with_the_lane_matchup_core(keyed_svc):
    _svc(keyed_svc, guides={(AATROX, DARIUS): GUIDE}, generals={(AATROX, "TOP"): GENERAL})
    (top,) = builds.recommend(keyed_svc, Filters(), {"TOP": AATROX}, {"TOP": DARIUS})["ally"]
    assert items_of(top)[:2] == [3071, 3053] and [b["kind"] for b in top["build"][:2]] == ["core", "core"]
    assert "Núcleo contra Darius" in top["build"][0]["reason"] and len(top["build"]) == 5
    assert top["runes"]["keystone"] == 8010 and top["boots"]["item"] == 3111 and top["matchup"]["games"] == 900
    assert len(set(items_of(top))) == 5 and all(b["reason"] for b in top["build"])


def test_against_a_lot_of_ap_a_fighter_gets_magic_resist_and_mercury(keyed_svc):
    _svc(keyed_svc, generals={(DARIUS, "TOP"): GENERAL})
    enemy = {"TOP": SYNDRA, "MID": AHRI, "SUPPORT": ORIANNA}  # casi todo daño mágico
    (darius,) = builds.recommend(keyed_svc, Filters(), {"TOP": DARIUS}, enemy)["ally"]
    mr = next(b for b in darius["build"] if b["item"] in (3065, 3156))
    assert mr["kind"] == "situational" and "Resistencia mágica" in mr["reason"]
    assert darius["boots"]["item"] == 3111 and darius["boots"]["situational"] and darius["enemy"]["magic"] >= 60
    assert 3075 not in items_of(darius)  # la armadura no pinta nada contra AP


def test_anti_heal_goes_to_two_players_per_team(keyed_svc):
    _svc(keyed_svc, generals={(DARIUS, "TOP"): GENERAL, (JINX, "ADC"): GENERAL, (LEE_SIN, "JUNGLE"): GENERAL})
    out = builds.recommend(keyed_svc, Filters(), {"TOP": DARIUS, "JUNGLE": LEE_SIN, "ADC": JINX}, {"TOP": AATROX})  # Aatrox se cura
    from app.coach.items import ANTI_HEAL_FALLBACK

    gw = {i for i, it in keyed_svc.dd.item_catalog.items.items() if it.get("gw")} | ANTI_HEAL_FALLBACK
    holders = {p["champ"] for p in out["ally"] if set(items_of(p)) & gw}
    assert holders == {JINX, DARIUS}  # el ADC y el top; el jungla no
    jinx = next(p for p in out["ally"] if p["champ"] == JINX)
    assert "con dos basta" in next(b for b in jinx["build"] if b["item"] in gw)["reason"]


def test_squishy_carry_gets_something_to_survive_assassins(keyed_svc):
    _svc(keyed_svc, generals={(JINX, "ADC"): GENERAL})
    enemy = {"JUNGLE": LEE_SIN, "MID": ZED, "TOP": VIEGO}  # tres asesinos
    (jinx,) = builds.recommend(keyed_svc, Filters(), {"ADC": JINX}, enemy)["ally"]
    cat = keyed_svc.dd.item_catalog
    survive = [b for b in jinx["build"] if cat.resist(b["item"])]
    assert survive and all(b["kind"] == "situational" for b in survive)
    assert any("asesinos" in b["reason"] or "Armadura" in b["reason"] for b in survive)


def test_what_otps_buy_against_this_kind_of_team_weighs_most(keyed_svc):
    """Sus OTPs compran Fauces de Malmortius mucho más contra equipos de AP: entra en la build aunque sea poco popular."""
    _svc(keyed_svc, generals={(DARIUS, "TOP"): {**GENERAL, "fourth": [], "fifth": [{"id": 3156, "pick_rate": 0.02}], "sixth": []}})
    folder = keyed_svc.settings.data_dir / "otps" / "Darius"
    folder.mkdir(parents=True)
    groups = {
        "all": {"games": 400, "items": {"3071": [380, 200], "3053": [300, 160], "3156": [60, 40]}, "boots": {"3047": [300, 160]}},
        "ap": {"games": 120, "items": {"3071": [110, 60], "3053": [90, 50], "3156": [45, 32]}, "boots": {"3047": [80, 40], "3111": [30, 18]}},
    }
    (folder / "resumen.json").write_text(json.dumps({"roles": {"TOP": {"all": {"items_by_enemy": groups}, "by_tier": {}}}}), encoding="utf-8")
    enemy = {"TOP": SYNDRA, "MID": AHRI, "SUPPORT": ORIANNA}
    (darius,) = builds.recommend(keyed_svc, Filters(), {"TOP": DARIUS}, enemy)["ally"]
    maw = next(b for b in darius["build"] if b["item"] == 3156)
    assert "Sus OTPs lo compran más contra equipos así" in maw["reason"] or "Resistencia mágica" in maw["reason"]
    assert "OTPs" in darius["sources"]


def test_support_gets_team_items_and_no_anti_heal_duty(keyed_svc):
    _svc(keyed_svc, generals={(THRESH, "SUPPORT"): GENERAL})
    (thresh,) = builds.recommend(keyed_svc, Filters(), {"SUPPORT": THRESH}, {"TOP": AATROX, "MID": AHRI, "JUNGLE": ZED})["ally"]
    assert "team_items" in thresh


def test_endpoint(keyed_svc):
    from tests.test_crawler_otp import _client

    _svc(keyed_svc, guides={(AATROX, DARIUS): GUIDE}, generals={(AATROX, "TOP"): GENERAL})
    with _client(keyed_svc) as client:
        res = client.post("/api/draft/builds", json={"ally": {"TOP": "Aatrox"}, "enemy": {"TOP": DARIUS}})
    assert res.status_code == 200, res.text
    assert [b["item"] for b in res.json()["ally"][0]["build"]][:2] == [3071, 3053]


def test_lolalytics_item_sets_are_parsed(keyed_svc, monkeypatch):
    from app.external.lolalytics import Lolalytics

    sets = {
        "itemSet1": [["3071", 600, 300], ["3161", 400, 210]],
        "itemSet5": [["3071_3053_3161_6694_3026", 100, 60], ["3071_3053_3156_6694_3026", 50, 35], ["3161_3053_3071_3156_3065", 30, 12]],
        "itemBootSet1": [["3047", 300, 150], ["3111", 200, 110]],
    }
    lola = Lolalytics(keyed_svc.db, keyed_svc.dd, enabled=True)
    monkeypatch.setattr(lola, "_get", lambda params: {"itemSets": sets, "response": {"valid": True}})
    monkeypatch.setattr(lola, "slug", lambda champ: "aatrox")
    data = lola.item_sets(AATROX, "TOP", "emerald_plus", "global", "16.22")
    assert data["games"] == 180 and data["items"]["3071"] == [180, 107] and data["items"]["3156"] == [80, 47]
    assert data["first"]["3071"] == [600, 300] and data["boots"]["3111"] == [200, 110]
    assert data["pos"]["3071"] == 1.33 and data["pos"]["3026"] == 5.0  # posición media de compra (1-5)
    assert data["sets"][0] == [[3071, 3053, 3161, 6694, 3026], 100, 60]


def test_lolalytics_boots_come_from_first_item_plus_boots(keyed_svc, monkeypatch):
    """itemBootSet1 solo cuenta las botas compradas antes que ningún objeto: las de siempre salen de itemBootSet2."""
    from app.external.lolalytics import Lolalytics

    sets = {
        "itemSet5": [["3071_3053_3161_6694_3026", 100, 60]],
        "itemBootSet1": [["3047", 300, 150], ["3071", 900, 450]],
        "itemBootSet2": [["3071_3111", 1700, 900], ["3047_3071", 600, 290]],
    }
    lola = Lolalytics(keyed_svc.db, keyed_svc.dd, enabled=True)
    monkeypatch.setattr(lola, "_get", lambda params: {"itemSets": sets, "response": {"valid": True}})
    monkeypatch.setattr(lola, "slug", lambda champ: "aatrox")
    data = lola.item_sets(AATROX, "TOP", "emerald_plus", "global", "16.22")
    assert data["boots"]["3111"] == [1700, 900] and data["boots"]["3047"] == [600, 290]


def test_otp_items_are_grouped_by_enemy_team_kind():
    from app.ingest.otp_details import _items_block

    info = {SYNDRA: (0.8, ["Mage"], "Syndra"), AHRI: (0.7, ["Mage", "Assassin"], "Ahri"), ORIANNA: (0.7, ["Mage"], "Orianna"),
            DARIUS: (0.1, ["Fighter", "Tank"], "Darius"), ZED: (0.1, ["Assassin"], "Zed"), AATROX: (0.25, ["Fighter"], "Aatrox")}
    champ_info = lambda c: {"magic": info[c][0], "tags": info[c][1], "name": info[c][2]}

    def game(enemies, items, win):
        return {"win": win, "enemies": {f"R{i}": c for i, c in enumerate(enemies)},
                "analysis": {"build": {"order": [{"item": i, "kind": "legendary"} for i in items] + [{"item": 3111, "kind": "boots"}]}}}

    rows = [game([SYNDRA, AHRI, ORIANNA], [3071, 3156], True) for _ in range(4)] + [game([DARIUS, ZED, AATROX], [3071, 3075], False) for _ in range(3)]
    groups = _items_block(rows, champ_info)["items_by_enemy"]
    assert groups["all"]["games"] == 7 and groups["ap"]["items"]["3156"] == [4, 4] and groups["ad"]["items"]["3075"] == [3, 0]
    assert "3156" not in groups["ad"]["items"] and groups["healers"]["games"] == 3  # Darius y Aatrox se curan
    assert groups["ap"]["boots"]["3111"] == [4, 4]


def test_matchup_core_with_few_games_falls_back_to_the_general_build(keyed_svc):
    """Un núcleo contra ese rival de 40 partidas es ruido: manda su build general (y se dice por qué)."""
    thin = {**GUIDE, "core_items": [{"ids": [3161, 6694], "games": 40, "wins": 25}]}
    _svc(keyed_svc, guides={(AATROX, DARIUS): thin}, generals={(AATROX, "TOP"): GENERAL})
    (top,) = builds.recommend(keyed_svc, Filters(), {"TOP": AATROX}, {"TOP": DARIUS})["ally"]
    assert items_of(top)[:2] == [3071, 3053] and top["matchup"] is None
    assert "pocas partidas: 40" in top["build"][0]["reason"]


def test_support_chemtech_only_if_the_team_lacks_two_anti_heal_holders(keyed_svc):
    chem = {"3011": {"name": "Putrificador tecnoquímico", "gold": {"base": 500, "total": 2300, "sell": 1600, "purchasable": True}, "tags": ["SpellDamage"], "maps": {"11": True}, "from": ["3916"], "into": [], "description": "Heridas graves"}}
    _svc(keyed_svc, generals={(THRESH, "SUPPORT"): GENERAL, (DARIUS, "TOP"): GENERAL, (JINX, "ADC"): GENERAL})
    keyed_svc.dd.item_catalog = ItemCatalog.from_ddragon({"data": {**ITEMS, **EXTRA, **chem}})
    enemy = {"TOP": AATROX, "MID": AHRI, "JUNGLE": ZED}  # Aatrox se cura
    alone = builds.recommend(keyed_svc, Filters(), {"SUPPORT": THRESH}, enemy)["ally"][0]
    assert 3011 in [x["item"] for x in alone["team_items"]]
    full = builds.recommend(keyed_svc, Filters(), {"TOP": DARIUS, "ADC": JINX, "SUPPORT": THRESH}, enemy)["ally"]
    thresh = next(p for p in full if p["champ"] == THRESH)
    assert 3011 not in [x["item"] for x in thresh["team_items"]]  # ya la llevan Jinx y Darius


def test_lolalytics_winrate_is_never_shown_as_a_bare_number():
    """El WR de las builds de 5 objetos es alto para todos: solo se dice si va claramente mejor o peor que el resto."""
    assert builds._wr_text(None, 5000) == "" and builds._wr_text(1.5, 5000) == "" and builds._wr_text(6, 100) == ""
    assert builds._wr_text(3.4, 2000) == ", 3 pp mejor que sus otras opciones en Lolalytics"
    assert "peor" in builds._wr_text(-4, 2000)


class FakeLola:
    """Lolalytics con builds completas: 3033 se compra de primero (camino aparte) y el resto va con el núcleo 3071 + 3053."""

    enabled = True

    def __init__(self, sets):
        self.sets = sets

    def item_sets(self, champ, role, tier, region, patch, allow_fetch=True):
        items, pos = {}, {}
        for ids, games, wins in self.sets:
            for k, i in enumerate(ids, start=1):
                cur = items.setdefault(str(i), [0, 0])
                cur[0] += games
                cur[1] += wins
                acc = pos.setdefault(str(i), [0, 0])
                acc[0] += k * games
                acc[1] += games
        return {"items": items, "first": {}, "boots": {}, "games": sum(g for _i, g, _w in self.sets), "paths": [],
                "pos": {i: round(t / g, 2) for i, (t, g) in pos.items()}, "sets": [list(r) for r in self.sets]}


def test_first_buy_items_are_never_recommended_late_and_usage_is_measured_with_the_core(keyed_svc):
    """Partida real: a Hecarim le salió Soberbia de 5.º objeto porque la llevaba el 36 % de sus builds, pero se compra de
    primero (camino de letalidad). Ahora va a alternativas como otra forma de empezar."""
    _svc(keyed_svc, generals={(AATROX, "TOP"): {**GENERAL, "fourth": [], "fifth": [], "sixth": []}})
    keyed_svc.lolalytics = FakeLola([
        ([3033, 3071, 3053, 6694, 3026], 400, 220),  # 3033 siempre de primero
        ([3033, 3071, 3053, 3156, 3026], 300, 160),
        ([3071, 3053, 3156, 6694, 3065], 350, 190),
        ([3071, 3053, 6694, 3156, 3026], 250, 140),
        ([3161, 3065, 3075, 3026, 3156], 200, 90),  # 3075 nunca va con el núcleo 3071 + 3053
    ])
    (top,) = builds.recommend(keyed_svc, Filters(), {"TOP": AATROX}, {"TOP": ZED})["ally"]  # sin curanderos: sin Heridas graves
    chosen = items_of(top)
    assert chosen[:2] == [3071, 3053] and 3033 not in chosen and 3075 not in chosen
    rush = next(a for a in top["alternatives"] if a["item"] == 3033)
    assert rush["reason"].startswith("Otra forma de empezar: se compra de primer objeto (posición media 1")
    # El resto, en el orden en que se compra.
    pos = keyed_svc.lolalytics.item_sets(AATROX, "TOP", "", "", "")["pos"]
    rest = chosen[2:]
    assert len(rest) == 3 and rest == sorted(rest, key=lambda i: pos.get(str(i), 9.0))
    popular = next(b for b in top["build"] if b["item"] == 6694)
    assert "de sus builds con ese núcleo" in popular["reason"]


def test_with_core_needs_enough_builds():
    lola = FakeLola([([3071, 3053, 6694, 3156, 3026], 100, 50)]).item_sets(0, "TOP", "", "", "")
    assert builds._with_core(lola, [3071, 3053]) is None  # 100 < 300: se usa el total
    lola = FakeLola([([3071, 3053, 6694, 3156, 3026], 400, 200), ([3161, 3065, 3075, 3026, 3156], 500, 250)]).item_sets(0, "TOP", "", "", "")
    out = builds._with_core(lola, [3071, 3053])
    assert out["games"] == 400 and "3075" not in out["items"] and out["items"]["6694"] == [400, 200]


def test_items_that_never_go_together_are_not_recommended_together(keyed_svc):
    """Auditoría: Dominik + Recordatorio letal (pasiva única) o Cuchilla negra + Rencor de Serylda salían juntos."""
    _svc(keyed_svc, generals={(AATROX, "TOP"): {**GENERAL, "fourth": [], "fifth": [], "sixth": []}})
    keyed_svc.lolalytics = FakeLola([
        ([3071, 3053, 6694, 3026, 3065], 500, 260),  # 6694 y 3156 nunca juntos (los dos muy comprados)
        ([3071, 3053, 3156, 3026, 3065], 450, 240),
        ([3071, 3053, 6694, 3065, 3075], 300, 150),
        ([3071, 3053, 3156, 3065, 3075], 250, 130),
    ])
    (top,) = builds.recommend(keyed_svc, Filters(), {"TOP": AATROX}, {"TOP": ZED})["ally"]
    chosen = items_of(top)
    assert not ({6694, 3156} <= set(chosen))
    exclusive = builds._Exclusive(keyed_svc.lolalytics.item_sets(0, "TOP", "", "", ""))
    assert exclusive(6694, 3156) and not exclusive(6694, 3026)


def test_aggressive_items_are_damage_with_penetration_and_no_defense():
    cat = ItemCatalog.from_ddragon({"data": {**ITEMS, **EXTRA}})
    assert builds._aggressive_item(cat, 3033)  # daño + penetración
    assert not builds._aggressive_item(cat, 3071)  # con vida (Cuchilla negra)
    assert not builds._aggressive_item(cat, 3026)  # con armadura


def test_the_game_plan_picks_a_real_snowball_build_when_it_fits():
    """El ejemplo del usuario: jungla, draft que pierde el late, mid con prioridad y ganando a su jungla: invadir con
    build de bola de nieve (si el campeón la tiene de verdad: ≥ 10 % de sus builds y sin perder más de 1,5 pp)."""
    cat = ItemCatalog.from_ddragon({"data": {**ITEMS, **EXTRA}})
    team = {"tempo": "early", "early": 54.0, "late": 45.0, "lanes": {"TOP": 0, "JUNGLE": 1, "MID": 1, "ADC": 0, "SUPPORT": 0}}
    plan = builds._player_plan(team, "JUNGLE")
    assert plan["style"] == "aggressive"
    assert plan["text"].startswith("Vuestro draft es mejor en partidas cortas (54 % antes del 25 frente a 45 % pasado el 35); ganas a su jungla; tu mid gana su línea. Invade su jungla")
    lola = FakeLola([([3071, 3053, 3156, 3026, 3065], 700, 360), ([3033, 3071, 3053, 6694, 3026], 300, 158)]).item_sets(0, "JUNGLE", "", "", "")
    paths = builds._paths(cat, lola)
    assert [(x["first"], x["second"], x["aggressive"]) for x in paths] == [(3071, 3053, False), (3033, 3071, True)]
    base = builds._choose_path(cat, {"core": [3071, 3053, 3161]}, paths, plan)
    assert base["core"][:2] == [3033, 3071] and base["path"]["share"] == 0.3
    # Normal o con una build agresiva que gana claramente menos: no se cambia.
    assert builds._choose_path(cat, {"core": [3071, 3053]}, paths, {"style": "normal"})["core"] == [3071, 3053]
    worse = FakeLola([([3071, 3053, 3156, 3026, 3065], 700, 380), ([3033, 3071, 3053, 6694, 3026], 300, 140)]).item_sets(0, "JUNGLE", "", "", "")
    assert builds._choose_path(cat, {"core": [3071, 3053]}, builds._paths(cat, worse), plan)["core"] == [3071, 3053]


def test_plan_texts_for_lanes_and_a_losing_jungle_matchup():
    lanes = {"TOP": -1, "JUNGLE": -1, "MID": 1, "ADC": 1, "SUPPORT": 0}
    even = {"tempo": "even", "early": 50.0, "late": 50.0, "lanes": lanes}
    jungle = builds._player_plan(even, "JUNGLE")
    assert jungle["style"] == "scaling" and "No le invadas" in jungle["text"]
    top = builds._player_plan(even, "TOP")
    assert top["text"] == "Pierdes tu línea. Juega seguro en línea, sin forzar intercambios, y escala."
    support = builds._player_plan({**even, "tempo": "early", "early": 55.0, "late": 45.0}, "SUPPORT")
    assert support["style"] == "aggressive" and "tu dúo gana la línea" in support["text"]
    assert builds._player_plan(None, "MID") == {"style": "normal", "text": None}


def test_transformed_items_are_recommended_as_the_one_you_can_buy():
    cat = ItemCatalog.from_ddragon({"data": {
        "3004": {"name": "Manamune", "gold": {"total": 2900, "purchasable": True}, "tags": ["Damage", "Mana"], "maps": {"11": True}, "from": ["1037"], "depth": 3},
        "3042": {"name": "Muramana", "gold": {"total": 2900, "purchasable": False}, "tags": ["Damage", "Mana"], "maps": {"11": True}, "inStore": False, "specialRecipe": 3004},
    }})
    assert cat.buyable_form(3042) == 3004 and cat.buyable_form(3004) == 3004 and cat.buyable_form(999) == 999


def test_game_limits_apply_even_without_data_for_the_champion():
    """Escenarios: Pantheon top con plan de bola de nieve llevaba Dominik + Recordatorio letal (los dos de Últimas
    palabras: el juego solo deja uno) porque de Pantheon top había pocas builds para verlo en los datos."""
    cat = ItemCatalog.from_ddragon({"data": {
        "3035": {"name": "Últimas palabras", "gold": {"total": 1450, "purchasable": True}, "tags": ["Damage"], "maps": {"11": True}, "into": ["3036", "3033"]},
        "3036": {"name": "Recuerdos de lord Dominik", "gold": {"total": 3100, "purchasable": True}, "tags": ["Damage", "CriticalStrike"], "maps": {"11": True}, "from": ["3035"], "depth": 3,
                 "description": "<passive>Verdugo de gigantes</passive>"},
        "3033": {"name": "Recordatorio letal", "gold": {"total": 3000, "purchasable": True}, "tags": ["Damage", "CriticalStrike"], "maps": {"11": True}, "from": ["3035"], "depth": 3,
                 "description": "<passive>Heridas graves</passive>"},
        "3053": {"name": "Calibrador de Sterak", "gold": {"total": 3200, "purchasable": True}, "tags": ["Health"], "maps": {"11": True}, "depth": 3,
                 "description": "<passive>Las garras que atrapan</passive><passive>Salvavidas</passive>"},
        "3156": {"name": "Fauces de Malmortius", "gold": {"total": 2800, "purchasable": True}, "tags": ["Damage", "SpellBlock"], "maps": {"11": True}, "depth": 3,
                 "description": "<passive>Salvavidas</passive>"},
        "3004": {"name": "Manamune", "gold": {"total": 2900, "purchasable": True}, "tags": ["Damage"], "maps": {"11": True}, "depth": 3, "description": "<passive>Carga de maná</passive>"},
        "3003": {"name": "Bastón del arcángel", "gold": {"total": 2900, "purchasable": True}, "tags": ["SpellDamage"], "maps": {"11": True}, "depth": 3, "description": "<passive>Carga de maná</passive>"},
    }})
    no_data = builds._Exclusive(None, cat)
    assert no_data(3036, 3033)  # mismo componente limitado
    assert no_data(3053, 3156)  # misma pasiva única (Salvavidas)
    assert not no_data(3036, 3053)
    # Si los datos los muestran juntos, mandan los datos (Manamune + Bastón del arcángel en Ryze).
    seen = FakeLola([([3004, 3003, 3053, 3036, 3156], 400, 200), ([3004, 3003, 3036, 3053, 3156], 300, 150), ([3053, 3036, 3156, 3004, 3003], 300, 150)]).item_sets(0, "MID", "", "", "")
    assert no_data(3004, 3003) and not builds._Exclusive(seen, cat)(3004, 3003)


def test_anti_heal_duty_never_clashes_with_the_core(keyed_svc):
    """Escenarios: a Smolder le tocaba Heridas graves y salía Recordatorio letal, que no va con su Cuchilla negra."""
    _svc(keyed_svc, generals={(AATROX, "TOP"): {**GENERAL, "fourth": [], "fifth": [], "sixth": []}})
    keyed_svc.lolalytics = FakeLola([
        ([3071, 3053, 6694, 3026, 3065], 500, 260),  # con la Cuchilla negra nunca va 3033
        ([3071, 3053, 3156, 3026, 3065], 450, 240),
        ([3033, 3161, 6694, 3026, 3065], 400, 200),
        ([3033, 3161, 3156, 3026, 3065], 350, 180),
    ])
    (top,) = builds.recommend(keyed_svc, Filters(), {"TOP": AATROX}, {"TOP": DARIUS})["ally"]  # Darius se cura
    assert items_of(top)[:2] == [3071, 3053] and 3033 not in items_of(top)


class _DD:
    runes: ClassVar[dict] = {8128: {"name": "Cosecha oscura"}, 8010: {"name": "Conquistador"}, 8230: {"name": "Irrupción de la tormenta"}}

    @staticmethod
    def name(champ):
        return {64: "Lee Sin"}.get(champ, str(champ))


KAYN_LOLA = [
    {"kind": "pick", "keystone": 8128, "primary": 8100, "secondary": 8300, "ids": [8128, 8143, 8140, 8135, 8347, 8304], "shards": [5008, 5008, 5001],
     "games": 28065, "wr": 51.67, "share": 0.465},
    {"kind": "win", "keystone": 8010, "primary": 8000, "secondary": 8100, "ids": [8010, 9111, 9105, 8299, 8321, 8313], "shards": [5008, 5008, 5013],
     "games": 24565, "wr": 51.89, "share": 0.407},
]


def test_runes_follow_the_game_plan_among_the_champions_real_pages():
    """Kayn puede ir con Cosecha (bola de nieve) o Conquistador (pelea larga): se elige por el plan de la partida."""
    base = {"source": "general", "runes": None}
    normal = builds._choose_runes(_DD, base, [], KAYN_LOLA, None, None)
    assert normal["keystone"] == 8128 and normal["why"] == "Cosecha oscura: la más jugada con él (46 % de sus partidas)"
    assert normal["shards"] == [5008, 5008, 5001] and normal["primary"] == 8100 and normal["names"][0] == "Cosecha oscura"
    scaling = builds._choose_runes(_DD, base, [], KAYN_LOLA, None, "durable")
    assert scaling["keystone"] == 8010 and "para pelear largo por el plan" in scaling["why"]
    assert builds._choose_runes(_DD, base, [], KAYN_LOLA, None, "aggressive")["keystone"] == 8128
    # Una página del plan que gana claramente menos no se usa.
    worse = [KAYN_LOLA[0], {**KAYN_LOLA[1], "wr": 49.0}]
    assert builds._choose_runes(_DD, base, [], worse, None, "durable")["keystone"] == 8128


def test_runes_prefer_the_page_played_against_that_opponent():
    guide = [{"keystone": 8010, "primary": 8000, "secondary": 8100, "ids": [8010, 9111, 9105, 8299, 8321, 8313], "games": 400, "wins": 216}]
    runes = builds._choose_runes(_DD, {"source": "matchup"}, guide, KAYN_LOLA, 64, None)
    assert runes["keystone"] == 8010 and runes["why"] == "Conquistador: la más jugada contra Lee Sin (400 partidas en OP.GG)"
    assert runes["shards"] == [5008, 5008, 5013]  # los fragmentos de la página de Lolalytics con la misma piedra angular
    thin = [{**guide[0], "games": 40}]  # con pocas partidas, manda la más jugada en general
    assert builds._choose_runes(_DD, {"source": "matchup"}, thin, KAYN_LOLA, 64, None)["keystone"] == 8128


def test_plan_lines_tell_the_jungler_where_to_gank_and_which_objectives_to_play():
    team = {"tempo": "early", "early": 55.0, "late": 45.0, "lanes": {"TOP": 1, "JUNGLE": 1, "MID": 0, "ADC": -1, "SUPPORT": 0}}
    mine = {"TOP": "Aatrox", "JUNGLE": "Hecarim", "MID": "Ahri", "ADC": "Jinx", "SUPPORT": "Thresh"}
    theirs = {"TOP": "Darius", "JUNGLE": "Skarner", "MID": "Syndra", "ADC": "Kai'Sa", "SUPPORT": "Nautilus"}
    lines = {l["topic"]: l["text"] for l in builds._plan_lines(team, "JUNGLE", mine, theirs)}
    assert lines["Partida"].startswith("Vuestro draft es mejor en partidas cortas (55 % antes del 25")
    assert lines["Jungla"].startswith("Ganas a Skarner: puedes robarle campamentos")  # mid igualado: invadir con cuidado
    assert lines["Ganks"] == ("Ganquea top (Aatrox contra Darius): ganan su línea y con tu ayuda hacen bola de nieve; "
                              "mid está igualado: un gank decide la línea; ADC pierde su línea: no fuerces ganks ahí, juega a contragankear y a cubrirlos en defensa.")
    assert lines["Objetivos"].startswith("Dragón: no tenéis prioridad (mid está igualado y ADC pierde su línea)")
    assert "Larvas y heraldo: tenéis prioridad (top gana su línea y mid está igualado)" in lines["Objetivos"]
    mid = {l["topic"]: l["text"] for l in builds._plan_lines(team, "MID", mine, theirs)}
    assert mid["Jungla"].startswith("Mid igualado: muévete con tu jungla solo cuando")
    support = {l["topic"]: l["text"] for l in builds._plan_lines(team, "SUPPORT", mine, theirs)}
    assert support["Línea"].startswith("Perdéis la línea (Jinx y Thresh contra Kai'Sa y Nautilus)")
    assert support["Objetivos"].startswith("Dragón: no tenéis prioridad")
    assert builds._plan_lines(None, "TOP", mine, theirs) == []
    assert builds.team_priorities(team) == {"dragon": "theirs", "top_side": "ours"}


def test_objectives_do_not_promise_priority_with_a_lane_still_without_rival():
    """Draft a medias: top gana su línea pero mid aún no tiene rival; no se puede decir «tenéis prioridad» arriba."""
    team = {"tempo": None, "early": 50.0, "late": 50.0, "lanes": {"TOP": 1, "JUNGLE": 0, "MID": 0, "ADC": -1, "SUPPORT": 0}}
    mine = {"TOP": "Darius", "JUNGLE": "Hecarim", "ADC": "Jinx"}
    theirs = {"TOP": "Garen", "JUNGLE": "Lee Sin", "ADC": "Kai'Sa"}
    lines = {l["topic"]: l["text"] for l in builds._plan_lines(team, "JUNGLE", mine, theirs)}
    assert "Larvas y heraldo: top gana su línea y mid aún sin rival; tendréis prioridad si mid no pierde la suya." in lines["Objetivos"]
    assert lines["Objetivos"].startswith("Dragón: mid aún sin rival y ADC pierde su línea; no tendréis prioridad salvo que mid gane la suya.")
    empty = {l["topic"]: l["text"] for l in builds._plan_lines(team, "JUNGLE", {"JUNGLE": "Hecarim"}, {"JUNGLE": "Lee Sin"})}
    assert empty["Objetivos"].startswith("Dragón: mid y ADC aún sin rival; se sabrá al elegirlos.")


def test_tempo_never_says_winning_when_behind_in_both_phases():
    behind = builds._tempo_line({"tempo": "late", "early": 39.0, "late": 43.0})[0]["text"]
    assert behind.startswith("Vuestro draft va por detrás en cualquier duración, pero sufre menos si la partida se alarga (39 % antes del 25, 43 % pasado el 35)")
    ahead = builds._tempo_line({"tempo": "early", "early": 61.0, "late": 57.0})[0]["text"]
    assert ahead.startswith("Vuestro draft es mejor en partidas cortas (61 % antes del 25 frente a 57 % en las largas)")
    assert builds._tempo_line({"tempo": "even", "early": 50.0, "late": 50.0}) == []
    lanes = {"TOP": -1, "JUNGLE": 0, "MID": -1, "ADC": -1, "SUPPORT": 0}
    names = {r: r.title() for r in ("TOP", "JUNGLE", "MID", "ADC", "SUPPORT")}
    lines = {l["topic"]: l["text"] for l in builds._plan_lines({"tempo": "even", "early": 50.0, "late": 50.0, "lanes": lanes}, "JUNGLE", names, names)}
    assert lines["Ganks"].startswith("Todas tus líneas pierden su línea: no fuerces ganks; farmea")


def test_rounded_fifty_is_not_behind():
    text = builds._tempo_line({"tempo": "early", "early": 49.6, "late": 46.0})[0]["text"]
    assert text.startswith("Vuestro draft es mejor en partidas cortas (50 % antes del 25 frente a 46 % pasado el 35)")


def test_lanes_without_both_champions_are_unknown_not_even():
    team = {"tempo": "even", "early": 50.0, "late": 50.0, "lanes": {"TOP": 0, "JUNGLE": 1, "MID": 1, "ADC": 0, "SUPPORT": 0}}
    mine = {"TOP": "Aatrox", "JUNGLE": "Hecarim", "MID": "Ahri", "ADC": "Jinx", "SUPPORT": "Thresh"}
    theirs = {"JUNGLE": "Lee Sin", "MID": "Syndra"}  # top y bot aún sin elegir
    lines = {l["topic"]: l["text"] for l in builds._plan_lines(team, "JUNGLE", mine, theirs)}
    assert lines["Ganks"] == "Ganquea mid (Ahri contra Syndra): ganan su línea y con tu ayuda hacen bola de nieve; top y ADC: aún sin rival."
    assert lines["Objetivos"].startswith("Dragón: mid gana su línea y ADC aún sin rival; tendréis prioridad si ADC no pierde la suya.")
    top = {l["topic"]: l["text"] for l in builds._plan_lines(team, "TOP", mine, theirs)}
    assert top["Línea"].startswith("Aún no se sabe tu rival de línea")


def test_situations_say_what_to_buy_if_their_biggest_threat_gets_ahead(keyed_svc):
    """Si su tirador (amenaza física) o su maga (amenaza mágica) se ponen fuertes: un objeto de su build con esa
    resistencia, en vez del último que no es del núcleo."""
    _svc(keyed_svc, generals={(AATROX, "TOP"): GENERAL})
    enemy = {"TOP": DARIUS, "MID": SYNDRA, "ADC": JINX, "SUPPORT": THRESH}
    (aatrox,) = builds.recommend(keyed_svc, Filters(), {"TOP": AATROX}, enemy)["ally"]
    by_when = {s["when"]: s for s in aatrox["situations"]}
    cat = keyed_svc.dd.item_catalog
    jinx, syndra = by_when.get("Si Jinx se pone fuerte"), by_when.get("Si Syndra se pone fuerte")
    if not any("armor" in cat.resist(i) for i in items_of(aatrox)):
        assert jinx and "armor" in cat.resist(jinx["item"]) and "daño físico" in jinx["reason"]
    if not any("mr" in cat.resist(i) for i in items_of(aatrox)):
        assert syndra and "mr" in cat.resist(syndra["item"]) and "daño mágico" in syndra["reason"]
    assert all(s["replaces"] in items_of(aatrox) for s in aatrox["situations"] if s["when"].startswith("Si ") and "fuerte" in s["when"])
    # Con el draft rival a medias (menos de 3), aún no se sabe quién es su amenaza.
    (early,) = builds.recommend(keyed_svc, Filters(), {"TOP": AATROX}, {"TOP": DARIUS})["ally"]
    assert early["situations"] == []


def test_plan_adds_what_the_champions_otps_show(keyed_svc):
    """Del resumen de sus OTPs: la línea que más decide sus partidas (jungla), el primer objetivo que más pesa y su ritmo."""
    folder = keyed_svc.settings.data_dir / "otps" / keyed_svc.dd.name(LEE_SIN).replace(" ", "_")
    folder.mkdir(parents=True, exist_ok=True)
    lanes = {"TOP": (60, 45), "MID": (75, 30), "ADC": (65, 40)}
    block = {
        "games": 200,
        "lanes_impact": {r: {"ahead": {"games": 60, "wr": a}, "behind": {"games": 60, "wr": b}} for r, (a, b) in lanes.items()},
        "objectives_first": {
            "dragon": {"ally_first_pct": 50, "ally_first_min": 8.1, "wr_ally_first": 66, "wr_enemy_first": 40, "games": 200},
            "horde": {"ally_first_pct": 50, "ally_first_min": 8.9, "wr_ally_first": 56, "wr_enemy_first": 48, "games": 200},
        },
        "first_camp": [{"key": "raptors", "pct": 71}],
        "metrics": {"first_gank_min": {"mean": 5.1}, "first_recall_min": {"mean": 3.9}},
    }
    (folder / "resumen.json").write_text(json.dumps({"roles": {"JUNGLE": {"all": block}}}), encoding="utf-8")
    lines = {l["topic"]: l["text"] for l in builds._otp_plan_lines(keyed_svc, LEE_SIN, "JUNGLE")}
    assert "más decide sus partidas es mid" in next(t for k, t in lines.items() if k.startswith("OTPs de"))
    assert "les pesa más el primer dragón" in lines["Primer objetivo"]
    assert lines["Ritmo"] == "Sus OTPs empiezan por aviarios (71 %), primer gank hacia el 5,1 y primera vuelta a base hacia el 3,9."
    assert builds._otp_plan_lines(keyed_svc, AHRI, "MID") == []  # sin OTPs de ese campeón: nada
