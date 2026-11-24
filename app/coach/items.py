"""Objetos de la Grieta (Data Dragon) e inventario reconstruido con el timeline.

El timeline de match-v5 trae cada compra, venta, «deshacer» y objeto gastado
(``ITEM_PURCHASED``, ``ITEM_SOLD``, ``ITEM_UNDO``, ``ITEM_DESTROYED``). Con el
árbol de recetas de Data Dragon se sabe en cada momento qué llevabas, cuántos
huecos libres tenías y qué podías comprar con tu oro para avanzar en tu build:

* Con hueco libre puedes comprar cualquier componente de tu siguiente objeto.
* Con el inventario lleno solo puedes comprar algo que **combine** componentes
  que ya llevas, o el objeto completo. Si no te llega, ese oro no está
  «parado»: es el ahorro para el siguiente objeto.
"""

from __future__ import annotations

import bisect
import math
import re
from collections import Counter

SLOTS = 6
SR = "11"

# Transformaciones sin evento de compra (el objeto viejo sale como ITEM_DESTROYED).
TRANSFORMS = {
    3865: 3866,  # Atlas mundial -> Brújula rúnica (misión de support)
    3866: 3867,  # Brújula rúnica -> Botín universal
    3867: 3867,  # Botín universal -> su mejora final (sin evento: se cuenta como el mismo hueco)
    3004: 3042,  # Manamune -> Muramana
    3003: 3040,  # Bastón del arcángel -> Abrazo de serafín
    3119: 3121,  # Llegada del invierno -> Fimbulvetr
}
# Objetos que llegan sin evento de compra y ocupan hueco: solo se ven cuando se gastan (ITEM_DESTROYED). Se dan por
# llevados desde lo antes que pueden llegar (lo prudente: así nunca se dice «tenías hueco» si quizá no lo había).
RECEIVED_FREE = {
    2010: 2 * 60_000,  # Galletas de la runa «Entrega de galletas»
    2422: 6 * 60_000,  # Calzado ligeramente mágico (runa «Calzado mágico»: a los 12 min o antes con derribos)
}
# Objetos con Heridas graves si la descripción no lo dice (nombres cambian por idioma).
ANTI_HEAL_FALLBACK = {3033, 3123, 3165, 3916, 3075, 3076, 6609, 3011}
_GW = re.compile(r"heridas graves|grievous wounds|blessure grave|ferite gravi", re.IGNORECASE)


def _ids(values) -> list[int]:
    out = []
    for v in values or []:
        try:
            out.append(int(v))
        except (TypeError, ValueError):
            continue
    return out


_PASSIVE = re.compile(r"<passive>(.*?)</passive>")


def _stack_size(value) -> int:
    try:
        return max(1, int(value or 1))
    except (TypeError, ValueError):
        return 1


def season(version) -> str | None:
    """Temporada de una versión del juego o de Data Dragon («16.22.1» -> «16»)."""
    head = str(version or "").split(".")[0]
    return head if head.isdigit() else None


def _on_rift(key, v: dict) -> bool:
    """¿Es una entrada de ``item.json`` con id numérico que existe en la Grieta (o que no dice en qué mapas está)?"""
    if not str(key).isdigit():
        return False
    maps = v.get("maps") or {}
    return not maps or bool(maps.get(SR, False))


def _item_entry(v: dict) -> dict:
    """Ficha de un objeto de ``item.json``: coste, receta, etiquetas, si se compra, pasivas…"""
    gold = v.get("gold") or {}
    return {
        "name": v.get("name", ""),
        "cost": int(gold.get("total") or 0),
        "sell": int(gold.get("sell") or 0),
        "from": _ids(v.get("from")),
        "into": _ids(v.get("into")),
        "tags": list(v.get("tags") or []),
        "depth": int(v.get("depth") or 1),
        "consumed": bool(v.get("consumed")),
        "stacks": _stack_size(v.get("stacks")),  # pociones (5), pinks (2)… comparten un hueco
        "buyable": bool(gold.get("purchasable", True)) and v.get("inStore", True) is not False and not v.get("requiredAlly"),
        "champion": v.get("requiredChampion") or "",
        "special": int(v.get("specialRecipe") or 0),  # objeto que se transforma en este (Manamune -> Muramana)
        "passives": sorted(set(_PASSIVE.findall(v.get("description") or ""))),  # pasivas únicas por su nombre
        "gw": bool(_GW.search(v.get("description") or "")),
    }


class ItemCatalog:
    """Datos de cada objeto: coste, receta, etiquetas y si es un objeto completo."""

    def __init__(self, items: dict[int, dict], version: str | None = None) -> None:
        self.items = items
        self.version = version

    @classmethod
    def from_ddragon(cls, item_json: dict | None) -> ItemCatalog:
        """Desde ``item.json`` de Data Dragon (``{"data": {"3071": {...}}}``)."""
        version = (item_json or {}).get("version")
        raw = (item_json or {}).get("data") or {}
        items: dict[int, dict] = {}
        for key, v in raw.items():
            if _on_rift(key, v):
                items[int(key)] = _item_entry(v)
        for item in items.values():  # solo recetas que existen en la Grieta
            item["from"] = [c for c in item["from"] if c in items]
            item["into"] = [c for c in item["into"] if c in items and items[c]["buyable"] and not items[c]["champion"]]
        return cls(items, str(version) if version else None)

    def __bool__(self) -> bool:
        return bool(self.items)

    def __contains__(self, item: int) -> bool:
        return item in self.items

    # ------------------------------------------------------------- consultas
    def get(self, item: int) -> dict:
        return self.items.get(item) or {}

    def buyable_form(self, item: int) -> int:
        """El objeto que se compra de verdad: Muramana, Abrazo del serafín o El gran invierno no están en la tienda,
        salen al transformarse Manamune, Bastón del arcángel o Llegada del invierno."""
        it = self.get(item)
        if it and not it.get("buyable", True) and it.get("special") in self.items:
            return it["special"]
        return item

    def name(self, item: int) -> str:
        return self.get(item).get("name") or f"#{item}"

    def cost(self, item: int) -> int:
        return self.get(item).get("cost", 0)

    def tags(self, item: int) -> list[str]:
        return self.get(item).get("tags") or []

    def is_trinket(self, item: int) -> bool:
        return "Trinket" in self.tags(item)

    def is_boots(self, item: int) -> bool:
        return "Boots" in self.tags(item)

    def is_consumable(self, item: int) -> bool:
        return self.get(item).get("consumed", False) or "Consumable" in self.tags(item)

    def is_starter(self, item: int) -> bool:
        """Objetos iniciales: Doran, Sello oscuro, mascotas de jungla, objeto de support."""
        it = self.get(item)
        if not it or self.is_consumable(item) or self.is_boots(item) or it["cost"] > 500:
            return False
        tags = it["tags"]
        return "GoldPer" in tags or item == 1082 or (("Lane" in tags or "Jungle" in tags) and not it["from"] and not it["into"])

    def is_legendary(self, item: int) -> bool:
        it = self.get(item)
        if not it or self.is_boots(item) or self.is_consumable(item) or self.is_trinket(item) or self.is_starter(item):
            return False
        return not it["into"] and it["cost"] >= 1500 and (bool(it["from"]) or it["cost"] >= 2500)

    def is_completed(self, item: int) -> bool:
        """Objeto terminado: legendario o botas mejoradas."""
        return self.is_legendary(item) or (self.is_boots(item) and self.cost(item) >= 700)

    def is_elixir(self, item: int) -> bool:
        """Elixires: se beben al comprarlos (no ocupan hueco)."""
        return self.get(item).get("consumed", False) and self.cost(item) >= 300

    def stacks(self, item: int) -> int:
        """Cuántas unidades caben en un mismo hueco (pociones, pinks, galletas)."""
        return self.get(item).get("stacks", 1) or 1

    def uses_slot(self, item: int) -> bool:
        # Un objeto que el catálogo no conoce (parche más nuevo o retirado) también ocupa hueco:
        # suponer lo contrario inventaría huecos libres que no existían.
        if item not in self.items:
            return bool(item)
        return not self.is_trinket(item) and not self.is_elixir(item)

    def anti_heal(self, item: int) -> bool:
        return self.get(item).get("gw", False) or item in ANTI_HEAL_FALLBACK

    def resist(self, item: int) -> set[str]:
        tags = set(self.tags(item))
        return {k for k, tag in (("armor", "Armor"), ("mr", "SpellBlock")) if tag in tags}

    # --------------------------------------------------------------- recetas
    def tree(self, item: int) -> Counter:
        """Todos los componentes (con repetición) bajo ``item``, sin incluirlo."""
        out: Counter = Counter()
        for c in self.get(item).get("from") or []:
            out[c] += 1
            out.update(self.tree(c))
        return out

    def _take(self, item: int, inv: Counter, used: Counter) -> int:
        """Valor de los componentes de ``item`` que ya llevas (los marca como usados)."""
        total = 0
        for c in sorted(self.get(item).get("from") or [], key=self.cost, reverse=True):
            if inv[c] - used[c] > 0:
                used[c] += 1
                total += self.cost(c)
            else:
                total += self._take(c, inv, used)
        return total

    def buy_cost(self, item: int, inv: Counter) -> tuple[int, int]:
        """Oro que cuesta comprar ``item`` ahora y huecos que libera (componentes que usa)."""
        used: Counter = Counter()
        value = self._take(item, inv, used)
        freed = sum(n for c, n in used.items() if self.uses_slot(c))
        return max(0, self.cost(item) - value), freed

    def options(self, targets: list[int], inv: Counter, free_slots: int) -> list[tuple[int, int]]:
        """Compras posibles (objeto, coste ahora) que avanzan hacia alguno de ``targets``."""
        seen: dict[int, int] = {}
        for target in targets:
            if target not in self.items:
                continue
            need = self.tree(target)
            for item in (target, *need):
                if item in seen:
                    continue
                cost = self._option_cost(item, target, need, inv, free_slots)
                if cost is not None:
                    seen[item] = cost
        return sorted(seen.items(), key=lambda x: x[1])

    def _option_cost(self, item: int, target: int, need: Counter, inv: Counter, free_slots: int) -> int | None:
        """Oro que cuesta ahora ``item`` camino de ``target`` (None si no se vende, ya lo llevas, no hay hueco o no cuesta nada)."""
        if not self.get(item).get("buyable", True):
            return None
        if item != target and inv[item] >= need[item]:  # ya lo llevas
            return None
        cost, freed = self.buy_cost(item, inv)
        if cost <= 0 or 1 - freed > free_slots:
            return None
        return cost

    def merges(self, inv: Counter, free_slots: int) -> list[tuple[int, int]]:
        """Sin saber tu build: objetos que combinan componentes que ya llevas."""
        targets = {up for item in inv if inv[item] > 0 for up in self.get(item).get("into") or []}
        return self.options(sorted(targets), inv, free_slots)


def used_slots(catalog: ItemCatalog, inv: Counter) -> int:
    """Huecos ocupados: los objetos apilables (pociones, pinks…) comparten hueco."""
    return sum(math.ceil(n / catalog.stacks(item)) for item, n in inv.items() if n > 0 and catalog.uses_slot(item))


class Inventory:
    """Inventario de un jugador en cada momento a partir de sus eventos de objetos.

    ``events``: eventos del timeline de ese jugador (ordenados por tiempo).
    """

    def __init__(self, catalog: ItemCatalog, events: list[dict]) -> None:
        self.catalog = catalog
        self.times: list[int] = []
        self.snaps: list[Counter] = []
        self.purchases: list[dict] = []  # {"t", "item", "cost", "undone", "consumed"}
        self.sold: list[dict] = []
        self.unknown: set[int] = set()  # objetos comprados que el catálogo no conoce
        self._free_last: dict[int, int] = {}  # último ITEM_DESTROYED de cada objeto que llega sin compra
        inv: Counter = Counter()
        # Objetos gastados en este instante: si a continuación hay una compra, eran su receta;
        # si no, se gastaron (poción, pink) o se transformaron (Manamune -> Muramana).
        pending: list[tuple[int, int]] = []
        for e in events:
            kind = e.get("type")
            t = int(e.get("timestamp") or 0)
            item = int(e.get("itemId") or 0)
            if pending and not (pending[-1][0] == t and kind in ("ITEM_DESTROYED", "ITEM_PURCHASED")):
                self._flush(inv, pending)
            if kind == "ITEM_DESTROYED":
                self._on_destroyed(item, t, inv, pending)
            elif kind == "ITEM_PURCHASED":
                self._on_purchased(item, t, inv, pending)
            elif kind == "ITEM_SOLD":
                self._on_sold(item, t, inv)
            elif kind == "ITEM_UNDO":
                self._on_undo(e, inv)
            else:
                continue
            self.times.append(t)
            self.snaps.append(Counter({k: v for k, v in inv.items() if v > 0}))
        self._flush(inv, pending)

    def _flush(self, inv: Counter, pending: list[tuple[int, int]]) -> None:
        """Lo gastado que no fue receta de una compra: lo que se transforma (Manamune -> Muramana) aparece ya transformado."""
        changed = False
        for _, old in pending:
            if old in TRANSFORMS:
                inv[TRANSFORMS[old]] += 1
                changed = True
        pending.clear()
        if changed and self.snaps:
            self.snaps[-1] = Counter({k: v for k, v in inv.items() if v > 0})

    def _on_destroyed(self, item: int, t: int, inv: Counter, pending: list[tuple[int, int]]) -> None:
        """ITEM_DESTROYED: el objeto sale del inventario y queda pendiente (receta de una compra, gastado o transformado)."""
        if inv[item] <= 0 and item in RECEIVED_FREE:
            self._received(item, t, inv)
        if inv[item] > 0:
            inv[item] -= 1
            pending.append((t, item))
        elif item in TRANSFORMS:  # objeto recibido sin compra (el de support viene dado por el rol)
            pending.append((t, item))

    def _on_purchased(self, item: int, t: int, inv: Counter, pending: list[tuple[int, int]]) -> None:
        """ITEM_PURCHASED: lo gastado justo antes en el mismo instante era su receta."""
        consumed = [i for _, i in pending]
        pending.clear()
        inv[item] += 1
        if item and item not in self.catalog:
            self.unknown.add(item)
        self.purchases.append({"t": t, "item": item, "cost": self.catalog.cost(item), "undone": False, "consumed": consumed})

    def _on_sold(self, item: int, t: int, inv: Counter) -> None:
        """ITEM_SOLD: sale del inventario y se anota la venta."""
        if inv[item] > 0:
            inv[item] -= 1
        self.sold.append({"t": t, "item": item})

    def _on_undo(self, e: dict, inv: Counter) -> None:
        """ITEM_UNDO: deshace la última compra de ``beforeId`` (vuelven sus componentes) o la venta de ``afterId``."""
        before, after = int(e.get("beforeId") or 0), int(e.get("afterId") or 0)
        if before and inv[before] > 0:
            inv[before] -= 1
            buy = next((p for p in reversed(self.purchases) if p["item"] == before and not p["undone"]), None)
            if buy:
                buy["undone"] = True
                inv.update(buy["consumed"])  # vuelven los componentes
        if after:
            inv[after] += 1
            sale = next((s for s in reversed(self.sold) if s["item"] == after), None)
            if sale:
                self.sold.remove(sale)

    def _received(self, item: int, t: int, inv: Counter) -> None:
        """Un objeto que llegó sin compra (lo delata su ITEM_DESTROYED): se añade desde que pudo llegar hasta ``t``."""
        # Desde lo antes que puede llegar, o desde que se gastó el anterior (ese ya se contó hasta su ITEM_DESTROYED).
        since = min(max(RECEIVED_FREE[item], self._free_last.get(item, 0)), t)
        self._free_last[item] = t
        i = bisect.bisect_left(self.times, since)
        if i == len(self.times) or self.times[i] != since:
            self.times.insert(i, since)
            self.snaps.insert(i, Counter(self.snaps[i - 1]) if i > 0 else Counter())
        for j in range(i, len(self.times)):
            self.snaps[j][item] += 1
        inv[item] += 1

    def at(self, t: int) -> Counter:
        """Inventario justo en el instante ``t`` (después de los eventos de ese instante)."""
        lo, hi = 0, len(self.times)
        while lo < hi:
            mid = (lo + hi) // 2
            if self.times[mid] <= t:
                lo = mid + 1
            else:
                hi = mid
        return Counter(self.snaps[lo - 1]) if lo else Counter()

    def bought(self, *, since: int = 0) -> list[dict]:
        return [p for p in self.purchases if p["t"] > since and not p["undone"]]

    def free_slots(self, inv: Counter) -> int:
        return max(0, SLOTS - used_slots(self.catalog, inv))

    def completed(self, inv: Counter) -> list[int]:
        return [i for i, n in inv.items() for _ in range(n) if self.catalog.is_completed(i)]
