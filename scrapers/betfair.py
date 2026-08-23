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

Descubierto el mismo dia (revisando por que Betfair traia muchos menos
partidos que el resto de casas): la pagina TAMBIEN mezcla partidos de
"mañana" con los de hoy, igual que Winamax/Pokerstars — pero aqui no
hace falta buscar un separador aparte, porque el propio atributo
`datetime` del `<time>` de cada partido trae la fecha completa en
formato Javascript (`"Sat Aug 22 2026 21:30:00 GMT+0200 ..."`), asi que
se compara directamente contra la fecha de hoy.

Ampliado y verificado en vivo el 2026-08-23 contra baloncesto (6
partidos, dia flojo — NBA en pretemporada todavia queda fuera por el
filtro de "hoy") y voleibol (9 partidos, incluido uno en vivo
detectado bien): el mismo codigo generico vale sin cambios para los
dos, no hizo falta ninguna rama especial.

Ampliado el mismo dia contra TENIS y encontrado un bug real: la pagina
traia 21 partidos en el DOM pero el scraper solo devolvia 10. Los 11
restantes eran partidos EN VIVO de Challenger/ITF que no usan
`div[class*="-status"]` con texto como el resto de deportes (baloncesto,
futbol...), sino un grid de sets/juegos/puntos con clases
`...-inPlay`/`...-serving` sin ninguna etiqueta de texto que leer — ni
`status_tag` ni `datetime_tag` existian para esos partidos, asi que se
perdian enteros. Añadido un tercer patron de deteccion de "en vivo" para
ese caso (ver `SELECTOR_MARCADOR_EN_VIVO`); tras el arreglo, 14 partidos
(quedan 92 en Flashscore ese mismo dia — Betfair simplemente no cubre
ni de lejos el volumen de challengers/ITF/UTR de bajo perfil que agrega
un sitio de resultados puro, eso no es un bug, es alcance real del
catalogo de apuestas).
"""

from __future__ import annotations

from datetime import datetime

from bs4 import BeautifulSoup
from bs4.element import Tag
from playwright.async_api import Page

from core.models import EstadoPartido, Partido
from scrapers.base import ScraperBase, aceptar_cookies, agrupar_por_liga, extraer_hora, registrar

# Formato Javascript Date.toString(), ej. "Sat Aug 22 2026 21:30:00 GMT+0200 (...)".
# Nos quedamos solo con los primeros 24 caracteres (dia semana, mes, dia,
# año, hora) e ignoramos el resto (zona horaria con nombre, que varia).
_FORMATO_FECHA_JS = "%a %b %d %Y %H:%M:%S"

SELECTOR_PARTIDO = 'a[class*="-fixtureHeader"]'
SELECTOR_LIGA = 'div[class*="-competitionHeader"]'
# Grid de sets/juegos/puntos en vivo (tenis) — ver nota en _parsear_partido.
SELECTOR_MARCADOR_EN_VIVO = '[class*="-inPlay"], [class*="-serving"]'


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

            fecha_attr = datetime_tag.get("datetime", "")
            if fecha_attr:
                try:
                    fecha_partido = datetime.strptime(fecha_attr[:24], _FORMATO_FECHA_JS).date()
                    if fecha_partido != datetime.now().date():
                        return None  # es de otro dia (normalmente "mañana"), no nos interesa
                except ValueError:
                    pass  # formato inesperado: no descartamos el partido por las dudas
        elif elemento.select_one(SELECTOR_MARCADOR_EN_VIVO):
            # Bug real detectado en vivo el 2026-08-23 escaneando tenis:
            # un partido en directo de Challenger/ITF no usa
            # div[class*="-status"] con texto como el resto de deportes,
            # sino un grid de sets/juegos/puntos con clases tipo
            # "...-inPlay"/"...-serving" — sin este patron de respaldo,
            # 6 de 21 partidos de la pagina se perdian enteros (0 status,
            # 0 datetime) en vez de detectarse como en vivo.
            estado = EstadoPartido.EN_VIVO
            detalle = "En Vivo"

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
