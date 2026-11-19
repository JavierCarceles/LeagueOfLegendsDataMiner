"""Punto de entrada: ``python run.py`` (o doble clic en ``iniciar.bat`` en Windows).

* Abre el navegador solo cuando el servidor está listo (``--no-browser`` para no hacerlo).
* Si la app ya está abierta en ese puerto, solo abre el navegador (no arranca otra copia).
* Si el puerto lo usa otro programa, busca el siguiente libre.
* En Windows baja la prioridad del proceso (configurable con ``LOW_PRIORITY=0``):
  las descargas en segundo plano nunca le quitan CPU al juego.
"""

from __future__ import annotations

import argparse
import json
import os
import socket
import sys
import threading
import time
import urllib.request
import webbrowser

if sys.version_info < (3, 10):
    sys.exit("Tempu Drafter necesita Python 3.10 o superior.")

# Consola en UTF-8 aunque Windows use otra página de códigos (tildes, «», →…).
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass

import uvicorn  # noqa: E402

from app.config import settings  # noqa: E402


def _url(host: str, port: int) -> str:
    shown = "localhost" if host in ("127.0.0.1", "0.0.0.0", "::", "") else host
    return f"http://{shown}:{port}"


def _our_app_running(port: int) -> bool:
    """¿Responde Tempu Drafter en ese puerto?"""
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/api/system/status", timeout=2) as response:
            data = json.loads(response.read().decode("utf-8"))
            return isinstance(data, dict) and "version" in data and "key" in data
    except Exception:
        return False


def _port_free(host: str, port: int) -> bool:
    bind_host = "127.0.0.1" if host in ("", "localhost") else host
    with socket.socket(socket.AF_INET6 if ":" in bind_host else socket.AF_INET, socket.SOCK_STREAM) as sock:
        if os.name != "nt":
            # Igual que uvicorn: un puerto con conexiones en TIME_WAIT (reinicio rápido) sí se puede usar.
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            sock.bind((bind_host, port))
        except OSError:
            return False
    return True


def _lower_priority() -> None:
    if os.name != "nt" or not settings.low_priority:
        return
    try:
        import psutil

        psutil.Process().nice(psutil.BELOW_NORMAL_PRIORITY_CLASS)
    except Exception:
        pass  # psutil no instalado o sin permiso: no es imprescindible


def _open_when_ready(url: str, port: int) -> None:
    def run() -> None:
        for _ in range(120):
            if _our_app_running(port):
                webbrowser.open(url)
                return
            time.sleep(0.5)

    threading.Thread(target=run, name="open-browser", daemon=True).start()


def main() -> None:
    parser = argparse.ArgumentParser(description="Tempu Drafter — servidor web")
    parser.add_argument("--host", default=settings.host, help="Por defecto solo local (127.0.0.1)")
    parser.add_argument("--port", type=int, default=settings.port)
    parser.add_argument("--reload", action="store_true", help="Recarga automática al editar código")
    parser.add_argument("--no-browser", action="store_true", help="No abrir el navegador al arrancar")
    args = parser.parse_args()

    port = args.port
    if _our_app_running(port):
        url = _url(args.host, port)
        print(f"\n  Tempu Drafter ya está abierto → {url}\n")
        if not args.no_browser:
            webbrowser.open(url)
        return
    if not _port_free(args.host, port):
        for candidate in range(port + 1, port + 20):
            if _port_free(args.host, candidate):
                print(f"  El puerto {port} está ocupado por otro programa: uso el {candidate}.")
                port = candidate
                break
        else:
            sys.exit(f"No hay ningún puerto libre entre {port} y {port + 19}. Cierra el programa que los usa o pasa --port.")

    url = _url(args.host, port)
    print(f"\n  Tempu Drafter → {url}\n  (Ctrl+C para cerrar)\n")
    _lower_priority()
    if not args.no_browser:
        _open_when_ready(url, port)
    uvicorn.run("app.main:create_app", factory=True, host=args.host, port=port, reload=args.reload, log_level="warning")


if __name__ == "__main__":
    main()
