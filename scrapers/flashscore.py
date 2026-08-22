"""Scraper de Flashscore: la fuente de verdad contra la que se audita el
resto de casas.

Verificado en vivo el 2026-08-22 contra www.flashscore.es/futbol/ (462
partidos, 100% con nombre de equipo, 336 programados + 126 en vivo o
finalizados — la suma cuadra exacta con el total).

Ampliado y verificado en vivo el mismo dia contra /baloncesto/: el
selector de la FILA de partido (SELECTOR_PARTIDO) ya era generico y
funcionaba tal cual, pero el de NOMBRE DE EQUIPO no — baloncesto no usa
el envoltorio .event__xxxParticipant + span con testid que usa futbol,
el texto va directo dentro de .event__participant--xxx. Sin este
segundo patron, baloncesto devolvia 0 partidos pese a haber filas de
verdad en la pagina (ver `_extraer_nombre`). No verificado todavia en
vivo para voleibol/waterpolo, pero es razonable esperar el mismo patron
"directo" ya que es la estructura mas simple de las dos.
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
    def _extraer_nombre(elemento: Tag, lado: str) -> str | None:
        """El nombre del equipo no vive siempre en el mismo sitio: en
        futbol esta DENTRO de un div .event__xxxParticipant, en un span
        [data-testid="wcl-scores-simple-text-01"] anidado. En baloncesto
        (verificado en vivo 2026-08-22 contra /baloncesto/ — 0 de 27
        partidos se reconocian con el patron de futbol) el texto esta
        DIRECTAMENTE dentro de div.event__participant--xxx, sin ese
        envoltorio ni el span. Se prueban los dos patrones; el segundo es
        ademas el candidato mas probable para el resto de deportes de
        equipo (voleibol, waterpolo...) por ser la estructura mas simple
        de las dos."""
        contenedor = elemento.select_one(f".event__{lado}Participant")
        if contenedor:
            nombre_tag = contenedor.select_one('[data-testid="wcl-scores-simple-text-01"]')
            if nombre_tag:
                return nombre_tag.get_text(strip=True)
        directo = elemento.select_one(f".event__participant--{lado}")
        if directo:
            return directo.get_text(strip=True)
        return None

    @staticmethod
    def _parsear_partido(elemento: Tag, liga: str, deporte: str) -> Partido | None:
        equipo_local = FlashscoreScraper._extraer_nombre(elemento, "home")
        equipo_visitante = FlashscoreScraper._extraer_nombre(elemento, "away")
        if not (equipo_local and equipo_visitante):
            return None

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
