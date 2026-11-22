"""Draft: build recomendada para cada jugador según su rival de línea y el equipo rival."""

from __future__ import annotations

from app.analytics import builds
from app.analytics.filters import Filters
from app.coach.items import ItemCatalog
from tests.factories import AATROX, AHRI, DARIUS, GAREN, ITEMS, JINX, LEE_SIN, ORIANNA, THRESH, VIEGO, ZED, FakeOpgg, install_opgg
from tests.test_crawler import keyed_svc  # noqa: F401  (fixture reutilizada)

EXTRA = {  # objetos completos que el catálogo de pruebas no trae (Heridas graves, armadura, resistencia, defensivos)
    "3033": {"name": "Recordatorio mortal", "gold": {"base": 800, "total": 3000, "sell": 2100, "purchasable": True}, "tags": ["Damage", "ArmorPenetration"], "maps": {"11": True}, "from": ["3123", "1037"], "into": [], "description": "Aplica Heridas graves"},
    "3075": {"name": "Malla de espinas", "gold": {"base": 800, "total": 2450, "sell": 1700, "purchasable": True}, "tags": ["Armor", "Health"], "maps": {"11": True}, "from": ["1029", "1028"], "into": [], "description": "Heridas graves"},
    "3065": {"name": "Semblante espiritual", "gold": {"base": 800, "total": 2700, "sell": 1900, "purchasable": True}, "tags": ["Health", "SpellBlock"], "maps": {"11": True}, "from": ["1033", "1028"], "into": []},
    "3026": {"name": "Ángel de la guarda", "gold": {"base": 800, "total": 3200, "sell": 2200, "purchasable": True}, "tags": ["Armor", "Damage"], "maps": {"11": True}, "from": ["1029", "1037"], "into": []},
}


def _svc(svc, guides=None, generals=None):
    catalog = ItemCatalog.from_ddragon({"data": {**ITEMS, **EXTRA}})
    svc.dd.item_catalog = catalog

    class Opgg(FakeOpgg):
        def matchup_guide(self, champ, opp, role, lang="es_ES", allow_fetch=True):
            return (guides or {}).get((champ, opp))

    fake = install_opgg(svc, Opgg(builds=generals or {}))
    return fake


GUIDE = {
    "core_items": [{"ids": [3071, 3053, 3161], "games": 900, "wins": 480}],
    "boots": [{"ids": [3111]}], "starter_items": [{"ids": [1055, 2003]}], "spells": [{"ids": [4, 12]}],
    "skills": [{"order": "QEWQQR"}],
    "runes": [{"keystone": 8010, "primary": 8000, "secondary": 8400, "ids": [8010, 9111], "names": ["Conquistador", "Triunfo"]}],
}


def test_base_build_comes_from_the_lane_matchup(keyed_svc):
    _svc(keyed_svc, guides={(AATROX, DARIUS): GUIDE})
    out = builds.recommend(keyed_svc, Filters(), {"TOP": AATROX}, {"TOP": DARIUS})
    (top,) = out["ally"]
    assert top["base"]["source"] == "matchup" and top["base"]["opponent"] == DARIUS
    assert top["base"]["core"] == [3071, 3053, 3161] and top["base"]["boots"] == [3111] and top["base"]["runes"]["keystone"] == 8010
    assert top["style"] == "ad" and out["enemy"][0]["champ"] == DARIUS


def test_general_build_when_there_is_no_lane_opponent(keyed_svc):
    _svc(keyed_svc, generals={(AATROX, "TOP"): {"core": [3071, 3053, 6694], "boots": [3047], "skill_max": ["Q", "W", "E"], "popular": [], "fourth": []}})
    (top,) = builds.recommend(keyed_svc, Filters(), {"TOP": AATROX}, {})["ally"]
    assert top["base"]["source"] == "general" and top["base"]["core"] == [3071, 3053, 6694] and top["situational"] == []


def test_draft_adjustments_have_a_reason(keyed_svc):
    """Contra Aatrox (se cura), mucho daño físico y dos asesinos: Heridas graves, botas blindadas, armadura y defensivo."""
    _svc(keyed_svc, guides={(DARIUS, AATROX): GUIDE}, generals={(DARIUS, "TOP"): {"core": [3071, 3053, 3161], "boots": [3111], "popular": [{"id": 3075}, {"id": 3033}], "fourth": []}})
    enemy = {"TOP": AATROX, "JUNGLE": VIEGO, "MID": ZED, "ADC": JINX, "SUPPORT": THRESH}
    out = builds.recommend(keyed_svc, Filters(), {"TOP": DARIUS, "MID": AHRI}, enemy)
    darius = next(p for p in out["ally"] if p["champ"] == DARIUS)
    kinds = {s["kind"]: s for s in darius["situational"]}
    assert kinds["anti_heal"]["item"] in (3033, 3075) and AATROX in kinds["anti_heal"]["vs"] and "Aatrox" in kinds["anti_heal"]["reason"]
    assert kinds["boots"]["item"] == 3047 and kinds["boots"]["replaces"] == 3111 and "físico" in kinds["boots"]["reason"]
    assert all(s["reason"] for s in darius["situational"])
    # Carry blando: botas solo si el daño rival es extremo (≥ 70 %), y nunca armadura de objeto.
    ahri = next(p for p in out["ally"] if p["champ"] == AHRI)
    boots = next((s for s in ahri["situational"] if s["kind"] == "boots"), None)
    assert boots is None or int(boots["reason"].split("%")[0].split()[-1]) >= 70
    assert "armor" not in {s["kind"] for s in ahri["situational"]}


def test_squishy_carry_gets_a_defensive_item_against_assassins(keyed_svc):
    _svc(keyed_svc)
    enemy = {"TOP": GAREN, "JUNGLE": LEE_SIN, "MID": ZED, "ADC": JINX, "SUPPORT": ORIANNA}
    (jinx,) = builds.recommend(keyed_svc, Filters(), {"ADC": JINX}, enemy)["ally"]
    defensive = next(s for s in jinx["situational"] if s["kind"] == "defensive")
    assert defensive["item"] == 3026 and set(defensive["vs"]) == {LEE_SIN, ZED}


def test_endpoint(keyed_svc):
    from tests.test_crawler_otp import _client

    _svc(keyed_svc, guides={(AATROX, DARIUS): GUIDE})
    with _client(keyed_svc) as client:
        res = client.post("/api/draft/builds", json={"ally": {"TOP": "Aatrox"}, "enemy": {"TOP": DARIUS}})
    assert res.status_code == 200, res.text
    assert res.json()["ally"][0]["base"]["core"] == [3071, 3053, 3161]


def test_only_two_players_per_team_get_anti_heal(keyed_svc):
    _svc(keyed_svc, generals={
        (DARIUS, "TOP"): {"core": [3071, 3053, 3161], "boots": [3047], "popular": [{"id": 3075}], "fourth": []},
        (JINX, "ADC"): {"core": [3071, 3053, 3161], "boots": [3006], "popular": [{"id": 3033}], "fourth": []},
        (LEE_SIN, "JUNGLE"): {"core": [3071, 3053, 3161], "boots": [3047], "popular": [{"id": 3033}], "fourth": []},
    })
    enemy = {"TOP": AATROX}  # se cura
    out = builds.recommend(keyed_svc, Filters(), {"TOP": DARIUS, "JUNGLE": LEE_SIN, "ADC": JINX}, enemy)
    with_gw = {p["champ"] for p in out["ally"] if any(s["kind"] == "anti_heal" for s in p["situational"])}
    assert with_gw == {JINX, DARIUS}  # el ADC y el top; el jungla no
    jinx = next(p for p in out["ally"] if p["champ"] == JINX)
    assert "con dos basta" in next(s for s in jinx["situational"] if s["kind"] == "anti_heal")["reason"]
