"""Formateo y envio de notificaciones de Telegram.

Rediseñado el 2026-08-22 tras feedback real de uso, en DOS pasadas:

1. Del formato original (un mensaje de Telegram por CADA discrepancia) a
   bloques compactos agrupados en el minimo numero de mensajes.
2. De ahi a esto: ni siquiera el detalle agrupado va ya como texto de
   chat. El chat solo recibe un CONTEO (cuantas, cuantas de alta/baja
   prioridad) con un boton que abre la Mini App en la pestaña de
   Reportes — una pagina puede mostrar cada discrepancia con jerarquia
   visual de verdad (Flashscore vs casa, liga de cada lado una debajo de
   la otra) sin pelear contra el limite de caracteres de un mensaje ni
   convertirse en un muro de texto cuando hay muchas.
"""

from __future__ import annotations

from pathlib import Path

from telegram import Bot, InlineKeyboardButton, InlineKeyboardMarkup, WebAppInfo
from telegram.constants import ParseMode

from config.catalogo_casas import CATALOGO_CASAS, EMOJIS_DEPORTE, casas_que_soportan, todos_los_deportes
from core import db
from core.models import Discrepancia, ResultadoEjecucion, TiempoEtapa
from telegram_bot import miniapp


def _sanitizar(texto: object) -> str:
    return str(texto).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def formatear_resumen_discrepancias(discrepancias: list[Discrepancia]) -> str:
    """Version corta pensada para chat: solo el conteo y el desglose por
    prioridad. El detalle (equipos, horas, liga de cada lado) vive en el
    panel — ver `enviar_resultado`."""
    total = len(discrepancias)
    altas = sum(1 for d in discrepancias if d.prioridad == "alta")
    bajas = total - altas

    singular = total == 1
    sustantivo = "oportunidad" if singular else "oportunidades"
    participio = "detectada" if singular else "detectadas"
    lineas = [f"🚨 <b>{total} {sustantivo} {participio}</b>"]

    if altas and bajas:
        lineas.append(f"🟢 {altas} de alta prioridad · 🔴 {bajas} de baja prioridad")
    elif altas:
        lineas.append("🟢 Todas de alta prioridad")
    else:
        lineas.append("🔴 Todas de baja prioridad")

    lineas.append("<i>Toca «Ver detalle» y revisa que la liga coincida antes de actuar.</i>")
    return "\n".join(lineas)


def _nombre_origen(origen: str) -> str:
    """ "flashscore" -> "⭐ Flashscore" (la referencia); cualquier casa ->
    "🏠 <nombre legible>". El emoji distinto es a proposito: al agrupar
    por deporte, tiene que saltar a la vista cual es el numero de
    referencia y cuales son los de cada casa, sin leer letra por letra."""
    if origen == "flashscore":
        return "⭐ Flashscore"
    casa = CATALOGO_CASAS.get(origen)
    nombre = casa.nombre_legible if casa else origen.capitalize()
    return f"🏠 {nombre}"


def formatear_resumen(resultado: ResultadoEjecucion, verboso: bool) -> str:
    lineas = [
        "📋 <b>Resumen de ejecución</b>",
        f"🖥 {_sanitizar(resultado.host)} · ⏱ {resultado.duracion_segundos:.0f}s · ⚙️ paralelismo {resultado.paralelismo}",
        f"🎯 {len(resultado.discrepancias)} discrepancias sobre {resultado.total_partidos} partidos procesados",
    ]

    # Agrupado por deporte (no una lista plana "casa/deporte") para que
    # comparar sea de verdad facil: dentro de cada deporte, Flashscore
    # (la referencia) siempre primero y despues cada casa.
    grupos: dict[str, list[tuple[str, TiempoEtapa]]] = {}
    for t in resultado.tiempos:
        origen, _, deporte = t.etiqueta.partition("/")
        grupos.setdefault(deporte, []).append((origen, t))

    if grupos:
        lineas.append("\n<b>📊 Partidos por deporte y fuente</b> (⭐ Flashscore es la referencia):")
        for deporte in todos_los_deportes():
            entradas = grupos.get(deporte)
            if not entradas:
                continue
            emoji = EMOJIS_DEPORTE.get(deporte, "❓")
            lineas.append(f"\n{emoji} <b>{deporte.capitalize()}</b>")
            for origen, t in sorted(
                entradas, key=lambda par: (par[0] != "flashscore", _nombre_origen(par[0]))
            ):
                lineas.append(f"  {_nombre_origen(origen)}: <b>{t.partidos}</b>")

    if resultado.errores:
        lineas.append(f"\n⚠️ <b>Errores ({len(resultado.errores)}):</b>")
        for err in resultado.errores[:5]:
            lineas.append(f"  {_sanitizar(err)}")

    if verboso and resultado.tiempos:
        lineas.append("\n<b>⏱ Tiempos por etapa</b> (más lentas primero):")
        for t in sorted(resultado.tiempos, key=lambda x: -x.segundos)[:15]:
            lineas.append(f"  {_sanitizar(t.etiqueta)}: {t.segundos:.1f}s")

    return "\n".join(lineas)


def _boton_panel(texto: str, ruta_db: Path, tab: str = "casas") -> InlineKeyboardMarkup:
    url = miniapp.construir_url_panel(ruta_db, tab=tab)
    return InlineKeyboardMarkup([[InlineKeyboardButton(texto, web_app=WebAppInfo(url=url))]])


async def enviar_resultado(
    bot: Bot, chat_id: int, ruta_db: Path, resultado: ResultadoEjecucion, verboso: bool
) -> None:
    if resultado.discrepancias:
        await bot.send_message(
            chat_id=chat_id,
            text=formatear_resumen_discrepancias(resultado.discrepancias),
            parse_mode=ParseMode.HTML,
            reply_markup=_boton_panel("📋 Ver detalle", ruta_db, tab="reportes"),
        )

    # Este resumen se manda SIEMPRE, incluso sin discrepancias: es el
    # "heartbeat" que confirma que el ciclo corrió bien, importante al no
    # haber pantalla delante de la Raspberry Pi. Lleva su propio boton al
    # panel completo (no solo a Reportes) para que siempre haya una
    # forma de llegar a todo desde el ultimo mensaje, sin tener que
    # buscar el boton persistente del chat.
    await bot.send_message(
        chat_id=chat_id,
        text=formatear_resumen(resultado, verboso),
        parse_mode=ParseMode.HTML,
        reply_markup=_boton_panel("🖥 Abrir panel", ruta_db),
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
