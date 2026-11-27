"""Borrado de las partidas recogidas para empezar de cero con una versión nueva del análisis.

Pedido el 26-11-2026: «vamos a eliminar todos los datos de partidas (menos las mías locales) […] y empezaremos de nuevo
para tener mejores métricas de comparación». Se hace una sola vez por ``RESET_ID`` al arrancar la app, antes del
recolector y del re-análisis, y queda apuntado en la base de datos para no repetirlo.

Se borra lo que sale del análisis de las partidas recogidas: OTPs, jugadores revisados, todo el ranking, partidas por
patrón, referencias de tu elo y sus carpetas (``data/otps``, ``data/partidas_campeon``, ``data/raw_otp``) con la caché
que sale de ellas. No se tocan tus partidas, tu perfil, revisiones, objetivos, grabaciones, vídeos, ``data/legacy``,
las estadísticas del draft ni los jugadores ya descubiertos (el recolector vuelve a recoger más rápido).
"""

from __future__ import annotations

import shutil
from pathlib import Path

from app.logbus import get_logger

log = get_logger("reinicio")

RESET_ID: str | None = "v16"  # confirmado por el usuario el 27-11-2026: «Solo los análisis»
META_KEY = "datos.reinicio"
TABLES = ("otp_games", "ranking_games", "pattern_games", "peer_samples", "otp_progress", "otp_skips", "otp_sources")
DIRS = ("otps", "partidas_campeon", "raw_otp")
CACHES = (".cache/errores_por_elo.json", ".cache/errores_por_elo_partidas.json.gz")
RESET_META = ("otp.reanalysis",)  # el contador del re-análisis: sin partidas guardadas no hay nada que rehacer


def maybe_reset(svc, reset_id: str | None = None) -> bool:
    """Borra las partidas recogidas si ``reset_id`` (``RESET_ID``) no se ha hecho aún. ``True`` si borró."""
    reset_id = reset_id if reset_id is not None else RESET_ID
    if not reset_id:
        return False
    db = svc.db
    if db.meta_get(META_KEY) == reset_id:
        return False
    counts = {t: int(db.scalar(f"SELECT COUNT(*) FROM {t}") or 0) for t in TABLES}
    log.warning("Empezando de cero (%s): borro las partidas recogidas %s. Lo tuyo se queda.", reset_id, counts)
    with db.write() as conn:
        for table in TABLES:
            conn.execute(f"DELETE FROM {table}")
        for key in RESET_META:
            conn.execute("DELETE FROM meta WHERE key = ?", (key,))
    data_dir = Path(svc.settings.data_dir)
    for name in DIRS:
        shutil.rmtree(data_dir / name, ignore_errors=True)
    for name in CACHES:
        (data_dir / name).unlink(missing_ok=True)
    db.meta_set(META_KEY, reset_id)
    log.warning("Partidas recogidas borradas (%s). El recolector empieza de nuevo con la versión actual del análisis.", reset_id)
    return True
