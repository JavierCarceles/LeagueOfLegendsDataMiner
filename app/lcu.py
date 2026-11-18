"""Lectura del cliente de LoL abierto (LCU API local).

Funciona en Windows y macOS mientras el cliente esté abierto y no usa la API
de Riot, así que no gasta cuota. Las credenciales salen del ``lockfile`` de
la carpeta de instalación (rutas por defecto) o, si está instalado ``psutil``,
de la línea de comandos del proceso del cliente.

* Lista de amigos.
* Tu cuenta (Riot ID y plataforma) para guardar el perfil sin escribirlo.
* Fase de la partida (``InProgress``, ``EndOfGame``…) para descargar tus
  partidas en cuanto terminan.
"""

from __future__ import annotations

import base64
import importlib.util
import os
from pathlib import Path

import requests
import urllib3

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
CLIENT_PROCESSES = {"LeagueClientUx.exe", "LeagueClientUx", "LeagueClient.exe", "LeagueClient"}
INSTALL_DIRS = [
    Path("C:/Riot Games/League of Legends"),
    Path("D:/Riot Games/League of Legends"),
    Path("/Applications/League of Legends.app/Contents/LoL"),
    Path.home() / "Applications/League of Legends.app/Contents/LoL",
]
if os.environ.get("LOL_INSTALL_DIR"):
    INSTALL_DIRS.insert(0, Path(os.environ["LOL_INSTALL_DIR"]))

# Región del cliente -> plataforma de la API.
CLIENT_REGION = {
    "EUW": "euw1", "EUNE": "eun1", "NA": "na1", "KR": "kr", "BR": "br1", "LAN": "la1", "LA1": "la1",
    "LAS": "la2", "LA2": "la2", "OCE": "oc1", "OC1": "oc1", "TR": "tr1", "RU": "ru", "JP": "jp1",
    "ME": "me1", "SG": "sg2", "PH": "sg2", "TH": "sg2", "TW": "tw2", "VN": "vn2",
}


class LCUError(Exception):
    pass


def available() -> bool:
    return importlib.util.find_spec("psutil") is not None or any(d.is_dir() for d in INSTALL_DIRS)


def _lockfile() -> tuple[str, str] | None:
    for folder in INSTALL_DIRS:
        path = folder / "lockfile"
        try:
            parts = path.read_text(encoding="utf-8").strip().split(":")
        except OSError:
            continue
        if len(parts) >= 5 and parts[2].isdigit():
            return parts[2], parts[3]
    return None


def _credentials() -> tuple[str, str]:
    found = _lockfile()
    if found:
        return found
    if importlib.util.find_spec("psutil") is None:
        raise LCUError("No se encontró el cliente de League of Legends abierto")
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


def _get(path: str, timeout: float = 4.0):
    port, token = _credentials()
    auth = base64.b64encode(f"riot:{token}".encode()).decode()
    try:
        response = requests.get(
            f"https://127.0.0.1:{port}{path}",
            headers={"Authorization": f"Basic {auth}", "Accept": "application/json"},
            verify=False,  # certificado autofirmado del cliente local
            timeout=timeout,
        )
    except requests.RequestException as exc:
        raise LCUError(f"El cliente no responde: {exc}") from exc
    if response.status_code != 200:
        raise LCUError(f"El cliente respondió {response.status_code}")
    try:
        return response.json()
    except ValueError as exc:
        raise LCUError("Respuesta del cliente no válida") from exc


def friends() -> list[dict]:
    out = []
    for friend in _get("/lol-chat/v1/friends"):
        name, tag = friend.get("gameName") or "", friend.get("gameTag") or ""
        if name:
            out.append({"game_name": name, "tag_line": tag, "puuid": friend.get("puuid"), "product": friend.get("product")})
    return sorted(out, key=lambda f: f["game_name"].lower())


def current_summoner() -> dict:
    """Cuenta con la sesión iniciada en el cliente: Riot ID, plataforma, icono y nivel."""
    me = _get("/lol-summoner/v1/current-summoner")
    if not isinstance(me, dict):
        raise LCUError("El cliente aún no ha cargado tu cuenta")
    name = me.get("gameName") or ""
    tag = me.get("tagLine") or ""
    if not name:
        display = me.get("displayName") or ""
        name, _, tag = display.partition("#")
    if not name or not tag:
        raise LCUError("El cliente no devolvió tu Riot ID")
    platform = None
    try:
        value = _get("/lol-platform-config/v1/namespaces/LoginDataPacket/platformId")
        if isinstance(value, str) and value.strip():
            platform = value.strip().lower()
    except LCUError:
        pass
    if not platform:
        try:
            region = (_get("/riotclient/region-locale") or {}).get("region") or ""
            platform = CLIENT_REGION.get(region.upper())
        except LCUError:
            pass
    return {
        "game_name": name,
        "tag_line": tag,
        "platform": platform,
        "profile_icon": me.get("profileIconId"),
        "summoner_level": me.get("summonerLevel"),
    }


def gameflow_phase() -> str | None:
    """``None`` si el cliente no está abierto; si no, ``Lobby``, ``InProgress``, ``EndOfGame``…"""
    try:
        value = _get("/lol-gameflow/v1/gameflow-phase", timeout=2.0)
    except LCUError:
        return None
    return value if isinstance(value, str) else None
