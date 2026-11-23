"""Lecturas y escrituras de tu perfil, tus partidas y tus propósitos."""

from __future__ import annotations

import json
import zlib

from app.coach.benchmarks import DEFAULT_TARGET, METRICS, normalize_tier
from app.data.db import Database, now_s

DEFAULT_SETTINGS = {"enabled": True, "backfill": 50, "poll_minutes": 3, "scanned": 0, "known_puuids": []}
RANKED_QUEUES = (420, 440)
QUEUE_GROUPS = ("all", "normal", "ranked")


def pack(obj) -> bytes | None:
    if obj is None:
        return None
    return zlib.compress(json.dumps(obj, separators=(",", ":"), ensure_ascii=False).encode("utf-8"), 6)


def unpack(blob):
    if blob is None:
        return None
    return json.loads(zlib.decompress(blob).decode("utf-8"))


def usual_spells(db: Database, champ: int, limit: int = 20) -> list[int] | None:
    """Tus hechizos de siempre con ese campeón (en tu orden D, F), de tus últimas partidas con él."""
    rows = db.query("SELECT match_raw, pid FROM my_games WHERE champ = ? AND match_raw IS NOT NULL ORDER BY game_start DESC LIMIT ?", (champ, limit))
    pairs: dict[tuple[int, int], int] = {}
    for r in rows:
        try:
            match = unpack(r["match_raw"]) or {}
        except (ValueError, zlib.error):
            continue
        me = next((x for x in (match.get("info") or {}).get("participants") or [] if x.get("participantId") == r["pid"]), None)
        if me and me.get("summoner1Id") and me.get("summoner2Id"):
            key = (int(me["summoner1Id"]), int(me["summoner2Id"]))
            pairs[key] = pairs.get(key, 0) + 1
    if not pairs:
        return None
    return list(max(pairs.items(), key=lambda kv: kv[1])[0])


def _riot_id(name: str | None, tag: str | None) -> str:
    return f"{(name or '').strip().casefold()}#{(tag or '').strip().casefold()}"


# ------------------------------------------------------------------ perfil
def get_profile(db: Database) -> dict | None:
    row = db.one("SELECT * FROM my_profile WHERE id = 1")
    if not row:
        return None
    profile = dict(row)
    try:
        settings = json.loads(profile.get("settings") or "{}")
    except ValueError:
        settings = {}
    profile["settings"] = {**DEFAULT_SETTINGS, **settings}
    profile["target_tier"] = normalize_tier(profile.get("target_tier"))
    return profile


def known_puuids(profile: dict | None) -> list[str]:
    if not profile:
        return []
    items = list(profile["settings"].get("known_puuids") or [])
    if profile.get("puuid") and profile["puuid"] not in items:
        items.append(profile["puuid"])
    return items


def save_profile(
    db: Database,
    *,
    game_name: str,
    tag_line: str,
    platform: str,
    puuid: str | None = None,
    profile_icon: int | None = None,
    summoner_level: int | None = None,
    target_tier: str | None = None,
    settings: dict | None = None,
) -> dict:
    """Crea o sustituye el perfil. Si es otra cuenta, empieza un historial nuevo."""
    old = get_profile(db)
    same_account = bool(old) and (
        _riot_id(old["game_name"], old["tag_line"]) == _riot_id(game_name, tag_line) or (puuid and puuid in known_puuids(old))
    )
    merged = {**DEFAULT_SETTINGS, **(old["settings"] if same_account else {}), **(settings or {})}
    known = known_puuids(old) if same_account else []
    if puuid and puuid not in known:
        known.append(puuid)
    merged["known_puuids"] = known
    if not same_account:
        merged["scanned"] = 0
    ts = now_s()
    with db.write() as conn:
        conn.execute(
            "INSERT INTO my_profile (id, puuid, game_name, tag_line, platform, profile_icon, summoner_level, target_tier, settings, created_at, updated_at) "
            "VALUES (1, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?) ON CONFLICT(id) DO UPDATE SET "
            "puuid = excluded.puuid, game_name = excluded.game_name, tag_line = excluded.tag_line, platform = excluded.platform, "
            "profile_icon = COALESCE(excluded.profile_icon, my_profile.profile_icon), "
            "summoner_level = COALESCE(excluded.summoner_level, my_profile.summoner_level), "
            "target_tier = excluded.target_tier, settings = excluded.settings, updated_at = excluded.updated_at",
            (
                puuid,
                game_name.strip(),
                tag_line.strip(),
                platform,
                profile_icon,
                summoner_level,
                normalize_tier(target_tier or (old or {}).get("target_tier") or DEFAULT_TARGET),
                json.dumps(merged),
                (old or {}).get("created_at") or ts if same_account else ts,
                ts,
            ),
        )
    return get_profile(db)


def update_profile(db: Database, **fields) -> dict | None:
    profile = get_profile(db)
    if not profile:
        return None
    settings = dict(profile["settings"])
    if "settings" in fields:
        settings.update(fields.pop("settings") or {})
    if fields.get("puuid") and fields["puuid"] not in settings["known_puuids"]:
        settings["known_puuids"] = [*settings["known_puuids"], fields["puuid"]]
    if "target_tier" in fields:
        fields["target_tier"] = normalize_tier(fields["target_tier"])
    allowed = {"puuid", "game_name", "tag_line", "platform", "profile_icon", "summoner_level", "target_tier"}
    values = {k: v for k, v in fields.items() if k in allowed and v is not None}
    values["settings"] = json.dumps(settings)
    values["updated_at"] = now_s()
    with db.write() as conn:
        conn.execute(f"UPDATE my_profile SET {', '.join(f'{k} = ?' for k in values)} WHERE id = 1", list(values.values()))
    return get_profile(db)


def delete_profile(db: Database, purge: bool = False) -> None:
    with db.write() as conn:
        conn.execute("DELETE FROM my_profile WHERE id = 1")
        if purge:
            conn.execute("DELETE FROM my_games")
            conn.execute("DELETE FROM my_goals")
            conn.execute("DELETE FROM my_reviews")
            conn.execute("DELETE FROM peer_samples")


# ---------------------------------------------------------------- partidas
def game_states(db: Database, ids: list[str]) -> dict[str, dict]:
    if not ids:
        return {}
    marks = ",".join("?" * len(ids))
    rows = db.query(f"SELECT match_id, status, tries, match_raw IS NOT NULL AS has_match FROM my_games WHERE match_id IN ({marks})", ids)
    return {r["match_id"]: {"status": r["status"], "tries": r["tries"], "has_match": bool(r["has_match"])} for r in rows}


def save_game(db: Database, row: dict) -> None:
    row = {**row, "fetched_at": row.get("fetched_at") or now_s()}
    for key in ("match_raw", "timeline_raw", "analysis"):
        if key in row and not isinstance(row[key], (bytes, type(None))):
            row[key] = pack(row[key])
    if "digest" in row and not isinstance(row["digest"], (str, type(None))):
        row["digest"] = json.dumps(row["digest"], separators=(",", ":"), ensure_ascii=False)
    cols = list(row)
    updates = ", ".join(f"{c} = COALESCE(excluded.{c}, my_games.{c})" if c in ("match_raw", "timeline_raw") else f"{c} = excluded.{c}" for c in cols if c != "match_id")
    with db.write() as conn:
        conn.execute(
            f"INSERT INTO my_games ({', '.join(cols)}) VALUES ({', '.join('?' * len(cols))}) ON CONFLICT(match_id) DO UPDATE SET {updates}",
            [row[c] for c in cols],
        )


def update_analysis(db: Database, match_id: str, analysis: dict, digest: dict, version: int) -> None:
    """Sustituye el análisis de una partida ya guardada (sin tocar su estado).

    El rol también se actualiza con el del análisis: si ``teamPosition`` venía vacío o repetido, el análisis
    lo descarta (None) y las referencias por rol no deben usar uno adivinado.
    """
    with db.write() as conn:
        conn.execute(
            "UPDATE my_games SET analysis = ?, digest = ?, version = ?, analyzed_at = ?, role = ? WHERE match_id = ?",
            (pack(analysis), json.dumps(digest, separators=(",", ":"), ensure_ascii=False), version, now_s(), analysis.get("role"), match_id),
        )


def update_digest(db: Database, match_id: str, digest: dict) -> None:
    with db.write() as conn:
        conn.execute("UPDATE my_games SET digest = ? WHERE match_id = ?", (json.dumps(digest, separators=(",", ":"), ensure_ascii=False), match_id))


def raw_game(db: Database, match_id: str) -> tuple[dict | None, dict | None, int | None]:
    row = db.one("SELECT match_raw, timeline_raw, pid FROM my_games WHERE match_id = ?", (match_id,))
    if not row:
        return None, None, None
    return unpack(row["match_raw"]), unpack(row["timeline_raw"]), row["pid"]


def raw_timeline(db: Database, match_id: str) -> dict | None:
    row = db.one("SELECT timeline_raw FROM my_games WHERE match_id = ? AND timeline_raw IS NOT NULL", (match_id,))
    return unpack(row["timeline_raw"]) if row else None


def _filters(puuids: list[str], queue: str | None, role: str | None) -> tuple[str, list]:
    where = ["status IN ('ready', 'no_timeline', 'pending_timeline')", "digest IS NOT NULL"]
    params: list = []
    if puuids:
        where.append(f"puuid IN ({','.join('?' * len(puuids))})")
        params.extend(puuids)
    else:
        where.append("0")
    if queue == "ranked":
        where.append(f"queue IN ({','.join('?' * len(RANKED_QUEUES))})")
        params.extend(RANKED_QUEUES)
    elif queue == "normal":
        where.append(f"queue NOT IN ({','.join('?' * len(RANKED_QUEUES))})")
        params.extend(RANKED_QUEUES)
    if role and role.upper() != "ALL":
        where.append("role = ?")
        params.append(role.upper())
    return " AND ".join(where), params


def list_games(db: Database, puuids: list[str], *, queue: str | None = None, role: str | None = None, limit: int | None = None) -> list[dict]:
    """Partidas analizadas (más recientes primero) con su resumen ligero."""
    where, params = _filters(puuids, queue, role)
    sql = f"SELECT match_id, queue, game_start, duration, champ, role, team, win, status, digest FROM my_games WHERE {where} ORDER BY game_start DESC"
    if limit:
        sql += " LIMIT ?"
        params.append(int(limit))
    out = []
    for r in db.query(sql, params):
        try:
            digest = json.loads(r["digest"])
        except (TypeError, ValueError):
            continue
        out.append(
            {
                "match_id": r["match_id"],
                "queue": r["queue"],
                "game_start": r["game_start"],
                "duration": r["duration"],
                "champ": r["champ"],
                "role": r["role"],
                "team": r["team"],
                "win": bool(r["win"]),
                "status": r["status"],
                "metrics": digest.get("m") or {},
                "deaths": digest.get("deaths") or [],
                "jungle": digest.get("jungle"),
                "build": digest.get("build"),
                "opponent_champ": digest.get("opponent_champ"),
                "enemy_jungler_champ": digest.get("enemy_jungler_champ"),
                "has_timeline": digest.get("has_timeline", False),
                "issues": digest.get("issues") or [],
                "mistakes": digest.get("mk"),
                "checks": digest.get("cc"),
            }
        )
    return out


def get_game(db: Database, match_id: str) -> dict | None:
    row = db.one("SELECT match_id, status, analysis, version, analyzed_at FROM my_games WHERE match_id = ?", (match_id,))
    if not row or row["analysis"] is None:
        return None
    analysis = unpack(row["analysis"])
    analysis["status"] = row["status"]
    analysis["analyzed_at"] = row["analyzed_at"]
    return analysis


def outdated(db: Database, version: int, with_items: bool = True) -> list[str]:
    """Partidas analizadas con una versión anterior del análisis.

    Las que se analizaron sin catálogo de objetos guardan ``version - 1``: solo se repiten
    si ahora hay catálogo (``with_items``), para no re-analizarlas en vano en cada arranque.
    """
    rows = db.query(
        "SELECT match_id, version FROM my_games WHERE match_raw IS NOT NULL AND status IN ('ready', 'no_timeline', 'pending_timeline') "
        "AND (version IS NULL OR version < ?)",
        (version,),
    )
    return [r["match_id"] for r in rows if with_items or r["version"] is None or r["version"] < version - 1]


def counts(db: Database, puuids: list[str]) -> dict:
    if not puuids:
        return {"ready": 0, "pending": 0, "partial": 0, "ignored": 0}
    marks = ",".join("?" * len(puuids))
    rows = db.query(f"SELECT status, COUNT(*) AS n FROM my_games WHERE puuid IN ({marks}) GROUP BY status", puuids)
    by = {r["status"]: r["n"] for r in rows}
    return {
        "ready": by.get("ready", 0),
        "pending": by.get("pending_timeline", 0),
        "partial": by.get("no_timeline", 0),
        "ignored": by.get("ignored", 0) + by.get("error", 0),
    }


def last_game(db: Database, puuids: list[str]) -> dict | None:
    if not puuids:
        return None
    marks = ",".join("?" * len(puuids))
    row = db.one(
        f"SELECT match_id, game_start, champ, role, win, analyzed_at FROM my_games WHERE puuid IN ({marks}) "
        f"AND status IN ('ready', 'no_timeline', 'pending_timeline') ORDER BY game_start DESC LIMIT 1",
        puuids,
    )
    return dict(row) if row else None


def newest_start(db: Database, puuids: list[str]) -> int | None:
    """Inicio (ms) de tu partida guardada más reciente, de cualquier modo."""
    if not puuids:
        return None
    marks = ",".join("?" * len(puuids))
    return db.scalar(f"SELECT MAX(game_start) FROM my_games WHERE puuid IN ({marks}) AND game_start > 0", puuids)


def retry_candidates(db: Database, puuids: list[str], max_tries: int) -> list[str]:
    """Partidas a la espera del timeline (o con error) que aún se pueden reintentar."""
    if not puuids:
        return []
    marks = ",".join("?" * len(puuids))
    return [
        r[0]
        for r in db.query(
            f"SELECT match_id FROM my_games WHERE puuid IN ({marks}) AND ((status = 'pending_timeline' AND tries < ?) "
            f"OR (status = 'error' AND tries < 3)) ORDER BY game_start DESC LIMIT 50",
            [*puuids, max_tries],
        )
    ]


def champion_pool(db: Database, puuids: list[str], role: str | None = None) -> list[dict]:
    if not puuids:
        return []
    marks = ",".join("?" * len(puuids))
    params: list = list(puuids)
    sql = (
        f"SELECT champ, role, COUNT(*) AS games, SUM(win) AS wins FROM my_games WHERE puuid IN ({marks}) "
        f"AND status IN ('ready', 'no_timeline', 'pending_timeline') AND champ IS NOT NULL"
    )
    if role:
        sql += " AND role = ?"
        params.append(role)
    sql += " GROUP BY champ, role ORDER BY games DESC, wins DESC"
    return [
        {"champ": r["champ"], "role": r["role"], "games": r["games"], "wins": r["wins"] or 0, "wr": round((r["wins"] or 0) / r["games"] * 100, 1)}
        for r in db.query(sql, params)
    ]


# --------------------------------------------------------------- propósitos
def list_goals(db: Database, active_only: bool = False) -> list[dict]:
    sql = "SELECT * FROM my_goals" + (" WHERE active = 1" if active_only else "") + " ORDER BY created_at, id"
    return [{**dict(r), "active": bool(r["active"])} for r in db.query(sql)]


def add_goal(db: Database, *, metric: str, op: str, target: float, label: str, role: str | None = None) -> dict:
    if metric not in METRICS:
        raise ValueError(f"Métrica desconocida: {metric}")
    if op not in ("<=", ">="):
        raise ValueError("El operador debe ser <= o >=")
    role = (role or "").upper() or None
    with db.write() as conn:
        dup = conn.execute(
            "SELECT id FROM my_goals WHERE metric = ? AND op = ? AND COALESCE(role, '') = COALESCE(?, '') AND active = 1",
            (metric, op, role),
        ).fetchone()
        if dup:  # mismo propósito: se actualiza el objetivo en lugar de duplicarlo
            conn.execute("UPDATE my_goals SET target = ?, label = ? WHERE id = ?", (float(target), label, dup[0]))
            goal_id = dup[0]
        else:
            goal_id = conn.execute(
                "INSERT INTO my_goals (metric, op, target, role, label, active, created_at) VALUES (?, ?, ?, ?, ?, 1, ?)",
                (metric, op, float(target), role, label, now_s()),
            ).lastrowid
    return next(g for g in list_goals(db) if g["id"] == goal_id)


def update_goal(db: Database, goal_id: int, **fields) -> dict | None:
    values = {k: v for k, v in fields.items() if k in ("target", "label", "active") and v is not None}
    if "active" in values:
        values["active"] = int(bool(values["active"]))
    if values:
        with db.write() as conn:
            conn.execute(f"UPDATE my_goals SET {', '.join(f'{k} = ?' for k in values)} WHERE id = ?", [*values.values(), goal_id])
    return next((g for g in list_goals(db) if g["id"] == goal_id), None)


def delete_goal(db: Database, goal_id: int) -> bool:
    with db.write() as conn:
        return conn.execute("DELETE FROM my_goals WHERE id = ?", (goal_id,)).rowcount > 0


# ------------------------------------------------------------ revisión guiada
def review_answers(db: Database, match_id: str) -> dict[str, str]:
    """Respuestas guardadas de una partida: {"<segundo>|<pregunta>": texto}."""
    rows = db.query("SELECT t, question, answer FROM my_reviews WHERE match_id = ?", (match_id,))
    return {f"{float(r['t']):.1f}|{r['question']}": r["answer"] for r in rows}


def save_review_answer(db: Database, match_id: str, t: float, question: str, answer: str) -> None:
    answer = (answer or "").strip()
    with db.write() as conn:
        if not answer:
            conn.execute("DELETE FROM my_reviews WHERE match_id = ? AND t = ? AND question = ?", (match_id, round(float(t), 1), question))
            return
        conn.execute(
            "INSERT INTO my_reviews (match_id, t, question, answer, updated_at) VALUES (?, ?, ?, ?, ?) "
            "ON CONFLICT(match_id, t, question) DO UPDATE SET answer = excluded.answer, updated_at = excluded.updated_at",
            (match_id, round(float(t), 1), question, answer[:2000], now_s()),
        )


def review_stats(db: Database, since: int) -> dict:
    """Partidas revisadas (con alguna respuesta): en total y desde ``since`` (s)."""
    total = db.scalar("SELECT COUNT(DISTINCT match_id) FROM my_reviews", default=0) or 0
    recent = db.scalar("SELECT COUNT(DISTINCT match_id) FROM my_reviews WHERE updated_at >= ?", (int(since),), default=0) or 0
    return {"games": int(total), "week": int(recent)}


# ------------------------------------------------- jugadores de tus partidas
def peers_missing(db: Database, version: int, limit: int) -> list[str]:
    """Partidas propias con timeline cuyos otros 9 jugadores aún no se han analizado con esta versión."""
    rows = db.query(
        "SELECT match_id FROM my_games g WHERE status = 'ready' AND timeline_raw IS NOT NULL "
        "AND NOT EXISTS (SELECT 1 FROM peer_samples p WHERE p.match_id = g.match_id AND p.version = ?) "
        "ORDER BY game_start DESC LIMIT ?",
        (version, int(limit)),
    )
    return [r[0] for r in rows]


def save_peer_samples(db: Database, match_id: str, game_start: int | None, version: int, rows: list[dict], source: str = "own") -> None:
    """Sustituye las muestras de una partida (``rows``: pid, role, reliable, metrics). Sin filas, deja la marca pid 0."""
    rows = rows or [{"pid": 0, "role": None, "reliable": False, "metrics": {}}]
    with db.write() as conn:
        conn.execute("DELETE FROM peer_samples WHERE match_id = ?", (match_id,))
        conn.executemany(
            "INSERT INTO peer_samples (match_id, pid, source, role, game_start, version, reliable, metrics) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            [
                (match_id, int(r["pid"]), source, r.get("role"), game_start, version, int(bool(r.get("reliable"))),
                 json.dumps(r.get("metrics") or {}, separators=(",", ":")))
                for r in rows
            ],
        )


def has_peer_match(db: Database, match_id: str) -> bool:
    return bool(db.scalar("SELECT 1 FROM peer_samples WHERE match_id = ? LIMIT 1", (match_id,)))


def peer_games(db: Database, source: str, version: int) -> int:
    """Partidas con muestras de esa fuente y versión del análisis."""
    return int(db.scalar("SELECT COUNT(DISTINCT match_id) FROM peer_samples WHERE source = ? AND version = ? AND pid > 0", (source, version), default=0) or 0)


def peer_values(db: Database, role: str, version: int, limit: int, source: str = "own") -> dict[str, list[float]]:
    """Valores de cada métrica en las muestras fiables más recientes de ese rol y esa fuente."""
    rows = db.query(
        "SELECT metrics FROM peer_samples WHERE source = ? AND role = ? AND version = ? AND reliable = 1 ORDER BY game_start DESC LIMIT ?",
        (source, role, version, int(limit)),
    )
    out: dict[str, list[float]] = {}
    for r in rows:
        try:
            metrics = json.loads(r["metrics"])
        except (TypeError, ValueError):
            continue
        for key, value in metrics.items():
            if isinstance(value, (int, float)) and not isinstance(value, bool):
                out.setdefault(key, []).append(float(value))
    return out
