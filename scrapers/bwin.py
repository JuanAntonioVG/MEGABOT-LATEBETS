"""Scraper de Bwin.

Verificado en vivo el 2026-08-22 contra la pagina de futbol de hoy:
- Partidos: `div.grid-event-wrapper` (selector vigente).
- Equipos: `div.participant` (vigente).
- Hora: `ms-prematch-timer` (vigente, formato "Hoy / 17:00").
- Liga: `ms-event-group` agrupa cabecera (`span.cgd-title`, ej. "España |
  LaLiga") y sus partidos dentro del MISMO elemento — no hace falta
  escaneo lineal, es una consulta con scope directo.

BUG REAL detectado por el usuario el 2026-08-23 (contando a mano con
Ctrl+F en la pagina real): el scraper solo devolvia 30-50 partidos de
futbol cuando en realidad habia 422 "Hoy" mas partidos en vivo/acabados,
471 en total. Causa: la pagina no carga mas partidos por scroll — tiene
un boton explicito `ms-grid-show-more` ("Más eventos") que hay que
PULSAR de verdad; ni siquiera scrollear en pasos pequeños manteniendolo
a la vista lo activa (probado en vivo, no cambia nada). Arreglado
sustituyendo el scroll por clicks reales al boton hasta que desaparece
(ver `hacer_click_hasta_agotar` en scrapers/base.py). Verificado de
nuevo en vivo tras el arreglo: 471 partidos, 422 de ellos con "Hoy" en
su horario (0 con "Mañana" — la pagina de "hoy" ya viene acotada al dia
de verdad, no hace falta ningun filtro de fecha aparte como en
Winamax/Pokerstars/Betfair).

La rama de baloncesto (`ms-six-pack-event` / `div.participant-container`),
heredada del bot anterior, SI se ha verificado en vivo hoy: 15 partidos
reales de baloncesto (WNBA, amistosos...) y 12 de voleibol — resulta que
Bwin usa el MISMO componente `ms-six-pack-event` para varios deportes no
futbolisticos, asi que `_detectar_selector_partido` ya cubre baloncesto
y voleibol sin cambios. Esos deportes no tenian el boton "Más eventos"
visible en el momento de probar (pocos partidos ese dia), pero el mismo
click-hasta-agotar se aplica igual por si algun dia si lo tienen — no
hace nada si el boton no existe. Waterpolo comprobado tambien: 0
partidos, pero confirmado con el propio texto de la pagina ("Lo
sentimos, no hay eventos disponibles actualmente para este filtro") —
no es un fallo de selector, es que ahora mismo no hay nada que scrapear.

Futsal verificado en vivo el 2026-08-23: mismo resultado que waterpolo
(0 partidos, mismo texto de "no hay eventos" en la pagina) — dia flojo
real, no un fallo.

BUG REAL detectado en vivo el mismo dia contra HOCKEY: el scraper
devolvia 5 "partidos de hoy" (Panthers vs Hurricanes, Canadiens vs Maple
Leafs...) con liga "Desconocida" y horas como "23:08"/"01:08" — daban
mal rollo, y con razon: son partidos de PRETEMPORADA DE LA NHL de finales
de SEPTIEMBRE, no de hoy. Causa de fondo: cuando no hay ningun partido
para hoy, `ms-event-group` en vez de mostrar "no hay eventos" (como en
waterpolo/futsal ese mismo dia) agrupa por FECHA futura
(`ms-date-group-details`, texto tipo "martes - 29/9/26") en vez de por
liga (`span.cgd-title`) — y como el codigo anterior no miraba esa fecha
para nada, se colaban partidos de dentro de mas de un mes como si fueran
de hoy. Arreglado filtrando cualquier grupo cuya cabecera de fecha no
sea la de hoy (ver `_es_fecha_de_hoy`); los grupos SIN cabecera de fecha
(el caso normal, agrupados por liga) no se tocan. Tras el arreglo:
0 partidos de hockey ese dia (correcto — la NHL no ha empezado, y las
ligas europeas tampoco).
"""

from __future__ import annotations

import re
from datetime import datetime

from bs4 import BeautifulSoup
from bs4.element import Tag
from playwright.async_api import Page

from core.models import EstadoPartido, Partido
from scrapers.base import ScraperBase, aceptar_cookies, extraer_hora, hacer_click_hasta_agotar, registrar

SELECTOR_GRUPO = "ms-event-group"
SELECTOR_TITULO_LIGA = "span.cgd-title"
SELECTOR_FECHA_GRUPO = "ms-date-group-details"
SELECTOR_PARTIDO_FUTBOL = "div.grid-event-wrapper"
SELECTOR_PARTIDO_BALONCESTO = "ms-six-pack-event"
SELECTOR_MAS_EVENTOS = "ms-grid-show-more"


def _es_fecha_de_hoy(texto: str) -> bool:
    """ "martes - 29/9/26" -> compara dia y mes contra la fecha real de
    hoy. Si el texto no se puede interpretar, NO se descarta el grupo
    (mejor un falso positivo que perder partidos reales por un cambio
    de formato)."""
    m = re.search(r"(\d{1,2})/(\d{1,2})/\d{2,4}", texto)
    if not m:
        return True
    dia, mes = int(m.group(1)), int(m.group(2))
    hoy = datetime.now()
    return dia == hoy.day and mes == hoy.month


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
            await hacer_click_hasta_agotar(page, SELECTOR_MAS_EVENTOS)

        html = await page.content()
        soup = BeautifulSoup(html, "lxml")

        if not selector_partido:
            return []

        participant_sel = (
            "div.participant" if selector_partido == SELECTOR_PARTIDO_FUTBOL else "div.participant-container"
        )

        partidos: list[Partido] = []
        for grupo in soup.select(SELECTOR_GRUPO):
            fecha_tag = grupo.select_one(SELECTOR_FECHA_GRUPO)
            if fecha_tag and not _es_fecha_de_hoy(fecha_tag.get_text(strip=True)):
                continue
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
