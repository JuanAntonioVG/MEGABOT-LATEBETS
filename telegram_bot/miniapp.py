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
from datetime import datetime
from pathlib import Path

from config.catalogo_casas import CATALOGO_CASAS
from core import db

# GitHub Pages del repo — ver README para como activarlo (Settings > Pages).
URL_BASE_PANEL = "https://juanantoniovg.github.io/MEGABOT-LATEBETS/panel.html"

# Cuantas discrepancias/ejecuciones/alias recientes se embeben en la URL.
# La pagina es estatica y no puede pedir "un poco mas" bajo demanda, asi
# que el limite se fija aqui.
#
# IMPORTANTE — esto choco de verdad contra un limite real: GitHub Pages
# (Fastly por debajo) devuelve "414 URI Too Long" bastante antes de lo
# que parece razonable a ojo. Con LIMITE_REPORTES=20 sin acortar nada,
# la URL real llego a 9323 caracteres (medido contra la base de datos
# real del proyecto) y ya no cargaba.
#
# Los numeros de aqui abajo NO son una estimacion, se han medido
# repetidas veces contra el peor caso real (ver el test
# `test_url_panel_no_supera_un_tamano_seguro_ni_en_el_peor_caso`).
#
# Reajustados el 2026-08-23 (2ª vez): el usuario reporto nombres de
# equipo/liga cortados a la mitad en las alertas — LIMITE_TEXTO_REPORTE
# estaba en 16, demasiado corto para nombres reales ("Atlético de
# Madrid" ya son 19). Como el recorte pasa AQUI, en el servidor (el
# texto completo ni siquiera llega a la pagina — un "toca para
# expandir" en el cliente no podria enseñar mas de lo que se manda),
# subirlo de verdad significaba tocar el presupuesto de caracteres, no
# solo el CSS. Dos cosas lo pagaron:
# 1. `ef` (nombres de Flashscore) ya no se manda cuando es igual a `ec`
#    (el caso normal tras el emparejamiento, ver mas abajo) — ahorra
#    bytes sin perder nada.
# 2. LIMITE_REPORTES baja de 13 a 9 (de vuelta cerca de las 10
#    originales) para dejarle sitio a nombres mas largos.
# Resultado: LIMITE_TEXTO_REPORTE de 16 a 28 (equipos/ligas ya no se
# cortan en la inmensa mayoria de los casos reales), peor caso forzado
# ~8056 caracteres — margen parecido al de antes bajo los 9323 reales.
LIMITE_REPORTES = 9
LIMITE_EJECUCIONES = 4
LIMITE_ALIAS = 6

# Cualquier nombre de equipo/liga/alias por encima de esto se recorta con
# "…" al embeberlo en la URL — proteccion dura contra el mismo problema
# de arriba si algun dia una casa devuelve nombres inusualmente largos
# (o alguien escribe un alias manual larguisimo). La base de datos
# guarda el texto completo tal cual; esto solo afecta a lo que se ve en
# el panel.
LIMITE_TEXTO_REPORTE = 28

# Acciones que el panel puede pedir junto con el guardado. Se procesan
# en telegram_bot/bot.py (no aqui) porque necesitan el Bot/Application
# de Telegram para avisar cuando terminen — esta funcion solo valida
# cuales se pidieron y las devuelve.
ACCIONES_VALIDAS = {"ejecutar_ciclo", "revisar_ollama"}


def _recortar(texto: str | None, limite: int = LIMITE_TEXTO_REPORTE) -> str:
    texto = texto or ""
    return texto if len(texto) <= limite else texto[: limite - 1] + "…"


def _fecha_compacta(iso: str | None) -> str:
    """ "2026-08-22T21:27:34.688943" -> "22/08 21:27". Mandar la fecha
    entera con microsegundos cuesta ~26 bytes por entrada cuando lo unico
    que hace falta para leerla de un vistazo son 11."""
    if not iso:
        return ""
    try:
        d = datetime.fromisoformat(iso)
    except ValueError:
        return iso[:16]
    return d.strftime("%d/%m %H:%M")


def construir_url_panel(ruta_db: Path, tab: str = "casas") -> str:
    """URL de la Mini App con el estado actual embebido en base64.

    `tab` preselecciona la pestaña con la que se abre el panel (ej. el
    boton "Ver detalle" de una notificacion abre directo en "reportes"
    en vez de en la pestaña por defecto) — ver docs/panel.html, que lee
    `?tab=` al arrancar.
    """
    # Los deportes son un catalogo pequeño y estable (config/catalogo_casas.py)
    # — tanto el nombre visible ("Baloncesto") como el emoji los deriva el
    # propio panel.html a partir del id (tiene su propio espejo de
    # EMOJIS_DEPORTE, ver el comentario en docs/panel.html) en vez de
    # mandarlos repetidos en cada casa; con ~35 combinaciones casa+deporte
    # cada campo de mas ahi pesaba lo suyo. Quitar el emoji aqui liberó
    # ~500 caracteres de margen bajo el limite de GitHub Pages — ver
    # LIMITE_REPORTES mas abajo.
    casas = []
    for casa in CATALOGO_CASAS.values():
        deportes = [
            {"id": deporte, "a": db.esta_activo(ruta_db, casa.id, deporte)} for deporte in casa.deportes
        ]
        casas.append({"id": casa.id, "n": casa.nombre_legible, "d": deportes})

    alias_pendientes = [
        {"id": f["id"], "va": _recortar(f["variante"]), "ca": _recortar(f["canonico"])}
        for f in db.listar_alias_pendientes(ruta_db, limite=LIMITE_ALIAS)
    ]
    alias_aprobados = [
        {"id": f["id"], "va": _recortar(f["variante"]), "ca": _recortar(f["canonico"]), "fu": f["fuente"]}
        for f in db.listar_alias_aprobados(ruta_db, limite=LIMITE_ALIAS)
    ]

    filas_reportes = db.listar_discrepancias_recientes(ruta_db, limite=LIMITE_REPORTES)
    reportes = []
    for f in filas_reportes:
        ec = [_recortar(f["equipo_local_casa"]), _recortar(f["equipo_visitante_casa"])]
        ef = [_recortar(f["equipo_local_fs"]), _recortar(f["equipo_visitante_fs"])]
        reporte = {
            "cs": _recortar(f["casa_nombre"]),
            "dp": f["deporte"],
            "lc": _recortar(f["liga"] or "Desconocida"),
            "lf": _recortar(f["liga_fs"] or "Desconocida"),
            "hc": f["detalle_casa"],
            "hf": f["detalle_fs"],
            "ec": ec,
            "s": f["similitud"],
            "p": f["prioridad"],
            "t": _fecha_compacta(f["creado_en"]),
        }
        # "ef" (nombres de Flashscore) solo se manda si de verdad difiere
        # de "ec" — tras el emparejamiento (>=70% de similitud) casi
        # siempre es el mismo texto o casi, y mandarlo siempre duplicaba
        # bytes sin aportar nada la mayoria de las veces. El panel (ver
        # docs/panel.html) usa "ec" como valor por defecto cuando "ef"
        # no viene.
        if ef != ec:
            reporte["ef"] = ef
        reportes.append(reporte)

    ejecuciones = [
        {
            "h": f["host"] or "?",
            "i": _fecha_compacta(f["iniciado_en"]),
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
        # Con el limite tan ajustado, tiene que quedar claro cuando se
        # esta viendo solo una parte — nada se pierde en la base de
        # datos, solo en esta vista compacta.
        "rm": len(filas_reportes) >= LIMITE_REPORTES,
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
