"""Importa el histórico ``matchups_stats.json`` del proyecto antiguo.

Ese fichero solo tenía victorias/derrotas por enfrentamiento y elo (sin
parche, región ni métricas de línea), así que se importa a los agregados con
``patch = platform = 'legacy'``. Detalles:

* Las claves eran no dirigidas (``a_vs_b`` con a < b); aquí se guardan en
  ambos sentidos para que las consultas sean directas.
* Los *mirrors* (``ahri_vs_ahri``) cuentan 2 partidas por enfrentamiento y
  exactamente un 50 % de victorias.
* Los IDs de partidas procesadas se guardan para que el recolector nuevo no
  vuelva a contarlas (evita duplicar datos entre histórico y datos nuevos).
* Aviso: el código antiguo no filtraba la cola, así que ~7 % de ese
  histórico procede de partidas normales. Se puede excluir con el filtro de
  parche "Solo datos nuevos".
"""

from __future__ import annotations

import json
import time
from collections import Counter, defaultdict
from pathlib import Path

from app.data.db import Database
from app.ddragon import DDragon
from app.logbus import get_logger
from app.riot.routing import ROLES, normalize_tier

log = get_logger("legacy")
LEGACY = "legacy"

ROLE_ALIASES = {"MIDDLE": "MID", "BOTTOM": "ADC", "BOT": "ADC", "UTILITY": "SUPPORT", "SUP": "SUPPORT"}


def legacy_status(db: Database) -> dict:
    raw = db.meta_get("legacy_import")
    return json.loads(raw) if raw else {"status": "pending"}


def import_legacy(db: Database, dd: DDragon, path: Path, force: bool = False) -> dict:
    current = legacy_status(db)
    if current.get("status") == "done" and not force:
        return current
    if not path.exists():
        report = {"status": "missing", "path": str(path)}
        db.meta_set("legacy_import", json.dumps(report))
        return report
    if not dd.champions:
        return {"status": "waiting_ddragon"}

    started = time.time()
    log.info("Importando histórico %s (puede tardar unos segundos)…", path.name)
    with path.open("r", encoding="utf-8") as fh:
        data = json.load(fh)

    directed: dict[tuple, list[int]] = defaultdict(lambda: [0, 0])
    unmapped: Counter = Counter()
    for role_raw, matchups in (data.get("stats") or {}).items():
        role = ROLE_ALIASES.get(str(role_raw).upper(), str(role_raw).upper())
        if role not in ROLES or not isinstance(matchups, dict):
            continue
        for key, value in matchups.items():
            if "_vs_" not in key or not isinstance(value, dict):
                continue
            name_a, name_b = key.split("_vs_", 1)
            a, b = dd.resolve(name_a), dd.resolve(name_b)
            if not a or not b:
                unmapped[name_a if not a else name_b] += int(value.get("wins", 0)) + int(value.get("losses", 0))
                continue
            elos = value.get("elos") or {"UNRANKED": {"wins": value.get("wins", 0), "losses": value.get("losses", 0)}}
            for tier_raw, ev in elos.items():
                wins, losses = int(ev.get("wins", 0)), int(ev.get("losses", 0))
                games = wins + losses
                if games <= 0:
                    continue
                tier = normalize_tier(tier_raw)
                if a == b:
                    row = directed[(role, a, a, tier)]
                    row[0] += 2 * games
                    row[1] += games
                else:
                    row = directed[(role, a, b, tier)]
                    row[0] += games
                    row[1] += wins
                    row = directed[(role, b, a, tier)]
                    row[0] += games
                    row[1] += losses

    champ_totals: dict[tuple, list[int]] = defaultdict(lambda: [0, 0])
    role_tier_games: dict[tuple, int] = defaultdict(int)
    for (role, champ, _opp, tier), (games, wins) in directed.items():
        total = champ_totals[(role, champ, tier)]
        total[0] += games
        total[1] += wins
        role_tier_games[(role, tier)] += games

    # Cada partida aporta 2 filas dirigidas por rol -> partidas = juegos/2 (media de roles).
    tier_matches: dict[str, float] = defaultdict(float)
    for (_role, tier), games in role_tier_games.items():
        tier_matches[tier] += games / 2 / len(ROLES)

    processed = data.get("processed_matches") or []
    match_ids = []
    for item in processed:
        mid = item.get("id") if isinstance(item, dict) else item
        if isinstance(mid, str) and "_" in mid:
            match_ids.append((mid,))

    with db.write() as conn:
        for table in ("agg_matchup", "agg_champ", "agg_totals"):
            conn.execute(f"DELETE FROM {table} WHERE patch = ?", (LEGACY,))
        conn.executemany(
            "INSERT INTO agg_matchup (role, champ, opp, patch, tier, platform, games, wins) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            [(r, c, o, LEGACY, t, LEGACY, g, w) for (r, c, o, t), (g, w) in directed.items()],
        )
        conn.executemany(
            "INSERT INTO agg_champ (role, champ, patch, tier, platform, games, wins) VALUES (?, ?, ?, ?, ?, ?, ?)",
            [(r, c, LEGACY, t, LEGACY, g, w) for (r, c, t), (g, w) in champ_totals.items()],
        )
        conn.executemany(
            "INSERT INTO agg_totals (patch, tier, platform, matches) VALUES (?, ?, ?, ?)",
            [(LEGACY, t, LEGACY, int(round(m))) for t, m in tier_matches.items() if m >= 1],
        )
        conn.executemany("INSERT OR IGNORE INTO legacy_matches(match_id) VALUES (?)", match_ids)

    report = {
        "status": "done",
        "imported_at": int(time.time()),
        "matches": int(round(sum(tier_matches.values()))),
        "processed_ids": len(match_ids),
        "matchup_rows": len(directed),
        "unmapped": dict(unmapped.most_common(20)),
        "seconds": round(time.time() - started, 1),
    }
    db.meta_set("legacy_import", json.dumps(report))
    log.info(
        "Histórico importado: ~%s partidas, %s filas de matchups en %.1fs.",
        f"{report['matches']:,}".replace(",", "."),
        f"{len(directed):,}".replace(",", "."),
        report["seconds"],
    )
    return report
