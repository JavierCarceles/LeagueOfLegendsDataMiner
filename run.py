"""Punto de entrada: ``python run.py`` y abre http://127.0.0.1:8000"""

from __future__ import annotations

import argparse
import sys

if sys.version_info < (3, 10):
    sys.exit("Tempu Drafter necesita Python 3.10 o superior.")

import uvicorn

from app.config import settings


def main() -> None:
    parser = argparse.ArgumentParser(description="Tempu Drafter — servidor web")
    parser.add_argument("--host", default=settings.host, help="Por defecto solo local (127.0.0.1)")
    parser.add_argument("--port", type=int, default=settings.port)
    parser.add_argument("--reload", action="store_true", help="Recarga automática al editar código")
    args = parser.parse_args()
    print(f"\n  Tempu Drafter → http://{'localhost' if args.host in ('127.0.0.1', '0.0.0.0') else args.host}:{args.port}\n")
    uvicorn.run("app.main:create_app", factory=True, host=args.host, port=args.port, reload=args.reload, log_level="warning")


if __name__ == "__main__":
    main()
