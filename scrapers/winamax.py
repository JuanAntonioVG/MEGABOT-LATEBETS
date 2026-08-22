"""Scraper de Winamax.

Verificado en vivo el 2026-08-22. Tres hallazgos importantes frente al
bot anterior:

1. Winamax renderiza la lista de partidos con una lista VIRTUALIZADA
   (`ReactVirtualized__Grid`): solo los partidos visibles en pantalla
   existen en el DOM en cada momento. Por eso NO basta con hacer scroll
   hasta el final y leer el HTML una vez — hay que ir leyendo el HTML
   DURANTE el scroll y acumular partidos unicos por su
   `data-testid="match-card-<id>"`, igual que hacia el bot anterior con
   su "estrategia acumulativa". Se conserva esa logica aqui.

2. Las clases internas de cada tarjeta (equipos, liga, marcador) ya NO
   tienen nombres semanticos (`competitorName_`, `time_`...) como antes:
   ahora son clases opacas generadas por styled-components (`sc-XXXXXX`),
   sin ningun sufijo legible al que agarrarse. Esto es, con diferencia,
   el punto mas fragil de los 5 scrapers — cualquier redeploy de Winamax
   puede cambiarlas. Se han verificado HOY contra un partido en vivo; la
   extraccion de la HORA de partidos programados (no en vivo) usa un
   fallback por expresion regular sobre el texto completo de la tarjeta
   porque no se pudo verificar en vivo ningun partido programado en el
   momento de escribir esto (solo habia partidos en directo en la
   muestra). Revisar esto es la primera prioridad si Winamax empieza a
   devolver menos partidos programados de los esperados.

3. El listado NO esta limitado a hoy: sigue scrolleando hacia partidos de
   "Mañana" y dias siguientes. Detectado en vivo un separador de dia
   (`<div>Mañana</div>`) en mitad del scroll. Como el bot solo debe
   avisar de discrepancias del dia en curso, se para la acumulacion en
   cuanto aparece ese separador — todo lo anterior en el documento se
   asume de hoy (no hay separador explicito de "Hoy", los partidos de hoy
   simplemente empiezan sin cabecera).
"""

from __future__ import annotations

import logging

from bs4 import BeautifulSoup
from bs4.element import Tag
from playwright.async_api import Page

from core.models import EstadoPartido, Partido
from scrapers.base import ScraperBase, aceptar_cookies, extraer_hora, registrar

logger = logging.getLogger(__name__)

SELECTOR_TARJETA = 'div[data-testid^="match-card-"]'
SELECTOR_DIVISOR_FECHA = "div.sc-faHdxz"


@registrar
class WinamaxScraper(ScraperBase):
    id = "winamax"
    nombre_legible = "Winamax"

    async def extraer(self, page: Page, url: str, deporte: str) -> list[Partido]:
        await page.goto(url, timeout=30000, wait_until="domcontentloaded")
        await aceptar_cookies(page)
        await page.wait_for_timeout(2000)

        partidos_por_id: dict[str, Partido] = {}
        scrolls_sin_novedad = 0
        max_scrolls_sin_novedad = 10

        # A proposito SIN tope bajo: queremos TODOS los partidos del mundo,
        # no solo los de las ligas principales. Medido en vivo: con el tope
        # antiguo de 80 se cortaba a los 776 partidos sin haber tenido ni
        # un solo scroll "sin novedad" todavia. 600 es solo red de
        # seguridad; quien decide cuando parar de verdad es
        # `max_scrolls_sin_novedad` (10 scrolls seguidos sin partidos nuevos).
        for intento in range(600):
            html = await page.content()
            soup = BeautifulSoup(html, "lxml")
            nuevos = 0

            # Recorremos tarjetas Y divisores de dia EN ORDEN DE DOCUMENTO,
            # para poder cortar en cuanto aparezca un divisor que no sea
            # "Hoy" — todo lo que venga a partir de ahi (en esta y en
            # futuras capturas) es de otro dia y no nos interesa.
            combinados = soup.select(f"{SELECTOR_DIVISOR_FECHA}, {SELECTOR_TARJETA}")
            ids_divisor = {id(el) for el in soup.select(SELECTOR_DIVISOR_FECHA)}
            cruzo_a_otro_dia = False

            for elemento in combinados:
                if id(elemento) in ids_divisor:
                    texto_divisor = elemento.get_text(strip=True).lower()
                    if texto_divisor and not texto_divisor.startswith("hoy"):
                        cruzo_a_otro_dia = True
                        break
                    continue

                tarjeta_id = elemento.get("data-testid", "")
                if tarjeta_id in partidos_por_id:
                    continue
                partido = self._parsear_tarjeta(elemento, deporte)
                if partido:
                    partidos_por_id[tarjeta_id] = partido
                    nuevos += 1

            if cruzo_a_otro_dia:
                logger.info(
                    "Encontrado el cambio a otro dia tras %d partidos de hoy — se detiene aqui.",
                    len(partidos_por_id),
                )
                break

            scrolls_sin_novedad = 0 if nuevos > 0 else scrolls_sin_novedad + 1
            logger.info(
                "Scroll %d: %d nuevos, %d acumulados (%d scrolls sin novedad)",
                intento + 1,
                nuevos,
                len(partidos_por_id),
                scrolls_sin_novedad,
            )
            if scrolls_sin_novedad >= max_scrolls_sin_novedad:
                break

            await page.mouse.wheel(0, 1800)
            await page.wait_for_timeout(500)

        return list(partidos_por_id.values())

    @staticmethod
    def _parsear_tarjeta(tarjeta: Tag, deporte: str) -> Partido | None:
        nombres_equipos = tarjeta.select("div.sc-brsddS")
        if len(nombres_equipos) < 2:
            return None
        equipo_local = nombres_equipos[0].get_text(strip=True)
        equipo_visitante = nombres_equipos[1].get_text(strip=True)
        if not equipo_local or not equipo_visitante:
            return None

        liga_tag = tarjeta.select_one("span.sc-huGNkN")
        liga = liga_tag.get_text(strip=True) if liga_tag else "Desconocida"

        en_vivo = tarjeta.select_one('[data-testid="live-indicator"]') is not None

        if en_vivo:
            timer_tag = tarjeta.select_one("span.sc-hRbjmR")
            detalle = timer_tag.get_text(strip=True) if timer_tag else "En Vivo"
            estado = EstadoPartido.EN_VIVO
        else:
            # Fallback: no se verifico en vivo el selector exacto de la hora
            # programada (ver aviso al principio del fichero). Se busca un
            # patron HH:MM en todo el texto de la tarjeta como red de
            # seguridad.
            estado = EstadoPartido.PROGRAMADO
            detalle = extraer_hora(tarjeta.get_text(" ", strip=True))

        return Partido(
            equipo_local=equipo_local,
            equipo_visitante=equipo_visitante,
            estado=estado,
            detalle_estado=detalle,
            liga=liga,
            fuente="winamax",
            deporte=deporte,
        )
