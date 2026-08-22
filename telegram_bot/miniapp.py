"""Puente entre el bot y la Mini App de Telegram (panel web que se abre
DENTRO de Telegram, alojado como pagina estatica en GitHub Pages —
ver docs/panel.html).

La pagina es estatica y no tiene forma de leer la base de datos por su
cuenta, asi que el estado actual (toggles, paralelismo, hora, verboso,
alias pendientes/aprobados, ultimas discrepancias y ejecuciones) se le
manda EMBEBIDO en la propia URL cada vez que se abre. Cuando el usuario
pulsa "Guardar cambios" dentro de la Mini App, Telegram entrega esos
cambios al bot como un mensaje especial (`web_app_data`), que se
decodifica y aplica aqui.
"""

from __future__ import annotations

import base64
import json
from pathlib import Path

from config.catalogo_casas import CATALOGO_CASAS, EMOJIS_DEPORTE
from core import db

# GitHub Pages del repo — ver README para como activarlo (Settings > Pages).
URL_BASE_PANEL = "https://juanantoniovg.github.io/MEGABOT-LATEBETS/panel.html"

# Cuantas discrepancias/ejecuciones recientes se embeben en la URL. La
# pagina es estatica y no puede pedir "un poco mas" bajo demanda, asi
# que el limite se fija aqui — de sobra para revisar una noche normal,
# acotado para que la URL no crezca sin control con meses de historial.
LIMITE_REPORTES = 20
LIMITE_EJECUCIONES = 6

# Acciones que el panel puede pedir junto con el guardado. Se procesan
# en telegram_bot/bot.py (no aqui) porque necesitan el Bot/Application
# de Telegram para avisar cuando terminen — esta funcion solo valida
# cuales se pidieron y las devuelve.
ACCIONES_VALIDAS = {"ejecutar_ciclo", "revisar_ollama"}


def construir_url_panel(ruta_db: Path, tab: str = "casas") -> str:
    """URL de la Mini App con el estado actual embebido en base64.

    `tab` preselecciona la pestaña con la que se abre el panel (ej. el
    boton "Ver detalle" de una notificacion abre directo en "reportes"
    en vez de en la pestaña por defecto) — ver docs/panel.html, que lee
    `?tab=` al arrancar.
    """
    casas = []
    for casa in CATALOGO_CASAS.values():
        deportes = [
            {
                "id": deporte,
                "n": deporte.capitalize(),
                "e": EMOJIS_DEPORTE.get(deporte, "❓"),
                "a": db.esta_activo(ruta_db, casa.id, deporte),
            }
            for deporte in casa.deportes
        ]
        casas.append({"id": casa.id, "n": casa.nombre_legible, "d": deportes})

    alias_pendientes = [
        {"id": f["id"], "va": f["variante"], "ca": f["canonico"]} for f in db.listar_alias_pendientes(ruta_db)
    ]
    alias_aprobados = [
        {"id": f["id"], "va": f["variante"], "ca": f["canonico"], "fu": f["fuente"]}
        for f in db.listar_alias_aprobados(ruta_db)
    ]

    reportes = [
        {
            "cs": f["casa_nombre"],
            "dp": f["deporte"],
            "lc": f["liga"] or "Desconocida",
            "lf": f["liga_fs"] or "Desconocida",
            "hc": f["detalle_casa"],
            "hf": f["detalle_fs"],
            "ec": [f["equipo_local_casa"], f["equipo_visitante_casa"]],
            "ef": [f["equipo_local_fs"], f["equipo_visitante_fs"]],
            "s": f["similitud"],
            "p": f["prioridad"],
            "t": f["creado_en"],
        }
        for f in db.listar_discrepancias_recientes(ruta_db, limite=LIMITE_REPORTES)
    ]

    ejecuciones = [
        {
            "h": f["host"] or "?",
            "i": f["iniciado_en"],
            "d": f["duracion_segundos"] or 0,
            "pt": f["total_partidos"] or 0,
            "dc": f["total_discrepancias"] or 0,
            "er": len(json.loads(f["errores"])) if f["errores"] else 0,
        }
        for f in db.listar_ejecuciones_recientes(ruta_db, limite=LIMITE_EJECUCIONES)
    ]

    estado = {
        "c": casas,
        "p": db.get_setting_int(ruta_db, "paralelismo", 2),
        "h": db.get_setting(ruta_db, "hora_ejecucion", "00:00"),
        "v": db.get_setting_bool(ruta_db, "verboso", False),
        "pp": db.get_setting_bool(ruta_db, "programacion_pausada", False),
        "al": {"pend": alias_pendientes, "apr": alias_aprobados},
        "r": reportes,
        "ej": ejecuciones,
    }
    crudo = json.dumps(estado, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    payload = base64.urlsafe_b64encode(crudo).decode("ascii")
    url = f"{URL_BASE_PANEL}?estado={payload}"
    if tab != "casas":
        url += f"&tab={tab}"
    return url


def aplicar_cambios(ruta_db: Path, datos_json: str) -> tuple[str, str | None, list[str]]:
    """Aplica a la base de datos los cambios mandados desde la Mini App.

    Devuelve (resumen_legible, nueva_hora_o_None, acciones_pedidas) —
    nueva_hora solo viene rellena si el usuario cambio la hora de
    ejecucion, para que quien llame pueda reprogramar el scheduler (esta
    funcion no conoce el Programador, eso es responsabilidad de
    telegram_bot/bot.py, que tambien es quien lanza `acciones_pedidas`).
    """
    cambios = json.loads(datos_json)
    partes: list[str] = []
    nueva_hora: str | None = None

    toggles = cambios.get("toggles", [])
    for t in toggles:
        db.set_toggle(ruta_db, t["c"], t["d"], bool(t["a"]))
    if toggles:
        partes.append(f"{len(toggles)} casa(s)/deporte(s) actualizados")

    if "paralelismo" in cambios:
        db.set_setting(ruta_db, "paralelismo", str(int(cambios["paralelismo"])))
        partes.append(f"paralelismo → {cambios['paralelismo']}")

    if "hora" in cambios and cambios["hora"]:
        nueva_hora = str(cambios["hora"])
        db.set_setting(ruta_db, "hora_ejecucion", nueva_hora)
        partes.append(f"hora → {nueva_hora}")

    if "verboso" in cambios:
        db.set_setting(ruta_db, "verboso", "1" if cambios["verboso"] else "0")
        partes.append(f"verbose → {'on' if cambios['verboso'] else 'off'}")

    if "programacion_pausada" in cambios:
        pausada = bool(cambios["programacion_pausada"])
        db.set_setting(ruta_db, "programacion_pausada", "1" if pausada else "0")
        partes.append("programación diaria → pausada" if pausada else "programación diaria → reanudada")

    decisiones = cambios.get("alias_decisiones", [])
    aprobados = rechazados = 0
    for d in decisiones:
        if d.get("accion") == "aprobar":
            db.aprobar_alias(ruta_db, int(d["id"]))
            aprobados += 1
        elif d.get("accion") == "rechazar":
            db.eliminar_alias(ruta_db, int(d["id"]))
            rechazados += 1
    if aprobados:
        partes.append(f"{aprobados} alias aprobado(s)")
    if rechazados:
        partes.append(f"{rechazados} alias eliminado(s)")

    nuevos = cambios.get("alias_nuevos", [])
    creados = 0
    for a in nuevos:
        variante = str(a.get("variante", "")).strip()
        canonico = str(a.get("canonico", "")).strip()
        if variante and canonico:
            db.guardar_alias(ruta_db, variante, canonico, fuente="manual", aprobado=True)
            creados += 1
    if creados:
        partes.append(f"{creados} alias nuevo(s) añadido(s) a mano")

    acciones_pedidas = [a for a in cambios.get("acciones", []) if a in ACCIONES_VALIDAS]
    if "ejecutar_ciclo" in acciones_pedidas:
        partes.append("ciclo manual lanzado")
    if "revisar_ollama" in acciones_pedidas:
        partes.append("revisión de Ollama lanzada")

    resumen = ", ".join(partes) if partes else "sin cambios"
    return resumen, nueva_hora, acciones_pedidas
