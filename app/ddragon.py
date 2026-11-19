"""Data Dragon: versión actual, campeones, objetos, hechizos y runas.

Se descarga al arrancar (en segundo plano) y se cachea en ``data/ddragon``
para funcionar sin conexión. Sustituye a la lista de campeones escrita a
mano y a la versión fija ``14.15.1`` que usaba el frontend.
"""

from __future__ import annotations

import json
import re
import threading
from pathlib import Path

import requests

from app.coach.items import ItemCatalog
from app.logbus import get_logger

log = get_logger("ddragon")
CDN = "https://ddragon.leagueoflegends.com"

# Alias habituales (inglés / apodos) que no salen de id ni del nombre localizado.
EXTRA_ALIASES = {
    "wukong": "MonkeyKing",
    "monkeyking": "MonkeyKing",
    "nunu": "Nunu",
    "nunuwillump": "Nunu",
    "renataglasc": "Renata",
    "masteryi": "MasterYi",
    "yi": "MasterYi",
    "mf": "MissFortune",
    "tf": "TwistedFate",
    "j4": "JarvanIV",
    "asol": "AurelionSol",
    "fiddle": "Fiddlesticks",
    "heimer": "Heimerdinger",
    "mundo": "DrMundo",
    "cait": "Caitlyn",
    "kogmaw": "KogMaw",
    "jadeezreal": "Ezreal",
}


def norm(text: str) -> str:
    return re.sub(r"[^a-z0-9]", "", str(text or "").lower())


class DDragon:
    def __init__(self, cache_dir: Path, lang: str = "es_ES") -> None:
        self.cache_dir = Path(cache_dir)
        self.lang = lang
        self.version: str | None = None
        self.champions: dict[int, dict] = {}
        self.items: dict[int, str] = {}
        self.item_catalog = ItemCatalog({})
        self.spells: dict[int, dict] = {}
        self.runes: dict[int, dict] = {}
        self._by_norm: dict[str, int] = {}
        self._by_key: dict[str, int] = {}
        self.ready = threading.Event()
        self._lock = threading.Lock()

    # ------------------------------------------------------------------ carga
    def load(self, allow_network: bool = True) -> bool:
        version = None
        if allow_network:
            try:
                versions = requests.get(f"{CDN}/api/versions.json", timeout=8).json()
                version = versions[0]
            except Exception as exc:  # sin red: se usa la caché
                log.warning("No se pudo consultar Data Dragon (%s). Uso la caché local.", exc)
        if version is None:
            version = self._latest_cached()
        if version is None:
            log.error("Data Dragon no disponible y sin caché: los nombres de campeones no se resolverán.")
            return False
        try:
            champions = self._fetch(version, f"data/{self.lang}/champion.json", allow_network)
            items = self._fetch(version, f"data/{self.lang}/item.json", allow_network, required=False)
            spells = self._fetch(version, f"data/{self.lang}/summoner.json", allow_network, required=False)
            runes = self._fetch(version, f"data/{self.lang}/runesReforged.json", allow_network, required=False)
        except Exception as exc:
            log.error("Error cargando Data Dragon %s: %s", version, exc)
            return False
        self._index(version, champions, items, spells, runes)
        log.info("Data Dragon %s cargado (%d campeones).", version, len(self.champions))
        self.ready.set()
        return True

    def _latest_cached(self) -> str | None:
        if not self.cache_dir.exists():
            return None
        versions = [p.name for p in self.cache_dir.iterdir() if p.is_dir() and (p / "champion.json").exists()]
        if not versions:
            return None
        return max(versions, key=lambda v: [int(x) if x.isdigit() else 0 for x in v.split(".")])

    def _fetch(self, version: str, path: str, allow_network: bool, required: bool = True):
        local = self.cache_dir / version / Path(path).name
        if local.exists():
            return json.loads(local.read_text(encoding="utf-8"))
        if not allow_network:
            if required:
                raise FileNotFoundError(local)
            return None
        try:
            response = requests.get(f"{CDN}/cdn/{version}/{path}", timeout=20)
            response.raise_for_status()
            data = response.json()
        except Exception:
            if required:
                raise
            return None
        local.parent.mkdir(parents=True, exist_ok=True)
        local.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
        return data

    def _index(self, version, champions, items, spells, runes) -> None:
        by_id: dict[int, dict] = {}
        by_norm: dict[str, int] = {}
        by_key: dict[str, int] = {}
        for champ in (champions or {}).get("data", {}).values():
            cid = int(champ["key"])
            by_id[cid] = {
                "id": cid,
                "key": champ["id"],
                "name": champ["name"],
                "title": champ.get("title", ""),
                "tags": champ.get("tags", []),
                "info": champ.get("info", {}),
            }
            by_key[champ["id"]] = cid
            by_norm[norm(champ["id"])] = cid
            by_norm[norm(champ["name"])] = cid
        for alias, key in EXTRA_ALIASES.items():
            if key in by_key:
                by_norm.setdefault(alias, by_key[key])

        item_map = {int(k): v.get("name", "") for k, v in ((items or {}).get("data") or {}).items() if k.isdigit()}
        catalog = ItemCatalog.from_ddragon(items)
        spell_map = {
            int(v["key"]): {"key": v["id"], "name": v.get("name", "")}
            for v in ((spells or {}).get("data") or {}).values()
            if str(v.get("key", "")).isdigit()
        }
        rune_map: dict[int, dict] = {}
        for style in runes or []:
            rune_map[int(style["id"])] = {"name": style.get("name", ""), "icon": style.get("icon", "")}
            for slot in style.get("slots", []):
                for rune in slot.get("runes", []):
                    rune_map[int(rune["id"])] = {"name": rune.get("name", ""), "icon": rune.get("icon", "")}

        with self._lock:
            self.version = version
            self.champions = by_id
            self._by_norm = by_norm
            self._by_key = by_key
            self.items = item_map
            self.item_catalog = catalog
            self.spells = spell_map
            self.runes = rune_map

    # -------------------------------------------------------------- consultas
    def resolve(self, value) -> int | None:
        """Acepta id numérico, key de DDragon ('MonkeyKing') o nombre libre ('wukong')."""
        if value is None or value == "":
            return None
        if isinstance(value, int):
            return value if value in self.champions else None
        text = str(value).strip()
        if text.isdigit():
            cid = int(text)
            return cid if cid in self.champions else None
        if text in self._by_key:
            return self._by_key[text]
        return self._by_norm.get(norm(text))

    def name(self, champ_id: int | None) -> str:
        champ = self.champions.get(champ_id or -1)
        return champ["name"] if champ else f"#{champ_id}"

    def key(self, champ_id: int | None) -> str:
        champ = self.champions.get(champ_id or -1)
        return champ["key"] if champ else ""

    def public(self) -> dict:
        with self._lock:
            return {
                "version": self.version,
                "lang": self.lang,
                "champions": sorted(self.champions.values(), key=lambda c: c["name"]),
                "spells": {str(k): v for k, v in self.spells.items()},
                "runes": {str(k): v for k, v in self.runes.items()},
                "items": {str(k): v for k, v in self.items.items()},
            }
