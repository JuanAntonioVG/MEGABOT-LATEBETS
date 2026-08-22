"""Configuracion de entorno: carga .env y expone rutas/ajustes globales.

Todo lo que es secreto (tokens) o depende de la maquina vive aqui, leido
de variables de entorno / `.env` — nunca hardcodeado ni versionado.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

RAIZ_PROYECTO = Path(__file__).resolve().parent.parent
load_dotenv(RAIZ_PROYECTO / ".env")


@dataclass(frozen=True, slots=True)
class Settings:
    telegram_bot_token: str
    telegram_admin_id: int
    db_path: Path
    headless: bool
    ollama_host: str
    ollama_model: str
    log_level: str


def cargar_settings() -> Settings:
    admin_id_raw = os.getenv("TELEGRAM_ADMIN_ID", "0")
    try:
        admin_id = int(admin_id_raw)
    except ValueError:
        admin_id = 0

    return Settings(
        telegram_bot_token=os.getenv("TELEGRAM_BOT_TOKEN", ""),
        telegram_admin_id=admin_id,
        db_path=RAIZ_PROYECTO / "datos" / os.getenv("DB_FILENAME", "bot.sqlite3"),
        headless=os.getenv("HEADLESS", "true").strip().lower() != "false",
        ollama_host=os.getenv("OLLAMA_HOST", "http://localhost:11434"),
        ollama_model=os.getenv("OLLAMA_MODEL", "llama3.2:latest"),
        log_level=os.getenv("LOG_LEVEL", "INFO"),
    )
