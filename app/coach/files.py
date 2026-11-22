"""Tus partidas, escritas solas en el proyecto (``<DATA_DIR>/mis_partidas``) para poder subirlas y revisarlas.

Estructura::

    mis_partidas/LEEME.md
    mis_partidas/resumen.json                                         (todas: por rol, campeón y cola, y medias)
    mis_partidas/<ROL>/<Campeón>/<AAAA-MM-DD>_<partida>_<V|D>.json.gz   (una por partida)

Cada partida lleva el análisis completo de «Mi progreso» (errores, métricas, build, jungla, repetición 2D) y la
partida y el timeline **en bruto** de Riot, así que se puede volver a analizar con cualquier versión del análisis.

La base de datos (``my_games``) es la fuente: cada minuto se escriben las partidas nuevas o reanalizadas (un índice
``.indice.json`` recuerda qué versión de cada una está escrita). Si cambia su rol al reanalizarla, se mueve.
"""

from __future__ import annotations

import gzip
import json
import re
import threading
import time
from collections import defaultdict
from pathlib import Path

from app.coach.store import get_profile, unpack
from app.data.db import now_s
from app.logbus import get_logger
from app.riot.routing import QUEUES

log = get_logger("mis-partidas")

FOLDER = "mis_partidas"
SYNC_EVERY_S = 60
FORMAT = 1
READY = ("ready", "no_timeline", "pending_timeline")

README = """# Mis partidas

Se escribe solo mientras la app está abierta (Tempu Drafter, «Mi progreso»): cada partida tuya analizada, ordenada
por rol y campeón.

* `resumen.json`: tu perfil, cuántas partidas tienes por rol, campeón y cola, las medias de tus métricas por rol
  (todas y las 20 últimas) y el índice de todas las partidas con su fichero.
* `<ROL>/<Campeón>/<fecha>_<partida>_<V|D>.json.gz`: una partida (V = victoria, D = derrota). Dentro:
  `analysis` (el análisis completo: errores con su contexto, métricas, build, jungla, series minuto a minuto y la
  repetición 2D), `match` y `timeline` (lo que devuelve Riot, en bruto, para volver a analizarla).

Se leen con cualquier lector de gzip + JSON. Si borras la carpeta, se vuelve a escribir sola desde la base de datos.
"""


def _slug(text: str) -> str:
    return re.sub(r"[^\w.#-]+", "_", text, flags=re.UNICODE).strip("_") or "sin_nombre"


class MyGamesFiles:
    def __init__(self, svc) -> None:
        self.svc = svc
        self.stop_event = threading.Event()
        self.wake = threading.Event()
        self.thread: threading.Thread | None = None
        self.lock = threading.Lock()
        self.state: dict = {"status": "idle", "games": 0, "written": 0}

    @property
    def base(self) -> Path:
        return Path(self.svc.settings.data_dir) / FOLDER

    # ---------------------------------------------------------- hilo
    def start(self) -> None:
        if self.thread is not None and self.thread.is_alive():
            return
        self.stop_event.clear()
        self.thread = threading.Thread(target=self._run, name="mis-partidas", daemon=True)
        self.thread.start()

    def stop(self) -> None:
        self.stop_event.set()
        self.wake.set()

    def _run(self) -> None:
        while not self.stop_event.is_set():
            try:
                self.sync()
            except Exception as exc:  # que no se caiga el hilo; se reintenta en el siguiente minuto
                log.exception("No se pudieron escribir tus partidas en %s", self.base)
                self.state.update(status="error", error=str(exc))
            self.wake.wait(SYNC_EVERY_S)
            self.wake.clear()

    # ---------------------------------------------------------- escritura
    def _champ_name(self, champ: int | None) -> str:
        dd = getattr(self.svc, "dd", None)
        name = dd.name(champ) if dd is not None and hasattr(dd, "name") and champ else None
        return name or str(champ or "desconocido")

    def _rel_path(self, row) -> str:
        day = time.strftime("%Y-%m-%d", time.gmtime((row["game_start"] or 0) / 1000))
        result = "V" if row["win"] else "D"
        return f"{row['role'] or 'SIN_ROL'}/{_slug(self._champ_name(row['champ']))}/{day}_{row['match_id']}_{result}.json.gz"

    def _index(self) -> dict:
        try:
            data = json.loads((self.base / ".indice.json").read_text(encoding="utf-8"))
            return data if data.get("format") == FORMAT else {}
        except (OSError, ValueError):
            return {}

    def sync(self) -> int:
        """Escribe las partidas nuevas o cambiadas y el resumen. Devuelve cuántas se escribieron."""
        db = self.svc.db
        with self.lock:
            rows = db.query(
                f"SELECT match_id, puuid, queue, game_start, duration, champ, role, win, status, version, analyzed_at, fetched_at "
                f"FROM my_games WHERE status IN ({','.join('?' * len(READY))}) AND analysis IS NOT NULL ORDER BY game_start",
                READY,
            )
            if not rows:
                self.state.update(status="idle", games=0)
                return 0
            index = self._index()
            games: dict = index.get("games") or {}
            self.base.mkdir(parents=True, exist_ok=True)
            written = 0
            for row in rows:
                if self.stop_event.is_set():
                    break
                rel = self._rel_path(row)
                stamp = [row["version"], row["analyzed_at"], row["status"]]
                known = games.get(row["match_id"])
                if known and known.get("stamp") == stamp and known.get("path") == rel and (self.base / rel).exists():
                    continue
                self._write_game(row, rel)
                if known and known.get("path") and known["path"] != rel:
                    (self.base / known["path"]).unlink(missing_ok=True)  # cambió de rol o de resultado al reanalizarla
                games[row["match_id"]] = {"path": rel, "stamp": stamp}
                written += 1
            if written or not (self.base / "resumen.json").exists():
                self._write_summary(rows, games)
                (self.base / "LEEME.md").write_text(README, encoding="utf-8")
                tmp = self.base / ".indice.json.tmp"
                tmp.write_text(json.dumps({"format": FORMAT, "games": games}, ensure_ascii=False), encoding="utf-8")
                tmp.replace(self.base / ".indice.json")
            self.state.update(status="ok", games=len(rows), written=self.state.get("written", 0) + written, synced_at=now_s(), path=str(self.base), error=None)
            if written:
                log.info("Mis partidas: %d escritas en %s (%d en total).", written, self.base, len(rows))
            return written

    def _write_game(self, row, rel: str) -> None:
        raw = self.svc.db.one("SELECT analysis, match_raw, timeline_raw FROM my_games WHERE match_id = ?", (row["match_id"],))
        analysis = unpack(raw["analysis"]) if raw else None
        payload = {
            "match_id": row["match_id"],
            "queue": row["queue"],
            "queue_label": QUEUES.get(row["queue"], f"Cola {row['queue']}"),
            "game_start": row["game_start"],
            "date": time.strftime("%Y-%m-%d %H:%M", time.gmtime((row["game_start"] or 0) / 1000)) + " UTC",
            "duration": row["duration"],
            "champ": row["champ"],
            "champ_name": self._champ_name(row["champ"]),
            "role": row["role"],
            "win": bool(row["win"]),
            "status": row["status"],
            "analysis_version": row["version"],
            "analysis": analysis,
            "match": unpack(raw["match_raw"]) if raw else None,
            "timeline": unpack(raw["timeline_raw"]) if raw else None,
        }
        path = self.base / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_name(path.name + ".tmp")
        with gzip.open(tmp, "wt", encoding="utf-8") as fh:
            json.dump(payload, fh, ensure_ascii=False, separators=(",", ":"))
        tmp.replace(path)

    def _write_summary(self, rows, games: dict) -> None:
        db = self.svc.db
        digests = {
            r["match_id"]: r["digest"]
            for r in db.query(f"SELECT match_id, digest FROM my_games WHERE status IN ({','.join('?' * len(READY))})", READY)
        }

        def metrics_of(match_id: str) -> dict:
            try:
                return (json.loads(digests.get(match_id) or "{}").get("m")) or {}
            except ValueError:
                return {}

        by_role: dict[str, dict] = {}
        role_rows: dict[str, list] = defaultdict(list)
        queues: dict[str, dict] = defaultdict(lambda: {"games": 0, "wins": 0})
        for r in rows:
            role = r["role"] or "SIN_ROL"
            role_rows[role].append(r)
            q = queues[QUEUES.get(r["queue"], f"Cola {r['queue']}")]
            q["games"] += 1
            q["wins"] += int(bool(r["win"]))

        def averages(subset) -> dict:
            sums: dict[str, list[float]] = defaultdict(list)
            for r in subset:
                for k, v in metrics_of(r["match_id"]).items():
                    if isinstance(v, (int, float)) and not isinstance(v, bool):
                        sums[k].append(float(v))
            return {k: round(sum(v) / len(v), 2) for k, v in sorted(sums.items())}

        for role, rs in sorted(role_rows.items(), key=lambda kv: -len(kv[1])):
            champs: dict[str, dict] = defaultdict(lambda: {"games": 0, "wins": 0})
            for r in rs:
                c = champs[self._champ_name(r["champ"])]
                c["games"] += 1
                c["wins"] += int(bool(r["win"]))
            recent = sorted(rs, key=lambda r: -(r["game_start"] or 0))[:20]
            wins = sum(1 for r in rs if r["win"])
            by_role[role] = {
                "games": len(rs),
                "wins": wins,
                "wr": round(100 * wins / len(rs), 1),
                "champions": dict(sorted(champs.items(), key=lambda kv: -kv[1]["games"])),
                "metrics_mean": averages(rs),
                "metrics_mean_last20": averages(recent),
            }
        profile = get_profile(db) or {}
        wins = sum(1 for r in rows if r["win"])
        summary = {
            "updated_at": now_s(),
            "profile": {k: profile.get(k) for k in ("game_name", "tag_line", "platform", "target_tier") if k in profile},
            "games": len(rows),
            "wins": wins,
            "wr": round(100 * wins / len(rows), 1) if rows else None,
            "by_queue": dict(queues),
            "by_role": by_role,
            "index": [
                {
                    "match_id": r["match_id"],
                    "date": time.strftime("%Y-%m-%d", time.gmtime((r["game_start"] or 0) / 1000)),
                    "champ": self._champ_name(r["champ"]),
                    "role": r["role"],
                    "queue": QUEUES.get(r["queue"], f"Cola {r['queue']}"),
                    "win": bool(r["win"]),
                    "file": (games.get(r["match_id"]) or {}).get("path"),
                }
                for r in sorted(rows, key=lambda r: -(r["game_start"] or 0))
            ],
        }
        tmp = self.base / "resumen.json.tmp"
        tmp.write_text(json.dumps(summary, ensure_ascii=False, indent=1), encoding="utf-8")
        tmp.replace(self.base / "resumen.json")
