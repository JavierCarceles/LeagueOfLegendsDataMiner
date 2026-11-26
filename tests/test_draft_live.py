"""Draft en vivo con el cliente de LoL: lectura de la selección de campeones e importación de la build."""

from __future__ import annotations

import threading

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
    def __init__(self, pages=None, slots=3, fail_runes=False):
        self.pages = pages if pages is not None else [
            {"id": 1, "name": "Mi página", "isEditable": True, "isDeletable": True},
            {"id": 2, "name": "Kayn TD", "isEditable": True, "isDeletable": True},
        ]
        self.slots = slots
        self.created, self.updated, self.deleted, self.current, self.spells, self.saved = [], [], [], None, None, None
        self.fail_runes = fail_runes
        self.sets = {"accountId": 99, "itemSets": [{"title": "Mi set", "uid": "a"}, {"title": "Tempu · Viejo", "uid": "b"}], "timestamp": 1}

    def rune_pages(self):
        return list(self.pages)

    def rune_page_slots(self):
        return self.slots

    def update_rune_page(self, page_id, page):
        if self.fail_runes:
            raise lcu.LCUError("no se puede editar", 400)
        self.updated.append((page_id, page))

    def delete_rune_page(self, page_id):
        self.deleted.append(page_id)

    def create_rune_page(self, page):
        self.created.append(page)
        return {**page, "id": 50}

    def set_current_rune_page(self, page_id):
        self.current = page_id

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


def test_runes_always_overwrite_the_td_page_in_its_slot():
    client = FakeClient()
    prefs = {**dl.DEFAULT_PREFS, "smite_key": "D"}
    done = dl.apply_import(client, REC, "Hecarim", "Lee Sin", prefs, {"keystone": 8230, "shards": [5008, 5008, 5001]})
    assert done["errors"] == [] and done["rune_page"] == "updated"
    page_id, page = client.updated[0]
    assert page_id == 2 and page["name"] == "Hecarim TD" and client.current == 2  # el mismo hueco, renombrada
    assert page["primaryStyleId"] == 8200 and page["subStyleId"] == 8000
    assert page["selectedPerkIds"] == [8230, 8275, 8234, 8232, 9105, 9111, 5008, 5008, 5001]
    assert client.created == [] and client.deleted == []  # «Mi página» intacta
    assert client.spells == [11, 6]  # Castigo en la D
    titles = [s["title"] for s in client.saved["itemSets"]]
    assert titles == ["Mi set", "Hecarim TD vs Lee Sin"]  # el conjunto antiguo de la app se sustituye
    blocks = {b["type"]: [i["id"] for i in b["items"]] for b in client.saved["itemSets"][1]["blocks"]}
    assert blocks["Inicio"] == ["1102"] and blocks["Núcleo"] == ["6631", "3071"] and blocks["Botas"] == ["3111"]


def test_same_name_is_overwritten_too_and_td_must_be_a_word():
    same = FakeClient(pages=[{"id": 7, "name": "Hecarim TD"}, {"id": 8, "name": "STDOUT"}])
    dl.apply_import(same, REC, "Hecarim", None, dl.DEFAULT_PREFS, None)
    assert [pid for pid, _p in same.updated] == [7]
    assert dl.is_app_page("Jinx TD") and dl.is_app_page("TD") and dl.is_app_page("Tempu · Viejo") and dl.is_app_page("Kayn (TD)")
    assert not dl.is_app_page("STDOUT") and not dl.is_app_page("Mi página")


def test_without_a_td_page_it_uses_a_free_slot_or_explains_what_to_do():
    free = FakeClient(pages=[{"id": 1, "name": "Mi página"}], slots=2)
    done = dl.apply_import(free, REC, "Hecarim", None, dl.DEFAULT_PREFS, None)
    assert done["rune_page"] == "created" and free.created[0]["name"] == "Hecarim TD" and free.current == 50
    assert free.created[0]["selectedPerkIds"][-3:] == dl.DEFAULT_SHARDS  # sin fragmentos de OP.GG: los más jugados
    full = FakeClient(pages=[{"id": 1, "name": "A"}, {"id": 3, "name": "B"}], slots=2)
    done = dl.apply_import(full, REC, "Hecarim", None, dl.DEFAULT_PREFS, None)
    assert done["runes"] is None and "Pon «TD» en el nombre" in done["errors"][0]
    assert full.created == [] and full.deleted == [] and full.updated == []  # nunca toca tus páginas
    # Las páginas por defecto del cliente (no editables) no cuentan como de la app.
    fixed = FakeClient(pages=[{"id": 9, "name": "TD", "isEditable": False, "isDeletable": False}], slots=5)
    assert dl.apply_import(fixed, REC, "Hecarim", None, dl.DEFAULT_PREFS, None)["rune_page"] == "created"


def test_apply_import_respects_prefs_and_falls_back_when_the_client_cannot_edit():
    client = FakeClient(fail_runes=True)
    done = dl.apply_import(client, REC, "Hecarim", None, {**dl.DEFAULT_PREFS, "items": False}, None)
    assert done["rune_page"] == "updated" and client.deleted == [2] and client.created[0]["name"] == "Hecarim TD"
    assert done["spells"] == [6, 11] and done["items"] is None and client.saved is None


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
    live._import_thread.join(5)  # la build se importa aparte: el bucle del draft no espera
    live.poll()  # la misma selección: no se vuelve a importar
    assert calls == [(HECARIM, "F")] and live.state["import"]["status"] == "ok"
    monkeypatch.setattr(lcu, "gameflow_phase", lambda: "InProgress")
    live.poll()
    assert live.state["active"] is False


def test_a_last_moment_champion_change_is_seen_while_the_build_is_computed(api, monkeypatch):
    # El 26-11-2026 la build tardó minutos en calcularse con el PC cargado y, mientras, el draft no vio el cambio de
    # campeón de última hora: ahora se calcula aparte y la del campeón viejo no pisa la del nuevo.
    _client, svc = api
    live = svc.draft_live
    calls, release = [], threading.Event()
    current = {"s": session(phase="FINALIZATION")}
    monkeypatch.setattr(lcu, "gameflow_phase", lambda: "ChampSelect")
    monkeypatch.setattr(lcu, "champ_select_session", lambda: current["s"])
    monkeypatch.setattr(live, "_share", lambda: share)
    monkeypatch.setattr(dl, "apply_import", lambda client, rec, name, opp, prefs, extra: calls.append(rec["champ"]) or {"runes": [1], "spells": [4, 11], "items": 3, "errors": []})
    from app.analytics import builds

    def slow(svc_, f, ally, enemy, sources="all"):
        release.wait(5)
        return {"ally": [{**REC, "champ": ally["JUNGLE"]}], "enemy": []}

    monkeypatch.setattr(builds, "recommend", slow)
    live.poll()  # completo con Hecarim: empieza a calcular su build (lenta)
    first = live._import_thread
    current["s"] = session(phase="FINALIZATION", my_champ=LEE, enemy=(DARIUS, HECARIM, SYNDRA, KAISA, NAUTILUS))
    live.poll()  # el draft ve el cambio aunque la build anterior siga calculándose
    assert live.state["draft"]["me"]["champ"] == LEE and calls == []
    release.set()
    first.join(5)
    live._import_thread.join(5)
    assert calls == [LEE] and live.state["import"]["champ"] == LEE


class FakeAccountClient:
    def __init__(self, platform="euw1"):
        self.platform = platform

    def current_summoner(self):
        return {"game_name": "Tempu", "tag_line": "EUW", "platform": self.platform, "profile_icon": 7, "summoner_level": 300}


def test_profile_is_detected_from_the_client_without_clicking(api):
    client, svc = api
    from app.coach import store

    assert dl.ensure_profile(svc, FakeAccountClient(platform=None)) is None  # sin región aún: se reintenta
    profile = dl.ensure_profile(svc, FakeAccountClient())
    assert profile["game_name"] == "Tempu" and profile["platform"] == "euw1" and store.get_profile(svc.db)["tag_line"] == "EUW"
    assert dl.ensure_profile(svc, FakeAccountClient()) is None  # ya hay perfil: no se toca
    # Si lo borras tú, no se vuelve a crear solo.
    assert client.delete("/api/profile").status_code == 200
    assert dl.ensure_profile(svc, FakeAccountClient()) is None and store.get_profile(svc.db) is None


class StubbornClient(FakeClient):
    """El cliente tarda en aplicar los hechizos (o no los aplica nunca)."""

    def __init__(self, applies_after=1):
        super().__init__()
        self.applies_after = applies_after
        self.calls = 0

    def set_summoner_spells(self, a, b):
        self.calls += 1
        if self.calls >= self.applies_after:
            self.spells = [a, b]

    def my_spells(self):
        return self.spells or [4, 14]


def test_spells_are_checked_in_the_client_and_retried(monkeypatch):
    monkeypatch.setattr(dl.time, "sleep", lambda s: None)
    late = StubbornClient(applies_after=2)
    assert dl.set_spells_checked(late, [6, 11]) == [6, 11] and late.calls == 2
    never = StubbornClient(applies_after=99)
    assert dl.set_spells_checked(never, [6, 11]) is None and never.calls == dl.SPELL_TRIES
    done = dl.apply_import(never, REC, "Hecarim", None, dl.DEFAULT_PREFS, None)
    assert done["spells"] is None and any("no los aceptó" in e for e in done["errors"])


def test_spells_never_stay_empty_when_opgg_was_late(api, monkeypatch):
    """Caso real: OP.GG aún no había respondido al acabar la selección y no se puso el Fantasmal."""
    _client, svc = api
    from app.coach import store

    assert dl.spells_for(svc, {"spells": [6, 11]}, HECARIM, "JUNGLE") == ([6, 11], "build")
    assert dl.spells_for(svc, {"spells": []}, HECARIM, "JUNGLE") == ([], None)  # sin OP.GG ni partidas tuyas
    match = {"info": {"participants": [{"participantId": 7, "summoner1Id": 6, "summoner2Id": 11}]}}
    with svc.db.write() as conn:
        for i in range(3):
            conn.execute("INSERT INTO my_games (match_id, pid, champ, role, game_start, status, match_raw, fetched_at) VALUES (?, 7, ?, 'JUNGLE', ?, 'ready', ?, 0)",
                         (f"EUW1_{i}", HECARIM, i, store.pack(match)))
    assert store.usual_spells(svc.db, HECARIM) == [6, 11]
    assert dl.spells_for(svc, {"spells": []}, HECARIM, "JUNGLE") == ([6, 11], "tuyos")

    class LateOpgg:
        enabled = True

        def champion_build(self, champ, role, allow_fetch=True):
            return {"spells": [4, 11]} if allow_fetch else None

    svc.opgg = LateOpgg()
    assert dl.spells_for(svc, {"spells": []}, HECARIM, "JUNGLE") == ([4, 11], "opgg")
