"""Teclados inline de Telegram para el panel de control.

Rediseñado el 2026-08-22: antes solo se podia navegar "por casa"
(elegir una casa, luego marcar sus deportes uno a uno). Ahora hay dos
formas de entrar segun cual te resulte mas natural en cada momento
("quiero apagar tenis en todas partes" vs "quiero dejar solo Bwin
encendido"), mas botones de activar/desactivar todo de golpe para no
tener que pulsar deporte a deporte.
"""

from __future__ import annotations

from pathlib import Path

from telegram import InlineKeyboardButton, InlineKeyboardMarkup

from config.catalogo_casas import CATALOGO_CASAS, EMOJIS_DEPORTE, casas_que_soportan, todos_los_deportes
from core import db


def teclado_menu_principal() -> InlineKeyboardMarkup:
    filas = [
        [InlineKeyboardButton("⚽ Por deporte", callback_data="menu_deportes")],
        [InlineKeyboardButton("🏠 Por casa", callback_data="menu_casas")],
        [InlineKeyboardButton("📊 Resumen de lo activo", callback_data="menu_resumen")],
    ]
    return InlineKeyboardMarkup(filas)


def teclado_casas() -> InlineKeyboardMarkup:
    filas = [
        [InlineKeyboardButton(casa.nombre_legible, callback_data=f"casa:{casa.id}")]
        for casa in CATALOGO_CASAS.values()
    ]
    filas.append([InlineKeyboardButton("⬅️ Menú", callback_data="volver_menu")])
    return InlineKeyboardMarkup(filas)


def teclado_deportes_de_casa(ruta_db: Path, casa_id: str) -> InlineKeyboardMarkup:
    casa = CATALOGO_CASAS[casa_id]
    filas = []
    for deporte in casa.deportes:
        activo = db.esta_activo(ruta_db, casa_id, deporte)
        marca = "✅" if activo else "❌"
        emoji = EMOJIS_DEPORTE.get(deporte, "❓")
        filas.append(
            [
                InlineKeyboardButton(
                    f"{marca} {emoji} {deporte.capitalize()}",
                    callback_data=f"toggle:{casa_id}:{deporte}:casa",
                )
            ]
        )
    filas.append(
        [
            InlineKeyboardButton("🟢 Activar todo", callback_data=f"bulk:casa:{casa_id}:on"),
            InlineKeyboardButton("🔴 Desactivar todo", callback_data=f"bulk:casa:{casa_id}:off"),
        ]
    )
    filas.append([InlineKeyboardButton("⬅️ Volver a casas", callback_data="menu_casas")])
    return InlineKeyboardMarkup(filas)


def teclado_deportes() -> InlineKeyboardMarkup:
    deportes = todos_los_deportes()
    filas = []
    fila_actual = []
    for deporte in deportes:
        emoji = EMOJIS_DEPORTE.get(deporte, "❓")
        fila_actual.append(
            InlineKeyboardButton(f"{emoji} {deporte.capitalize()}", callback_data=f"dep:{deporte}")
        )
        if len(fila_actual) == 2:
            filas.append(fila_actual)
            fila_actual = []
    if fila_actual:
        filas.append(fila_actual)
    filas.append([InlineKeyboardButton("⬅️ Menú", callback_data="volver_menu")])
    return InlineKeyboardMarkup(filas)


def teclado_casas_de_deporte(ruta_db: Path, deporte: str) -> InlineKeyboardMarkup:
    casas = casas_que_soportan(deporte)
    filas = []
    for casa in casas:
        activo = db.esta_activo(ruta_db, casa.id, deporte)
        marca = "✅" if activo else "❌"
        filas.append(
            [
                InlineKeyboardButton(
                    f"{marca} {casa.nombre_legible}", callback_data=f"toggle:{casa.id}:{deporte}:dep"
                )
            ]
        )
    filas.append(
        [
            InlineKeyboardButton("🟢 Activar todo", callback_data=f"bulk:deporte:{deporte}:on"),
            InlineKeyboardButton("🔴 Desactivar todo", callback_data=f"bulk:deporte:{deporte}:off"),
        ]
    )
    filas.append([InlineKeyboardButton("⬅️ Volver a deportes", callback_data="menu_deportes")])
    return InlineKeyboardMarkup(filas)


def teclado_volver_menu() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ Menú", callback_data="volver_menu")]])
