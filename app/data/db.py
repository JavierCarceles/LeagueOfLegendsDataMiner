"""Base de datos SQLite (modo WAL) y esquema.

Sustituye al ``matchups_stats.json`` de ~90 MB que se cargaba entero en cada
petición. Hay dos niveles:

* **Datos crudos**: ``matches`` y ``participants`` (una fila por jugador y
  partida) para perfiles, análisis de partidas y backtesting del modelo.
* **Agregados** (``agg_*``): contadores incrementales por
  rol/campeón/rival/parche/tier/plataforma. Las consultas de la web solo
  suman filas de estas tablas, así que responden en milisegundos.

El histórico importado del JSON antiguo vive en los agregados con
``patch = 'legacy'`` y ``platform = 'legacy'`` (no tenía parche ni región).
"""

from __future__ import annotations

import sqlite3
import threading
import time
from contextlib import contextmanager
from pathlib import Path

SCHEMA_VERSION = 1

SCHEMA = """
CREATE TABLE IF NOT EXISTS meta (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS players (
    id                 INTEGER PRIMARY KEY,
    puuid              TEXT NOT NULL UNIQUE,
    game_name          TEXT,
    tag_line           TEXT,
    platform           TEXT,
    tier               TEXT,
    division           TEXT,
    lp                 INTEGER,
    ranked_wins        INTEGER,
    ranked_losses      INTEGER,
    rank_checked_at    INTEGER,
    profile_icon       INTEGER,
    summoner_level     INTEGER,
    profile_checked_at INTEGER,
    is_seed            INTEGER NOT NULL DEFAULT 0,
    last_crawled_at    INTEGER,
    newest_match_at    INTEGER,
    updated_at         INTEGER
);
CREATE INDEX IF NOT EXISTS idx_players_seed ON players(is_seed, platform, tier, last_crawled_at);
CREATE INDEX IF NOT EXISTS idx_players_name ON players(game_name COLLATE NOCASE, tag_line COLLATE NOCASE);

CREATE TABLE IF NOT EXISTS seed_pages (
    platform     TEXT NOT NULL,
    queue        INTEGER NOT NULL,
    tier         TEXT NOT NULL,
    division     TEXT NOT NULL,
    next_page    INTEGER NOT NULL DEFAULT 1,
    exhausted_at INTEGER,
    refreshed_at INTEGER,
    PRIMARY KEY (platform, queue, tier, division)
) WITHOUT ROWID;

CREATE TABLE IF NOT EXISTS matches (
    match_id     TEXT PRIMARY KEY,
    platform     TEXT NOT NULL,
    region       TEXT NOT NULL,
    queue        INTEGER NOT NULL,
    map_id       INTEGER,
    patch        TEXT NOT NULL,
    game_start   INTEGER NOT NULL,
    duration     INTEGER NOT NULL,
    tier         TEXT NOT NULL,
    winner       INTEGER,
    is_remake    INTEGER NOT NULL DEFAULT 0,
    surrender    INTEGER NOT NULL DEFAULT 0,
    full_roles   INTEGER NOT NULL DEFAULT 0,
    aggregated   INTEGER NOT NULL DEFAULT 0,
    first_blood  INTEGER,
    first_tower  INTEGER,
    first_dragon INTEGER,
    first_herald INTEGER,
    first_grubs  INTEGER,
    first_baron  INTEGER,
    first_inhib  INTEGER,
    blue_kills   INTEGER, red_kills   INTEGER,
    blue_gold    INTEGER, red_gold    INTEGER,
    blue_towers  INTEGER, red_towers  INTEGER,
    blue_dragons INTEGER, red_dragons INTEGER,
    blue_barons  INTEGER, red_barons  INTEGER,
    blue_grubs   INTEGER, red_grubs   INTEGER,
    blue_heralds INTEGER, red_heralds INTEGER,
    ingested_at  INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_matches_filter ON matches(aggregated, patch, platform, tier);
CREATE INDEX IF NOT EXISTS idx_matches_start ON matches(game_start);

CREATE TABLE IF NOT EXISTS participants (
    match_id       TEXT NOT NULL,
    pid            INTEGER NOT NULL,
    player_id      INTEGER,
    team           INTEGER NOT NULL,
    role           TEXT,
    champ          INTEGER NOT NULL,
    win            INTEGER NOT NULL,
    kills          INTEGER, deaths INTEGER, assists INTEGER,
    level          INTEGER,
    cs             INTEGER,
    cs10           REAL,
    gold           INTEGER,
    dmg            INTEGER, dmg_phys INTEGER, dmg_magic INTEGER, dmg_true INTEGER,
    taken          INTEGER, mitigated INTEGER,
    cc             REAL,
    vision         INTEGER, wards INTEGER, wards_killed INTEGER, control_wards INTEGER,
    kp             REAL,
    dmg_share      REAL,
    solo_kills     INTEGER,
    plates         INTEGER,
    lane_adv       INTEGER,
    early_lane_adv INTEGER,
    max_cs_adv     REAL,
    first_blood    INTEGER,
    items          TEXT,
    spells         TEXT,
    runes          TEXT,
    PRIMARY KEY (match_id, pid)
);
CREATE INDEX IF NOT EXISTS idx_participants_player ON participants(player_id);

CREATE TABLE IF NOT EXISTS legacy_matches (match_id TEXT PRIMARY KEY) WITHOUT ROWID;

CREATE TABLE IF NOT EXISTS skipped_matches (
    match_id TEXT PRIMARY KEY,
    reason   TEXT,
    at       INTEGER
) WITHOUT ROWID;

CREATE TABLE IF NOT EXISTS timelines (
    match_id   TEXT PRIMARY KEY,
    data       TEXT NOT NULL,
    fetched_at INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS agg_champ (
    role TEXT NOT NULL, champ INTEGER NOT NULL, patch TEXT NOT NULL, tier TEXT NOT NULL, platform TEXT NOT NULL,
    games INTEGER NOT NULL DEFAULT 0, wins INTEGER NOT NULL DEFAULT 0, dgames INTEGER NOT NULL DEFAULT 0,
    kills INTEGER NOT NULL DEFAULT 0, deaths INTEGER NOT NULL DEFAULT 0, assists INTEGER NOT NULL DEFAULT 0,
    cs INTEGER NOT NULL DEFAULT 0, gold INTEGER NOT NULL DEFAULT 0, minutes REAL NOT NULL DEFAULT 0,
    dmg INTEGER NOT NULL DEFAULT 0, dmg_phys INTEGER NOT NULL DEFAULT 0, dmg_magic INTEGER NOT NULL DEFAULT 0,
    dmg_true INTEGER NOT NULL DEFAULT 0, taken INTEGER NOT NULL DEFAULT 0, mitigated INTEGER NOT NULL DEFAULT 0,
    cc REAL NOT NULL DEFAULT 0, vision INTEGER NOT NULL DEFAULT 0,
    cs10_sum REAL NOT NULL DEFAULT 0, cs10_n INTEGER NOT NULL DEFAULT 0,
    kp_sum REAL NOT NULL DEFAULT 0, kp_n INTEGER NOT NULL DEFAULT 0,
    dshare_sum REAL NOT NULL DEFAULT 0, dshare_n INTEGER NOT NULL DEFAULT 0,
    lane_w INTEGER NOT NULL DEFAULT 0, lane_l INTEGER NOT NULL DEFAULT 0, lane_n INTEGER NOT NULL DEFAULT 0,
    solo_kills INTEGER NOT NULL DEFAULT 0,
    g0 INTEGER NOT NULL DEFAULT 0, w0 INTEGER NOT NULL DEFAULT 0,
    g1 INTEGER NOT NULL DEFAULT 0, w1 INTEGER NOT NULL DEFAULT 0,
    g2 INTEGER NOT NULL DEFAULT 0, w2 INTEGER NOT NULL DEFAULT 0,
    g3 INTEGER NOT NULL DEFAULT 0, w3 INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (role, champ, patch, tier, platform)
) WITHOUT ROWID;

CREATE TABLE IF NOT EXISTS agg_matchup (
    role TEXT NOT NULL, champ INTEGER NOT NULL, opp INTEGER NOT NULL,
    patch TEXT NOT NULL, tier TEXT NOT NULL, platform TEXT NOT NULL,
    games INTEGER NOT NULL DEFAULT 0, wins INTEGER NOT NULL DEFAULT 0, dgames INTEGER NOT NULL DEFAULT 0,
    cs10_diff REAL NOT NULL DEFAULT 0, cs10_n INTEGER NOT NULL DEFAULT 0,
    gpm_diff REAL NOT NULL DEFAULT 0, dpm_diff REAL NOT NULL DEFAULT 0,
    lane_w INTEGER NOT NULL DEFAULT 0, lane_l INTEGER NOT NULL DEFAULT 0, lane_n INTEGER NOT NULL DEFAULT 0,
    solo_k INTEGER NOT NULL DEFAULT 0, solo_d INTEGER NOT NULL DEFAULT 0,
    plates_for INTEGER NOT NULL DEFAULT 0, plates_against INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (role, champ, opp, patch, tier, platform)
) WITHOUT ROWID;
CREATE INDEX IF NOT EXISTS idx_matchup_opp ON agg_matchup(role, opp);

CREATE TABLE IF NOT EXISTS agg_synergy (
    role1 TEXT NOT NULL, champ1 INTEGER NOT NULL, role2 TEXT NOT NULL, champ2 INTEGER NOT NULL,
    patch TEXT NOT NULL, tier TEXT NOT NULL, platform TEXT NOT NULL,
    games INTEGER NOT NULL DEFAULT 0, wins INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (role1, champ1, role2, champ2, patch, tier, platform)
) WITHOUT ROWID;
CREATE INDEX IF NOT EXISTS idx_synergy_2 ON agg_synergy(role2, champ2);

CREATE TABLE IF NOT EXISTS agg_bans (
    champ INTEGER NOT NULL, patch TEXT NOT NULL, tier TEXT NOT NULL, platform TEXT NOT NULL,
    bans INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (champ, patch, tier, platform)
) WITHOUT ROWID;

CREATE TABLE IF NOT EXISTS ext_cache (
    source     TEXT NOT NULL,
    key        TEXT NOT NULL,
    fetched_at INTEGER NOT NULL,
    payload    TEXT NOT NULL,
    PRIMARY KEY (source, key)
) WITHOUT ROWID;

CREATE TABLE IF NOT EXISTS agg_totals (
    patch TEXT NOT NULL, tier TEXT NOT NULL, platform TEXT NOT NULL,
    matches INTEGER NOT NULL DEFAULT 0,
    dmatches INTEGER NOT NULL DEFAULT 0,
    blue_wins INTEGER NOT NULL DEFAULT 0,
    minutes REAL NOT NULL DEFAULT 0,
    PRIMARY KEY (patch, tier, platform)
) WITHOUT ROWID;
"""


class Database:
    """Conexiones por hilo + un candado global de escritura."""

    def __init__(self, path: Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._local = threading.local()
        self.write_lock = threading.RLock()
        self._init_schema()

    def conn(self) -> sqlite3.Connection:
        conn = getattr(self._local, "conn", None)
        if conn is None:
            conn = sqlite3.connect(self.path, timeout=30, check_same_thread=False, isolation_level=None)
            conn.row_factory = sqlite3.Row
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA synchronous=NORMAL")
            conn.execute("PRAGMA busy_timeout=30000")
            conn.execute("PRAGMA cache_size=-65536")
            conn.execute("PRAGMA temp_store=MEMORY")
            self._local.conn = conn
        return conn

    def _init_schema(self) -> None:
        with self.write_lock:
            conn = self.conn()
            conn.executescript(SCHEMA)
            conn.execute(
                "INSERT INTO meta(key, value) VALUES('schema_version', ?) ON CONFLICT(key) DO NOTHING",
                (str(SCHEMA_VERSION),),
            )

    @contextmanager
    def write(self):
        """Transacción de escritura serializada (BEGIN IMMEDIATE)."""
        with self.write_lock:
            conn = self.conn()
            conn.execute("BEGIN IMMEDIATE")
            try:
                yield conn
            except BaseException:
                conn.execute("ROLLBACK")
                raise
            else:
                conn.execute("COMMIT")

    def query(self, sql: str, params: tuple | list = ()) -> list[sqlite3.Row]:
        return self.conn().execute(sql, params).fetchall()

    def one(self, sql: str, params: tuple | list = ()) -> sqlite3.Row | None:
        return self.conn().execute(sql, params).fetchone()

    def scalar(self, sql: str, params: tuple | list = (), default=None):
        row = self.conn().execute(sql, params).fetchone()
        return row[0] if row and row[0] is not None else default

    def meta_get(self, key: str, default: str | None = None) -> str | None:
        row = self.one("SELECT value FROM meta WHERE key = ?", (key,))
        return row["value"] if row else default

    def meta_set(self, key: str, value: str) -> None:
        with self.write() as conn:
            conn.execute(
                "INSERT INTO meta(key, value) VALUES(?, ?) ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                (key, value),
            )

    def close(self) -> None:
        conn = getattr(self._local, "conn", None)
        if conn is not None:
            conn.close()
            self._local.conn = None


def now_s() -> int:
    return int(time.time())
