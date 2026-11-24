"""Índice de guías en disco (formato compatible con la carpeta ``coachings`` antigua).

Estructura: ``<carpeta>/<Rol>/<Campeón>/<Campeón>_vs_<Rival>[_IA].txt``.
Los nombres se resuelven con Data Dragon, así que da igual si una carpeta se
llama ``Khazix``, ``Kha'Zix`` o ``khazix`` (antes cada función comparaba los
nombres de una forma distinta y muchas guías no se encontraban).
"""

from __future__ import annotations

import threading
import time
from pathlib import Path

from app.ddragon import DDragon

ROLE_FOLDERS = {
    "top": "TOP",
    "jungle": "JUNGLE",
    "jungla": "JUNGLE",
    "jg": "JUNGLE",
    "mid": "MID",
    "middle": "MID",
    "adc": "ADC",
    "bot": "ADC",
    "bottom": "ADC",
    "support": "SUPPORT",
    "supp": "SUPPORT",
    "soporte": "SUPPORT",
    "utility": "SUPPORT",
    "general": None,
}
ROLE_DIR_NAME = {"TOP": "Top", "JUNGLE": "Jungle", "MID": "Mid", "ADC": "Adc", "SUPPORT": "Support"}


def parse_guide_name(name: str) -> tuple[str, str, bool] | None:
    """(campeón, rival, ¿de IA?) de «Campeón_vs_Rival[_IA].txt», sin distinguir mayúsculas; ``None`` si no lo es.

    Sin expresión regular: la de antes (``^(.+?)_vs_(.+?)(_IA)?\\.txt$``) podía tardar mucho con nombres raros (dos
    partes «lo mínimo posible» seguidas de una opcional). Mismo resultado: el primer «_vs_» que no está al principio y
    un «_IA» final, si el rival no se queda vacío."""
    if len(name) < 4 or name[-4:].lower() != ".txt":
        return None
    stem = name[:-4]
    cut = stem.lower().find("_vs_", 1)
    if cut < 1:
        return None
    main, opp = stem[:cut], stem[cut + 4 :]
    ia = len(opp) > 3 and opp[-3:].lower() == "_ia"
    if ia:
        opp = opp[:-3]
    return (main, opp, ia) if opp else None

RESCAN_SECONDS = 15


class GuideIndex:
    def __init__(self, root: Path, dd: DDragon) -> None:
        self.root = Path(root)
        self.dd = dd
        self._lock = threading.Lock()
        self._entries: list[dict] = []
        self._by_key: dict[tuple, dict] = {}
        self._scanned_at = 0.0

    # ---------------------------------------------------------------- índice
    def _entry(self, path: Path) -> dict | None:
        """La guía de un fichero (rol de la carpeta, campeón, rival y si es de IA) o ``None`` si no lo es."""
        parsed = parse_guide_name(path.name)
        if parsed is None:
            return None
        main, opp_name, ia = parsed
        rel = path.relative_to(self.root).parts
        role = ROLE_FOLDERS.get(rel[0].lower()) if len(rel) >= 2 else None
        champ = self.dd.resolve(main) or (self.dd.resolve(rel[1]) if len(rel) >= 3 else None)
        opp = self.dd.resolve(opp_name)
        if not champ or not opp:
            return None
        return {
            "role": role,
            "champ": champ,
            "opp": opp,
            "source": "IA" if ia else "Vídeo",
            "path": str(path.relative_to(self.root)),
            "mtime": int(path.stat().st_mtime),
        }

    def _scan(self) -> None:
        entries: list[dict] = []
        if self.root.is_dir():
            entries = [e for e in (self._entry(path) for path in self.root.rglob("*.txt")) if e is not None]
        by_key: dict[tuple, dict] = {}
        # Las de IA primero y las de vídeo después: si hay ambas, gana la del
        # coach humano. (None, a, b) es el comodín "cualquier rol".
        for entry in sorted(entries, key=lambda e: e["source"] == "Vídeo"):
            by_key[(entry["role"], entry["champ"], entry["opp"])] = entry
            by_key[(None, entry["champ"], entry["opp"])] = entry
        with self._lock:
            self._entries = entries
            self._by_key = by_key
            self._scanned_at = time.monotonic()

    def _ensure(self) -> None:
        if time.monotonic() - self._scanned_at > RESCAN_SECONDS:
            self._scan()

    def refresh(self) -> None:
        self._scan()

    # -------------------------------------------------------------- consultas
    def _lookup(self, role: str | None, champ: int, opp: int) -> dict | None:
        self._ensure()
        return self._by_key.get((role, champ, opp)) or self._by_key.get((None, champ, opp))

    def exists(self, role: str | None, champ: int, opp: int) -> bool:
        return self._lookup(role, champ, opp) is not None

    def find(self, role: str | None, champ: int, opp: int, include_content: bool = True) -> dict | None:
        entry = self._lookup(role, champ, opp)
        if not entry:
            return None
        result = dict(entry)
        if include_content:
            result["content"] = self.read(entry["path"])
        return result

    def read(self, rel_path: str) -> str:
        path = (self.root / rel_path).resolve()
        if self.root.resolve() not in path.parents:
            raise PermissionError("Ruta fuera de la carpeta de guías")
        return path.read_text(encoding="utf-8", errors="replace")

    def tree(self) -> dict:
        self._ensure()
        out: dict[str, dict[int, list[dict]]] = {}
        for entry in self._entries:
            role = entry["role"] or "GENERAL"
            out.setdefault(role, {}).setdefault(entry["champ"], []).append(
                {"opp": entry["opp"], "source": entry["source"], "path": entry["path"], "mtime": entry["mtime"]}
            )
        return {
            "root": str(self.root),
            "count": len(self._entries),
            "roles": {role: [{"champ": c, "guides": g} for c, g in champs.items()] for role, champs in out.items()},
        }

    # --------------------------------------------------------------- escritura
    def save(self, role: str, champ: int, opp: int, content: str, ai: bool = True) -> str:
        folder = self.root / ROLE_DIR_NAME.get(role, "General") / (self.dd.key(champ) or str(champ))
        folder.mkdir(parents=True, exist_ok=True)
        name = f"{self.dd.key(champ) or champ}_vs_{self.dd.key(opp) or opp}{'_IA' if ai else ''}.txt"
        path = folder / name
        path.write_text(content, encoding="utf-8")
        self._scan()
        return str(path.relative_to(self.root))
