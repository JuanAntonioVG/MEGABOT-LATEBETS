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

import logging
from collections.abc import Awaitable, Callable
from functools import wraps

from telegram import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    MenuButtonWebApp,
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
            await enviar_resultado(app.bot, settings.telegram_admin_id, resultado, verboso)
        except Exception as e:
            logger.exception("Fallo el ciclo automatico")
            await enviar_error(app.bot, settings.telegram_admin_id, f"{type(e).__name__}: {e}")

    async def _actualizar_boton_menu(app: Application) -> None:
        """Refresca el boton persistente (el que aparece junto al campo de
        texto) con la Mini App, embebiendo el estado ACTUAL en su URL —
        la pagina es estatica y no puede leer la base de datos ella sola,
        asi que hay que llamarla de nuevo cada vez que algo cambia para
        que el boton no abra un panel con datos desactualizados."""
        try:
            url = miniapp.construir_url_panel(settings.db_path)
            await app.bot.set_chat_menu_button(
                chat_id=settings.telegram_admin_id,
                menu_button=MenuButtonWebApp(text="Panel", web_app=WebAppInfo(url=url)),
            )
        except Exception:
            logger.exception("No se pudo actualizar el botón del panel (Mini App).")

    async def _post_init(app: Application) -> None:
        db.inicializar_db(settings.db_path)
        hora_str = db.get_setting(settings.db_path, "hora_ejecucion", "00:00")
        hora, minuto = (int(x) for x in hora_str.split(":"))
        programador.programar_ciclo_diario(lambda: ejecutar_y_notificar(app), hora, minuto)
        programador.iniciar()
        await _actualizar_boton_menu(app)
        logger.info("Bot listo. Próxima ejecución programada: %s", programador.proxima_ejecucion())

    # ---------------------------------------------------------- comandos
    @solo_admin
    async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        await update.effective_message.reply_text(
            "🤖 <b>Bot LateBets activo.</b>\n\n"
            "🖥 <b>/panel</b> — abrir el panel visual (casas, deportes, ajustes) "
            "— también disponible en el botón junto al campo de texto\n"
            "/menu — lo mismo pero con botones de chat, sin salir del chat\n"
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
        url = miniapp.construir_url_panel(settings.db_path)
        teclado = InlineKeyboardMarkup([[InlineKeyboardButton("🖥 Abrir panel", web_app=WebAppInfo(url=url))]])
        await update.effective_message.reply_text(
            "Se abre como una pantalla dentro de Telegram — cambia lo que quieras y pulsa "
            "«Guardar cambios» ahí dentro.",
            reply_markup=teclado,
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
            resumen, nueva_hora = miniapp.aplicar_cambios(settings.db_path, datos)
        except Exception as e:
            logger.exception("No se pudieron aplicar los cambios de la Mini App")
            await update.effective_message.reply_text(f"❌ No se pudo guardar: {type(e).__name__}: {e}")
            return

        if nueva_hora:
            hora, minuto = (int(x) for x in nueva_hora.split(":"))
            programador.programar_ciclo_diario(
                lambda: ejecutar_y_notificar(context.application), hora, minuto
            )

        await _actualizar_boton_menu(context.application)
        await update.effective_message.reply_text(f"✅ Guardado: {resumen}")

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
        await _actualizar_boton_menu(context.application)

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
            await _actualizar_boton_menu(context.application)
            await _editar_seguro(
                query,
                f"Deportes de {CATALOGO_CASAS[casa_id].nombre_legible}:",
                reply_markup=teclados.teclado_deportes_de_casa(settings.db_path, casa_id),
            )
        else:  # eje == "deporte"
            deporte = valor
            for casa in casas_que_soportan(deporte):
                db.set_toggle(settings.db_path, casa.id, deporte, activo)
            await _actualizar_boton_menu(context.application)
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
        programador.programar_ciclo_diario(lambda: ejecutar_y_notificar(app), hora, minuto)
        await _actualizar_boton_menu(app)
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
        await _actualizar_boton_menu(context.application)
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
        await _actualizar_boton_menu(context.application)
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
            revisados, propuestas = alias_ia.revisar_cola_pendientes(
                settings.db_path, settings.ollama_host, settings.ollama_model
            )
            await update.effective_message.reply_text(
                f"Revisados {revisados} casos. Propuestas nuevas: {propuestas}.\nUsa /alias para verlas."
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
        await update.effective_message.reply_text(
            f"📡 Próxima ejecución: {programador.proxima_ejecucion()}\n"
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
