"""Draft en vivo con el cliente de LoL: lectura de la selección de campeones e importación de la build."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

import app.services
from app import draft_live as dl
from app import lcu
from app.main import create_app
from tests.factories import make_settings, write_ddragon_cache, write_env

# Campeones de ejemplo (ids de Data Dragon).
AATROX, HECARIM, AHRI, JINX, THRESH = 266, 120, 103, 222, 412
DARIUS, LEE, SYNDRA, KAISA, NAUTILUS = 122, 64, 134, 145, 111
SHARE = {
    AATROX: {"TOP": 0.95}, HECARIM: {"JUNGLE": 0.9, "TOP": 0.1}, AHRI: {"MID": 0.97}, JINX: {"ADC": 0.98}, THRESH: {"SUPPORT": 0.99},
    DARIUS: {"TOP": 0.9, "JUNGLE": 0.05}, LEE: {"JUNGLE": 0.85, "TOP": 0.1}, SYNDRA: {"MID": 0.9, "SUPPORT": 0.05},
    KAISA: {"ADC": 0.95}, NAUTILUS: {"SUPPORT": 0.9, "TOP": 0.05},
}


def share(champ, role):
    return SHARE.get(champ, {}).get(role, 0.0)


def member(cell, champ=0, position="", intent=0, spells=(4, 14)):
    return {"cellId": cell, "championId": champ, "championPickIntent": intent, "assignedPosition": position,
            "spell1Id": spells[0], "spell2Id": spells[1]}


def session(*, my_champ=HECARIM, my_locked=True, enemy=(DARIUS, LEE, SYNDRA, KAISA, NAUTILUS), phase="BAN_PICK", positions=True, game_id=7):
    pos = ["top", "jungle", "middle", "bottom", "utility"] if positions else [""] * 5
    allies = [AATROX, my_champ, AHRI, JINX, THRESH]
    my_team = [member(i, c, pos[i], spells=(4, 11) if i == 1 else (4, 14)) for i, c in enumerate(allies)]
    their = [member(5 + i, c) for i, c in enumerate(enemy)]
    actions = [[{"type": "ban", "completed": True, "championId": 157, "isAllyAction": True, "actorCellId": 0}]]
    for i, c in enumerate(allies):
        if i == 1 and not my_locked:
            continue
        actions.append([{"type": "pick", "completed": True, "championId": c, "actorCellId": i, "isAllyAction": True}])
    for i, c in enumerate(enemy):
        if c:
            actions.append([{"type": "pick", "completed": True, "championId": c, "actorCellId": 5 + i, "isAllyAction": False}])
    return {"localPlayerCellId": 1, "myTeam": my_team, "theirTeam": their, "actions": actions, "timer": {"phase": phase}, "gameId": game_id}


# ------------------------------------------------------------------ hechizos
def test_smite_goes_to_the_preferred_key():
    assert dl.order_spells([4, 11], "D") == [11, 4]
    assert dl.order_spells([11, 4], "F") == [4, 11]
    assert dl.order_spells([6, 11], "F") == [6, 11]
    assert dl.order_spells([4, 14], "D") == [4, 14]  # sin Castigo: el orden de OP.GG
    assert dl.order_spells([4], "D") is None and dl.order_spells([4, 4], "D") is None


# ------------------------------------------------------------------ roles
def test_assign_roles_uses_where_each_champion_is_played_and_smite():
    out = dl.assign_roles([NAUTILUS, DARIUS, KAISA, LEE, SYNDRA], share)
    assert out == {"TOP": DARIUS, "JUNGLE": LEE, "MID": SYNDRA, "ADC": KAISA, "SUPPORT": NAUTILUS}
    # Darius con Castigo: va a la jungla aunque casi siempre se juegue arriba.
    smite = dl.assign_roles([DARIUS, LEE], share, smite={DARIUS})
    assert smite["JUNGLE"] == DARIUS and smite["TOP"] == LEE
    # Los roles que da el cliente se respetan.
    fixed = dl.assign_roles([AATROX, HECARIM], share, fixed={HECARIM: "TOP"})
    assert fixed["TOP"] == HECARIM and fixed["JUNGLE"] != HECARIM


# ------------------------------------------------------------------ sesión
def test_parse_session_builds_both_teams_and_detects_the_end():
    d = dl.parse_session(session(), share)
    assert d["ally"] == {"TOP": AATROX, "JUNGLE": HECARIM, "MID": AHRI, "ADC": JINX, "SUPPORT": THRESH}
    assert d["enemy"] == {"TOP": DARIUS, "JUNGLE": LEE, "MID": SYNDRA, "ADC": KAISA, "SUPPORT": NAUTILUS}
    assert d["me"] == {"role": "JUNGLE", "champ": HECARIM, "locked": True, "cell": 1}
    assert d["complete"] and d["bans"]["ally"] == [157]


def test_parse_session_waits_for_the_last_picks_and_your_lock():
    missing = dl.parse_session(session(enemy=(DARIUS, LEE, SYNDRA, KAISA, 0)), share)
    assert not missing["complete"] and "SUPPORT" not in missing["enemy"]
    hovering = session(my_locked=False)
    hovering["myTeam"][1].update(championId=0, championPickIntent=HECARIM)
    d = dl.parse_session(hovering, share)
    assert d["ally"]["JUNGLE"] == HECARIM and d["hovers"] == [HECARIM]  # se analiza ya, pero sin importar
    assert not d["complete"] and d["me"]["locked"] is False


def test_blind_pick_imports_in_finalization_and_deduces_your_role():
    s = session(enemy=(0, 0, 0, 0, 0), positions=False, phase="FINALIZATION")
    s["theirTeam"] = []  # a ciegas el cliente no manda al rival
    assert not dl.parse_session(session(enemy=(0, 0, 0, 0, 0)), share)["blind"]  # en draft, al principio, sí lo manda vacío
    d = dl.parse_session(s, share)
    assert d["blind"] and d["enemy"] == {}
    assert d["me"]["role"] == "JUNGLE"  # lleva Castigo
    assert d["complete"]


# ------------------------------------------------------------------ importar
class FakeClient:
    def __init__(self, fail_runes=False):
        self.pages = [{"id": 1, "name": "Mi página", "isDeletable": True}, {"id": 2, "name": "Tempu · Viejo", "isDeletable": True}]
        self.created, self.deleted, self.spells, self.saved = [], [], None, None
        self.fail_runes = fail_runes
        self.sets = {"accountId": 99, "itemSets": [{"title": "Mi set", "uid": "a"}, {"title": "Tempu · Viejo", "uid": "b"}], "timestamp": 1}

    def rune_pages(self):
        return list(self.pages)

    def delete_rune_page(self, page_id):
        self.deleted.append(page_id)

    def create_rune_page(self, page):
        if self.fail_runes:
            raise lcu.LCUError("Max pages reached", 400)
        self.created.append(page)
        return page

    def set_summoner_spells(self, a, b):
        self.spells = [a, b]

    def summoner_id(self):
        return 99

    def item_sets(self, sid):
        return dict(self.sets)

    def save_item_sets(self, sid, payload):
        self.saved = payload


REC = {
    "champ": HECARIM, "opponent": LEE,
    "runes": {"keystone": 8230, "primary": 8200, "secondary": 8000, "ids": [8230, 8275, 8234, 8232, 9105, 9111]},
    "spells": [6, 11], "starter": [1102], "boots": {"item": 3111},
    "build": [{"item": 6631, "kind": "core"}, {"item": 3071, "kind": "core"}, {"item": 3065, "kind": "situational"}],
    "alternatives": [{"item": 3742}],
}


def test_apply_import_writes_runes_spells_and_item_set_without_touching_yours():
    client = FakeClient()
    prefs = {**dl.DEFAULT_PREFS, "smite_key": "D"}
    extra = {"keystone": 8230, "shards": [5008, 5008, 5001]}
    done = dl.apply_import(client, REC, "Hecarim", "Lee Sin", prefs, extra)
    assert done["errors"] == []
    assert client.deleted == [2]  # solo la página que creó la app
    page = client.created[0]
    assert page["primaryStyleId"] == 8200 and page["subStyleId"] == 8000 and page["current"]
    assert page["selectedPerkIds"] == [8230, 8275, 8234, 8232, 9105, 9111, 5008, 5008, 5001]
    assert client.spells == [11, 6]  # Castigo en la D
    titles = [s["title"] for s in client.saved["itemSets"]]
    assert titles[0] == "Mi set" and len(titles) == 2 and titles[1].startswith("Tempu · Hecarim vs Lee Sin")
    blocks = {b["type"]: [i["id"] for i in b["items"]] for b in client.saved["itemSets"][1]["blocks"]}
    assert blocks["Inicio"] == ["1102"] and blocks["Núcleo"] == ["6631", "3071"] and blocks["Botas"] == ["3111"]
    assert client.saved["itemSets"][1]["associatedChampions"] == [HECARIM] and client.saved["accountId"] == 99


def test_apply_import_respects_prefs_and_reports_client_errors():
    client = FakeClient(fail_runes=True)
    done = dl.apply_import(client, REC, "Hecarim", None, {**dl.DEFAULT_PREFS, "items": False}, None)
    assert done["runes"] is None and "Runas: Max pages reached" in done["errors"][0]
    assert done["spells"] == [6, 11] and done["items"] is None and client.saved is None
    # Sin fragmentos de OP.GG: los más jugados.
    ok = FakeClient()
    dl.apply_import(ok, REC, "Hecarim", None, dl.DEFAULT_PREFS, None)
    assert ok.created[0]["selectedPerkIds"][-3:] == dl.DEFAULT_SHARDS


# ------------------------------------------------------------------ vigilante y API
@pytest.fixture
def api(tmp_path, monkeypatch):
    settings = make_settings(tmp_path)
    monkeypatch.setattr(app.services, "ENV_PATH", write_env(tmp_path))
    application = create_app(settings, run_startup=False)
    svc = application.state.svc
    write_ddragon_cache(settings.ddragon_dir)
    assert svc.dd.load(allow_network=False)
    with TestClient(application) as client:
        yield client, svc
    svc.db.close()


def test_prefs_api_and_import_needs_champ_select(api):
    client, svc = api
    body = client.get("/api/draft/live").json()
    assert body["active"] is False and body["prefs"] == dl.DEFAULT_PREFS
    saved = client.put("/api/draft/live/prefs", json={"smite_key": "d", "items": False}).json()
    assert saved["smite_key"] == "D" and saved["items"] is False and saved["runes"] is True
    assert dl.get_prefs(svc.db)["smite_key"] == "D"
    assert client.put("/api/draft/live/prefs", json={"smite_key": "Q"}).status_code == 422
    assert client.post("/api/draft/live/import").status_code == 409


def test_watcher_follows_champ_select_and_imports_once(api, monkeypatch):
    _client, svc = api
    live = svc.draft_live
    calls = []
    current = {"s": session(phase="BAN_PICK", enemy=(DARIUS, LEE, SYNDRA, KAISA, 0))}
    monkeypatch.setattr(lcu, "gameflow_phase", lambda: "ChampSelect")
    monkeypatch.setattr(lcu, "champ_select_session", lambda: current["s"])
    monkeypatch.setattr(live, "_share", lambda: share)
    monkeypatch.setattr(dl, "apply_import", lambda client, rec, name, opp, prefs, extra: calls.append((rec["champ"], prefs["smite_key"])) or {"runes": [1], "spells": [4, 11], "items": 3, "errors": []})
    from app.analytics import builds

    monkeypatch.setattr(builds, "recommend", lambda svc_, f, ally, enemy, sources="all": {"ally": [{**REC, "champ": ally["JUNGLE"]}], "enemy": []})
    live.poll()
    assert live.state["active"] and not live.state["draft"]["complete"] and calls == []
    current["s"] = session(phase="FINALIZATION")
    live.poll()
    live.poll()  # la misma selección: no se vuelve a importar
    assert calls == [(HECARIM, "F")] and live.state["import"]["status"] == "ok"
    monkeypatch.setattr(lcu, "gameflow_phase", lambda: "InProgress")
    live.poll()
    assert live.state["active"] is False
