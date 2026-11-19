"""Lectura del cliente de LoL abierto (LCU API local).

Funciona en Windows y macOS mientras el cliente esté abierto y no usa la API
de Riot, así que no gasta cuota. Las credenciales salen del ``lockfile`` de
la carpeta de instalación (rutas por defecto) o, si está instalado ``psutil``,
de la línea de comandos del proceso del cliente.

* Lista de amigos.
* Tu cuenta (Riot ID y plataforma) para guardar el perfil sin escribirlo.
* Fase de la partida (``InProgress``, ``EndOfGame``…) para descargar tus
  partidas en cuanto terminan.
* Repeticiones reales (.rofl): estado, descarga y abrirlas en el cliente. Con
  ``EnableReplayApi=1`` en ``Config/game.cfg``, la *Replay API* del juego
  (``https://127.0.0.1:2999``) permite saltar al segundo exacto de un error.
"""

from __future__ import annotations

import base64
import importlib.util
import os
import re
import shutil
import stat
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


def _riot_client_installs() -> list[Path]:
    """Carpetas de LoL que conoce el Riot Client (Windows): RiotClientInstalls.json."""
    base = Path(os.environ.get("PROGRAMDATA") or "C:/ProgramData") / "Riot Games" / "RiotClientInstalls.json"
    try:
        import json

        data = json.loads(base.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    out = []
    for folder in (data.get("associated_client") or {}) if isinstance(data, dict) else {}:
        if "league of legends" in str(folder).lower():
            out.append(Path(folder))
    return out


from app.config import env as _config_env  # noqa: E402

for _folder in _riot_client_installs():
    if _folder not in INSTALL_DIRS:
        INSTALL_DIRS.insert(0, _folder)
_custom_dir = _config_env("LOL_INSTALL_DIR")  # del .env (o del entorno), como dice .env.example
if _custom_dir:
    INSTALL_DIRS.insert(0, Path(_custom_dir))

# Región del cliente -> plataforma de la API.
CLIENT_REGION = {
    "EUW": "euw1", "EUNE": "eun1", "NA": "na1", "KR": "kr", "BR": "br1", "LAN": "la1", "LA1": "la1",
    "LAS": "la2", "LA2": "la2", "OCE": "oc1", "OC1": "oc1", "TR": "tr1", "RU": "ru", "JP": "jp1",
    "ME": "me1", "SG": "sg2", "PH": "sg2", "TH": "sg2", "TW": "tw2", "VN": "vn2",
}


REPLAY_API = "https://127.0.0.1:2999"
REPLAY_COMPONENT = {"componentType": "replay-button_match-history"}


class LCUError(Exception):
    def __init__(self, message: str, status: int | None = None) -> None:
        super().__init__(message)
        self.status = status


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


def _request(method: str, path: str, body=None, timeout: float = 4.0):
    port, token = _credentials()
    auth = base64.b64encode(f"riot:{token}".encode()).decode()
    try:
        response = requests.request(
            method,
            f"https://127.0.0.1:{port}{path}",
            json=body,
            headers={"Authorization": f"Basic {auth}", "Accept": "application/json"},
            verify=False,  # certificado autofirmado del cliente local
            timeout=timeout,
        )
    except requests.RequestException as exc:
        raise LCUError(f"El cliente no responde: {exc}") from exc
    if response.status_code == 204 or not response.content:
        if response.status_code >= 400:
            raise LCUError(f"El cliente respondió {response.status_code}", response.status_code)
        return None
    try:
        data = response.json()
    except ValueError as exc:
        if response.status_code >= 400:
            raise LCUError(f"El cliente respondió {response.status_code}", response.status_code) from exc
        raise LCUError("Respuesta del cliente no válida") from exc
    if response.status_code >= 400:
        message = data.get("message") if isinstance(data, dict) else None
        raise LCUError(message or f"El cliente respondió {response.status_code}", response.status_code)
    return data


def _get(path: str, timeout: float = 4.0):
    return _request("GET", path, timeout=timeout)


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


# ---------------------------------------------------------------- instalación
def _is_pbe(folder: Path) -> bool:
    return "pbe" in folder.name.lower() or "(pbe)" in str(folder).lower()


def install_dir() -> Path | None:
    """Carpeta de instalación de LoL.

    Primero la del cliente abierto (``lockfile``), luego la versión normal antes que la PBE (con las dos
    instaladas, ``RiotClientInstalls.json`` puede listar la PBE primero) y, si no, la del proceso del cliente.
    """
    candidates = [f for f in INSTALL_DIRS if (f / "lockfile").exists() or (f / "Config").is_dir()]
    candidates.sort(key=lambda f: (not (f / "lockfile").exists(), _is_pbe(f)))
    if candidates:
        return candidates[0]
    if importlib.util.find_spec("psutil") is None:
        return None
    import psutil

    for proc in psutil.process_iter(["name", "cmdline", "exe"]):
        if proc.info.get("name") not in CLIENT_PROCESSES:
            continue
        for arg in proc.info.get("cmdline") or []:
            if arg.startswith("--install-directory="):
                folder = Path(arg.split("=", 1)[1].strip('"'))
                if folder.is_dir():
                    return folder
        exe = proc.info.get("exe")
        if exe and Path(exe).parent.is_dir():
            return Path(exe).parent
    return None


def game_cfg() -> Path | None:
    folder = install_dir()
    if folder is None:
        return None
    path = folder / "Config" / "game.cfg"
    return path if path.exists() else None


def _read_cfg(path: Path) -> str:
    raw = path.read_bytes()
    for encoding in ("utf-8-sig", "latin-1"):
        try:
            return raw.decode(encoding)
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", errors="replace")


def replay_api_enabled() -> bool | None:
    """``True``/``False`` según ``EnableReplayApi`` en game.cfg; ``None`` si no se encuentra el archivo."""
    path = game_cfg()
    if path is None:
        return None
    try:
        text = _read_cfg(path)
    except OSError:
        return None
    match = re.search(r"^\s*EnableReplayApi\s*=\s*(\S+)", text, re.M | re.I)
    return bool(match and match.group(1).strip().lower() in ("1", "true"))


def _write_atomic(path: Path, text: str) -> None:
    """Escribe en un temporal junto al archivo y lo cambia de golpe (un corte a medias nunca deja game.cfg roto).

    Si el archivo estaba en solo lectura (hay quien lo pone para que el cliente no le cambie la configuración), se
    quita lo justo para escribir y se vuelve a poner.
    """
    mode = stat.S_IMODE(path.stat().st_mode)
    read_only = not os.access(path, os.W_OK)
    tmp = path.with_name(path.name + ".tempu-tmp")
    tmp.write_text(text, encoding="utf-8", newline="")
    try:
        if read_only:
            os.chmod(path, mode | stat.S_IWRITE)  # Windows no deja reemplazar un archivo de solo lectura
        os.replace(tmp, path)
    except OSError:
        tmp.unlink(missing_ok=True)
        if read_only and path.exists():
            os.chmod(path, mode)
        raise
    os.chmod(path, mode)  # los mismos permisos que tenía (también el «solo lectura»)


def enable_replay_api() -> Path:
    """Pone ``EnableReplayApi=1`` en la sección [General] de game.cfg (guardando una copia antes)."""
    path = game_cfg()
    if path is None:
        raise LCUError("No encuentro Config/game.cfg de League of Legends. Abre el cliente o indica LOL_INSTALL_DIR en .env")
    text = _read_cfg(path)
    newline = "\r\n" if "\r\n" in text else "\n"
    if re.search(r"^\s*EnableReplayApi\s*=\s*1\s*$", text, re.M | re.I):
        return path
    backup = path.with_name("game.cfg.tempu-backup")
    try:
        if not backup.exists():
            shutil.copy2(path, backup)
        if re.search(r"^\s*EnableReplayApi\s*=", text, re.M | re.I):
            text = re.sub(r"^([ \t]*)EnableReplayApi[ \t]*=[^\r\n]*", r"\1EnableReplayApi=1", text, flags=re.M | re.I)
        elif re.search(r"^\[General\][ \t]*\r?$", text, re.M):
            # Tras la cabecera [General], respetando el final de línea del archivo (CRLF en Windows).
            text = re.sub(r"^(\[General\][ \t]*)(\r?\n|$)", lambda m: m.group(1) + newline + "EnableReplayApi=1" + (m.group(2) or newline), text, count=1, flags=re.M)
        else:
            text = f"[General]{newline}EnableReplayApi=1{newline}" + text
        _write_atomic(path, text)
    except PermissionError as exc:
        raise LCUError("Windows no deja modificar game.cfg (¿está abierto el juego?). Ciérralo y vuelve a intentarlo.") from exc
    except OSError as exc:
        raise LCUError(f"No se pudo modificar game.cfg: {exc}") from exc
    return path


# ---------------------------------------------------------------- repeticiones
def replay_metadata(game_id: int) -> dict:
    """``{state, downloadProgress}`` de la repetición de una partida (la crea si el cliente aún no la conoce)."""
    try:
        data = _get(f"/lol-replays/v1/metadata/{game_id}")
    except LCUError as exc:
        if exc.status != 404:
            raise
        _request("POST", f"/lol-replays/v2/metadata/{game_id}/create", {"gameId": game_id, "gameType": "", "gameVersion": "", "queueId": 0})
        data = _get(f"/lol-replays/v1/metadata/{game_id}")
    return data if isinstance(data, dict) else {}


def replay_download(game_id: int) -> None:
    try:
        _request("POST", f"/lol-replays/v1/rofls/{game_id}/download", REPLAY_COMPONENT, timeout=10)
    except LCUError as exc:
        if exc.status != 404:
            raise
        _request("POST", f"/lol-replays/v1/rofls/{game_id}/download/graceful", REPLAY_COMPONENT, timeout=10)


def replay_watch(game_id: int) -> None:
    _request("POST", f"/lol-replays/v1/rofls/{game_id}/watch", REPLAY_COMPONENT, timeout=10)


def replay_playback() -> dict | None:
    """Estado de la repetición abierta en el juego (``None`` si la Replay API no responde)."""
    try:
        response = requests.get(f"{REPLAY_API}/replay/playback", verify=False, timeout=2)
    except requests.RequestException:
        return None
    if response.status_code != 200:
        return None
    try:
        data = response.json()
    except ValueError:
        return None
    return data if isinstance(data, dict) else None


def replay_seek(seconds: float, paused: bool = False) -> dict | None:
    try:
        response = requests.post(f"{REPLAY_API}/replay/playback", json={"time": max(0.0, float(seconds)), "paused": paused, "speed": 1.0}, verify=False, timeout=3)
    except requests.RequestException as exc:
        raise LCUError(f"La repetición no responde: {exc}") from exc
    if response.status_code != 200:
        raise LCUError(f"La Replay API respondió {response.status_code}")
    try:
        return response.json()
    except ValueError:
        return None
