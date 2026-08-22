"""Formateo y envio de notificaciones de Telegram.

Rediseñado el 2026-08-22 tras feedback real de uso: el formato anterior
(un mensaje de Telegram por CADA discrepancia, con separadores pesados y
muchos emojis distintos compitiendo entre si) resultaba dificil de leer y,
con muchos hallazgos en una noche, se convertia en un aluvion de avisos
separados. Ahora se agrupan todas las discrepancias en el minimo numero
de mensajes posible (respetando el limite de caracteres de Telegram), en
bloques compactos donde lo primero que se ve es justo lo que importa: la
hora de cada lado y si la liga coincide.
"""

from __future__ import annotations

from pathlib import Path

from telegram import Bot
from telegram.constants import ParseMode

from config.catalogo_casas import casas_que_soportan, todos_los_deportes
from core import db
from core.models import Discrepancia, ResultadoEjecucion, TiempoEtapa

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

# Margen de sobra bajo el limite real de Telegram (4096) para no
# arriesgarse a que un mensaje se rechace por quedarse justo al borde.
LIMITE_CARACTERES_MENSAJE = 3200


def _sanitizar(texto: object) -> str:
    return str(texto).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def formatear_bloque_discrepancia(d: Discrepancia, indice: int) -> str:
    """Un bloque compacto para UNA discrepancia — pensado para ir varios
    seguidos en un mismo mensaje, no para enviarse solo."""
    emoji_deporte = EMOJIS_DEPORTE.get(d.deporte, "❓")
    semaforo = "🟢" if d.prioridad == "alta" else "🔴"
    etiqueta_prioridad = "alta prioridad" if d.prioridad == "alta" else "baja prioridad"
    return (
        f"{semaforo} <b>#{indice}</b> {emoji_deporte} · {d.similitud:.0f}% similitud · {etiqueta_prioridad}\n\n"
        f"⭐ Flashscore — <i>{_sanitizar(d.liga_fs)}</i>\n"
        f"🕐 <b>{_sanitizar(d.detalle_fs)}</b>  <code>{_sanitizar(d.equipo_local_fs)} vs "
        f"{_sanitizar(d.equipo_visitante_fs)}</code>\n\n"
        f"🏠 {_sanitizar(d.casa_nombre)} — <i>{_sanitizar(d.liga)}</i>\n"
        f"🕐 <b>{_sanitizar(d.detalle_casa)}</b>  <code>{_sanitizar(d.equipo_local_casa)} vs "
        f"{_sanitizar(d.equipo_visitante_casa)}</code>"
    )


def formatear_mensajes_discrepancias(discrepancias: list[Discrepancia]) -> list[str]:
    """Agrupa TODAS las discrepancias en el menor numero de mensajes de
    Telegram posible, en vez de uno por discrepancia. Devuelve la lista de
    mensajes ya listos para enviar (vacia si no hay ninguna)."""
    if not discrepancias:
        return []

    ordenadas = sorted(discrepancias, key=lambda x: -x.similitud)
    bloques = [formatear_bloque_discrepancia(d, i) for i, d in enumerate(ordenadas, start=1)]

    singular = len(ordenadas) == 1
    sustantivo = "oportunidad" if singular else "oportunidades"
    participio = "detectada" if singular else "detectadas"
    cabecera = (
        f"🚨 <b>{len(ordenadas)} {sustantivo} {participio}</b>\n"
        f"<i>Revisa que la liga coincida en ambos lados antes de actuar.</i>"
    )

    mensajes: list[str] = []
    actual = cabecera
    for bloque in bloques:
        candidato = f"{actual}\n\n{bloque}"
        if len(candidato) > LIMITE_CARACTERES_MENSAJE and actual != cabecera:
            mensajes.append(actual)
            actual = bloque
        else:
            actual = candidato
    mensajes.append(actual)
    return mensajes


def _partidos_por_casa(tiempos: list[TiempoEtapa]) -> list[TiempoEtapa]:
    return sorted((t for t in tiempos if not t.etiqueta.startswith("flashscore/")), key=lambda t: t.etiqueta)


def formatear_resumen(resultado: ResultadoEjecucion, verboso: bool) -> str:
    lineas = [
        "📋 <b>Resumen de ejecución</b>",
        f"🖥 {_sanitizar(resultado.host)} · ⏱ {resultado.duracion_segundos:.0f}s · ⚙️ paralelismo {resultado.paralelismo}",
        f"🎯 {len(resultado.discrepancias)} discrepancias sobre {resultado.total_partidos} partidos procesados",
    ]

    etapas_casas = _partidos_por_casa(resultado.tiempos)
    if etapas_casas:
        lineas.append("\n<b>📊 Partidos por casa</b> (para que compares que sea razonable entre ellas):")
        for t in etapas_casas:
            lineas.append(f"  {_sanitizar(t.etiqueta)}: <b>{t.partidos}</b>")

    if resultado.errores:
        lineas.append(f"\n⚠️ <b>Errores ({len(resultado.errores)}):</b>")
        for err in resultado.errores[:5]:
            lineas.append(f"  {_sanitizar(err)}")

    if verboso and resultado.tiempos:
        lineas.append("\n<b>⏱ Tiempos por etapa</b> (más lentas primero):")
        for t in sorted(resultado.tiempos, key=lambda x: -x.segundos)[:15]:
            lineas.append(f"  {_sanitizar(t.etiqueta)}: {t.segundos:.1f}s")

    return "\n".join(lineas)


async def enviar_resultado(bot: Bot, chat_id: int, resultado: ResultadoEjecucion, verboso: bool) -> None:
    for mensaje in formatear_mensajes_discrepancias(resultado.discrepancias):
        await bot.send_message(chat_id=chat_id, text=mensaje, parse_mode=ParseMode.HTML)

    # Este resumen se manda SIEMPRE, incluso sin discrepancias: es el
    # "heartbeat" que confirma que el ciclo corrió bien, importante al no
    # haber pantalla delante de la Raspberry Pi.
    await bot.send_message(
        chat_id=chat_id, text=formatear_resumen(resultado, verboso), parse_mode=ParseMode.HTML
    )


def formatear_resumen_activos(ruta_db: Path) -> str:
    """Vista de texto de que casas/deportes estan activos ahora mismo,
    para el boton "Resumen" del menu — un vistazo rapido sin tener que
    entrar casa por casa o deporte por deporte."""
    lineas = ["📊 <b>Resumen de activos</b>\n"]
    for deporte in todos_los_deportes():
        casas = casas_que_soportan(deporte)
        activas = [c for c in casas if db.esta_activo(ruta_db, c.id, deporte)]
        emoji = EMOJIS_DEPORTE.get(deporte, "❓")
        if not activas:
            lineas.append(f"{emoji} {deporte.capitalize()}: <i>apagado en todas</i>")
        elif len(activas) == len(casas):
            lineas.append(f"{emoji} {deporte.capitalize()}: <b>todas</b> ({len(casas)}/{len(casas)})")
        else:
            nombres = ", ".join(c.nombre_legible for c in activas)
            lineas.append(f"{emoji} {deporte.capitalize()}: {nombres} ({len(activas)}/{len(casas)})")
    return "\n".join(lineas)


async def enviar_error(bot: Bot, chat_id: int, mensaje: str) -> None:
    await bot.send_message(
        chat_id=chat_id,
        text=f"❌ <b>Error en el ciclo automático</b>\n\n{_sanitizar(mensaje)}",
        parse_mode=ParseMode.HTML,
    )
