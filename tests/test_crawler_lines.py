"""Recolector: OTPs de toda una línea, sin tope de campeones, prioridad de variedad y partidas de un campeón."""

from __future__ import annotations

import json
from types import SimpleNamespace
from typing import ClassVar

from app.ingest import crawler as crawler_mod
from app.ingest.crawler import CrawlerConfig, RegionWorker, otp_list_key, role_pool, target_sql
from tests.factories import AATROX, AHRI, DARIUS, GAREN, LEE_SIN, VIEGO


def _now():
    from app.data.db import now_s

    return now_s()


def test_saved_config_always_resumes_with_the_minute_by_minute_analysis():
    old = CrawlerConfig.from_saved({"champions": [AATROX], "save_details": False, "days": 14, "active_days": 90})
    assert old.save_details and old.champions == [AATROX]


def test_no_limit_of_ten_champions():
    cfg = CrawlerConfig(champions=list(range(1, 40))).validated()
    assert len(cfg.champions) == 39 and len(cfg.targets) == 39


def test_whole_line_counts_only_games_in_that_line():
    cfg = CrawlerConfig(roles=["top", "TOP", "XX"], role_champions=[AATROX, DARIUS]).validated()
    assert cfg.roles == ["TOP"] and cfg.targets == [AATROX, DARIUS]
    assert cfg.is_target(DARIUS, "TOP") and not cfg.is_target(DARIUS, "JUNGLE") and not cfg.is_target(GAREN, "TOP")
    sql, params = target_sql(cfg)
    assert sql == "((champ IN (?,?) AND role IN (?)))" and params == [AATROX, DARIUS, "TOP"]
    # La clave del historial depende de la línea, no de su lista de campeones (que cambia un poco cada parche).
    other = CrawlerConfig(roles=["TOP"], role_champions=[AATROX, DARIUS, GAREN]).validated()
    assert otp_list_key(cfg) == otp_list_key(other) and "|l:TOP" in otp_list_key(cfg)
    # Sin líneas, la lista de campeones de la línea no se guarda.
    assert CrawlerConfig(role_champions=[AATROX]).validated().role_champions == []


def test_picked_champion_and_line_roles_are_combined():
    cfg = CrawlerConfig(champions=[LEE_SIN, AATROX], champion_roles={LEE_SIN: ["JUNGLE"]}, roles=["TOP"], role_champions=[AATROX, DARIUS]).validated()
    assert cfg.roles_for(LEE_SIN) == ["JUNGLE"] and cfg.roles_for(AATROX) is None and cfg.roles_for(DARIUS) == ["TOP"]
    assert cfg.targets == [LEE_SIN, AATROX, DARIUS]


def test_role_pool_comes_from_the_tier_list_and_falls_back_to_your_games(keyed_svc, monkeypatch):
    from app.analytics import evidence

    scope = SimpleNamespace(tier="emerald_plus", region="global", patches=("16.23",))
    monkeypatch.setattr(evidence, "ext_scope", lambda svc, f: scope)
    tiers = {"TOP": {AATROX: {"role_rate": 0.9, "pick": 0.05}, DARIUS: {"role_rate": 0.8, "pick": 0.09}, AHRI: {"role_rate": 0.02, "pick": 0.1}}}
    keyed_svc.opgg = SimpleNamespace(enabled=True, tier_list=lambda role, tier, region, patch: tiers.get(role))
    keyed_svc.lolalytics = None
    assert role_pool(keyed_svc, ["TOP"]) == [DARIUS, AATROX]  # Ahri casi nunca va top; de más a menos jugados
    # Sin webs: tus partidas guardadas (≥ 20 partidas en esa línea y ≥ 8 % de las suyas).
    keyed_svc.opgg = None
    with keyed_svc.db.write() as conn:
        for i in range(25):
            conn.execute("INSERT INTO participants (match_id, pid, team, champ, role, win) VALUES (?, 1, 100, ?, 'TOP', 1)", (f"M{i}", GAREN))
            conn.execute("INSERT INTO participants (match_id, pid, team, champ, role, win) VALUES (?, 2, 100, ?, 'MID', 1)", (f"M{i}", AHRI))
    assert role_pool(keyed_svc, ["TOP"]) == [GAREN]


class _Worker:
    """Sustituye a ``RegionWorker`` para ver con qué configuración arrancaría (sin hilos)."""

    started: ClassVar[dict] = {}

    def __init__(self, crawler, region, platforms, config):
        _Worker.started["cfg"] = config
        self.state, self.scouts = {}, []

    def start(self):
        pass

    def is_alive(self):
        return False

    def join(self, timeout=None):
        pass

    def wake(self):
        pass


def test_start_resolves_the_line_into_champions(keyed_svc, monkeypatch):
    monkeypatch.setattr(crawler_mod, "role_pools", lambda svc, roles: {"TOP": [DARIUS, AATROX]})
    started = _Worker.started
    started.clear()
    monkeypatch.setattr(crawler_mod, "RegionWorker", _Worker)
    keyed_svc.crawler.start(CrawlerConfig(platforms=["euw1"], roles=["TOP"]))
    assert started["cfg"].role_champions == [DARIUS, AATROX] and started["cfg"].targets == [DARIUS, AATROX]
    # Sin red al reanudar: se usa la lista de la última vez; sin ninguna, se avisa.
    keyed_svc.crawler.workers = {}
    monkeypatch.setattr(crawler_mod, "role_pools", lambda svc, roles: {})
    started.clear()
    keyed_svc.crawler.start(CrawlerConfig(platforms=["euw1"], roles=["TOP"], role_champions=[GAREN]))
    assert started["cfg"].role_champions == [GAREN]
    keyed_svc.crawler.workers = {}
    try:
        keyed_svc.crawler.start(CrawlerConfig(platforms=["euw1"], roles=["TOP"]))
        raise AssertionError("debía fallar")
    except ValueError as exc:
        assert "línea" in str(exc)


def _otp(db, puuid, champ, tier, lp=100, visited=False, hits=0, cfg=None):
    now = _now()
    with db.write() as conn:
        conn.execute(
            "INSERT INTO players (puuid, platform, tier, is_seed, lp, last_crawled_at) VALUES (?, 'euw1', ?, 1, ?, ?)",
            (puuid, tier, lp, now if visited else None),
        )
        conn.execute("INSERT INTO mastery_checks VALUES (?, 'euw1', ?, ?)", (puuid, now, json.dumps([[champ, 900_000, now]])))
        if visited:
            conn.execute(
                "INSERT INTO otp_progress (puuid, list_key, anchor, back_offset, hits, seen, updated_at) VALUES (?, ?, ?, 0, ?, ?, ?)",
                (puuid, otp_list_key(cfg), now - 3600, hits, hits, now),
            )


def test_new_otps_of_champions_without_any_go_first(keyed_svc):
    """Variedad: primero un OTP de cada campeón (aunque haya otros de más elo), luego cada elo, luego la profundidad."""
    cfg = CrawlerConfig(platforms=["euw1"], tiers=["EMERALD", "DIAMOND", "MASTER"], champions=[AATROX, DARIUS], otp_depth="all").validated()
    db = keyed_svc.db
    _otp(db, "aat-1", AATROX, "MASTER", visited=True, hits=10, cfg=cfg)
    _otp(db, "aat-2", AATROX, "MASTER", lp=900)  # mismo campeón y elo, mucho LP
    _otp(db, "aat-3", AATROX, "EMERALD")  # mismo campeón, elo sin ninguno
    _otp(db, "dar-1", DARIUS, "EMERALD", lp=1)  # campeón sin ningún OTP todavía
    worker = RegionWorker(keyed_svc.crawler, "europe", ["euw1"], cfg)
    assert worker._next_otp()["puuid"] == "dar-1"
    _otp(db, "dar-x", DARIUS, "DIAMOND", visited=True, cfg=cfg)  # Darius ya tiene uno
    assert worker._next_otp()["puuid"] == "aat-3"  # Aatrox en Esmeralda: elo sin OTPs de ese campeón
    assert worker.found_by_tier[("euw1", "MASTER")] == 2


def test_depth_goes_to_the_champion_with_fewest_games(keyed_svc):
    cfg = CrawlerConfig(platforms=["euw1"], tiers=["MASTER"], champions=[AATROX, DARIUS], otp_depth="all", games_per_otp=0).validated()
    db = keyed_svc.db
    _otp(db, "aat-1", AATROX, "MASTER", lp=900, visited=True, hits=80, cfg=cfg)
    _otp(db, "dar-1", DARIUS, "MASTER", lp=1, visited=True, hits=40, cfg=cfg)
    _otp(db, "dar-2", DARIUS, "MASTER", lp=2, visited=True, hits=50, cfg=cfg)
    worker = RegionWorker(keyed_svc.crawler, "europe", ["euw1"], cfg)
    assert worker._next_otp()["puuid"] == "aat-1"  # Aatrox suma 80 partidas; Darius, 90
    with db.write() as conn:
        conn.execute("UPDATE otp_progress SET hits = 200 WHERE puuid = 'aat-1'")
    assert worker._next_otp()["puuid"] == "dar-1"  # dentro de Darius, el que menos lleva


def test_champion_games_mode_keeps_any_game_with_the_champion_in_that_role():
    """«Partidas de un campeón»: vale cualquier partida en la que alguien lo juegue en esa línea, sea OTP o no."""
    cfg = CrawlerConfig(champions=[LEE_SIN], champion_roles={LEE_SIN: ["JUNGLE"]}, focus="games", min_mastery=20_000, only_target_games=False).validated()
    assert cfg.focus == "games" and cfg.only_target_games and cfg.save_details  # el análisis, siempre
    now = _now()
    assert cfg.qualifies([[AHRI, 900_000, now], [VIEGO, 500_000, now], [LEE_SIN, 30_000, now]], now)  # en su top 3
    db = SimpleNamespace(scalar=lambda *a: None, query=lambda *a: [])  # nadie es OTP
    worker = SimpleNamespace(config=cfg, svc=SimpleNamespace(db=db))

    def part(puuid, champ, role):
        return SimpleNamespace(puuid=puuid, champ=champ, role=role)

    record = SimpleNamespace(participants=[part("me", AHRI, "MID"), part("other", LEE_SIN, "JUNGLE")])
    assert RegionWorker._other_target(worker, record, "me")
    record = SimpleNamespace(participants=[part("me", AHRI, "MID"), part("other", LEE_SIN, "TOP")])
    assert not RegionWorker._other_target(worker, record, "me")  # Lee Sin, pero no en jungla
    otp = CrawlerConfig(champions=[LEE_SIN]).validated()
    worker.config = otp
    record = SimpleNamespace(participants=[part("me", AHRI, "MID"), part("other", LEE_SIN, "JUNGLE")])
    assert not RegionWorker._other_target(worker, record, "me")  # buscando OTPs, el otro tiene que ser OTP


def test_endpoint_accepts_lines_and_champion_games(keyed_svc, monkeypatch):
    from tests.test_crawler_otp import _client

    monkeypatch.setattr(crawler_mod, "role_pools", lambda svc, roles: {"TOP": [DARIUS, AATROX]})
    monkeypatch.setattr(crawler_mod, "RegionWorker", _Worker)
    with _client(keyed_svc) as client:
        body = {"platforms": ["euw1"], "tiers": ["EMERALD"], "roles": ["TOP"], "focus": "games", "otp_depth": "30", "save_details": False}
        res = client.post("/api/crawler/start", json=body)
        assert res.status_code == 200, res.text
        cfg = res.json()["config"]
        assert cfg["roles"] == ["TOP"] and cfg["role_champions"] == [DARIUS, AATROX] and cfg["focus"] == "games" and cfg["otp_depth"] == "30"
        assert "active_days" not in cfg and "days" not in cfg
        assert cfg["save_details"] is True  # el análisis minuto a minuto no se puede apagar desde fuera
        preview = client.get("/api/crawler/role-pool", params={"roles": "TOP"}).json()
        assert preview["champions"] == [DARIUS, AATROX]


def test_champion_games_mode_uses_the_same_mastery_minimum(keyed_svc):
    """«Cualquiera que lo juegue» también admite un mínimo de maestría (el mismo filtro que los especialistas); por
    defecto, el mínimo de siempre (tenerlo entre sus 3 más jugados)."""
    from app.ingest.crawler import GAMES_MIN_MASTERY, search_funnel

    assert CrawlerConfig(champions=[LEE_SIN], focus="games", min_mastery=5).validated().min_mastery == GAMES_MIN_MASTERY
    cfg = CrawlerConfig(platforms=["euw1"], tiers=["EMERALD"], champions=[LEE_SIN], focus="games", min_mastery=30_000).validated()
    assert cfg.min_mastery == 30_000 and cfg.min_share == 0
    now = _now()
    with keyed_svc.db.write() as conn:
        for puuid, top in (("a", [[AHRI, 900_000, now], [LEE_SIN, 40_000, now]]), ("b", [[AHRI, 900_000, now]]), ("c", [[LEE_SIN, 20_000, now]])):
            conn.execute("INSERT INTO players (puuid, platform, tier, is_seed, lp) VALUES (?, 'euw1', 'EMERALD', 1, 10)", (puuid,))
            conn.execute("INSERT INTO mastery_checks VALUES (?, 'euw1', ?, ?)", (puuid, now, json.dumps(top)))
    funnel = search_funnel(keyed_svc.db, cfg, now)
    assert funnel["stages"] == ["checked", "champ", "points", "active"]
    assert funnel["total"]["champ"] == 2 and funnel["total"]["active"] == 1


def test_each_champion_of_a_line_counts_only_in_its_own_line():
    """Top y mid enteros: Darius solo en top y Ahri solo en mid (antes, todos los de ambas listas en las dos líneas)."""
    cfg = CrawlerConfig(roles=["TOP", "MID"], role_champions=[DARIUS, AHRI, AATROX], role_pools={"TOP": [DARIUS, AATROX], "mid": [AHRI, AATROX]}).validated()
    assert cfg.roles_for(DARIUS) == ["TOP"] and cfg.roles_for(AHRI) == ["MID"] and cfg.roles_for(AATROX) == ["TOP", "MID"]
    assert cfg.is_target(DARIUS, "TOP") and not cfg.is_target(DARIUS, "MID")
    old = CrawlerConfig(roles=["TOP", "MID"], role_champions=[DARIUS, AHRI]).validated()  # guardada antes del cambio
    assert old.roles_for(AHRI) == ["TOP", "MID"]


def test_per_player_cap_counts_every_game_when_collecting_a_champions_games():
    """Con la jungla entera y «Cualquiera que lo juegue» casi toda partida se guarda: el tope «sus 30 partidas» cuenta
    todas las que se bajan (antes solo las suyas con el campeón: más de 100 descargas por jugador antes de pasar a otro)."""
    from app.ingest.crawler import cap_count

    games = CrawlerConfig(roles=["JUNGLE"], role_champions=[LEE_SIN], focus="games").validated()
    otp = CrawlerConfig(champions=[LEE_SIN], focus="otp").validated()
    assert cap_count(games, hits=8, seen=30) == 30 and cap_count(otp, hits=8, seen=30) == 8


def test_al_cambiar_la_busqueda_lo_recogido_sigue_contando_y_van_primero_los_elos_sin_partidas(keyed_svc):
    """Al añadir campeones o líneas, los jugadores ya visitados vuelven a la cola (su historial se recorre de nuevo),
    pero sus partidas siguen contando: un Oro recién encontrado va antes que un Diamante con 100 partidas."""
    old = CrawlerConfig(platforms=["euw1"], tiers=["GOLD", "DIAMOND"], champions=[AATROX], roles=["TOP"]).validated()
    new = CrawlerConfig(platforms=["euw1"], tiers=["GOLD", "DIAMOND"], champions=[AATROX, DARIUS], roles=["TOP"]).validated()
    assert otp_list_key(old) != otp_list_key(new)
    db = keyed_svc.db
    _otp(db, "dia-1", AATROX, "DIAMOND", lp=900, visited=True, hits=100, cfg=old)
    _otp(db, "dia-2", DARIUS, "DIAMOND", lp=800, visited=True, hits=100, cfg=old)
    _otp(db, "gold-1", DARIUS, "GOLD", lp=10)
    worker = RegionWorker(keyed_svc.crawler, "europe", ["euw1"], new)
    assert worker._next_otp()["puuid"] == "gold-1"
