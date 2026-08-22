@echo off
title Bot LateBets - Telegram
cd /d "%~dp0"

echo ============================================
echo   Bot LateBets - arrancando...
echo ============================================
echo.

if not exist ".venv\Scripts\python.exe" (
    echo ERROR: no se encuentra el entorno virtual en .venv
    echo Ejecuta primero en una terminal: python -m venv .venv
    echo y luego: pip install -r requirements.txt
    echo.
    pause
    exit /b 1
)

if not exist ".env" (
    echo ERROR: no existe el archivo .env
    echo Copia .env.example a .env y rellena tu token de Telegram y tu ID.
    echo.
    pause
    exit /b 1
)

.venv\Scripts\python.exe run.py

echo.
echo ============================================
echo   El bot se ha detenido.
echo ============================================
pause
