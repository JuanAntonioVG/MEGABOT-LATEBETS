"""Bot de Telegram: panel de control remoto + notificaciones.

Todos los comandos que reconfiguran algo o disparan una accion estan
protegidos por `_solo_admin`: SOLO el `TELEGRAM_ADMIN_ID` configurado en
`.env` puede usarlos. Esto importa porque, a diferencia del bot anterior
(que solo enviaba avisos hacia fuera), este bot ACEPTA comandos que
reconfiguran o ejecutan cosas en tu maquina o en la Raspberry Pi — sin
esta comprobacion, cualquiera que le escribiera al bot podria tocar tu
configuracion.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from functools import wraps

from telegram import (
    BotCommand,
    CallbackQuery,
    KeyboardButton,
    MenuButtonDefault,
    ReplyKeyboardMarkup,
    Update,
    WebAppInfo,
)
from telegram.constants import ParseMode
from telegram.error import BadRequest
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

from config.catalogo_casas import CATALOGO_CASAS, casas_que_soportan
from config.settings import Settings
from core import alias_ia, db
from core.orquestador import ejecutar_ciclo
from core.scheduler import Programador
from telegram_bot import miniapp, teclados
from telegram_bot.notificaciones import enviar_error, enviar_resultado, formatear_resumen_activos

logger = logging.getLogger(__name__)


def _teclado_panel(url: str) -> ReplyKeyboardMarkup:
    """El boton de teclado que abre la Mini App. Lleva el estado
    EMBEBIDO en la URL (pagina estatica, sin backend propio — ver
    telegram_bot/miniapp.py), asi que es una foto fija: si se reutiliza
    un boton antiguo (de un mensaje viejo, sin volver a llamar a esta
    funcion), se abre con los datos de cuando se creo, no con los de
    ahora — BUG REAL detectado por el usuario el 2026-08-23: guardaba
    cambios de verdad (la base de datos se actualizaba, la confirmacion
    "✅ Guardado" lo demuestra) pero al reabrir con el MISMO boton de
    antes seguia viendo el estado viejo, y parecia que no se habia
    guardado nada. Arreglado adjuntando un teclado fresco (con una URL
    nueva, construida en el momento) a la propia confirmacion de
    guardado en `cb_web_app_data` — Telegram sustituye el teclado
    visible por el que traiga el ultimo mensaje, asi que el mismo botón
    que ve el usuario queda apuntando ya al estado recien guardado sin
    que tenga que acordarse de volver a escribir /panel."""
    return ReplyKeyboardMarkup(
        [[KeyboardButton("🖥 Abrir panel", web_app=WebAppInfo(url=url))]],
        resize_keyboard=True,
    )


async def _editar_seguro(query: CallbackQuery, texto: str, **kwargs) -> None:
    """Como `query.edit_message_text`, pero no revienta si el contenido
    nuevo es identico al que ya se estaba mostrando — ej. el usuario
    pulsa "Volver" estando ya en esa pantalla, o pulsa dos veces el mismo
    botón. Telegram rechaza esa edición como error (BadRequest), pero no
    hay nada real que arreglar: se ignora ese caso concreto y se
    relanzan los demás errores tal cual."""
    try:
        await query.edit_message_text(texto, **kwargs)
    except BadRequest as e:
        if "Message is not modified" not in str(e):
            raise


def _solo_admin(settings: Settings) -> Callable:
    def decorador(func: Callable[..., Awaitable[None]]) -> Callable[..., Awaitable[None]]:
        @wraps(func)
        async def envoltura(update: Update, context: ContextTypes.DEFAULT_TYPE, *args, **kwargs):
            usuario_id = update.effective_user.id if update.effective_user else None
            if usuario_id != settings.telegram_admin_id:
                logger.warning("Comando rechazado: user_id=%s no es el admin configurado.", usuario_id)
                if update.effective_message:
                    await update.effective_message.reply_text("No autorizado.")
                return
            return await func(update, context, *args, **kwargs)

        return envoltura

    return decorador


def construir_app(settings: Settings) -> Application:
    solo_admin = _solo_admin(settings)
    programador = Programador()

    async def ejecutar_y_notificar(app: Application) -> None:
        try:
            verboso = db.get_setting_bool(settings.db_path, "verboso", False)
            resultado = await ejecutar_ciclo(settings.db_path, headless=settings.headless)
            await enviar_resultado(app.bot, settings.telegram_admin_id, settings.db_path, resultado, verboso)
        except Exception as e:
            logger.exception("Fallo el ciclo automatico")
            await enviar_error(app.bot, settings.telegram_admin_id, f"{type(e).__name__}: {e}")

    async def _ejecutar_programado(app: Application) -> None:
        """Punto de entrada del scheduler diario — a diferencia de un
        /ejecutar manual o de la acción "Ejecutar ciclo ahora" del panel
        (que deben funcionar siempre, ya que son un gesto explícito),
        este respeta la pausa de programación: para eso existe, poder
        decirle al bot "esta noche no" desde el panel sin tener que
        acordarse de qué hora había configurada para restaurarla luego."""
        if db.get_setting_bool(settings.db_path, "programacion_pausada", False):
            logger.info("Ejecución programada saltada: programación pausada desde el panel.")
            await app.bot.send_message(
                chat_id=settings.telegram_admin_id,
                text="⏸ Ejecución diaria saltada — programación pausada desde el panel.",
            )
            return
        await ejecutar_y_notificar(app)

    async def _revisar_ollama_en_fondo(app: Application) -> None:
        """Lanza la revisión de Ollama SIN bloquear el bucle de eventos.
        `alias_ia.revisar_cola_pendientes` usa `requests` (sincrono) y
        puede tardar segundos por caso — con una cola larga, llamarla
        directamente aqui dejaria el bot entero sin responder mientras
        tanto (ni un simple /estado), algo especialmente malo en una
        máquina sin pantalla donde reiniciar a mano no es trivial."""
        try:
            revisados, propuestas = await asyncio.get_running_loop().run_in_executor(
                None,
                alias_ia.revisar_cola_pendientes,
                settings.db_path,
                settings.ollama_host,
                settings.ollama_model,
            )
            await app.bot.send_message(
                chat_id=settings.telegram_admin_id,
                text=f"🔎 Ollama revisó {revisados} caso(s). Propuestas nuevas: {propuestas}.",
            )
        except Exception as e:
            logger.exception("Fallo la revisión de Ollama en segundo plano")
            await app.bot.send_message(
                chat_id=settings.telegram_admin_id,
                text=f"❌ Falló la revisión de Ollama: {type(e).__name__}: {e}",
            )

    async def _configurar_menu(app: Application) -> None:
        """Se llama UNA sola vez al arrancar (no hace falta refrescarlo
        nunca mas, a diferencia del botón de Mini App que tenia antes).

        Antes habia aqui un boton de menu (MenuButtonWebApp) que abria el
        panel junto al campo de texto. Se quitó a proposito: segun la
        documentacion oficial de Telegram, `sendData()` (lo que el panel
        usa para guardar cambios/lanzar acciones) SOLO funciona si la
        Mini App se abrió desde un boton de TECLADO (KeyboardButton) —
        ni un boton de menu ni uno inline lo soportan, y Telegram no
        avisa de ningún error: simplemente no entrega los datos al bot.
        Tener ese boton ahi invitaba a usarlo para guardar y que pareciera
        que "no hacía nada". Ahora el menú solo muestra la lista de
        comandos (normal de Telegram); el ÚNICO sitio que de verdad deja
        guardar es /panel (cmd_panel, más abajo)."""
        try:
            await app.bot.set_my_commands(
                [
                    BotCommand("panel", "Abrir el panel (única forma de guardar cambios)"),
                    BotCommand("menu", "Activar/desactivar casas y deportes con botones"),
                    BotCommand("hora", "Cambiar la hora de ejecución diaria"),
                    BotCommand("paralelismo", "Cambiar cuántos scrapers corren a la vez"),
                    BotCommand("verbose", "Tiempos detallados en el resumen: on/off"),
                    BotCommand("ejecutar", "Lanzar un ciclo ahora mismo"),
                    BotCommand("alias", "Revisar propuestas de alias de equipos"),
                    BotCommand("estado", "Próxima ejecución y ajustes actuales"),
                ]
            )
            await app.bot.set_chat_menu_button(
                chat_id=settings.telegram_admin_id, menu_button=MenuButtonDefault()
            )
        except Exception:
            logger.exception("No se pudo configurar el menú de comandos.")

    async def _post_init(app: Application) -> None:
        db.inicializar_db(settings.db_path)
        hora_str = db.get_setting(settings.db_path, "hora_ejecucion", "00:00")
        hora, minuto = (int(x) for x in hora_str.split(":"))
        programador.programar_ciclo_diario(lambda: _ejecutar_programado(app), hora, minuto)
        programador.iniciar()
        await _configurar_menu(app)
        logger.info("Bot listo. Próxima ejecución programada: %s", programador.proxima_ejecucion())

    # ---------------------------------------------------------- comandos
    @solo_admin
    async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        await update.effective_message.reply_text(
            "🤖 <b>Bot LateBets activo.</b>\n\n"
            "🖥 <b>/panel</b> — abrir el panel visual: casas y deportes, reportes, "
            "alias de equipos (con Ollama) y ajustes — incluye botones para lanzar "
            "un ciclo o pedirle a Ollama que revise, sin usar comandos. Es la <b>única</b> "
            "forma de guardar cambios — usa este comando cada vez que quieras cambiar algo\n"
            "/menu — activar/desactivar casas y deportes con botones de chat\n"
            "/hora HH:MM — cambiar la hora de ejecución diaria\n"
            "/paralelismo N — cambiar cuántos scrapers corren a la vez\n"
            "/verbose on|off — tiempos detallados en el resumen\n"
            "/ejecutar — lanzar un ciclo ahora mismo\n"
            "/alias — revisar propuestas de alias de equipos (Ollama)\n"
            "/estado — próxima ejecución y ajustes actuales",
            parse_mode=ParseMode.HTML,
        )

    @solo_admin
    async def cmd_panel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        # IMPORTANTE: segun la documentacion oficial de Telegram, sendData()
        # (lo que usa el panel para guardar cambios/lanzar acciones) SOLO
        # funciona cuando la Mini App se abre desde un boton de TECLADO
        # (KeyboardButton) — NO desde un boton inline ni desde el boton de
        # menu. Este es el UNICO sitio que de verdad permite guardar —
        # ver el comentario en _configurar_menu para el porque.
        url = miniapp.construir_url_panel(settings.db_path)
        await update.effective_message.reply_text(
            "Pulsa el botón de abajo (junto al teclado, no arriba en el mensaje) — "
            "cambia lo que quieras y pulsa «Guardar» ahí dentro.",
            reply_markup=_teclado_panel(url),
        )

    async def cb_web_app_data(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        # Los datos de una Mini App no pasan por CommandHandler/CallbackQueryHandler
        # normales, asi que la comprobacion de admin se hace aqui a mano en vez
        # de con el decorador @solo_admin (pensado para esos otros handlers).
        usuario_id = update.effective_user.id if update.effective_user else None
        if usuario_id != settings.telegram_admin_id:
            logger.warning("Datos de Mini App rechazados: user_id=%s no es el admin.", usuario_id)
            return

        datos = update.effective_message.web_app_data.data
        try:
            resumen, nueva_hora, acciones = miniapp.aplicar_cambios(settings.db_path, datos)
        except Exception as e:
            logger.exception("No se pudieron aplicar los cambios de la Mini App")
            await update.effective_message.reply_text(f"❌ No se pudo guardar: {type(e).__name__}: {e}")
            return

        if nueva_hora:
            hora, minuto = (int(x) for x in nueva_hora.split(":"))
            programador.programar_ciclo_diario(
                lambda: _ejecutar_programado(context.application), hora, minuto
            )

        # Las acciones se lanzan en segundo plano (create_task) y no se
        # esperan aqui: un ciclo o una revision de Ollama pueden tardar
        # minutos, y esta respuesta ("Guardado") tiene que llegar ya —
        # el resultado real llega despues como mensaje(s) aparte, igual
        # que /ejecutar y /alias revisar.
        if "ejecutar_ciclo" in acciones:
            context.application.create_task(
                ejecutar_y_notificar(context.application), name="ejecutar_ciclo_panel"
            )
        if "revisar_ollama" in acciones:
            context.application.create_task(
                _revisar_ollama_en_fondo(context.application), name="revisar_ollama_panel"
            )

        # Reenviar un teclado fresco (URL nueva, con el estado que se
        # acaba de guardar) es lo que de verdad arregla el bug de arriba
        # — el mismo botón visible en el chat queda apuntando ya a los
        # datos recien guardados, sin que el usuario tenga que acordarse
        # de escribir /panel otra vez para "refrescarlo".
        url_fresca = miniapp.construir_url_panel(settings.db_path)
        await update.effective_message.reply_text(
            f"✅ Guardado: {resumen}", reply_markup=_teclado_panel(url_fresca)
        )

    @solo_admin
    async def cmd_menu(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        await update.effective_message.reply_text(
            "¿Cómo quieres verlo?", reply_markup=teclados.teclado_menu_principal()
        )

    @solo_admin
    async def cb_volver_menu(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        query = update.callback_query
        await query.answer()
        await _editar_seguro(query, "¿Cómo quieres verlo?", reply_markup=teclados.teclado_menu_principal())

    @solo_admin
    async def cb_menu_casas(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        query = update.callback_query
        await query.answer()
        await _editar_seguro(query, "Elige una casa de apuestas:", reply_markup=teclados.teclado_casas())

    @solo_admin
    async def cb_menu_deportes(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        query = update.callback_query
        await query.answer()
        await _editar_seguro(query, "Elige un deporte:", reply_markup=teclados.teclado_deportes())

    @solo_admin
    async def cb_menu_resumen(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        query = update.callback_query
        await query.answer()
        await _editar_seguro(
            query,
            formatear_resumen_activos(settings.db_path),
            reply_markup=teclados.teclado_volver_menu(),
            parse_mode=ParseMode.HTML,
        )

    @solo_admin
    async def cb_casa(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        query = update.callback_query
        await query.answer()
        casa_id = query.data.split(":", 1)[1]
        await _editar_seguro(
            query,
            f"Deportes de {CATALOGO_CASAS[casa_id].nombre_legible}:",
            reply_markup=teclados.teclado_deportes_de_casa(settings.db_path, casa_id),
        )

    @solo_admin
    async def cb_deporte(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        query = update.callback_query
        await query.answer()
        deporte = query.data.split(":", 1)[1]
        await _editar_seguro(
            query,
            f"Casas para {deporte.capitalize()}:",
            reply_markup=teclados.teclado_casas_de_deporte(settings.db_path, deporte),
        )

    @solo_admin
    async def cb_toggle(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        query = update.callback_query
        await query.answer()
        _, casa_id, deporte, origen = query.data.split(":")
        activo_actual = db.esta_activo(settings.db_path, casa_id, deporte)
        db.set_toggle(settings.db_path, casa_id, deporte, not activo_actual)

        if origen == "dep":
            await _editar_seguro(
                query,
                f"Casas para {deporte.capitalize()}:",
                reply_markup=teclados.teclado_casas_de_deporte(settings.db_path, deporte),
            )
        else:
            await _editar_seguro(
                query,
                f"Deportes de {CATALOGO_CASAS[casa_id].nombre_legible}:",
                reply_markup=teclados.teclado_deportes_de_casa(settings.db_path, casa_id),
            )

    @solo_admin
    async def cb_bulk(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        query = update.callback_query
        await query.answer()
        _, eje, valor, accion = query.data.split(":")
        activo = accion == "on"

        if eje == "casa":
            casa_id = valor
            for deporte in CATALOGO_CASAS[casa_id].deportes:
                db.set_toggle(settings.db_path, casa_id, deporte, activo)
            await _editar_seguro(
                query,
                f"Deportes de {CATALOGO_CASAS[casa_id].nombre_legible}:",
                reply_markup=teclados.teclado_deportes_de_casa(settings.db_path, casa_id),
            )
        else:  # eje == "deporte"
            deporte = valor
            for casa in casas_que_soportan(deporte):
                db.set_toggle(settings.db_path, casa.id, deporte, activo)
            await _editar_seguro(
                query,
                f"Casas para {deporte.capitalize()}:",
                reply_markup=teclados.teclado_casas_de_deporte(settings.db_path, deporte),
            )

    @solo_admin
    async def cmd_hora(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if not context.args:
            actual = db.get_setting(settings.db_path, "hora_ejecucion", "00:00")
            await update.effective_message.reply_text(f"Hora actual: {actual}\nUso: /hora HH:MM")
            return
        try:
            hora, minuto = (int(x) for x in context.args[0].split(":"))
            if not (0 <= hora <= 23 and 0 <= minuto <= 59):
                raise ValueError
        except ValueError:
            await update.effective_message.reply_text("Formato inválido. Usa /hora HH:MM (ej. /hora 00:30)")
            return

        db.set_setting(settings.db_path, "hora_ejecucion", f"{hora:02d}:{minuto:02d}")
        app = context.application
        programador.programar_ciclo_diario(lambda: _ejecutar_programado(app), hora, minuto)
        await update.effective_message.reply_text(
            f"✅ Hora de ejecución actualizada a {hora:02d}:{minuto:02d}"
        )

    @solo_admin
    async def cmd_paralelismo(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if not context.args:
            actual = db.get_setting_int(settings.db_path, "paralelismo", 2)
            await update.effective_message.reply_text(f"Paralelismo actual: {actual}\nUso: /paralelismo N")
            return
        try:
            n = int(context.args[0])
            if n < 1:
                raise ValueError
        except ValueError:
            await update.effective_message.reply_text("Usa un número entero ≥ 1. Ej: /paralelismo 2")
            return
        db.set_setting(settings.db_path, "paralelismo", str(n))
        await update.effective_message.reply_text(f"✅ Paralelismo actualizado a {n}")

    @solo_admin
    async def cmd_verbose(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if not context.args or context.args[0].lower() not in ("on", "off"):
            actual = db.get_setting_bool(settings.db_path, "verboso", False)
            await update.effective_message.reply_text(
                f"Modo verboso: {'on' if actual else 'off'}\nUso: /verbose on|off"
            )
            return
        nuevo = context.args[0].lower() == "on"
        db.set_setting(settings.db_path, "verboso", "1" if nuevo else "0")
        await update.effective_message.reply_text(f"✅ Modo verboso: {'on' if nuevo else 'off'}")

    @solo_admin
    async def cmd_ejecutar(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        await update.effective_message.reply_text("⏳ Lanzando ciclo manual...")
        await ejecutar_y_notificar(context.application)

    @solo_admin
    async def cmd_alias(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        args = context.args

        if args and args[0] == "revisar":
            await update.effective_message.reply_text("🔎 Consultando a Ollama sobre la cola pendiente...")
            context.application.create_task(
                _revisar_ollama_en_fondo(context.application), name="revisar_ollama_cmd"
            )
            return

        if args and args[0].isdigit():
            alias_id = int(args[0])
            if len(args) > 1 and args[1].lower() == "rechazar":
                db.rechazar_alias(settings.db_path, alias_id)
                await update.effective_message.reply_text(f"🗑️ Alias #{alias_id} rechazado.")
            else:
                db.aprobar_alias(settings.db_path, alias_id)
                await update.effective_message.reply_text(f"✅ Alias #{alias_id} aprobado y en uso.")
            return

        pendientes = db.listar_alias_pendientes(settings.db_path)
        if not pendientes:
            await update.effective_message.reply_text(
                "No hay alias pendientes.\nUsa /alias revisar para que Ollama busque nuevos en la cola."
            )
            return
        lineas = ["📖 <b>Alias pendientes de aprobar:</b>\n"]
        for fila in pendientes[:20]:
            lineas.append(f"#{fila['id']}: <code>{fila['variante']}</code> → <code>{fila['canonico']}</code>")
        lineas.append(
            "\nUsa <code>/alias ID</code> para aprobar, <code>/alias ID rechazar</code> para descartar."
        )
        await update.effective_message.reply_text("\n".join(lineas), parse_mode=ParseMode.HTML)

    @solo_admin
    async def cmd_estado(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        toggles = db.listar_toggles(settings.db_path)
        activos = sum(1 for v in toggles.values() if v)
        pausada = db.get_setting_bool(settings.db_path, "programacion_pausada", False)
        await update.effective_message.reply_text(
            f"📡 Próxima ejecución: {'⏸ pausada' if pausada else programador.proxima_ejecucion()}\n"
            f"🔛 Combinaciones activas: {activos}\n"
            f"⚙️ Paralelismo: {db.get_setting_int(settings.db_path, 'paralelismo', 2)}\n"
            f"🗣️ Verboso: {'on' if db.get_setting_bool(settings.db_path, 'verboso', False) else 'off'}"
        )

    app = Application.builder().token(settings.telegram_bot_token).post_init(_post_init).build()
    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("panel", cmd_panel))
    app.add_handler(CommandHandler("menu", cmd_menu))
    app.add_handler(CommandHandler("hora", cmd_hora))
    app.add_handler(CommandHandler("paralelismo", cmd_paralelismo))
    app.add_handler(CommandHandler("verbose", cmd_verbose))
    app.add_handler(CommandHandler("ejecutar", cmd_ejecutar))
    app.add_handler(CommandHandler("alias", cmd_alias))
    app.add_handler(CommandHandler("estado", cmd_estado))
    app.add_handler(CallbackQueryHandler(cb_volver_menu, pattern=r"^volver_menu$"))
    app.add_handler(CallbackQueryHandler(cb_menu_casas, pattern=r"^menu_casas$"))
    app.add_handler(CallbackQueryHandler(cb_menu_deportes, pattern=r"^menu_deportes$"))
    app.add_handler(CallbackQueryHandler(cb_menu_resumen, pattern=r"^menu_resumen$"))
    app.add_handler(CallbackQueryHandler(cb_casa, pattern=r"^casa:"))
    app.add_handler(CallbackQueryHandler(cb_deporte, pattern=r"^dep:"))
    app.add_handler(CallbackQueryHandler(cb_toggle, pattern=r"^toggle:"))
    app.add_handler(CallbackQueryHandler(cb_bulk, pattern=r"^bulk:"))
    app.add_handler(MessageHandler(filters.StatusUpdate.WEB_APP_DATA, cb_web_app_data))
    return app
