"""Orquestador: ejecuta un ciclo completo (todas las combinaciones de
casa+deporte que esten activas), scrapeando en paralelo (acotado por el
nivel de paralelismo configurado), auditando cada resultado contra
Flashscore, y devolviendo un `ResultadoEjecucion` con discrepancias y
telemetria de tiempos por etapa.

A proposito NO envia nada a Telegram directamente — eso lo hace quien
llame a `ejecutar_ciclo` (el bot o `run.py --once`). Mantiene el
orquestador facil de probar y de lanzar en modo manual sin credenciales.
"""

from __future__ import annotations

import asyncio
import logging
import platform
import time
from datetime import datetime
from pathlib import Path

from playwright.async_api import Browser, async_playwright

from config.catalogo_casas import CATALOGO_CASAS, FLASHSCORE
from core import db
from core.matcher import auditar
from core.models import Partido, ResultadoEjecucion, TiempoEtapa

# Importar los modulos de scrapers para que se registren (@registrar) en
# scrapers.base.REGISTRO_SCRAPERS. Es el unico sitio del proyecto que
# necesita conocerlos a todos por nombre; anadir una casa nueva es
# escribir su scraper y sumar una linea de import aqui.
from scrapers import betfair as _betfair  # noqa: F401
from scrapers import bwin as _bwin  # noqa: F401
from scrapers import flashscore as _flashscore  # noqa: F401
from scrapers import pokerstars as _pokerstars  # noqa: F401
from scrapers import winamax as _winamax  # noqa: F401
from scrapers.base import REGISTRO_SCRAPERS

logger = logging.getLogger(__name__)

# NOTA: a proposito NO se fija un user_agent personalizado al crear cada
# contexto (ver mas abajo). Este proyecto llego a llevar un User-Agent
# fijo heredado del bot anterior ("Chrome/122...") mientras el Chromium
# real que instala Playwright es mucho mas nuevo (151 en el momento de
# escribir esto) — ese desajuste (la pagina cree que habla con un Chrome
# que no es el que de verdad la esta renderizando) es sospechoso numero
# uno de los cuelgues intermitentes que se observaron en vivo en
# Flashscore: scripts de terceros (anuncios sobre todo) que detectan el
# navegador por el User-Agent pueden comportarse mal si no coincide con
# el motor real. Dejar que Playwright use su User-Agent real (el que de
# verdad corresponde al Chromium que esta usando) evita ese desajuste, y
# ademas no hay que mantenerlo sincronizado a mano con cada actualizacion.

# Red de seguridad ante cuelgues reales del sitio (observado en vivo: un
# anuncio o contenido pesado en una liga concreta de Flashscore puede dejar
# la pagina ocupada sin que ninguna de las esperas internas del scraper lo
# detecte, y confirmado en vivo que ni siquiera cambiar el metodo de click
# lo evita del todo — parece inestabilidad real e intermitente del sitio,
# no algo deterministicamente arreglable desde aqui). Pasado este tiempo se
# cancela ESE intento; combinado con MAX_INTENTOS_SCRAPER de abajo, el
# scraper se reintenta con una pagina nueva antes de darse por vencido. 5
# min por intento da margen de sobra frente a los ~2-3 min que tarda incluso
# el mas pesado en condiciones normales.
TIMEOUT_SCRAPER_SEGUNDOS = 300

# Con reintentos, dos intentos independientes bastan para que un fallo
# intermitente (no uno sistematico) rara vez tumbe el ciclo entero.
MAX_INTENTOS_SCRAPER = 2


async def _scrapear_uno(
    browser: Browser,
    scraper_id: str,
    url: str,
    deporte: str,
    semaforo: asyncio.Semaphore,
) -> tuple[list[Partido], float, str | None]:
    """Ejecuta un scraper en su propio contexto/pestaña aislada, acotado
    por el semaforo de paralelismo y por TIMEOUT_SCRAPER_SEGUNDOS, con hasta
    MAX_INTENTOS_SCRAPER reintentos (cada uno con una pagina nueva) si el
    intento anterior se atasca o falla. Devuelve (partidos, segundos, error)
    — segundos es el tiempo TOTAL sumando todos los intentos."""
    cls = REGISTRO_SCRAPERS.get(scraper_id)
    if cls is None:
        return [], 0.0, f"scraper '{scraper_id}' no registrado"

    inicio_total = time.monotonic()
    ultimo_error = "sin intentos"

    async with semaforo:
        for intento in range(1, MAX_INTENTOS_SCRAPER + 1):
            etiqueta = f"{scraper_id}/{deporte}" + (f" (intento {intento})" if intento > 1 else "")
            logger.info("[%s] Iniciando scraper...", etiqueta)
            context = await browser.new_context(viewport={"width": 1920, "height": 1080})
            page = await context.new_page()
            try:
                instancia = cls()
                partidos = await asyncio.wait_for(
                    instancia.extraer(page, url, deporte), timeout=TIMEOUT_SCRAPER_SEGUNDOS
                )
                segundos = time.monotonic() - inicio_total
                logger.info("[%s] OK: %d partidos en %.1fs (total)", etiqueta, len(partidos), segundos)
                return partidos, segundos, None
            except TimeoutError:
                ultimo_error = f"Timeout tras {TIMEOUT_SCRAPER_SEGUNDOS}s (posible cuelgue del sitio)"
                logger.warning(
                    "[%s] TIMEOUT tras %ds — %s",
                    etiqueta,
                    TIMEOUT_SCRAPER_SEGUNDOS,
                    "reintentando..." if intento < MAX_INTENTOS_SCRAPER else "sin más intentos, se abandona",
                )
            except Exception as e:
                ultimo_error = f"{type(e).__name__}: {e}"
                logger.warning(
                    "[%s] ERROR: %s: %s — %s",
                    etiqueta,
                    type(e).__name__,
                    e,
                    "reintentando..." if intento < MAX_INTENTOS_SCRAPER else "sin más intentos, se abandona",
                )
            finally:
                # Si el propio cierre se queda colgado (el navegador quedo
                # en mal estado), no dejamos que eso bloquee el resto.
                try:
                    await asyncio.wait_for(context.close(), timeout=15)
                except Exception:
                    logger.warning("[%s] No se pudo cerrar el contexto limpiamente.", etiqueta)

        segundos = time.monotonic() - inicio_total
        return [], segundos, ultimo_error


def _combinaciones_activas(
    ruta_db: Path, deportes_filtro: list[str] | None
) -> dict[str, list[tuple[str, str, str]]]:
    """Devuelve, agrupado por deporte, la lista de (casa_id, casa_nombre,
    url) que estan activas segun la tabla de toggles (por defecto activo
    si nunca se ha tocado el toggle)."""
    toggles = db.listar_toggles(ruta_db)
    combinaciones: dict[str, list[tuple[str, str, str]]] = {}

    for casa in CATALOGO_CASAS.values():
        for deporte, url in casa.deportes.items():
            if not url:
                continue
            if deportes_filtro and deporte not in deportes_filtro:
                continue
            if not toggles.get((casa.id, deporte), True):
                continue
            combinaciones.setdefault(deporte, []).append((casa.id, casa.nombre_legible, url))

    return combinaciones


async def ejecutar_ciclo(
    ruta_db: Path,
    deportes_filtro: list[str] | None = None,
    paralelismo: int | None = None,
    headless: bool = True,
) -> ResultadoEjecucion:
    db.inicializar_db(ruta_db)

    # Por defecto 2: con cobertura completa (todas las ligas, no solo las
    # top) cada scraper pesado ya tira bastante de CPU el solo; meter mas
    # de 2-3 contextos de Chrome a la vez compite por recursos y en la
    # practica no acelera el ciclo, lo ralentiza. Ajustable por Telegram
    # con datos reales (modo verboso) segun la maquina donde corra.
    paralelismo = paralelismo or db.get_setting_int(ruta_db, "paralelismo", 2)
    host = platform.node()
    resultado = ResultadoEjecucion(inicio=datetime.now(), host=host, paralelismo=paralelismo)
    ejecucion_id = db.crear_ejecucion(ruta_db, host, paralelismo)

    combinaciones_por_deporte = _combinaciones_activas(ruta_db, deportes_filtro)
    semaforo = asyncio.Semaphore(max(1, paralelismo))

    logger.info(
        "Iniciando ciclo (host=%s, paralelismo=%d, deportes=%s)",
        host,
        paralelismo,
        list(combinaciones_por_deporte) or "ninguno activo",
    )

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=headless)
        try:
            for deporte, casas in combinaciones_por_deporte.items():
                logger.info("=== %s: %d casas activas ===", deporte.upper(), len(casas))
                url_fs = FLASHSCORE.deportes.get(deporte)
                if not url_fs:
                    logger.warning("[%s] No hay URL de Flashscore, se salta.", deporte)
                    resultado.errores.append(f"No hay URL de Flashscore para '{deporte}', se salta.")
                    continue

                partidos_fs, segundos_fs, error_fs = await _scrapear_uno(
                    browser, "flashscore", url_fs, deporte, semaforo
                )
                resultado.tiempos.append(TiempoEtapa(f"flashscore/{deporte}", segundos_fs))
                if error_fs:
                    resultado.errores.append(f"Flashscore/{deporte}: {error_fs}")
                    continue
                resultado.total_partidos += len(partidos_fs)

                tareas = [
                    _scrapear_uno(browser, CATALOGO_CASAS[casa_id].scraper_id, url, deporte, semaforo)
                    for casa_id, _nombre, url in casas
                ]
                resultados_casas = await asyncio.gather(*tareas)

                for (casa_id, casa_nombre, _url), (partidos_casa, segundos, error) in zip(
                    casas, resultados_casas, strict=True
                ):
                    resultado.tiempos.append(TiempoEtapa(f"{casa_id}/{deporte}", segundos))
                    if error:
                        resultado.errores.append(f"{casa_nombre}/{deporte}: {error}")
                        continue
                    resultado.total_partidos += len(partidos_casa)

                    discrepancias, stats = auditar(
                        ruta_db, casa_id, casa_nombre, deporte, partidos_casa, partidos_fs
                    )
                    logger.info(
                        "[%s/%s] Auditoría: %d/%d verificados (%.0f%%), %d discrepancias",
                        casa_id,
                        deporte,
                        stats.verificados,
                        stats.total_casa,
                        stats.cobertura,
                        len(discrepancias),
                    )
                    for d in discrepancias:
                        db.guardar_discrepancia(ruta_db, d, ejecucion_id)
                    resultado.discrepancias.extend(discrepancias)
        finally:
            await browser.close()

    resultado.fin = datetime.now()
    for tiempo in resultado.tiempos:
        db.guardar_tiempo_etapa(ruta_db, ejecucion_id, tiempo)
    db.cerrar_ejecucion(
        ruta_db,
        ejecucion_id,
        resultado.total_partidos,
        len(resultado.discrepancias),
        resultado.errores,
        resultado.duracion_segundos,
    )
    logger.info(
        "Ciclo completado en %.1fs: %d partidos, %d discrepancias, %d errores",
        resultado.duracion_segundos,
        resultado.total_partidos,
        len(resultado.discrepancias),
        len(resultado.errores),
    )
    return resultado
