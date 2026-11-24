"""Datos de OP.GG: counters, tier list, sinergias y guías de matchup.

Dos vías, ambas públicas y sin saltarse ninguna protección:

* **Web** (``op.gg/lol/champions/...``): las páginas incluyen los datos en
  JSON. De ahí salen los *counters* completos de cada campeón y la tier list
  por línea, con filtros de parche, elo y región (el robots.txt de OP.GG
  permite clientes genéricos).
* **MCP oficial** (``mcp-api.op.gg/mcp``): la API que OP.GG ofrece para apps.
  Aporta la guía de cada enfrentamiento (quién gana la línea, solo kills,
  consejo, build contra ese rival), sinergias y tipo de daño.

Todo se cachea en SQLite (``ext_cache``) y se limita el ritmo de peticiones
para ser un cliente educado. onetricks.gg, u.gg o mobalytics bloquean
clientes automáticos con un checkpoint anti-bot, así que no se usan.
"""

from __future__ import annotations

import contextlib
import contextvars
import json
import re
import threading
import time
from concurrent.futures import ThreadPoolExecutor, wait

import requests

from app import __version__, budget
from app.data.db import Database
from app.ddragon import DDragon
from app.external.base import CachedSource, Throttle
from app.logbus import get_logger

log = get_logger("opgg")

WEB = "https://op.gg"
MCP_URL = "https://mcp-api.op.gg/mcp"
USER_AGENT = f"TempuDrafter/{__version__} (personal stats tool)"
CLIENT_APP = f"TempuDrafter/{__version__}"

POSITIONS = {"TOP": "top", "JUNGLE": "jungle", "MID": "mid", "ADC": "adc", "SUPPORT": "support"}
POSITION_NAMES = {
    "top": "TOP", "jungle": "JUNGLE", "mid": "MID", "middle": "MID", "adc": "ADC", "bottom": "ADC",
    "bot": "ADC", "support": "SUPPORT", "utility": "SUPPORT",
}
TIERS = [
    "all", "iron", "bronze", "silver", "gold", "gold_plus", "platinum", "platinum_plus", "emerald",
    "emerald_plus", "diamond", "diamond_plus", "master", "master_plus", "grandmaster", "challenger",
]
REGIONS = {
    "global": "Global", "euw": "EUW", "eune": "EUNE", "na": "NA", "kr": "KR", "jp": "JP", "br": "BR",
    "lan": "LAN", "las": "LAS", "oce": "OCE", "tr": "TR", "ru": "RU", "me": "ME", "sg": "SEA", "tw": "TW", "vn": "VN",
}
PLATFORM_TO_REGION = {
    "euw1": "euw", "eun1": "eune", "na1": "na", "kr": "kr", "jp1": "jp", "br1": "br", "la1": "lan",
    "la2": "las", "oc1": "oce", "tr1": "tr", "ru": "ru", "me1": "me", "sg2": "sg", "tw2": "tw", "vn2": "vn",
}

TTL_LATEST = 6 * 3600  # el parche en curso se actualiza durante el día
TTL_OLD = 7 * 86400  # un parche cerrado ya no cambia
TTL_MCP = 12 * 3600
TTL_VERSIONS = 3600


class OpggError(Exception):
    pass


# ----------------------------------------------------------------- parsers
_PUSH = re.compile(r'self\.__next_f\.push\(\[1,"(.*?)"\]\)</script>', re.DOTALL)


def rsc_payload(html: str) -> str:
    """Une los trozos ``self.__next_f.push`` de una página Next.js."""
    out = []
    for chunk in _PUSH.findall(html):
        try:
            out.append(json.loads(f'"{chunk}"'))
        except ValueError:
            continue
    return "".join(out)


def _string_end(text: str, i: int) -> int:
    """Posición de las comillas que cierran la cadena JSON que sigue en ``i`` (o el final del texto)."""
    n = len(text)
    while i < n and text[i] != '"':
        i += 2 if text[i] == "\\" else 1  # lo escapado no cierra la cadena
    return min(i, n)


def _object_end(text: str, start: int) -> int | None:
    """Posición justo después de la llave que cierra el objeto que empieza en ``start`` (``None`` si no se cierra)."""
    depth = 0
    i = start
    while i < len(text):
        ch = text[i]
        if ch == '"':
            i = _string_end(text, i + 1)  # las llaves dentro de una cadena no cuentan
        elif ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return i + 1
        i += 1
    return None


def iter_objects(text: str, marker: str):
    """Extrae objetos JSON completos que empiezan por ``marker``."""
    start = text.find(marker)
    while start != -1:
        end = _object_end(text, start)
        if end is None:
            return
        with contextlib.suppress(ValueError):  # si no es JSON válido, se salta
            yield json.loads(text[start:end])
        start = text.find(marker, end)


# Formato compacto del MCP. Este patrón reconoce cada token por su comienzo (cadena JSON, signo, identificador o
# número); el identificador y el número completos se leen después con el suyo.
_MCP_TOKEN = re.compile(r'(?P<str>"(?:[^"\\]|\\.)*")|(?P<p>[()\[\],])|(?P<id>[A-Za-z_])|(?P<num>-?\d)')
_MCP_FULL = {
    "id": re.compile(r"[A-Za-z_]\w*", re.ASCII),
    "num": re.compile(r"-?\d+(?:\.\d+)?(?:[eE][-+]?\d+)?"),
}
_MCP_LITERALS = {"true": True, "false": False, "null": None, "None": None}


def _mcp_tokens(body: str) -> list[tuple[str, str]]:
    """``(tipo, texto)`` de cada token del formato compacto, en orden (lo que no es un token se salta)."""
    tokens = []
    match = _MCP_TOKEN.search(body)
    while match is not None:
        kind = match.lastgroup
        start, end = match.span()
        full = _MCP_FULL.get(kind)
        if full is not None:
            end = full.match(body, start).end()
        tokens.append((kind, body[start:end]))
        match = _MCP_TOKEN.search(body, end)
    return tokens


def _mcp_classes(text: str) -> tuple[dict[str, list[str]], str]:
    """Separa las cabeceras ``class Nombre: campo, …`` del cuerpo (que se devuelve sin líneas en blanco)."""
    classes: dict[str, list[str]] = {}
    body_lines = []
    for line in text.splitlines():
        if line.startswith("class ") and ":" in line:
            name, fields = line[6:].split(":", 1)
            classes[name.strip()] = [f.strip() for f in fields.split(",") if f.strip()]
        elif line.strip():
            body_lines.append(line)
    return classes, "\n".join(body_lines)


class _McpReader:
    """Lee un valor del formato compacto: ``Clase(arg, …)``, listas, cadenas, números y literales."""

    def __init__(self, tokens: list[tuple[str, str]], classes: dict[str, list[str]]) -> None:
        self.tokens = tokens
        self.classes = classes
        self.pos = 0

    def _sign(self) -> str | None:
        """Signo del token actual (``None`` si es otra cosa)."""
        kind, text = self.tokens[self.pos]
        return text if kind == "p" else None

    def _sequence(self, close: str) -> list:
        """Valores separados por comas hasta ``close``, que también se consume."""
        values = []
        while self._sign() != close:
            values.append(self.value())
            if self._sign() == ",":
                self.pos += 1
        self.pos += 1
        return values

    def value(self):
        kind, text = self.tokens[self.pos]
        self.pos += 1
        if kind == "str":
            return json.loads(text)
        if kind == "num":
            return float(text) if any(c in text for c in ".eE") else int(text)
        if text == "[":
            return self._sequence("]")
        return self._name(text if kind == "id" else None)

    def _name(self, ident: str | None):
        """Literal, objeto ``Clase(…)`` (con sus campos si se conoce la clase) o identificador suelto."""
        if ident in _MCP_LITERALS:
            return _MCP_LITERALS[ident]
        if self.pos < len(self.tokens) and self._sign() == "(":
            self.pos += 1
            args = self._sequence(")")
            fields = self.classes.get(ident)
            if fields and len(fields) == len(args):
                return dict(zip(fields, args))
            return {"_type": ident, "args": args}
        return ident


def parse_mcp_text(text: str):
    """Parsea las respuestas del MCP de OP.GG (JSON o su formato compacto de clases)."""
    text = text.strip()
    if text.startswith(("{", "[")):
        return json.loads(text)
    classes, body = _mcp_classes(text)
    tokens = _mcp_tokens(body)
    return _McpReader(tokens, classes).value() if tokens else None


# ------------------------------------------------------------------ cliente


WEB_KINDS = {"versions", "tier", "counters"}


def _runes_of(raw) -> dict | None:
    """Página de runas más jugada (``data.runes``), en el mismo formato que la de las guías de matchup."""
    page = raw[0] if isinstance(raw, list) and raw else raw
    if not isinstance(page, dict) or not page.get("id"):
        return None
    return {
        "keystone": page.get("id"),
        "primary": page.get("primary_page_id"),
        "secondary": page.get("secondary_page_id"),
        "ids": (page.get("primary_rune_ids") or []) + (page.get("secondary_rune_ids") or []),
        "names": (page.get("primary_rune_names") or []) + (page.get("secondary_rune_names") or []),
        "shards": _shards(page),
        "games": page.get("play"),
        "wins": page.get("win"),
    }


def _shards(page: dict) -> list[int]:
    """Los 3 fragmentos de estadísticas (``stat_mod_ids``), los que pide el cliente para crear la página."""
    out = []
    for value in page.get("stat_mod_ids") or []:
        try:
            out.append(int(value))
        except (TypeError, ValueError):
            return []
    return out if len(out) == 3 else []


def _ids(entry) -> list[int]:
    """``ids`` de una entrada del MCP como enteros (se saltan los que no lo son)."""
    out = []
    for value in (entry or {}).get("ids") or []:
        try:
            out.append(int(value))
        except (TypeError, ValueError):
            continue
    return out


def _options(entries, limit: int = 6) -> list[dict]:
    """Objetos que se eligen en un hueco de la build (4.º, 5.º, 6.º…) con sus partidas."""
    rows = []
    for e in (entries or [])[:limit]:
        item = _ids(e)
        if item:
            rows.append({"id": item[0], "games": e.get("play"), "wins": e.get("win"), "pick_rate": e.get("pick_rate")})
    return rows


def _first_entry(value):
    """El MCP da unas veces un objeto y otras una lista de ellos: el objeto o el primero (``{}`` si no hay)."""
    return value if isinstance(value, dict) else (value or [{}])[0]


def _core_entry(value):
    """Núcleo de la build: el objeto o el primero de la lista."""
    value = value or {}
    return value[0] if isinstance(value, list) and value else value


def _field(entry, key: str):
    """``entry[key]`` si ``entry`` es un objeto (si no, ``None``)."""
    return entry.get(key) if isinstance(entry, dict) else None


def _texts(entry, key: str) -> list[str]:
    """``entry[key]`` como lista de textos (vacía si ``entry`` no es un objeto o no lo trae)."""
    return [str(s) for s in _field(entry, key) or []]


def _build_of(data: dict) -> dict:
    """Build de ``lol_get_champion_analysis``: objetos, botas, inicial, 4.º-6.º objeto, runas, hechizos y habilidades."""
    core = _core_entry(data.get("core_items"))
    return {
        "core": _ids(core),
        "core_games": _field(core, "play"),
        "core_wins": _field(core, "win"),
        "boots": _ids(_first_entry(data.get("boots"))),
        "starter": _ids(_first_entry(data.get("starter_items"))),
        "popular": _options(data.get("last_items")),
        "fourth": _options(data.get("fourth_items"), 4),
        "fifth": _options(data.get("fifth_items"), 4),
        "sixth": _options(data.get("sixth_items"), 4),
        "runes": _runes_of(data.get("runes")),
        "spells": _ids(_first_entry(data.get("summoner_spells"))),
        "skills": _texts(data.get("skills"), "order"),
        "skill_max": _texts(data.get("skill_masteries"), "ids"),
    }


class Opgg(CachedSource):
    name = "opgg"
    default_lane = "opgg-mcp"
    kind_lanes = dict.fromkeys(WEB_KINDS, "opgg-web")  # lo que sale de la web; lo demás, del MCP

    def __init__(self, db: Database, dd: DDragon, enabled: bool = True, warmer=None) -> None:
        super().__init__(db, enabled=enabled, warmer=warmer)
        self.dd = dd
        self._local = threading.local()
        self._web_throttle = Throttle(3.0)
        self._mcp_throttle = Throttle(4.0)
        self._mcp_lock = threading.Lock()
        self._mcp_session: str | None = None
        self._ids = 0
        self.stats.update({"web_requests": 0, "mcp_requests": 0})
        self.sync_state: dict = {"status": "idle"}

    def _http(self) -> requests.Session:
        session = getattr(self._local, "session", None)
        if session is None:
            session = requests.Session()
            session.headers["User-Agent"] = USER_AGENT
            self._local.session = session
        return session

    # --------------------------------------------------------------- web
    def _page(self, path: str, params: dict) -> str:
        self._web_throttle.wait()
        self.stats["web_requests"] += 1
        response = self._http().get(f"{WEB}{path}", params=params, timeout=20)
        if response.status_code != 200:
            self.trip(response.status_code, response.headers.get("Retry-After"))
            raise OpggError(f"HTTP {response.status_code} en {path}")
        return rsc_payload(response.text)

    def versions(self, allow_fetch: bool = True) -> list[str]:
        """Parches con datos en OP.GG, del más reciente al más antiguo."""

        def fetch():
            payload = self._page("/lol/champions", {"position": "top"})
            # Según la página aparece como lista literal o como referencia RSC.
            for pattern in (r'"championVersions":(\[[^\]]*\])', r'"versions":(\[[^\]]*\])'):
                match = re.search(pattern, payload)
                if match:
                    found = [v for v in json.loads(match.group(1)) if re.fullmatch(r"\d+\.\d+", str(v))]
                    if found:
                        return sorted(found, key=lambda v: tuple(int(x) for x in v.split(".")), reverse=True)
            latest = re.search(r'"metaLatestVersion":"(\d+\.\d+)"', payload)
            return [latest.group(1)] if latest else None

        # Se cachea en memoria un rato para no repetir la consulta en cada análisis.
        cached = getattr(self, "_versions_memo", None)
        if cached and time.monotonic() - cached[0] < 300:
            return cached[1]
        value = self._cached("versions", "lol", TTL_VERSIONS, fetch, allow_fetch) or []
        if value:
            self._versions_memo = (time.monotonic(), value)
        return value

    def _ttl(self, patch: str) -> int:
        versions = self.versions(allow_fetch=False)
        return TTL_LATEST if not versions or patch == versions[0] else TTL_OLD

    def tier_list(self, role: str, tier: str, region: str, patch: str, allow_fetch: bool = True) -> dict[int, dict] | None:
        """{champ_id: {wr, pick, ban, role_rate, tier, rank, counters}} para una línea."""
        position = POSITIONS[role]
        key = f"{position}|{tier}|{region}|{patch}"

        def fetch():
            payload = self._page("/lol/champions", {"position": position, "region": region, "tier": tier, "patch": patch})
            applied = re.search(r'"tier":"([a-z_]+)","position":"' + re.escape(position) + r'","version":"(\d+\.\d+)"', payload)
            if applied and (applied.group(1) != tier or applied.group(2) != patch):
                # Como en los counters: sin datos de ese parche, OP.GG devuelve el actual sin avisar (pedir 16.10 da 16.23).
                log.debug("OP.GG aplicó %s en vez de %s/%s en la tier list: no se guarda", applied.groups(), tier, patch)
                return None
            return self._tier_rows(payload, role) or None

        data = self._cached("tier", key, self._ttl(patch), fetch, allow_fetch)
        return {int(k): v for k, v in data.items()} if data else None

    def _tier_rows(self, payload: str, role: str) -> dict[str, dict]:
        """Filas de la tier list de una línea sacadas de la página: ``{campeón: {wr, pick, ban, …}}``."""
        out = {}
        for obj in iter_objects(payload, '{"key":"'):
            if "positionWinRate" not in obj:
                continue
            champ = self.dd.resolve(obj.get("key")) or self.dd.resolve(obj.get("name"))
            if not champ or POSITION_NAMES.get(str(obj.get("positionName", "")).lower(), role) != role:
                continue
            out[str(champ)] = {
                "wr": obj.get("positionWinRate"),
                "pick": obj.get("positionPickRate"),
                "ban": obj.get("positionBanRate"),
                "role_rate": obj.get("positionRoleRate"),
                "tier": obj.get("positionTier"),
                "rank": obj.get("positionRank"),
                "counters": [
                    {"champ": self.dd.resolve(c.get("key")) or c.get("champion_id"), "games": c.get("play"), "wins": c.get("win")}
                    for c in obj.get("positionCounters") or []
                ],
            }
        return out

    def counters(self, champ: int, role: str, tier: str, region: str, patch: str, allow_fetch: bool = True) -> dict[int, tuple[int, int]] | None:
        """{rival: (partidas, victorias del campeón)} — perspectiva de ``champ``."""
        position = POSITIONS[role]
        slug = self.dd.key(champ).lower()
        if not slug:
            return None
        key = f"{champ}|{position}|{tier}|{region}|{patch}"

        def fetch():
            payload = self._page(
                f"/lol/champions/{slug}/counters/{position}",
                {"region": region, "type": "ranked", "tier": tier, "patch": patch},
            )
            applied = re.search(r'"game_tier":"([^"]+)","game_patch_version":"([^"]+)"', payload)
            if not applied:
                return None  # sin la marca de elo/parche (página de error o cambio de formato): no se guarda nada
            if applied.group(1) != tier or applied.group(2) != patch:
                # OP.GG devuelve en silencio otro elo/parche cuando no tiene el pedido (p. ej. un parche antiguo → el
                # actual): guardarlo con la clave pedida mostraría datos de otro parche como si fueran de ese.
                log.debug("OP.GG aplicó %s en vez de %s/%s: no se guarda", applied.groups(), tier, patch)
                return None
            return self._counter_rows(payload, champ)

        data = self._cached("counters", key, self._ttl(patch), fetch, allow_fetch)
        if data is None:
            return None
        return {int(k): (v[0], v[1]) for k, v in data.items()}

    def _counter_rows(self, payload: str, champ: int) -> dict[str, list[int]]:
        """``{rival: [partidas, victorias de champ]}`` de la página de counters (cuenta la primera fila de cada rival)."""
        out: dict[str, list[int]] = {}
        for match in re.finditer(r'\{"play":(\d+),"win":(\d+),"win_rate":[\d.]+,"champion":(\{[^{}]*\})\}', payload):
            try:
                info = json.loads(match.group(3))
            except ValueError:
                continue
            opp = self.dd.resolve(info.get("key")) or self.dd.resolve(info.get("name"))
            if opp and opp != champ and str(opp) not in out:
                out[str(opp)] = [int(match.group(1)), int(match.group(2))]
        return out

    # --------------------------------------------------------------- MCP
    def _mcp_post(self, payload: dict, session: str | None):
        headers = {"Content-Type": "application/json", "Accept": "application/json, text/event-stream"}
        if session:
            headers["Mcp-Session-Id"] = session
        response = self._http().post(MCP_URL, headers=headers, json=payload, timeout=45)
        if response.status_code >= 400:
            self.trip(response.status_code, response.headers.get("Retry-After"))
            raise OpggError(f"MCP HTTP {response.status_code}")
        body = response.text.strip()
        if not body:
            return response, None
        data_lines = [line[5:].strip() for line in body.splitlines() if line.startswith("data:")]
        return response, json.loads(data_lines[-1] if data_lines else body)

    def _mcp_session_id(self) -> str:
        with self._mcp_lock:
            if self._mcp_session:
                return self._mcp_session
            self._ids += 1
            response, _ = self._mcp_post(
                {
                    "jsonrpc": "2.0",
                    "id": self._ids,
                    "method": "initialize",
                    "params": {"protocolVersion": "2025-06-18", "capabilities": {}, "clientInfo": {"name": "TempuDrafter", "version": __version__}},
                },
                None,
            )
            session = response.headers.get("mcp-session-id") or ""
            self._mcp_post({"jsonrpc": "2.0", "method": "notifications/initialized"}, session)
            self._mcp_session = session
            return session

    def mcp_call(self, tool: str, arguments: dict):
        for attempt in range(2):
            session = self._mcp_session_id()
            self._mcp_throttle.wait()
            self.stats["mcp_requests"] += 1
            with self._mcp_lock:
                self._ids += 1
                request_id = self._ids
            try:
                _, data = self._mcp_post(
                    {
                        "jsonrpc": "2.0",
                        "id": request_id,
                        "method": "tools/call",
                        "params": {"name": tool, "arguments": {**arguments, "client_app": CLIENT_APP}},
                    },
                    session,
                )
            except OpggError:
                if attempt == 0:  # sesión caducada: se renueva una vez
                    with self._mcp_lock:
                        self._mcp_session = None
                    continue
                raise
            return self._mcp_result(data)
        raise OpggError("No se pudo abrir sesión con el MCP de OP.GG")

    @staticmethod
    def _mcp_result(data):
        """Valor que devuelve una herramienta del MCP (``OpggError`` si responde con un error o vacío)."""
        if not data or "error" in data:
            raise OpggError((data or {}).get("error", {}).get("message", "respuesta vacía"))
        content = (data.get("result") or {}).get("content") or []
        texts = [c.get("text", "") for c in content if c.get("type") == "text"]
        if data.get("result", {}).get("isError"):
            raise OpggError(" ".join(texts)[:200] or "error del MCP")
        return parse_mcp_text("\n".join(texts)) if texts else None

    def mcp_name(self, champ: int) -> str:
        """Nombre que espera el MCP: inglés en mayúsculas con guiones bajos (``LEE_SIN``)."""
        names = self._cached("mcp_champions", "en_US", 7 * 86400, self._fetch_mcp_names, True) or {}
        english = names.get(str(champ))
        if not english:  # sin lista: aproximación desde la key de Data Dragon
            english = re.sub(r"(?<=[a-z])(?=[A-Z])", " ", self.dd.key(champ) or "")
        return re.sub(r"\s+", "_", re.sub(r"[^A-Za-z0-9 ]", "", english).strip()).upper()

    def _fetch_mcp_names(self) -> dict | None:
        raw = self.mcp_call("lol_list_champions", {"lang": "en_US"})
        champions = ((raw or {}).get("data") or {}).get("champions") or []
        out = {str(c["champion_id"]): c.get("name") for c in champions if c.get("champion_id") and c.get("name")}
        return out or None

    def rune_page(self, champ: int, role: str, allow_fetch: bool = True) -> dict | None:
        """Página de runas más jugada con sus fragmentos (para importarla al cliente). Solo pide las runas."""
        key = f"{champ}|{POSITIONS[role]}|runes"

        def fetch():
            raw = self.mcp_call(
                "lol_get_champion_analysis",
                {"game_mode": "ranked", "champion": self.mcp_name(champ), "position": POSITIONS[role], "lang": "en_US",
                 "desired_output_fields": ["data.runes"]},
            )
            return _runes_of(((raw or {}).get("data") or {}).get("runes"))

        return self._cached("build", key, TTL_MCP, fetch, allow_fetch)

    def matchup_guide(self, champ: int, opp: int, role: str, lang: str = "es_ES", allow_fetch: bool = True) -> dict | None:
        """Consejo, ventaja de línea, build y curva por duración contra un rival."""
        key = f"{champ}|{opp}|{POSITIONS[role]}|{lang}"

        def fetch():
            raw = self.mcp_call(
                "lol_get_lane_matchup_guide",
                {
                    "position": POSITIONS[role],
                    "my_champion": self.mcp_name(champ),
                    "opponent_champion": self.mcp_name(opp),
                    "lang": lang,
                },
            )
            data = (raw or {}).get("data") or {}
            if not data:
                return None

            def items(entries, limit=3):
                return [
                    {"ids": e.get("ids"), "names": e.get("ids_names"), "games": e.get("play"), "wins": e.get("win"), "pick_rate": e.get("pick_rate")}
                    for e in (entries or [])[:limit]
                ]

            runes = []
            for page in (data.get("runes") or [])[:2]:
                runes.append(
                    {
                        "keystone": page.get("id"),
                        "primary": page.get("primary_page_id"),
                        "secondary": page.get("secondary_page_id"),
                        "ids": (page.get("primary_rune_ids") or []) + (page.get("secondary_rune_ids") or []),
                        "names": (page.get("primary_rune_names") or []) + (page.get("secondary_rune_names") or []),
                        "shards": _shards(page),
                        "games": page.get("play"),
                        "wins": page.get("win"),
                        "pick_rate": page.get("pick_rate"),
                    }
                )
            return {
                "tip": data.get("opponent_champion_tip"),
                "lane_advantage": self.dd.resolve(data.get("lane_advantage_champion")),
                "solo_kill_advantage": self.dd.resolve(data.get("lane_solo_kill_advantage_champion")),
                "play_style": data.get("recommended_play_style"),
                "core_items": items(data.get("core_items")),
                "boots": items(data.get("boots"), 2),
                "starter_items": items(data.get("starter_items"), 2),
                "spells": items(data.get("summoner_spells"), 2),
                "skills": [{"order": s.get("order"), "games": s.get("play"), "wins": s.get("win")} for s in (data.get("skills") or [])[:1]],
                "runes": runes,
                "game_lengths": [{"minute": g.get("game_length"), "wr": g.get("rate")} for g in data.get("game_lengths") or []],
            }

        return self._cached("guide", key, TTL_MCP, fetch, allow_fetch)

    def champion_build(self, champ: int, role: str, allow_fetch: bool = True) -> dict | None:
        """Build más jugada de un campeón en su rol: objetos clave, botas, inicial, opciones del 4.º al 6.º objeto
        (los situacionales), runas, hechizos y orden de habilidades."""
        key = f"{champ}|{POSITIONS[role]}|v2"  # v2: con 5.º y 6.º objeto, runas y hechizos

        def fetch():
            raw = self.mcp_call(
                "lol_get_champion_analysis",
                {
                    "game_mode": "ranked",
                    "champion": self.mcp_name(champ),
                    "position": POSITIONS[role],
                    "lang": "en_US",
                    "desired_output_fields": [
                        "data.core_items", "data.boots", "data.starter_items", "data.last_items",
                        "data.fourth_items", "data.fifth_items", "data.sixth_items", "data.skills", "data.skill_masteries",
                        "data.runes", "data.summoner_spells",
                    ],
                },
            )
            data = (raw or {}).get("data") or {}
            return _build_of(data) if data else None

        return self._cached("build", key, TTL_MCP, fetch, allow_fetch)

    def synergies(self, champ: int, role: str, partner_role: str, allow_fetch: bool = True) -> dict[int, tuple[int, int]] | None:
        key = f"{champ}|{POSITIONS[role]}|{POSITIONS[partner_role]}"

        def fetch():
            raw = self.mcp_call(
                "lol_get_champion_synergies",
                {"champion": self.mcp_name(champ), "my_position": POSITIONS[role], "synergy_position": POSITIONS[partner_role], "lang": "en_US"},
            )
            entries = (((raw or {}).get("data") or {}).get("synergies")) or []
            out = {}
            for e in entries:
                partner = e.get("synergy_champion_id")
                if partner:
                    out[str(partner)] = [int(e.get("play") or 0), int(e.get("win") or 0)]
            return out

        data = self._cached("synergy", key, TTL_MCP, fetch, allow_fetch)
        return {int(k): (v[0], v[1]) for k, v in data.items()} if data else None

    def champion_info(self, champ: int, role: str, allow_fetch: bool = True) -> dict | None:
        key = f"{champ}|{POSITIONS[role]}"

        def fetch():
            raw = self.mcp_call(
                "lol_get_champion_analysis",
                {
                    "game_mode": "ranked",
                    "champion": self.mcp_name(champ),
                    "position": POSITIONS[role],
                    "lang": "en_US",
                    "desired_output_fields": ["data.summary", "data.damage_type"],
                },
            )
            data = (raw or {}).get("data") or {}
            return {"damage_type": data.get("damage_type")} if data else None

        return self._cached("champion", key, 7 * 86400, fetch, allow_fetch)

    # ------------------------------------------------------- precarga/sync
    def prefetch(self, jobs: list, timeout: float = 15.0, workers: int = 6) -> int:
        """Ejecuta en paralelo funciones de descarga (lambdas). Devuelve las completadas.

        Atendiendo una petición de la web nunca espera más que su presupuesto: lo
        que no termine a tiempo sigue en segundo plano (o se encarga al
        precargador si ni siquiera empezó) y la respuesta se marca como pendiente.
        """
        if not jobs:
            return 0
        left = budget.remaining()
        if left is not None:
            timeout = min(timeout, left)
        pool = ThreadPoolExecutor(max_workers=workers, thread_name_prefix="opgg")
        futures = [pool.submit(contextvars.copy_context().run, job) for job in jobs]
        done, not_done = wait(futures, timeout=max(0.0, timeout))
        pool.shutdown(wait=False, cancel_futures=True)
        if not_done:
            budget.mark_pending("opgg")
            if self.warmer is not None:
                for future, job in zip(futures, jobs):
                    if future.cancelled():
                        self.warmer.defer(job)
        return len(done)

    def sync(self, tier: str, region: str, patches: list[str], min_role_rate: float = 0.08, stop: threading.Event | None = None) -> dict:
        """Descarga tier lists y counters de todos los campeones (en segundo plano)."""
        state = self.sync_state = {"status": "running", "done": 0, "total": 0, "started_at": time.time(), "tier": tier, "region": region, "patches": patches}
        try:
            for patch in patches:
                for role in POSITIONS:
                    self.tier_list(role, tier, region, patch)
            work = self._sync_work(tier, region, patches, min_role_rate)
            state["total"] = len(work)
            for champ, role, patch in work:
                if stop is not None and stop.is_set():
                    state["status"] = "stopped"
                    return state
                self.counters(champ, role, tier, region, patch)
                state["done"] += 1
            state["status"] = "done"
        except Exception as exc:
            state["status"] = "error"
            state["error"] = str(exc)
            log.exception("Fallo sincronizando OP.GG")
        state["finished_at"] = time.time()
        log.info("Sincronización de OP.GG: %d/%d páginas.", state.get("done", 0), state.get("total", 0))
        return state

    def _sync_work(self, tier: str, region: str, patches: list[str], min_role_rate: float) -> list[tuple[int, str, str]]:
        """``(campeón, línea, parche)`` de los counters que hay que descargar (de las tier lists ya guardadas)."""
        work = []
        for patch in patches:
            for role in POSITIONS:
                for champ, row in (self.tier_list(role, tier, region, patch, allow_fetch=False) or {}).items():
                    if (row.get("role_rate") or 0) >= min_role_rate:
                        work.append((champ, role, patch))
        return work
