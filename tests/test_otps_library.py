"""Biblioteca de OTPs analizados (#/otps): ranking de campeones, reparto, lista filtrable y detalle."""

from __future__ import annotations

from types import SimpleNamespace

from app.ingest import otp_details
from tests.factories import AATROX, make_match
from tests.test_crawler_otp import _client

LEE = 64  # el jungla azul por defecto en make_match


def _add(svc, puuid, *, pid, n, tier, platform="euw1", lp=100, name=None, win_every=2, start=1_795_000_000_000):
    plain = SimpleNamespace(db=svc.db, dd=svc.dd, settings=svc.settings)
    with svc.db.write() as conn:
        conn.execute(
            "INSERT OR IGNORE INTO players (puuid, platform, tier, is_seed, lp, game_name, tag_line) VALUES (?, ?, ?, 1, ?, ?, 'X')",
            (puuid, platform, tier, lp, name or puuid),
        )
    for i in range(n):
        match = make_match(f"{platform.upper()}_{abs(hash(puuid)) % 10**6}{i:03d}", puuids={pid: puuid}, blue_win=i % win_every == 0, game_start=start + i * 3_600_000)
        assert otp_details.save(svc.db, plain, match, None, puuid, {"tier": tier, "division": "I", "lp": lp}, write_files=False)


def test_library_ranks_champions_by_otps_then_elo(keyed_svc):
    _add(keyed_svc, "a1", pid=1, n=3, tier="EMERALD")  # Aatrox top
    _add(keyed_svc, "a2", pid=1, n=2, tier="PLATINUM", platform="kr")
    _add(keyed_svc, "l1", pid=2, n=4, tier="CHALLENGER", lp=1200)  # Lee Sin jungla
    _add(keyed_svc, "l2", pid=2, n=1, tier="MASTER")
    _add(keyed_svc, "x1", pid=1, n=1, tier="DIAMOND")  # otro Aatrox
    with _client(keyed_svc) as client:
        lib = client.get("/api/otps/library").json()
    assert lib["totals"] == {"otps": 5, "players": 5, "games": 11, "champions": 2, "platforms": 2}
    assert [c["champ"] for c in lib["champions"]] == [AATROX, LEE] and lib["champions"][0]["otps"] == 3
    assert lib["champions"][1]["best_tier"] == "CHALLENGER" and next(iter(lib["tiers"])) == "CHALLENGER"
    assert lib["platforms"]["euw1"]["otps"] == 4 and lib["roles"]["TOP"]["games"] == 6


def test_tie_in_otps_goes_to_the_higher_elo(keyed_svc):
    _add(keyed_svc, "a1", pid=1, n=5, tier="EMERALD")
    _add(keyed_svc, "l1", pid=2, n=1, tier="GRANDMASTER")
    with _client(keyed_svc) as client:
        lib = client.get("/api/otps/library").json()
    assert [c["champ"] for c in lib["champions"]] == [LEE, AATROX]  # 1 OTP cada uno: gana el de más elo


def test_players_list_filters_sorts_and_pages(keyed_svc):
    _add(keyed_svc, "a1", pid=1, n=3, tier="EMERALD", name="Alpha")
    _add(keyed_svc, "a2", pid=1, n=5, tier="PLATINUM", platform="kr", name="Bravo")
    _add(keyed_svc, "l1", pid=2, n=2, tier="CHALLENGER", name="Charlie")
    with _client(keyed_svc) as client:
        everyone = client.get("/api/otps/players").json()
        aatrox = client.get("/api/otps/players", params={"champ": AATROX, "sort": "elo"}).json()
        kr = client.get("/api/otps/players", params={"platform": "kr"}).json()
        jungle = client.get("/api/otps/players", params={"role": "JUNGLE"}).json()
        q = client.get("/api/otps/players", params={"q": "alp"}).json()
        page = client.get("/api/otps/players", params={"limit": 1, "offset": 1}).json()
    assert [p["game_name"] for p in everyone["players"]] == ["Bravo", "Alpha", "Charlie"]  # más partidas primero
    assert [p["game_name"] for p in aatrox["players"]] == ["Alpha", "Bravo"]  # Esmeralda antes que Platino
    assert [p["game_name"] for p in kr["players"]] == ["Bravo"] and [p["game_name"] for p in jungle["players"]] == ["Charlie"]
    assert [p["game_name"] for p in q["players"]] == ["Alpha"]
    assert page["matched"] == 3 and [p["game_name"] for p in page["players"]] == ["Alpha"]


def test_player_detail(keyed_svc):
    _add(keyed_svc, "l1", pid=2, n=4, tier="CHALLENGER", name="Charlie")
    with _client(keyed_svc) as client:
        d = client.get("/api/otps/players/l1", params={"champ": LEE, "role": "JUNGLE"}).json()
        assert client.get("/api/otps/players/nadie", params={"champ": LEE}).status_code == 404
    assert d["games_count"] == 4 and d["wins"] == 2 and d["tier"] == "CHALLENGER" and d["player"]["game_name"] == "Charlie"
    assert d["metrics"]["deaths"]["mine"] >= 0 and len(d["games"]) == 4
