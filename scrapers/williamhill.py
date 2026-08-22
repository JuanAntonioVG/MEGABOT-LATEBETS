"""Scraper de William Hill.

Verificado en vivo el 2026-08-23 contra "Fútbol > Lista Diaria > Hoy":
- Partidos: `article[data-testid^="event-ob-"]` (selector estable, el
  sufijo es el id de cada evento).
- Equipos: dentro de `.sp-o-market__title span.sp-betName`, un SOLO
  texto tipo "At. Madrid ₋ Villarreal" — OJO, el separador NO es un
  guion normal "-", es el caracter Unicode "₋" (U+208B, guion
  subindice). Con un guion normal no separa nada.
- Hora: `.sp-o-market__clock__time` (formato limpio, ej. "17:00").
- Liga: cada bloque `section[data-testid^="accordion-ob-"]` con un
  `<header>` de texto tipo "España - LaLiga EA Sports".
- Carga perezosa por SCROLL PURO (a diferencia de Bwin, que necesita
  clicks): verificado en vivo pasando de 20 a 394 partidos con scroll
  normal, sin ningun boton que pulsar.

BUG evitado (mismo patron que Winamax/Pokerstars/Betfair): la pestaña
"Hoy" no viene perfectamente acotada — se encontraron partidos bajo una
cabecera de fecha "Lun, 24 Ago" (mañana) mezclados con los de "Dom, 23
Ago" (hoy) dentro de las mismas ligas. Cada bloque de partidos lleva su
propia cabecera de fecha (`div[data-testid="market-group-header"]` >
`.sp-o-market__header-clock`, texto tipo "dom, 23 ago" — el dia y mes
exactos, no una etiqueta relativa "hoy/mañana" como en las otras casas),
así que se compara esa fecha contra la fecha real de hoy y se descarta
lo que no coincida.
"""

from __future__ import annotations

import re
from datetime import datetime

from bs4 import BeautifulSoup
from bs4.element import Tag
from playwright.async_api import Page

from core.models import EstadoPartido, Partido
from scrapers.base import (
    ScraperBase,
    aceptar_cookies,
    agrupar_por_liga,
    extraer_hora,
    registrar,
    scroll_hasta_estabilizar,
)

SELECTOR_COOKIES = "#consent_prompt_submit"
SELECTOR_LIGA = 'section[data-testid^="accordion-ob-"]'
SELECTOR_PARTIDO = 'article[data-testid^="event-ob-"]'
SELECTOR_FECHA_GRUPO = 'div[data-testid="market-group-header"]'
SEPARADOR_EQUIPOS = "₋"  # "₋" guion subindice, NO un guion normal

_MESES_ES = {
    "ene": 1, "feb": 2, "mar": 3, "abr": 4, "may": 5, "jun": 6,
    "jul": 7, "ago": 8, "sep": 9, "oct": 10, "nov": 11, "dic": 12,
}  # fmt: skip


def _titulo_liga(section: Tag) -> str:
    header = section.select_one("header")
    return header.get_text(strip=True) if header else "Desconocida"


def _texto_fecha_grupo(header: Tag) -> str:
    reloj = header.select_one(".sp-o-market__header-clock")
    return reloj.get_text(" ", strip=True) if reloj else ""


def _es_fecha_de_hoy(texto_fecha: str) -> bool:
    """ "dom, 23 ago" -> compara dia y mes contra la fecha real de hoy.
    Si el texto no se puede interpretar, NO se descarta el partido
    (mejor un falso positivo que perderlo por un cambio de formato)."""
    m = re.search(r"(\d{1,2})\s+([a-z]{3,})", texto_fecha.lower())
    if not m:
        return True
    dia = int(m.group(1))
    mes = _MESES_ES.get(m.group(2)[:3])
    if mes is None:
        return True
    hoy = datetime.now()
    return dia == hoy.day and mes == hoy.month


@registrar
class WilliamHillScraper(ScraperBase):
    id = "williamhill"
    nombre_legible = "William Hill"

    async def extraer(self, page: Page, url: str, deporte: str) -> list[Partido]:
        await page.goto(url, timeout=30000, wait_until="domcontentloaded")
        await aceptar_cookies(page, [SELECTOR_COOKIES])
        await page.wait_for_timeout(1500)

        await scroll_hasta_estabilizar(
            page, SELECTOR_PARTIDO, max_scrolls_sin_cambios=8, max_scrolls_total=80
        )

        html = await page.content()
        soup = BeautifulSoup(html, "lxml")

        partidos: list[Partido] = []
        for liga_section in soup.select(SELECTOR_LIGA):
            liga = _titulo_liga(liga_section)
            agrupados = agrupar_por_liga(
                liga_section, SELECTOR_FECHA_GRUPO, SELECTOR_PARTIDO, _texto_fecha_grupo
            )
            for articulo, fecha_texto in agrupados:
                if not _es_fecha_de_hoy(fecha_texto):
                    continue
                partido = self._parsear_partido(articulo, liga, deporte)
                if partido:
                    partidos.append(partido)
        return partidos

    @staticmethod
    def _parsear_partido(articulo: Tag, liga: str, deporte: str) -> Partido | None:
        nombre_tag = articulo.select_one(".sp-o-market__title span.sp-betName")
        if not nombre_tag:
            return None
        partes = nombre_tag.get_text(strip=True).split(SEPARADOR_EQUIPOS)
        if len(partes) != 2:
            return None
        equipo_local = partes[0].strip()
        equipo_visitante = partes[1].strip()
        if not equipo_local or not equipo_visitante:
            return None

        hora_tag = articulo.select_one(".sp-o-market__clock__time")
        detalle = extraer_hora(hora_tag.get_text(strip=True)) if hora_tag else "N/A"
        if detalle == "N/A":
            return None

        return Partido(
            equipo_local=equipo_local,
            equipo_visitante=equipo_visitante,
            estado=EstadoPartido.PROGRAMADO,
            detalle_estado=detalle,
            liga=liga,
            fuente="williamhill",
            deporte=deporte,
        )
