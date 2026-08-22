"""Scraper de Betfair.

IMPORTANTE: el frontend de Betfair cambio por completo desde el bot
anterior (Gemini) — los selectores viejos (`div.event-information`,
`span.team-name`...) ya NO EXISTEN. Esta version esta escrita desde cero
contra la estructura real, verificada en vivo el 2026-08-22.

Betfair usa CSS-in-JS con clases del tipo "_443c5e6894fef559-teamName":
el prefijo hash cambia con cada despliegue de Betfair, pero el sufijo
semantico (-teamName, -datetime, -status, -competitionHeader) se
mantiene, asi que se usa `[class*="-sufijo"]` en vez de la clase completa.
Esto es mas resistente a cambios que una clase exacta, pero sigue siendo
el punto mas fragil de los 5 scrapers: si Betfair vuelve a rediseñar,
es la primera sospechosa.

Tambien se detecto (y se filtra) un carrusel de partidos "destacados" al
principio de la pagina que repite un partido varias veces ANTES de la
primera cabecera de liga real — se descartan los partidos sin liga
conocida para no duplicar datos.

Solo se ha verificado la pagina de futbol; baloncesto/otros deportes usan
la misma logica pero no se han comprobado en vivo todavia.
"""

from __future__ import annotations

from bs4 import BeautifulSoup
from bs4.element import Tag
from playwright.async_api import Page

from core.models import EstadoPartido, Partido
from scrapers.base import ScraperBase, aceptar_cookies, agrupar_por_liga, extraer_hora, registrar

SELECTOR_PARTIDO = 'a[class*="-fixtureHeader"]'
SELECTOR_LIGA = 'div[class*="-competitionHeader"]'


def _titulo_liga(header: Tag) -> str:
    titulo_tag = header.select_one('span[class*="-title"]')
    return titulo_tag.get_text(strip=True) if titulo_tag else ""


@registrar
class BetfairScraper(ScraperBase):
    id = "betfair"
    nombre_legible = "Betfair"

    async def extraer(self, page: Page, url: str, deporte: str) -> list[Partido]:
        await page.goto(url, timeout=30000, wait_until="domcontentloaded")
        await aceptar_cookies(page)
        await page.wait_for_timeout(2000)
        await page.mouse.wheel(0, 15000)
        await page.wait_for_timeout(1500)

        html = await page.content()
        soup = BeautifulSoup(html, "lxml")

        agrupados = agrupar_por_liga(soup, SELECTOR_LIGA, SELECTOR_PARTIDO, _titulo_liga)

        partidos: list[Partido] = []
        for elemento, liga in agrupados:
            if liga == "Desconocida":
                # Carrusel de "destacados" antes de la primera cabecera de
                # liga real: son duplicados del mismo partido, se descartan.
                continue
            partido = self._parsear_partido(elemento, liga, deporte)
            if partido:
                partidos.append(partido)
        return partidos

    @staticmethod
    def _parsear_partido(elemento: Tag, liga: str, deporte: str) -> Partido | None:
        equipos = elemento.select('span[class*="-teamName"]')
        if len(equipos) < 2:
            return None
        equipo_local = equipos[0].get_text(strip=True)
        equipo_visitante = equipos[1].get_text(strip=True)

        estado = EstadoPartido.DESCONOCIDO
        detalle = "N/A"

        status_tag = elemento.select_one('div[class*="-status"]')
        datetime_tag = elemento.select_one('time[class*="-datetime"]')

        if status_tag:
            texto = status_tag.get_text(strip=True)
            estado = EstadoPartido.FINALIZADO if "FIN" in texto.upper() else EstadoPartido.EN_VIVO
            detalle = texto
        elif datetime_tag:
            estado = EstadoPartido.PROGRAMADO
            detalle = extraer_hora(datetime_tag.get_text(" ", strip=True))

        if estado == EstadoPartido.DESCONOCIDO:
            return None

        return Partido(
            equipo_local=equipo_local,
            equipo_visitante=equipo_visitante,
            estado=estado,
            detalle_estado=detalle,
            liga=liga,
            fuente="betfair",
            deporte=deporte,
        )
