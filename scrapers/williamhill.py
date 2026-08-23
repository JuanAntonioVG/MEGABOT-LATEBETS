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

Ampliado y verificado en vivo el 2026-08-23 contra
".../baloncesto/partidos/competición/hoy/": la URL sigue el mismo
patron que futbol y responde, PERO el contenido usa una plantilla
totalmente distinta ("clasica", con clases `btmarket__*` — no queda
claro si es solo baloncesto o el resto de deportes no rediseñados
todavia, no se ha comprobado). Los selectores de futbol
(`article[data-testid^="event-ob-"]`...) dan 0 en esta pagina aunque
hay partidos reales (13 verificados a mano ese dia, sin contar 2
partidos de "eBasketball" simulado que se descartan a proposito). Por
eso `extraer` detecta el formato por conteo y usa una rama de parseo
separada (`_extraer_formato_clasico`) solo cuando la de futbol no
encuentra nada.

Estructura real de la plantilla clasica (la liga vive en
`article.comp-container > header > h3`, un nivel por ENCIMA de
`section.btmarket__wrapper`):
- Deportes "normales" (amistosos, ligas nacionales...): cada partido es
  un `div.event[data-startdatetime]` con el ISO datetime en UTC como
  atributo (nada que interpretar en texto, a diferencia de la mayoria
  de casas) y los nombres en `div.btmarket__link-name > span` (dos
  spans, local y visitante en ese orden — mas fiable que el atributo
  `title` del enlace, que junta ambos con el mismo separador especial
  "₋" que ya se vio en futbol).
- Deportes "US" (WNBA, verificado en vivo): el partido es un
  `section.markettable__event[data-startdatetime]` en vez de un
  `div.event`, y los nombres viven en `div.event__team > p` en vez de
  `div.btmarket__link-name > span` — de ahi que se prueben ambos
  patrones, igual que Flashscore prueba dos patrones para el nombre de
  equipo. El `time[datetime]` con la hora visible ("23 ago. 22:00") SI
  es comun a ambas variantes.
- En vivo: el `label.wh-label.btmarket__live.go` (con el marcador del
  minuto/marcador en vivo como texto) pierde la clase `displayNone`
  cuando el partido esta en directo — verificado en vivo contra 2
  partidos de baloncesto chino ("Chinese NBL Women"). Sin ese indicador
  se asume que es de la plantilla US y se mira si el badge
  `.event__badge.in-play-scores` esta fuera de un contenedor
  `displayNone`.
- NO se ha añadido ningun filtro de fecha aparte (a diferencia de
  futbol arriba): la pestaña "Hoy" de baloncesto vino limpia en la
  comprobacion en vivo — incluye partidos de WNBA marcados "24 ago.
  01:00" (pasada la medianoche) sin ningun separador de "mañana" de por
  medio, señal de que el sitio ya los considera parte de la sesion de
  "hoy". Si algun dia se detectan partidos de otro dia de verdad
  colandose, añadir aqui el mismo tipo de filtro que arriba.
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

# Plantilla "clasica" (baloncesto y, probablemente, deportes sin
# rediseñar todavia) — ver nota en el docstring del modulo.
SELECTOR_COMPETICION_CLASICA = "article.comp-container"
SELECTOR_PARTIDO_CLASICO = "[data-startdatetime]"
SELECTOR_EQUIPOS_CLASICO_NORMAL = "div.btmarket__link-name > span"
SELECTOR_EQUIPOS_CLASICO_US = "div.event__team > p"
SELECTOR_LIVE_CLASICO = "label.wh-label.btmarket__live.go"
SELECTOR_LIVE_BADGE_US = ".event__badge.in-play-scores"
# Ligas de baloncesto "simulado" (eBasketball) — no son partidos reales,
# Flashscore no las tiene, y colarlas solo mete ruido en la cola de
# alias sin emparejar. Detectado en vivo el 2026-08-23 ("eBasketball NBA").
LIGAS_VIRTUALES_EXCLUIDAS = ("ebasketball", "esports", "e-sports")

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

        if await page.locator(SELECTOR_PARTIDO).count() > 0:
            return await self._extraer_formato_moderno(page, deporte)
        return await self._extraer_formato_clasico(page, deporte)

    async def _extraer_formato_moderno(self, page: Page, deporte: str) -> list[Partido]:
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

    async def _extraer_formato_clasico(self, page: Page, deporte: str) -> list[Partido]:
        # No es una lista virtualizada (verificado en vivo: el conteo no
        # cambia tras 20 scrolls de sobra), pero se scrollea igual por si
        # algun dia carga algo perezosamente — no hace daño si no hace falta.
        await scroll_hasta_estabilizar(
            page,
            f"{SELECTOR_COMPETICION_CLASICA} {SELECTOR_PARTIDO_CLASICO}",
            max_scrolls_sin_cambios=5,
            max_scrolls_total=40,
        )

        html = await page.content()
        soup = BeautifulSoup(html, "lxml")

        partidos: list[Partido] = []
        vistos: set[str] = set()
        for competicion in soup.select(SELECTOR_COMPETICION_CLASICA):
            titulo_tag = competicion.select_one("h3")
            liga = titulo_tag.get_text(strip=True) if titulo_tag else "Desconocida"
            if any(virtual in liga.lower() for virtual in LIGAS_VIRTUALES_EXCLUIDAS):
                continue
            for elemento in competicion.select(SELECTOR_PARTIDO_CLASICO):
                id_evento = elemento.get("id", "")
                if id_evento and id_evento in vistos:
                    continue
                partido = self._parsear_partido_clasico(elemento, liga, deporte)
                if partido:
                    if id_evento:
                        vistos.add(id_evento)
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

    @staticmethod
    def _parsear_partido_clasico(elemento: Tag, liga: str, deporte: str) -> Partido | None:
        equipos = [s.get_text(strip=True) for s in elemento.select(SELECTOR_EQUIPOS_CLASICO_NORMAL)]
        if len(equipos) < 2:
            equipos = [s.get_text(strip=True) for s in elemento.select(SELECTOR_EQUIPOS_CLASICO_US)]
        if len(equipos) < 2:
            return None
        equipo_local, equipo_visitante = equipos[0], equipos[1]
        if not (equipo_local and equipo_visitante):
            return None

        label_vivo = elemento.select_one(SELECTOR_LIVE_CLASICO)
        en_vivo = label_vivo is not None and "displayNone" not in (label_vivo.get("class") or [])
        if not en_vivo:
            badge_vivo = elemento.select_one(SELECTOR_LIVE_BADGE_US)
            en_vivo = badge_vivo is not None and badge_vivo.find_parent(class_="displayNone") is None

        if en_vivo:
            estado = EstadoPartido.EN_VIVO
            detalle = label_vivo.get_text(strip=True) if label_vivo else ""
            detalle = detalle or "En Vivo"
        else:
            hora_tag = elemento.select_one("time[datetime]")
            if not hora_tag:
                return None
            estado = EstadoPartido.PROGRAMADO
            detalle = extraer_hora(hora_tag.get_text(strip=True))
            if detalle == "N/A":
                return None

        return Partido(
            equipo_local=equipo_local,
            equipo_visitante=equipo_visitante,
            estado=estado,
            detalle_estado=detalle,
            liga=liga,
            fuente="williamhill",
            deporte=deporte,
        )
