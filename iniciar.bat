@echo off
rem Tempu Drafter: doble clic para abrir la web (prepara todo la primera vez).
setlocal EnableExtensions
title Tempu Drafter
cd /d "%~dp0"
chcp 65001 >nul
set "PYTHONUTF8=1"
set "PYTHONIOENCODING=utf-8"

set "PYCMD="
py -3.14 -c "import sys" >nul 2>nul && set "PYCMD=py -3.14" && goto :found
py -3 -c "import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)" >nul 2>nul && set "PYCMD=py -3" && goto :found
python -c "import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)" >nul 2>nul && set "PYCMD=python" && goto :found

echo.
echo   No encuentro Python 3.10 o superior.
echo   Instala Python 3.14 desde https://www.python.org/downloads/
echo   (marca "Add python.exe to PATH") y vuelve a abrir este archivo.
echo.
pause
exit /b 1

:found
%PYCMD% launcher.py %*
if errorlevel 1 (
  echo.
  echo   Tempu Drafter se ha cerrado con un error. Revisa el mensaje de arriba.
  pause
  exit /b 1
)
endlocal
