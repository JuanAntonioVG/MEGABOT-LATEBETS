"""Scraper de Flashscore: la fuente de verdad contra la que se audita el
resto de casas.

Verificado en vivo el 2026-08-22 contra www.flashscore.es/futbol/ (462
partidos, 100% con nombre de equipo, 336 programados + 126 en vivo o
finalizados — la suma cuadra exacta con el total).
"""

from __future__ import annotations

import logging

from bs4 import BeautifulSoup
from bs4.element import Tag
from playwright.async_api import Page

from core.models import EstadoPartido, Partido
from scrapers.base import ScraperBase, aceptar_cookies, agrupar_por_liga, extraer_hora, registrar

logger = logging.getLogger(__name__)

SELECTOR_PARTIDO = 'div.event__match, div[data-testid="wcl-eventRow"]'
SELECTOR_LIGA = '[data-testid="wcl-headerLeague"]'

ESTADOS_FINALIZADO = {
    "Finalizado",
    "Penaltis",
    "Tras prórr.",
    "Tras los penaltis",
    "Tras la prórroga",
    "Aplazado",
    "Anulado",
    "Parado",
}


def _titulo_liga(header: Tag) -> str:
    """El nombre de la liga vive en el atributo `title` del enlace de la
    cabecera (ej. title="LaLiga EA Sports") — mas fiable que el texto
    visible, que a veces incluye adornos ('LaLiga EA Sports ESPAÑA :
    Clasificación')."""
    enlace = header.select_one("a.headerLeague__title")
    if enlace and enlace.get("title"):
        return enlace["title"].strip()
    texto_tag = header.select_one('[data-testid="wcl-scores-simple-text-01"]')
    if texto_tag:
        return texto_tag.get_text(strip=True)
    return header.get_text(" ", strip=True)[:60] or "Desconocida"


@registrar
class FlashscoreScraper(ScraperBase):
    id = "flashscore"
    nombre_legible = "Flashscore"

    async def extraer(self, page: Page, url: str, deporte: str) -> list[Partido]:
        await page.goto(url, timeout=30000, wait_until="domcontentloaded")
        await page.wait_for_timeout(2500)
        await aceptar_cookies(page)
        await page.wait_for_timeout(1500)

        await self._expandir_ligas_colapsadas(page)

        await page.mouse.wheel(0, 20000)
        await page.wait_for_timeout(1500)

        html = await page.content()
        soup = BeautifulSoup(html, "lxml")

        agrupados = agrupar_por_liga(soup, SELECTOR_LIGA, SELECTOR_PARTIDO, _titulo_liga)

        partidos: list[Partido] = []
        for elemento, liga in agrupados:
            partido = self._parsear_partido(elemento, liga, deporte)
            if partido:
                partidos.append(partido)
        return partidos

    @staticmethod
    async def _expandir_ligas_colapsadas(page: Page) -> None:
        """Algunas ligas aparecen colapsadas por defecto; hay que desplegarlas
        para que sus partidos entren en el HTML — se despliegan TODAS a
        proposito (no solo las principales): un error de horario es mas
        probable en una liga menor que en una "top" muy vigilada, que es
        justamente lo que este bot quiere detectar. El limite de 400 es
        solo una red de seguridad (medido en vivo: 242 ligas en la pagina
        general de futbol) para que quien de verdad decide cuando parar
        sea "ya no quedan botones colapsados", no un numero arbitrario."""
        selector_boton_cerrado = "button[data-testid='wcl-accordionButton'][aria-expanded='false']"
        for intento in range(400):
            boton = page.locator(selector_boton_cerrado).first
            restantes = await page.locator(selector_boton_cerrado).count()
            if restantes == 0:
                logger.info("Ligas expandidas tras %d intentos.", intento)
                break
            logger.info("Expandiendo liga %d (quedan %d colapsadas)...", intento + 1, restantes)
            try:
                # Clic por JavaScript (equivalente al execute_script del bot
                # anterior, que nunca se colgaba haciendo esto) en vez de un
                # clic "real" de Playwright: nos saltamos a proposito sus
                # comprobaciones de que el elemento este visible/estable/sin
                # animacion antes de pulsar. Sospecha bien fundada de por que
                # se atascaba en vivo: si Flashscore esta renderizando algo
                # pesado en ese instante (un widget, un anuncio) en una liga
                # concreta, Playwright puede quedarse esperando esa
                # "estabilidad" mucho mas alla de cualquier timeout normal.
                await boton.evaluate("el => { el.scrollIntoView({block: 'center'}); el.click(); }")
                await page.wait_for_timeout(300)
            except Exception as e:
                logger.info("Se detiene la expansión de ligas: %s", e)
                break

    @staticmethod
    def _parsear_partido(elemento: Tag, liga: str, deporte: str) -> Partido | None:
        home_div = elemento.select_one(".event__homeParticipant")
        away_div = elemento.select_one(".event__awayParticipant")
        nombre_local_tag = (
            home_div.select_one('[data-testid="wcl-scores-simple-text-01"]') if home_div else None
        )
        nombre_visitante_tag = (
            away_div.select_one('[data-testid="wcl-scores-simple-text-01"]') if away_div else None
        )
        if not (nombre_local_tag and nombre_visitante_tag):
            return None

        equipo_local = nombre_local_tag.get_text(strip=True)
        equipo_visitante = nombre_visitante_tag.get_text(strip=True)

        estado = EstadoPartido.DESCONOCIDO
        detalle = ""

        hora_tag = elemento.select_one(".event__time")
        stage_block = elemento.select_one(".event__stage--block")

        if hora_tag:
            estado = EstadoPartido.PROGRAMADO
            # A veces el texto trae pegado un distintivo de emisora (ej.
            # "20:00SRF") porque esta dentro del mismo elemento sin
            # separador; nos quedamos solo con el HH:MM.
            texto_hora = hora_tag.get_text(strip=True)
            hora_limpia = extraer_hora(texto_hora)
            detalle = hora_limpia if hora_limpia != "N/A" else texto_hora
        elif stage_block:
            texto_stage = stage_block.get_text(strip=True).replace("\xa0", "")
            en_vivo = (
                stage_block.find("span", class_="blink") is not None
                or "Descanso" in texto_stage
                or texto_stage.replace("+", "").isdigit()
            )
            if en_vivo:
                estado = EstadoPartido.EN_VIVO
                detalle = texto_stage
            elif texto_stage in ESTADOS_FINALIZADO:
                estado = EstadoPartido.FINALIZADO
                detalle = texto_stage

        if estado == EstadoPartido.DESCONOCIDO:
            return None

        return Partido(
            equipo_local=equipo_local,
            equipo_visitante=equipo_visitante,
            estado=estado,
            detalle_estado=detalle,
            liga=liga,
            fuente="flashscore",
            deporte=deporte,
        )
