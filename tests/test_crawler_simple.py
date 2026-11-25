"""Buscador sencillo del recolector (26-11-2026): campeones o roles, cualquiera u OTPs (por su maestría), colas en
lista (también normales), variedad antes que cantidad y el elo de la partida para quien no tiene elo propio."""

from __future__ import annotations

import json
from types import SimpleNamespace

from app.ingest import crawler as crawler_mod
from app.ingest import otp_details
from app.ingest.crawler import CrawlerConfig, RegionWorker, otp_list_key, rank_of_value, rank_value, search_funnel
from tests.factories import AATROX, DARIUS, GAREN, make_match


def _now() -> int:
    from app.data.db import now_s

    return now_s()


def _ratio(**kw) -> CrawlerConfig:
    return CrawlerConfig(platforms=["euw1"], tiers=["GOLD"], champions=[AATROX, DARIUS], otp_rule="ratio", **kw).validated()


def test_an_otp_is_whoever_plays_their_main_far_more_than_the_second():
    """OTP = su campeón con más maestría (de los buscados), con 1,5 veces los puntos del segundo; sin mínimo de puntos
    (solo el de cuentas nuevas) y sin revisar sus partidas para decidirlo."""
    cfg = _ratio()
    now = _now()
    assert cfg.min_mastery == crawler_mod.GAMES_MIN_MASTERY and cfg.min_share == 0 and cfg.focus == "otp"
    assert cfg.main_otp([[AATROX, 30_000, now], [GAREN, 20_000, now]]) == AATROX
    assert cfg.main_otp([[AATROX, 30_000, now], [GAREN, 21_000, now]]) is None  # 1,43 veces: no
    assert cfg.main_otp([[GAREN, 900_000, now], [AATROX, 100_000, now]]) is None  # el buscado no es su principal
    assert cfg.main_otp([[DARIUS, 15_000, now]]) == DARIUS  # sin segundo
    assert cfg.main_otp([[DARIUS, 5_000, now]]) is None  # cuenta casi nueva
    assert cfg.main_otp([[AATROX, 900_000, 1]]) is None  # no lo ha jugado en el periodo
    assert cfg.qualifies([[AATROX, 30_000, now], [GAREN, 20_000, now]])
    assert cfg.own_champs([[AATROX, 30_000, now], [DARIUS, 20_000, now]]) == {AATROX}  # solo su principal
    assert crawler_mod.otp_champs(cfg, [[AATROX, 30_000, now], [DARIUS, 10_000, now]]) == [AATROX]
    assert cfg.share_ok(20, 0)  # no se le revisa: lo dice su maestría


def test_any_player_still_counts_whoever_has_it_among_their_three_mains():
    cfg = CrawlerConfig(champions=[AATROX], focus="games", otp_rule="ratio").validated()
    now = _now()
    assert cfg.focus == "games" and cfg.min_mastery == crawler_mod.GAMES_MIN_MASTERY
    assert cfg.qualifies([[GAREN, 900_000, now], [AATROX, 40_000, now]])


def test_queues_ask_riot_for_just_what_counts():
    """SoloQ, Flex y normales: una cola o un tipo entero si es justo lo elegido; si no, todas y se descarta lo que no
    cuenta al bajarlo. La clave de la lista no cambia para lo de antes (SoloQ, SoloQ y Flex)."""
    cases = {
        ("solo",): ((420, None), "420"),
        ("flex",): ((440, None), "q:flex"),
        ("solo", "flex"): ((None, "ranked"), "ranked"),
        ("normal",): ((None, "normal"), "q:normal"),
        ("solo", "normal"): ((None, None), "q:solo+normal"),
    }
    for queues, (flt, key) in cases.items():
        cfg = CrawlerConfig(champions=[AATROX], queues=list(reversed(queues))).validated()
        assert tuple(cfg.queues) == queues and cfg.list_filter() == flt
        assert otp_list_key(cfg).startswith(key + "|")
    normal = CrawlerConfig(champions=[AATROX], queues=["normal"]).validated()
    assert normal.queue_ids() == (400, 430, 480, 490) and not normal.include_flex
    old = CrawlerConfig(champions=[AATROX], include_flex=True).validated()  # configuración antigua
    assert old.queues == ["solo", "flex"] and old.queue_ids() == (420, 440)


def test_only_games_of_the_chosen_queues_count(keyed_svc):
    """Sin filtro en la lista (SoloQ y normales), una de Flex o un ARAM no cuentan; una normal sí."""
    cfg = CrawlerConfig(platforms=["euw1"], tiers=["GOLD"], champions=[AATROX], queues=["solo", "normal"], save_details=False).validated()
    worker = RegionWorker(keyed_svc.crawler, "europe", ["euw1"], cfg)
    payloads = {
        "EUW1_1": make_match("EUW1_1", puuids={1: "p-q"}, blue={"TOP": AATROX}, queue=400),
        "EUW1_2": make_match("EUW1_2", puuids={1: "p-q"}, blue={"TOP": AATROX}, queue=440),
        "EUW1_3": make_match("EUW1_3", puuids={1: "p-q"}, blue={"TOP": AATROX}, queue=450, map_id=12),
    }
    worker._download_match = lambda match_id: (payloads[match_id], None)
    player = {"id": 1, "puuid": "p-q", "tier": "GOLD", "champs": {AATROX}}
    with keyed_svc.db.write() as conn:
        conn.execute("INSERT INTO players (id, puuid, platform, tier, is_seed) VALUES (1, 'p-q', 'euw1', 'GOLD', 1)")
    got = {m: worker._ingest(m, player) for m in payloads}
    assert {m: g.valid for m, g in got.items()} == {"EUW1_1": True, "EUW1_2": False, "EUW1_3": False}
    # Ni la de Flex ni el ARAM se guardan como partidas de Aatrox (antes la lista ya venía filtrada).
    assert {m: g.result for m, g in got.items()} == {"EUW1_1": "stored", "EUW1_2": "other", "EUW1_3": "other"}


def test_saved_searches_move_to_the_simple_one():
    """Una búsqueda guardada con el buscador antiguo se reanuda con el nuevo: OTP por maestría, variedad (20 partidas
    de cada jugador primero, sin volver a por las nuevas) y las colas que tenía."""
    old = CrawlerConfig.from_saved(
        {"champions": [AATROX], "focus": "otp", "min_share": 0.5, "min_mastery": 50_000, "include_flex": True,
         "recrawl_hours": 12, "games_per_otp": 30, "only_target_games": False}
    )
    assert (old.otp_rule, old.focus, old.min_share, old.min_mastery) == ("ratio", "otp", 0.0, crawler_mod.GAMES_MIN_MASTERY)
    assert (old.recrawl_hours, old.games_per_otp, old.only_target_games, old.queues) == (0.0, 20, True, ["solo", "flex"])
    assert CrawlerConfig.from_saved({"champions": [AATROX], "focus": "games"}).focus == "games"
    assert CrawlerConfig.from_saved({"champions": [AATROX], "focus": "mastery"}).focus == "otp"
    kept = CrawlerConfig.from_saved({"champions": [AATROX], "otp_rule": "ratio", "queues": ["normal"]})  # ya nueva
    assert kept.queues == ["normal"]


def test_variety_never_goes_back_for_the_new_games_of_a_player(keyed_svc):
    """Sin «partidas nuevas cada 12 h»: un jugador ya visitado no vuelve a la cola por sus partidas nuevas."""
    cfg = _ratio(recrawl_hours=0, games_per_otp=20)
    assert cfg.recrawl_hours == 0
    db, now = keyed_svc.db, _now()
    with db.write() as conn:
        conn.execute("INSERT INTO players (puuid, platform, tier, is_seed, lp, last_crawled_at) VALUES ('seen', 'euw1', 'GOLD', 1, 10, ?)", (now - 5 * 86400,))
        conn.execute("INSERT INTO mastery_checks VALUES ('seen', 'euw1', ?, ?)", (now, json.dumps([[AATROX, 90_000, now]])))
        conn.execute(
            "INSERT INTO otp_progress (puuid, list_key, anchor, back_offset, back_done_at, back_done_start, back_done_reason, hits, seen, updated_at, config_key) "
            "VALUES ('seen', ?, ?, 40, ?, 0, 'end', 20, 40, ?, ?)",
            (otp_list_key(cfg), now - 6 * 86400, now - 5 * 86400, now, crawler_mod.otp_config_key(cfg)),
        )
    worker = RegionWorker(keyed_svc.crawler, "europe", ["euw1"], cfg)
    assert worker._next_otp() is None
    assert CrawlerConfig(champions=[AATROX], recrawl_hours=12).validated().recrawl_hours == 12


def test_the_funnel_of_otps_by_mastery(keyed_svc):
    db, now = keyed_svc.db, _now()
    tops = {
        "a": [[AATROX, 90_000, now], [GAREN, 20_000, now]],  # OTP
        "b": [[AATROX, 90_000, now], [GAREN, 80_000, now]],  # principal, pero no tan por encima
        "c": [[GAREN, 90_000, now], [AATROX, 50_000, now]],  # Aatrox de segundo
        "d": [[DARIUS, 90_000, 1]],  # OTP de antes del periodo
    }
    with db.write() as conn:
        for puuid, top in tops.items():
            conn.execute("INSERT INTO players (puuid, platform, tier, is_seed, lp) VALUES (?, 'euw1', 'GOLD', 1, 1)", (puuid,))
            conn.execute("INSERT INTO mastery_checks VALUES (?, 'euw1', ?, ?)", (puuid, now, json.dumps(top)))
    funnel = search_funnel(db, _ratio(), now)
    assert funnel["stages"] == ["checked", "champ", "ratio", "active"]
    assert {k: funnel["total"][k] for k in funnel["stages"]} == {"checked": 4, "champ": 3, "ratio": 2, "active": 1}


def test_unvisited_otps_by_mastery_are_the_ones_that_enter_the_queue(keyed_svc, monkeypatch):
    monkeypatch.setattr(crawler_mod, "PLAN_UNVISITED_PER_TIER", 1)
    db, now = keyed_svc.db, _now()
    with db.write() as conn:
        for puuid, top, lp in (("close", [[AATROX, 90_000, now], [GAREN, 80_000, now]], 900), ("otp", [[AATROX, 60_000, now], [GAREN, 10_000, now]], 1)):
            conn.execute("INSERT INTO players (puuid, platform, tier, is_seed, lp) VALUES (?, 'euw1', 'GOLD', 1, ?)", (puuid, lp))
            conn.execute("INSERT INTO mastery_checks VALUES (?, 'euw1', ?, ?)", (puuid, now, json.dumps(top)))
    rows = crawler_mod.otp_rows_for_plan(db, _ratio(), now)
    assert [r["puuid"] for r, _ in rows] == ["otp"]  # el de más LP no es OTP: no ocupa el sitio


def test_games_of_an_otp_by_mastery_count_as_otp_at_once(keyed_svc):
    """Lo que el buscador llama OTP también lo es en las carpetas y la gráfica: al guardar la partida (sin esperar a
    revisar 20 suyas) y al recontar. Antes de la regla nueva, alguien así quedaba en «resto» si jugaba otros campeones."""
    db = keyed_svc.db
    svc = SimpleNamespace(db=db, dd=keyed_svc.dd, settings=keyed_svc.settings)
    now = _now()
    with db.write() as conn:
        conn.execute("INSERT INTO mastery_checks VALUES ('p-r', 'euw1', ?, ?)", (now, json.dumps([[AATROX, 30_000, now], [GAREN, 15_000, now]])))
        conn.execute("INSERT INTO otp_progress (puuid, list_key, anchor, seen, hits, updated_at) VALUES ('p-r', '420', 0, 3, 3, 0)")
    otp_details.live_counts(db)
    assert otp_details.classify_ex(db, "p-r", AATROX) == ("otp", True)
    assert otp_details.save(db, svc, make_match("EUW1_9100", puuids={1: "p-r"}), None, "p-r", {"tier": "GOLD", "division": "I", "lp": 1})
    assert otp_details.live_counts(db)["tiers_otp"]["GOLD"] == 1
    assert otp_details.counts(db)["tiers_otp"]["GOLD"] == 1  # el recuento no la cuenta como sin clasificar
    # Guardada antes como «resto»: al abrir la app pasa a OTP.
    with db.write() as conn:
        conn.execute("UPDATE otp_games SET kind = 'player' WHERE puuid = 'p-r'")
    assert ("p-r", AATROX) in otp_details.reclassify(db)
    assert db.scalar("SELECT kind FROM otp_games WHERE puuid = 'p-r'") == "otp"


def test_rank_numbers_average_into_an_elo():
    assert rank_value("IRON", "IV") == 0 and rank_value("PLATINUM", "II") == 18 and rank_value("MASTER", "I") == 28
    assert rank_value(None, None) is None and rank_value("UNRANKED", "I") is None
    assert rank_of_value((rank_value("GOLD", "I") + rank_value("PLATINUM", "III")) / 2) == {"tier": "PLATINUM", "division": "IV", "lp": None}
    assert rank_of_value(36.0)["tier"] == "CHALLENGER" and rank_of_value(99)["tier"] == "CHALLENGER"


def test_a_player_without_elo_gets_the_average_elo_of_the_game(keyed_svc):
    """Sin elo propio (sin clasificar en SoloQ), la partida se guarda con la media del elo de los demás: el que ya se
    sabe y, si no, su liga (y se recuerda para la siguiente)."""
    db = keyed_svc.db
    cfg = _ratio()
    worker = RegionWorker(keyed_svc.crawler, "europe", ["euw1"], cfg)
    match = make_match("EUW1_9200", puuids={1: "me", 2: "known", 3: "asked", 4: "none"})
    with db.write() as conn:
        for puuid, tier, division in (("me", None, None), ("known", "GOLD", "II"), ("asked", None, None), ("none", None, None)):
            conn.execute("INSERT INTO players (puuid, platform, tier, division) VALUES (?, 'euw1', ?, ?)", (puuid, tier, division))
    asked = []

    def entries(platform, puuid, **kw):
        asked.append(puuid)
        return [{"queueType": "RANKED_SOLO_5x5", "tier": "PLATINUM", "rank": "II", "leaguePoints": 50}] if puuid == "asked" else []

    keyed_svc.riot.league_entries_by_puuid = entries
    others = {p["puuid"] for p in match["info"]["participants"]} - {"me", "known", "asked", "none"}
    elo = worker._game_elo(match, "me")
    assert elo["tier"] == "PLATINUM" and elo["division"] == "IV"  # media de Oro II y Platino II
    assert "known" not in asked and {"asked", "none"} <= set(asked) and others <= set(asked)
    assert db.scalar("SELECT tier FROM players WHERE puuid = 'asked'") == "PLATINUM"
    asked.clear()
    worker._game_elo(match, "me")
    assert "asked" not in asked and "none" not in asked  # recordado: ni el clasificado ni el sin clasificar
