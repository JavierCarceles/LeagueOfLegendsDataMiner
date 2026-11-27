"""Empezar de cero: se borran las partidas recogidas una sola vez y lo tuyo se queda."""

from __future__ import annotations

from app import reinicio


def test_borra_lo_recogido_una_vez_y_deja_lo_tuyo(keyed_svc):
    svc = keyed_svc
    db = svc.db
    data = svc.settings.data_dir
    for name in reinicio.DIRS:
        (data / name / "x").mkdir(parents=True, exist_ok=True)
        (data / name / "x" / "f.json").write_text("{}", encoding="utf-8")
    (data / "mis_partidas").mkdir(parents=True, exist_ok=True)
    (data / "mis_partidas" / "mia.json").write_text("{}", encoding="utf-8")
    with db.write() as conn:
        conn.execute("INSERT INTO ranking_games (match_id, version, blob, fetched_at) VALUES ('EUW1_1', 1, x'00', 0)")
        conn.execute("INSERT INTO my_games (match_id, puuid, status, fetched_at) VALUES ('EUW1_2', 'yo', 'ready', 0)")
    db.meta_set("otp.reanalysis", "{}")

    assert not reinicio.maybe_reset(svc, "")  # sin identificador: no borra nada
    assert db.scalar("SELECT COUNT(*) FROM ranking_games") == 1

    assert reinicio.maybe_reset(svc, "prueba")
    assert db.scalar("SELECT COUNT(*) FROM ranking_games") == 0
    assert db.scalar("SELECT COUNT(*) FROM my_games") == 1
    assert not any((data / name).exists() for name in reinicio.DIRS)
    assert (data / "mis_partidas" / "mia.json").exists()
    assert db.meta_get("otp.reanalysis") is None
    assert not reinicio.maybe_reset(svc, "prueba")  # una sola vez
