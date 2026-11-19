"""Lanzador: prepara el entorno la primera vez y arranca la web.

``iniciar.bat`` (Windows) lo ejecuta con el Python que encuentre. Hace:

1. Crea el entorno virtual ``.venv`` si no existe (o si está roto).
2. Instala las dependencias solo cuando ``requirements.txt`` ha cambiado.
3. Arranca ``run.py`` con el Python del entorno (abre el navegador solo).

También funciona en macOS/Linux: ``python3 launcher.py``.
"""

from __future__ import annotations

import hashlib
import os
import subprocess
import sys
import venv
from pathlib import Path

ROOT = Path(__file__).resolve().parent
VENV = ROOT / ".venv"
REQUIREMENTS = ROOT / "requirements.txt"
HASH_FILE = VENV / ".req-hash"
# 0 normal; -2/-15 y 130/143 señales en POSIX; 0xC000013A (3221225786) Ctrl+C en Windows.
CLEAN_EXIT_CODES = {0, -2, -15, 130, 143, 3221225786, -1073741510}


def venv_python() -> Path:
    return VENV / ("Scripts/python.exe" if os.name == "nt" else "bin/python")


def say(text: str) -> None:
    print(f"  {text}", flush=True)


def works(python: Path) -> bool:
    if not python.exists():
        return False
    try:
        return subprocess.run([str(python), "-c", "import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)"], timeout=60).returncode == 0
    except (OSError, subprocess.SubprocessError):
        return False


def ensure_venv() -> Path:
    python = venv_python()
    if works(python):
        return python
    say("Preparando el entorno de Python (solo la primera vez, ~1 minuto)…")
    venv.EnvBuilder(with_pip=True, clear=VENV.exists()).create(VENV)
    if HASH_FILE.exists():
        HASH_FILE.unlink()
    return python


def ensure_requirements(python: Path) -> None:
    digest = hashlib.sha256(REQUIREMENTS.read_bytes()).hexdigest()
    try:
        if HASH_FILE.read_text(encoding="utf-8").strip() == digest:
            return
    except OSError:
        pass
    say("Instalando dependencias…")
    base = [str(python), "-m", "pip", "--disable-pip-version-check"]
    subprocess.run([*base, "install", "-q", "--upgrade", "pip"], check=False)
    subprocess.run([*base, "install", "-q", "-r", str(REQUIREMENTS)], check=True)
    HASH_FILE.write_text(digest, encoding="utf-8")


def main() -> int:
    if sys.version_info < (3, 10):
        say("Tempu Drafter necesita Python 3.10 o superior (recomendado 3.14): https://www.python.org/downloads/")
        return 1
    os.chdir(ROOT)
    try:
        python = ensure_venv()
        ensure_requirements(python)
    except subprocess.CalledProcessError:
        say("No se pudieron instalar las dependencias. Revisa tu conexión a internet y vuelve a abrir iniciar.bat.")
        return 1
    except OSError as exc:
        say(f"No se pudo preparar el entorno: {exc}")
        return 1
    env = {**os.environ, "PYTHONUTF8": "1", "PYTHONIOENCODING": "utf-8"}
    proc = subprocess.Popen([str(python), str(ROOT / "run.py"), *sys.argv[1:]], env=env)
    try:
        code = proc.wait()
    except KeyboardInterrupt:
        # Ctrl+C: el servidor también lo recibe y se cierra ordenadamente; se le da un momento.
        try:
            code = proc.wait(timeout=15)
        except (subprocess.TimeoutExpired, KeyboardInterrupt):
            proc.terminate()
            code = 0
    # Cerrar con Ctrl+C o cerrando la ventana no es un error (no mostrar «se ha cerrado con un error»).
    return 0 if code in CLEAN_EXIT_CODES else code


if __name__ == "__main__":
    sys.exit(main())
