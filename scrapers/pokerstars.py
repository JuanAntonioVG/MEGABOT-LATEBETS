"""Scraper de Pokerstars.

Verificado en vivo el 2026-08-22 contra la pagina de futbol:
- 160 partidos encontrados con `li[data-testid="event"]` (selector vigente).
- Cada liga vive en un `details[data-testid="sports-expandable-accordion"]`
  con su `summary` (nombre de liga limpio, ej. "La Liga Española") y sus
  partidos dentro del MISMO elemento — verificado sin duplicados ni
  anidamiento (160 partidos totales = 160 sumando por accordion).
- Hora: atributo `datetime` de `<time>`, formato limpio "17:00".

La rama de baloncesto/voleibol (formato tabla,
`table[data-testid="compound-table-column"]`) se conserva del bot
anterior pero NO se ha vuelto a verificar en vivo hoy.
"""

from __future__ import annotations

from bs4 import BeautifulSoup
from bs4.element import Tag
from playwright.async_api import Page

from core.models import EstadoPartido, Partido
from scrapers.base import ScraperBase, aceptar_cookies, extraer_hora, registrar

SELECTOR_ACCORDION = 'details[data-testid="sports-expandable-accordion"]'
SELECTOR_PARTIDO_FUTBOL = 'li[data-testid="event"]'
SELECTOR_TABLA_BALONCESTO = 'table[data-testid="compound-table-column"]'


@registrar
class PokerstarsScraper(ScraperBase):
    id = "pokerstars"
    nombre_legible = "Pokerstars"

    async def extraer(self, page: Page, url: str, deporte: str) -> list[Partido]:
        await page.goto(url, timeout=30000, wait_until="domcontentloaded")
        await aceptar_cookies(page)
        await page.wait_for_timeout(5000)

        html = await page.content()
        soup = BeautifulSoup(html, "lxml")

        if soup.select_one(SELECTOR_PARTIDO_FUTBOL):
            return self._extraer_formato_lista(soup, deporte)
        if soup.select_one(SELECTOR_TABLA_BALONCESTO):
            return self._extraer_formato_tabla(soup, deporte)
        return []

    @staticmethod
    def _extraer_formato_lista(soup: BeautifulSoup, deporte: str) -> list[Partido]:
        partidos: list[Partido] = []
        for accordion in soup.select(SELECTOR_ACCORDION):
            resumen = accordion.select_one("summary")
            liga = resumen.get_text(strip=True) if resumen else "Desconocida"
            for evento in accordion.select(SELECTOR_PARTIDO_FUTBOL):
                partido = PokerstarsScraper._parsear_evento_lista(evento, liga, deporte)
                if partido:
                    partidos.append(partido)
        return partidos

    @staticmethod
    def _parsear_evento_lista(evento: Tag, liga: str, deporte: str) -> Partido | None:
        equipos_tags = evento.select("span._ee287b1")
        if len(equipos_tags) < 2:
            return None
        # El primer span incluye un separador "-" anidado que hay que descartar.
        equipo_local = equipos_tags[0].get_text(strip=True).replace("-", "").strip()
        equipo_visitante = equipos_tags[1].get_text(strip=True)

        hora_tag = evento.select_one("time[datetime]")
        detalle = hora_tag["datetime"].strip() if hora_tag and hora_tag.get("datetime") else "N/A"
        if detalle == "N/A" and hora_tag:
            detalle = extraer_hora(hora_tag.get_text(strip=True))

        return Partido(
            equipo_local=equipo_local,
            equipo_visitante=equipo_visitante,
            estado=EstadoPartido.PROGRAMADO,
            detalle_estado=detalle,
            liga=liga,
            fuente="pokerstars",
            deporte=deporte,
        )

    @staticmethod
    def _extraer_formato_tabla(soup: BeautifulSoup, deporte: str) -> list[Partido]:
        partidos: list[Partido] = []
        for accordion in soup.select(SELECTOR_ACCORDION):
            resumen = accordion.select_one("summary")
            liga = resumen.get_text(strip=True) if resumen else "Desconocida"
            for tabla in accordion.select(SELECTOR_TABLA_BALONCESTO):
                partido = PokerstarsScraper._parsear_tabla(tabla, liga, deporte)
                if partido:
                    partidos.append(partido)
        return partidos

    @staticmethod
    def _parsear_tabla(tabla: Tag, liga: str, deporte: str) -> Partido | None:
        filas_equipos = tabla.select("tr._309a519")
        if len(filas_equipos) < 2:
            return None
        equipo_local_tag = filas_equipos[0].select_one("a")
        equipo_visitante_tag = filas_equipos[1].select_one("a")
        if not (equipo_local_tag and equipo_visitante_tag):
            return None

        hora_tag = tabla.select_one("tfoot time[datetime]")
        detalle = hora_tag.get_text(strip=True) if hora_tag else "N/A"

        return Partido(
            equipo_local=equipo_local_tag.get_text(strip=True),
            equipo_visitante=equipo_visitante_tag.get_text(strip=True),
            estado=EstadoPartido.PROGRAMADO,
            detalle_estado=detalle,
            liga=liga,
            fuente="pokerstars",
            deporte=deporte,
        )
