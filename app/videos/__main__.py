"""``python -m app.videos``: el recolector de vídeos sin abrir la web.

Ejemplos::

    python -m app.videos añadir "https://www.youtube.com/@CoachCurtis"
    python -m app.videos añadir "lux support" --idiomas es,en --n 5
    python -m app.videos añadir "" --rol jungla --tema rutas
    python -m app.videos procesar --max 3          # procesa la cola hasta 3 vídeos y sale
    python -m app.videos estado
    python -m app.videos revisar                   # marca lo recogido que parece mal (--todos: también lo que está bien)

``--datos <carpeta>`` usa otra carpeta de datos (para pruebas: nunca toques ``data/`` en pruebas).
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


class _MiniServices:
    """Lo mínimo que necesita el recolector fuera de la app (sin base de datos ni Riot)."""

    def __init__(self, data_dir: Path) -> None:
        from app.config import Settings
        from app.ddragon import DDragon

        base = Settings.load()
        self.settings = Settings(data_dir=data_dir, ddragon_dir=base.ddragon_dir, pause_in_game=False)
        self.dd = DDragon(base.ddragon_dir, base.ddragon_lang)
        self.tracker = None


def _collector(data_dir: Path | None):
    from app.config import settings
    from app.videos.collector import VideoCollector

    return VideoCollector(_MiniServices(Path(data_dir) if data_dir else Path(settings.data_dir)))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m app.videos", description="Recolector de vídeos de YouTube")
    parser.add_argument("--datos", type=Path, default=None, help="Carpeta de datos (por defecto la de la app)")
    sub = parser.add_subparsers(dest="cmd", required=True)
    add = sub.add_parser("añadir", aliases=["add"], help="Enlace de vídeo, lista o canal, o una búsqueda")
    add.add_argument("texto")
    add.add_argument("--rol", default="")
    add.add_argument("--tema", default="")
    add.add_argument("--campeon", default="")
    add.add_argument("--idiomas", default="es,en")
    add.add_argument("--n", type=int, default=10, help="Resultados por búsqueda e idioma")
    add.add_argument("--limite", type=int, default=50, help="Vídeos como mucho de un canal o lista")
    run = sub.add_parser("procesar", aliases=["run"], help="Procesa la cola (fuentes y vídeos)")
    run.add_argument("--max", type=int, default=0, help="Como mucho estos vídeos (0 = toda la cola)")
    sub.add_parser("estado", aliases=["status"], help="Cuentas de la cola")
    check = sub.add_parser("revisar", aliases=["review"], help="Marca lo recogido que parece mal (no cambia nada)")
    check.add_argument("--todos", action="store_true", help="Lista también los vídeos sin avisos")
    args = parser.parse_args(argv)

    if args.cmd in ("revisar", "review"):
        return _review(args.datos, args.todos)
    collector = _collector(args.datos)
    if args.cmd in ("añadir", "add"):
        langs = tuple(x.strip() for x in args.idiomas.split(",") if x.strip() in ("es", "en")) or ("es", "en")
        sources = collector.add(args.texto, args.rol, args.tema, args.campeon, langs, args.n, args.limite, start=False)
        print(json.dumps(sources, ensure_ascii=False, indent=1))
        return 0
    if args.cmd in ("procesar", "run"):
        return _run(collector, args.max)
    print(json.dumps(collector.store.counts(), ensure_ascii=False, indent=1))
    return 0


def _run(collector, max_videos: int) -> int:
    from app.videos import deps

    if not deps.ensure_base():
        print("No se pudieron instalar las herramientas de vídeo.", file=sys.stderr)
        return 1
    collector.svc.dd.load()
    done = 0
    while True:
        source = collector.store.next_source()
        if source is not None:
            collector._expand(source)
            continue
        video = collector.store.next_video()
        if video is None or (max_videos and done >= max_videos):
            break
        print(f"→ {video['id']} {video.get('titulo') or ''}", flush=True)
        collector._process(video)
        done += 1
        after = collector.store.get(video["id"]) or {}
        print(f"  {after.get('estado')}: {json.dumps(after.get('resumen') or after.get('error'), ensure_ascii=False)}", flush=True)
    print(json.dumps(collector.store.counts(), ensure_ascii=False))
    return 0


def _review(data_dir: Path | None, everything: bool) -> int:
    """Lo recogido que parece mal, para mirarlo a mano (solo lee: no necesita las herramientas de vídeo)."""
    from app.config import settings
    from app.videos import review
    from app.videos.store import DONE, VideoStore

    store = VideoStore(Path(data_dir or settings.data_dir) / "videos")
    results = [data for video in store.videos(DONE, limit=1_000_000) if (data := store.load_result(video["id"]))]
    lines, counts = review.report(results, everything)
    for text in lines:
        print(text)
    print(f"{counts['revisados']} vídeos revisados, {counts['con_avisos']} con avisos")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
