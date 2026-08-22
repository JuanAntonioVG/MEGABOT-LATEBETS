"""Teclados inline de Telegram para el panel de control: activar o
desactivar casas y deportes con botones, sin tocar ningun fichero."""

from __future__ import annotations

from pathlib import Path

from telegram import InlineKeyboardButton, InlineKeyboardMarkup

from config.catalogo_casas import CATALOGO_CASAS
from core import db


def teclado_casas() -> InlineKeyboardMarkup:
    filas = [
        [InlineKeyboardButton(casa.nombre_legible, callback_data=f"casa:{casa.id}")]
        for casa in CATALOGO_CASAS.values()
    ]
    return InlineKeyboardMarkup(filas)


def teclado_deportes_de_casa(ruta_db: Path, casa_id: str) -> InlineKeyboardMarkup:
    casa = CATALOGO_CASAS[casa_id]
    filas = []
    for deporte in casa.deportes:
        activo = db.esta_activo(ruta_db, casa_id, deporte)
        marca = "✅" if activo else "❌"
        filas.append(
            [
                InlineKeyboardButton(
                    f"{marca} {deporte.capitalize()}", callback_data=f"toggle:{casa_id}:{deporte}"
                )
            ]
        )
    filas.append([InlineKeyboardButton("⬅️ Volver a casas", callback_data="volver_casas")])
    return InlineKeyboardMarkup(filas)
