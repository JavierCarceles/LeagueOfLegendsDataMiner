"""Lectura de la lista de amigos del cliente de LoL abierto (LCU API local).

Funciona en Windows y macOS mientras el cliente esté abierto. Requiere
``psutil`` (opcional). No usa la API de Riot, así que no gasta cuota.
"""

from __future__ import annotations

import base64
import importlib.util

import requests
import urllib3

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
CLIENT_PROCESSES = {"LeagueClientUx.exe", "LeagueClientUx", "LeagueClient.exe", "LeagueClient"}


class LCUError(Exception):
    pass


def available() -> bool:
    return importlib.util.find_spec("psutil") is not None


def _credentials() -> tuple[str, str]:
    if not available():
        raise LCUError("Instala psutil para leer el cliente (pip install psutil)")
    import psutil

    for proc in psutil.process_iter(["name", "cmdline"]):
        if proc.info.get("name") not in CLIENT_PROCESSES:
            continue
        port = token = None
        for arg in proc.info.get("cmdline") or []:
            if arg.startswith("--app-port="):
                port = arg.split("=", 1)[1]
            elif arg.startswith("--remoting-auth-token="):
                token = arg.split("=", 1)[1]
        if port and token:
            return port, token
    raise LCUError("No se encontró el cliente de League of Legends abierto")


def friends() -> list[dict]:
    port, token = _credentials()
    auth = base64.b64encode(f"riot:{token}".encode()).decode()
    response = requests.get(
        f"https://127.0.0.1:{port}/lol-chat/v1/friends",
        headers={"Authorization": f"Basic {auth}", "Accept": "application/json"},
        verify=False,  # certificado autofirmado del cliente local
        timeout=5,
    )
    if response.status_code != 200:
        raise LCUError(f"El cliente respondió {response.status_code}")
    out = []
    for friend in response.json():
        name, tag = friend.get("gameName") or "", friend.get("gameTag") or ""
        if name:
            out.append({"game_name": name, "tag_line": tag, "puuid": friend.get("puuid"), "product": friend.get("product")})
    return sorted(out, key=lambda f: f["game_name"].lower())
