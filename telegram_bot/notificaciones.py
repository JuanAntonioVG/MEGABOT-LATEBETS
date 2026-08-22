"""Formateo y envio de notificaciones de Telegram: discrepancias
individuales, resumen de ejecucion (con modo silencioso/verboso), y
avisos de error del ciclo automatico."""

from __future__ import annotations

from telegram import Bot
from telegram.constants import ParseMode

from core.models import Discrepancia, ResultadoEjecucion

EMOJIS_DEPORTE = {
    "futbol": "⚽",
    "baloncesto": "🏀",
    "tenis": "🎾",
    "voleibol": "🏐",
    "balonmano": "🤾",
    "hockey": "🏒",
    "waterpolo": "🤽",
    "futsal": "🥅",
}


def _sanitizar(texto: object) -> str:
    return str(texto).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def formatear_discrepancia(d: Discrepancia, indice: int, total: int) -> str:
    emoji_deporte = EMOJIS_DEPORTE.get(d.deporte, "❓")
    semaforo = "🟢" if d.prioridad == "alta" else "🔴"
    return (
        f"{semaforo} <b>OPORTUNIDAD #{indice}/{total}</b>\n"
        f"🎯 <i>Similitud: {d.similitud:.0f}%</i>\n"
        f"➖➖➖➖➖➖➖➖➖➖\n\n"
        f"<b>{emoji_deporte} {d.deporte.upper()}</b>\n\n"
        f"⭐ <b>FLASHSCORE</b> — <i>{_sanitizar(d.liga_fs)}</i>\n"
        f"    └ ⏰ {_sanitizar(d.detalle_fs)}\n"
        f"    └ <code>{_sanitizar(d.equipo_local_fs)} vs {_sanitizar(d.equipo_visitante_fs)}</code>\n\n"
        f"🏠 <b>{_sanitizar(d.casa_nombre.upper())}</b> — <i>{_sanitizar(d.liga)}</i>\n"
        f"    └ ⏰ {_sanitizar(d.detalle_casa)}\n"
        f"    └ <code>{_sanitizar(d.equipo_local_casa)} vs {_sanitizar(d.equipo_visitante_casa)}</code>\n\n"
        f"⚠️ <i>Revisa que ambas ligas sean la misma competición antes de actuar.</i>"
    )


def formatear_resumen(resultado: ResultadoEjecucion, verboso: bool) -> str:
    lineas = [
        "📋 <b>RESUMEN DE EJECUCIÓN</b>",
        f"🖥️ Host: {_sanitizar(resultado.host)}",
        f"⏱️ Duración total: {resultado.duracion_segundos:.1f}s",
        f"⚙️ Paralelismo: {resultado.paralelismo}",
        f"📊 Partidos procesados: {resultado.total_partidos}",
        f"🎯 Discrepancias encontradas: {len(resultado.discrepancias)}",
    ]
    if resultado.errores:
        lineas.append(f"⚠️ Errores ({len(resultado.errores)}):")
        for err in resultado.errores[:5]:
            lineas.append(f"    └ {_sanitizar(err)}")

    if verboso and resultado.tiempos:
        lineas.append("\n<b>⏱️ Tiempos por etapa (más lentas primero):</b>")
        for t in sorted(resultado.tiempos, key=lambda x: -x.segundos)[:15]:
            lineas.append(f"    └ {_sanitizar(t.etiqueta)}: {t.segundos:.1f}s")

    return "\n".join(lineas)


async def enviar_resultado(bot: Bot, chat_id: int, resultado: ResultadoEjecucion, verboso: bool) -> None:
    if resultado.discrepancias:
        await bot.send_message(
            chat_id=chat_id,
            text=f"🚨 <b>¡{len(resultado.discrepancias)} OPORTUNIDADES DETECTADAS!</b> 🚨",
            parse_mode=ParseMode.HTML,
        )
        ordenadas = sorted(resultado.discrepancias, key=lambda x: -x.similitud)
        for i, d in enumerate(ordenadas, start=1):
            await bot.send_message(
                chat_id=chat_id,
                text=formatear_discrepancia(d, i, len(ordenadas)),
                parse_mode=ParseMode.HTML,
            )

    # Este resumen se manda SIEMPRE, incluso sin discrepancias: es el
    # "heartbeat" que confirma que el ciclo corrió bien, importante al no
    # haber pantalla delante de la Raspberry Pi.
    await bot.send_message(
        chat_id=chat_id, text=formatear_resumen(resultado, verboso), parse_mode=ParseMode.HTML
    )


async def enviar_error(bot: Bot, chat_id: int, mensaje: str) -> None:
    await bot.send_message(
        chat_id=chat_id,
        text=f"❌ <b>ERROR EN EL CICLO AUTOMÁTICO</b>\n\n{_sanitizar(mensaje)}",
        parse_mode=ParseMode.HTML,
    )
