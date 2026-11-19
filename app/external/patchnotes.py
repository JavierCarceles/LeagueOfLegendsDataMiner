"""Notas de parche oficiales de Riot (leagueoflegends.com).

Su ``robots.txt`` permite todo (``Allow: /``). De cada versión se extraen los
cambios de campeones y objetos:

* contexto (el texto con el que Riot explica el cambio),
* bloques de cambios (estadísticas básicas, pasiva, Q/W/E/R…) con sus líneas
  ``valor anterior ⇒ valor nuevo``,
* una estimación de si es una **mejora**, una **debilitación** o un **ajuste**
  sacada de los números: subir un daño es mejor, subir un enfriamiento o un
  coste es peor. Solo se dice «mejora»/«debilitación» cuando casi todos los
  cambios apuntan en la misma dirección; si no, «ajustes».

Se parsea con ``html.parser`` de la biblioteca estándar (sin dependencias).
"""

from __future__ import annotations

import html
import re
import threading
from html.parser import HTMLParser

import requests

from app import __version__
from app.data.db import Database
from app.ddragon import DDragon
from app.external.base import CachedSource, Throttle
from app.logbus import get_logger

log = get_logger("parches")

SITE = "https://www.leagueoflegends.com"
USER_AGENT = f"TempuDrafter/{__version__} (personal stats tool)"
TTL_INDEX = 6 * 3600
TTL_NOTES = 3 * 86400  # Riot a veces corrige las notas los primeros días
LOCALES = {"es_ES": "es-es", "es_MX": "es-mx", "en_US": "en-us", "en_GB": "en-gb", "fr_FR": "fr-fr", "de_DE": "de-de", "it_IT": "it-it", "pt_BR": "pt-br"}
_SLUG = re.compile(r"league-of-legends-patch-(\d+)-(\d+)-notes")
_CHAMP_IMG = re.compile(r"/img/champion/([A-Za-z0-9]+)\.png")
_ITEM_IMG = re.compile(r"/img/item/(\d+)\.png")
_NUM = re.compile(r"\d+(?:[.,]\d+)?")  # sin signo: «1150-3500» es un rango
RIFT_SECTIONS = re.compile(r"^(campeones|champions|objetos|items|runas|runes|hechizos de invocador|summoner spells)$", re.I)
# En estas estadísticas un valor más bajo es mejor para el campeón.
LOWER_IS_BETTER = re.compile(
    r"enfriamiento|recarga|coste|maná|mana|energía|tiempo de lanzamiento|retraso|cooldown|cost\b|cast time|delay|"
    r"tiempo de reaparición|vulnerab",
    re.I,
)


def patch_label(slug_major: str, slug_minor: str) -> str:
    return f"{int(slug_major)}.{int(slug_minor)}"


def _numbers(text: str) -> list[float]:
    out = []
    for match in _NUM.finditer(text or ""):
        try:
            out.append(float(match.group(0).replace(",", ".")))
        except ValueError:
            continue
    return out


def change_direction(line: str) -> int:
    """+1 si el cambio favorece al campeón/objeto, -1 si lo empeora, 0 si no se sabe.

    Compara número a número (``80/120/160 ⇒ 80/125/170`` es una mejora aunque el
    primero no cambie) y da la vuelta al resultado en enfriamientos, costes, etc.
    """
    if "⇒" not in line:
        return 0
    label, sep, values = line.partition(":")
    if not sep:
        values, label = label, ""
    before, _, after = values.partition("⇒")
    a = _numbers(before)
    b = _numbers(after)
    if not a or not b:
        return 0
    score = 0
    for x, y in zip(a, b):
        if y > x:
            score += 1
        elif y < x:
            score -= 1
    if score == 0:
        return 0
    better = score > 0
    if LOWER_IS_BETTER.search(label):
        better = not better
    return 1 if better else -1


def verdict(lines: list[str]) -> str:
    ups = downs = 0
    for line in lines:
        d = change_direction(line)
        if d > 0:
            ups += 1
        elif d < 0:
            downs += 1
    if ups and downs == 0:
        return "buff"
    if downs and ups == 0:
        return "nerf"
    if ups >= 3 * downs and ups >= 3:
        return "buff"
    if downs >= 3 * ups and downs >= 3:
        return "nerf"
    return "adjust"


class _NotesParser(HTMLParser):
    """Recorre la página y agrupa los bloques de cambios bajo cada sección (h2)."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.section = ""
        self.blocks: list[dict] = []
        self.current: dict | None = None
        self._capture: str | None = None  # "h2" | "h3" | "h4" | "li" | "context"
        self._buf: list[str] = []
        self._in_context = False
        self._last_img: str | None = None
        self.title = ""
        self._in_title = False

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        cls = a.get("class") or ""
        if tag == "title":
            self._in_title = True
        if tag == "img" and a.get("src"):
            self._last_img = a["src"]
        if tag == "h2":
            self._start("h2")
        elif tag == "h3" and "change-title" in cls:
            self._start("h3")
            self.current = {"section": self.section, "name": "", "img": self._last_img, "context": "", "groups": []}
            self.blocks.append(self.current)
        elif tag == "h4" and "change-detail-title" in cls and self.current is not None:
            self._start("h4")
        elif tag == "blockquote" and "context" in cls and self.current is not None:
            self._in_context = True
            self._start("context")
        elif tag == "li" and self.current is not None and not self._in_context:
            self._start("li")

    def handle_endtag(self, tag):
        if tag == "title":
            self._in_title = False
        if tag == "h2" and self._capture == "h2":
            self.section = self._end()
        elif tag == "h3" and self._capture == "h3" and self.current is not None:
            self.current["name"] = self._end()
        elif tag == "h4" and self._capture == "h4" and self.current is not None:
            self.current["groups"].append({"title": self._end(), "lines": []})
        elif tag == "blockquote" and self._in_context and self.current is not None:
            self._in_context = False
            self.current["context"] = self._end()
        elif tag == "li" and self._capture == "li" and self.current is not None:
            text = self._end()
            if text:
                if not self.current["groups"]:
                    self.current["groups"].append({"title": "", "lines": []})
                self.current["groups"][-1]["lines"].append(text)

    def handle_data(self, data):
        if self._in_title:
            self.title += data
        if self._capture:
            self._buf.append(data)

    def _start(self, kind: str) -> None:
        self._capture = kind
        self._buf = []

    def _end(self) -> str:
        text = re.sub(r"\s+", " ", html.unescape("".join(self._buf))).strip()
        self._capture = None
        self._buf = []
        return text


def parse_notes(page: str, dd: DDragon | None = None) -> dict:
    parser = _NotesParser()
    parser.feed(page)
    champions: dict[str, dict] = {}
    items: dict[str, dict] = {}
    for block in parser.blocks:
        section = (block.get("section") or "").strip().lower()
        if section and not RIFT_SECTIONS.search(section):
            continue  # ARAM, Arena y otros modos tienen sus propios ajustes
        groups = [g for g in block["groups"] if g["lines"]]
        lines = [line for g in groups for line in g["lines"]]
        if not lines and not block["context"]:
            continue
        entry = {
            "name": block["name"],
            "context": block["context"][:900],
            "groups": [{"title": g["title"], "lines": g["lines"][:25]} for g in groups][:12],
            "verdict": verdict(lines),
        }
        img = block.get("img") or ""
        champ_key = _CHAMP_IMG.search(img)
        item_id = _ITEM_IMG.search(img)
        if champ_key and dd is not None:
            champ = dd.resolve(champ_key.group(1)) or dd.resolve(block["name"])
            if champ:
                champions[str(champ)] = entry
                continue
        if item_id:
            items[item_id.group(1)] = entry
        elif dd is not None and dd.resolve(block["name"]):
            champions[str(dd.resolve(block["name"]))] = entry
    title = re.sub(r"\s*\|.*$", "", parser.title).strip()
    return {"title": title, "champions": champions, "items": items}


class PatchNotes(CachedSource):
    name = "patchnotes"
    default_lane = "riot-web"

    def __init__(self, db: Database, dd: DDragon, enabled: bool = True, warmer=None, lang: str = "es_ES") -> None:
        super().__init__(db, enabled=enabled, warmer=warmer)
        self.dd = dd
        self.locale = LOCALES.get(lang, "en-us")
        self._local = threading.local()
        self._throttle = Throttle(1.0)

    def _http(self) -> requests.Session:
        session = getattr(self._local, "session", None)
        if session is None:
            session = requests.Session()
            session.headers["User-Agent"] = USER_AGENT
            self._local.session = session
        return session

    def _page(self, path: str) -> str:
        self._throttle.wait()
        response = self._http().get(f"{SITE}{path}", timeout=20)
        if response.status_code != 200:
            raise RuntimeError(f"HTTP {response.status_code} en {path}")
        response.encoding = "utf-8"
        return response.text

    def index(self, allow_fetch: bool = True) -> list[dict]:
        """Versiones con notas publicadas, de la más reciente a la más antigua."""

        def fetch():
            page = self._page(f"/{self.locale}/news/tags/patch-notes/")
            found: dict[str, dict] = {}
            for match in _SLUG.finditer(page):
                label = patch_label(match.group(1), match.group(2))
                found.setdefault(label, {"patch": label, "path": f"/{self.locale}/news/game-updates/{match.group(0)}/"})
            out = sorted(found.values(), key=lambda x: tuple(int(p) for p in x["patch"].split(".")), reverse=True)
            return out[:12] or None

        return self._cached("patch_index", self.locale, TTL_INDEX, fetch, allow_fetch) or []

    def notes(self, patch: str, allow_fetch: bool = True) -> dict | None:
        entry = next((x for x in self.index(allow_fetch=allow_fetch) if x["patch"] == patch), None)
        if entry is None:
            return None
        key = f"{self.locale}|{patch}"

        def fetch():
            page = self._page(entry["path"])
            data = parse_notes(page, self.dd)
            if not data["champions"] and not data["items"]:
                return None
            return {**data, "patch": patch, "url": f"{SITE}{entry['path']}"}

        return self._cached("patch_notes", key, TTL_NOTES, fetch, allow_fetch)

    def recent(self, count: int = 3, allow_fetch: bool = True) -> list[dict]:
        out = []
        for entry in self.index(allow_fetch=allow_fetch)[:count]:
            data = self.notes(entry["patch"], allow_fetch=allow_fetch)
            if data:
                out.append(data)
        return out

    def champion_changes(self, champ: int, count: int = 3, allow_fetch: bool = False) -> list[dict]:
        """Cambios de un campeón en las últimas ``count`` versiones (de la más reciente a la más antigua)."""
        out = []
        for data in self.recent(count, allow_fetch=allow_fetch):
            change = (data.get("champions") or {}).get(str(champ))
            if change:
                out.append({"patch": data["patch"], "url": data.get("url"), **change})
        return out

    def latest_verdicts(self, allow_fetch: bool = False) -> dict:
        """{patch, champions: {id: buff|nerf|adjust}} de la última versión con notas."""
        recent = self.recent(1, allow_fetch=allow_fetch)
        if not recent:
            return {"patch": None, "champions": {}}
        data = recent[0]
        return {"patch": data["patch"], "url": data.get("url"), "champions": {k: v["verdict"] for k, v in (data.get("champions") or {}).items()}}
