"""Draft: build completa de cada jugador contra todo el equipo rival y coordinada con su equipo."""

from __future__ import annotations

import json

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
