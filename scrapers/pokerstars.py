"""Scraper de Pokerstars.

Verificado en vivo el 2026-08-22 contra la pagina de futbol:
- 160 partidos encontrados con `li[data-testid="event"]` (selector vigente).
- Cada liga vive en un `details[data-testid="sports-expandable-accordion"]`
  con su `summary` (nombre de liga limpio, ej. "La Liga Española") y sus
  partidos dentro del MISMO elemento — verificado sin duplicados ni
  anidamiento (160 partidos totales = 160 sumando por accordion).
- Hora: atributo `datetime` de `<time>`, formato limpio "17:00".
- El listado de cada liga NO esta limitado a hoy: dentro del mismo
  accordion aparece una cabecera `<div class="_2725eb4">Hoy, 22 de agosto
  de 2026</div>` antes de los partidos de hoy, y (si los hay) otra para
  "Mañana" antes de los de mañana. Se descartan los partidos que no
  esten bajo una cabecera que empiece por "hoy".

La rama de formato tabla (`table[data-testid="compound-table-column"]`),
heredada del bot anterior, SI se ha verificado en vivo hoy para
BALONCESTO: 8 partidos reales. Voleibol y balonmano, en cambio, tienen
un problema mas de fondo y NO funcionan todavia: sus URLs en
`config/catalogo_casas.py` apuntan a la pagina de "competiciones" de ese
deporte (`/sports/voleibol/998917/`, sin sufijo), no a una pagina de
"todos los partidos" como el resto — confirmado en vivo que faltaba el
`/matches/` que si tienen futbol/baloncesto/tenis/hockey, PERO añadirlo
sin mas no basta: `/sports/voleibol/998917/matches/` devuelve una pagina
de error propia de Pokerstars ("Lo sentimos, se ha producido un error"),
no la lista de partidos. Los partidos de estos dos deportes parecen
vivir solo dentro de cada competicion por separado (se ven enlaces tipo
`/sports/voleibol/998917/amistosos-internacionales-masculinos/...`) —
resolverlo de verdad necesitaria recorrer esos enlaces uno a uno, que es
mas trabajo del que tiene sentido meter aqui sin confirmar antes que
merece la pena. Por ahora esta casa se queda sin cobertura real para
estos dos deportes (`extraer` devuelve una lista vacia, no falla).
"""

from __future__ import annotations

from bs4 import BeautifulSoup
from bs4.element import Tag
from playwright.async_api import Page

from core.models import EstadoPartido, Partido
from scrapers.base import ScraperBase, aceptar_cookies, agrupar_por_liga, extraer_hora, registrar

SELECTOR_ACCORDION = 'details[data-testid="sports-expandable-accordion"]'
SELECTOR_PARTIDO_FUTBOL = 'li[data-testid="event"]'
SELECTOR_TABLA_BALONCESTO = 'table[data-testid="compound-table-column"]'
SELECTOR_DIVISOR_FECHA = 'div[class="_2725eb4"]'


def _texto_divisor_fecha(header: Tag) -> str:
    return header.get_text(strip=True).lower()


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

            # Reutilizamos agrupar_por_liga con otro significado: aqui
            # agrupa por SECCION DE FECHA ("hoy, ..." / "mañana, ...")
            # dentro de este accordion, no por liga (eso ya lo tenemos).
            por_fecha = agrupar_por_liga(
                accordion, SELECTOR_DIVISOR_FECHA, SELECTOR_PARTIDO_FUTBOL, _texto_divisor_fecha
            )
            for evento, seccion_fecha in por_fecha:
                if not seccion_fecha.startswith("hoy"):
                    continue
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
