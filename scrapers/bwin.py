"""Scraper de Bwin.

Verificado en vivo el 2026-08-22 contra la pagina de futbol de hoy:
- 50 partidos encontrados con `div.grid-event-wrapper` (selector vigente).
- Equipos: `div.participant` (vigente).
- Hora: `ms-prematch-timer` (vigente, formato "Hoy / 17:00").
- Liga: `ms-event-group` agrupa cabecera (`span.cgd-title`, ej. "España |
  LaLiga") y sus partidos dentro del MISMO elemento — no hace falta
  escaneo lineal, es una consulta con scope directo.

La rama de baloncesto (`ms-six-pack-event` / `div.participant-container`)
se conserva del bot anterior pero NO se ha vuelto a verificar en vivo
hoy — revisarla la primera vez que actives Bwin+Baloncesto.
"""

from __future__ import annotations

from bs4 import BeautifulSoup
from bs4.element import Tag
from playwright.async_api import Page

from core.models import EstadoPartido, Partido
from scrapers.base import ScraperBase, aceptar_cookies, extraer_hora, registrar, scroll_hasta_estabilizar

SELECTOR_GRUPO = "ms-event-group"
SELECTOR_TITULO_LIGA = "span.cgd-title"
SELECTOR_PARTIDO_FUTBOL = "div.grid-event-wrapper"
SELECTOR_PARTIDO_BALONCESTO = "ms-six-pack-event"


@registrar
class BwinScraper(ScraperBase):
    id = "bwin"
    nombre_legible = "Bwin"

    async def extraer(self, page: Page, url: str, deporte: str) -> list[Partido]:
        await page.goto(url, timeout=30000, wait_until="domcontentloaded")
        await aceptar_cookies(page)
        await page.wait_for_timeout(1500)

        selector_partido = await self._detectar_selector_partido(page)
        if selector_partido:
            await scroll_hasta_estabilizar(page, selector_partido)

        html = await page.content()
        soup = BeautifulSoup(html, "lxml")

        if not selector_partido:
            return []

        participant_sel = (
            "div.participant" if selector_partido == SELECTOR_PARTIDO_FUTBOL else "div.participant-container"
        )

        partidos: list[Partido] = []
        for grupo in soup.select(SELECTOR_GRUPO):
            titulo_tag = grupo.select_one(SELECTOR_TITULO_LIGA)
            liga = titulo_tag.get_text(strip=True) if titulo_tag else "Desconocida"
            for elemento in grupo.select(selector_partido):
                partido = self._parsear_partido(elemento, liga, deporte, participant_sel)
                if partido:
                    partidos.append(partido)
        return partidos

    @staticmethod
    async def _detectar_selector_partido(page: Page) -> str | None:
        if await page.locator(SELECTOR_PARTIDO_FUTBOL).count() > 0:
            return SELECTOR_PARTIDO_FUTBOL
        if await page.locator(SELECTOR_PARTIDO_BALONCESTO).count() > 0:
            return SELECTOR_PARTIDO_BALONCESTO
        return None

    @staticmethod
    def _parsear_partido(elemento: Tag, liga: str, deporte: str, participant_sel: str) -> Partido | None:
        equipos = elemento.select(participant_sel)
        if len(equipos) < 2:
            return None
        equipo_local = equipos[0].get_text(strip=True)
        equipo_visitante = equipos[1].get_text(strip=True)

        estado = EstadoPartido.DESCONOCIDO
        detalle = "N/A"

        vivo_tag = elemento.select_one("ms-live-timer")
        if vivo_tag:
            texto = vivo_tag.get_text(strip=True)
            estado = EstadoPartido.FINALIZADO if "Finalizado" in texto else EstadoPartido.EN_VIVO
            detalle = "Finalizado" if estado == EstadoPartido.FINALIZADO else " ".join(texto.split())
        else:
            prematch_tag = elemento.select_one("ms-prematch-timer, div.starting-time")
            if prematch_tag:
                estado = EstadoPartido.PROGRAMADO
                detalle = extraer_hora(prematch_tag.get_text(strip=True))

        if estado == EstadoPartido.DESCONOCIDO:
            return None

        return Partido(
            equipo_local=equipo_local,
            equipo_visitante=equipo_visitante,
            estado=estado,
            detalle_estado=detalle,
            liga=liga,
            fuente="bwin",
            deporte=deporte,
        )
