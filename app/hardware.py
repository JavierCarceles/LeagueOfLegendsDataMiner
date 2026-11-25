"""El equipo en el que corre la app (núcleos, CPU libre, gráfica NVIDIA) y cuántos hilos dar a un análisis pesado.

Al empezar cada análisis se mira un momento cuánta CPU está libre y se usa hasta el 70 % de esa parte: con 24 hilos y
el PC al 10 %, 15 hilos; con el PC ocupado, menos. Mientras juegas los recolectores ya están en pausa. La gráfica
NVIDIA (si ``nvidia-smi`` responde) se usa donde se puede: decodificar vídeo y pasar voz a texto.
"""

from __future__ import annotations

import functools
import os
import shutil
import subprocess

import psutil

SHARE = 0.7  # parte de la CPU libre que se usa
SAMPLE_S = 0.5  # cuánto se mide la CPU antes de decidir


def cores() -> int:
    return psutil.cpu_count(logical=True) or os.cpu_count() or 2


def free_pct(sample_s: float = SAMPLE_S) -> float:
    """Porcentaje de CPU libre ahora mismo (medido ``sample_s`` segundos)."""
    try:
        used = psutil.cpu_percent(interval=sample_s)
    except (OSError, RuntimeError):
        used = 50.0
    return max(0.0, 100.0 - float(used))


def threads(share: float = SHARE, sample_s: float = SAMPLE_S) -> int:
    """Hilos para un análisis: el ``share`` (70 %) de los núcleos libres en este momento, al menos 1."""
    return max(1, int(cores() * free_pct(sample_s) / 100 * share))


@functools.cache
def gpu() -> dict | None:
    """La gráfica NVIDIA (nombre y memoria) si ``nvidia-smi`` responde; ``None`` si no hay."""
    exe = shutil.which("nvidia-smi")
    if not exe:
        return None
    try:
        proc = subprocess.run(
            [exe, "--query-gpu=name,memory.total", "--format=csv,noheader,nounits"], capture_output=True, text=True,
            timeout=10, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0), check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    lines = proc.stdout.strip().splitlines()
    if proc.returncode != 0 or not lines:
        return None
    name, _, memory = lines[0].partition(",")
    memory = memory.strip()
    return {"nombre": name.strip(), "memoria_mb": int(memory) if memory.isdigit() else None} if name.strip() else None


def summary(used_threads: int | None = None) -> dict:
    """Para la web: núcleos, hilos que usa el análisis en curso (o el último) y la gráfica."""
    return {"nucleos": cores(), "hilos": used_threads, "parte": SHARE, "gpu": gpu()}
