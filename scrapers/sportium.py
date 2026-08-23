"""Scraper de Sportium.

Verificado en vivo el 2026-08-23 contra "Fútbol > Partidos > Hoy":
- Ligas: `div.ta-EventListGroup`, con cabecera `div.ta-GroupHeader` >
  `.ta-headerTypeAndClassContainer` (nombre limpio, ej. "La Liga
  (España)") — OJO, el texto completo de `.ta-GroupHeader` trae pegadas
  las etiquetas de columna ("1 X 2 Más de Menos de..."), hay que leer
  solo ese sub-contenedor, no la cabecera entera.
- IMPORTANTE (como pidio el usuario: "las ligas vienen minimizadas,
  parecido a Flashscore"): de 86 grupos totales en el DOM al cargar,
  solo 3 traian sus partidos ya renderizados (10 partidos). El resto
  no tiene NINGUN partido en el DOM hasta hacer click en su cabecera —
  a diferencia de Bwin (que scrollear no activaba nada) o de un
  `<details>` nativo (que mantiene el contenido aunque este colapsado),
  aqui el contenido ni siquiera existe hasta expandir. Se hace click en
  la cabecera de cada grupo que aun no tenga partidos. Verificado en
  vivo: de 10 a 226 partidos tras expandir los 83 grupos restantes.
- Partidos: `div.ta-EventListItem` es la fila COMPLETA de un partido —
  OJO, no confundir con `a.ta-EventListItemDetails` (el enlace clicable
  con los nombres de equipo), que es solo un HIJO de esa fila. La
  fecha/hora ("Hoy, 17:00") vive FUERA de ese enlace, como hermano
  suyo dentro de `ta-EventListItem` — buscarla dentro del `<a>` (como
  se hizo en un primer intento) siempre falla y descarta el partido
  entero, aunque los nombres de equipo se lean bien. Confirmado en
  vivo tras el fallo: 226 filas `ta-EventListItem` = 226 partidos.
- Equipos: dos `div.ta-participantName` dentro de cada fila, en orden
  (local, visitante) — sin caracteres raros de separador, cada nombre
  es su propio elemento.
- Fecha+hora: un `<div>` SIN clase (solo estilo inline) con texto
  "Hoy, 17:00" — se localiza por patron de texto, no hay selector CSS
  al que agarrarse. A diferencia de Bwin/William Hill/Pokerstars, aqui
  la pestaña "Hoy" vino perfectamente limpia en la comprobacion en vivo
  (226 de 226 partidos con el prefijo "Hoy", cero de otro dia) — aun
  asi se sigue comprobando el prefijo por si un dia deja de estarlo.
- La pagina tarda en pedir los partidos por detras tras cargar el HTML
  base (SPA) — un `wait_for_timeout` fijo fallo alguna vez en pruebas
  (0 grupos encontrados); se espera de verdad a que aparezca el primer
  grupo en vez de adivinar cuanto tarda.
"""

from __future__ import annotations

import re

from bs4 import BeautifulSoup
from bs4.element import Tag
from playwright.async_api import Page

from core.models import EstadoPartido, Partido
from scrapers.base import ScraperBase, aceptar_cookies, extraer_hora, registrar

SELECTOR_COOKIES = "#CybotCookiebotDialogBodyLevelButtonLevelOptinAllowAll"
SELECTOR_GRUPO_LIGA = "div.ta-EventListGroup"
SELECTOR_HEADER = "div.ta-GroupHeader"
SELECTOR_PARTIDO = "div.ta-EventListItem"
SELECTOR_NOMBRE_LIGA = ".ta-headerTypeAndClassContainer"
SELECTOR_NOMBRE_EQUIPO = ".ta-participantName"
_PATRON_FECHA_HORA = re.compile(r"^([A-Za-zÀ-ÿ]+),?\s*(\d{1,2}:\d{2})$")

# Red de seguridad — el dia verificado en vivo habia 86 grupos.
MAX_GRUPOS_A_EXPANDIR = 300


@registrar
class SportiumScraper(ScraperBase):
    id = "sportium"
    nombre_legible = "Sportium"

    async def extraer(self, page: Page, url: str, deporte: str) -> list[Partido]:
        await page.goto(url, timeout=30000, wait_until="domcontentloaded")
        await aceptar_cookies(page, [SELECTOR_COOKIES])

        # Es una SPA que pide los partidos por detras DESPUES de cargar
        # el HTML base — un `wait_for_timeout` fijo fallo en produccion
        # (0 grupos) porque unas veces alcanza y otras no. Se espera al
        # primer grupo real en vez de adivinar cuanto tarda.
        await page.wait_for_selector(SELECTOR_GRUPO_LIGA, timeout=20000)
        await page.wait_for_timeout(500)

        n_grupos = await page.locator(SELECTOR_GRUPO_LIGA).count()
        for i in range(min(n_grupos, MAX_GRUPOS_A_EXPANDIR)):
            grupo = page.locator(SELECTOR_GRUPO_LIGA).nth(i)
            try:
                if await grupo.locator(SELECTOR_PARTIDO).count() > 0:
                    continue  # ya tiene partidos cargados, no hace falta expandir
                header = grupo.locator(SELECTOR_HEADER).first
                await header.scroll_into_view_if_needed(timeout=3000)
                await header.click(timeout=3000)
                await page.wait_for_timeout(250)
            except Exception:
                continue

        html = await page.content()
        soup = BeautifulSoup(html, "lxml")

        partidos: list[Partido] = []
        for grupo in soup.select(SELECTOR_GRUPO_LIGA):
            header = grupo.select_one(SELECTOR_HEADER)
            contenedor_nombre = header.select_one(SELECTOR_NOMBRE_LIGA) if header else None
            liga = contenedor_nombre.get_text(strip=True) if contenedor_nombre else "Desconocida"
            for articulo in grupo.select(SELECTOR_PARTIDO):
                partido = self._parsear_partido(articulo, liga, deporte)
                if partido:
                    partidos.append(partido)
        return partidos

    @staticmethod
    def _parsear_partido(articulo: Tag, liga: str, deporte: str) -> Partido | None:
        nombres = articulo.select(SELECTOR_NOMBRE_EQUIPO)
        if len(nombres) < 2:
            return None
        equipo_local = nombres[0].get_text(strip=True)
        equipo_visitante = nombres[1].get_text(strip=True)
        if not equipo_local or not equipo_visitante:
            return None

        texto_fecha_hora = articulo.find(string=_PATRON_FECHA_HORA)
        if not texto_fecha_hora:
            return None
        m = _PATRON_FECHA_HORA.match(texto_fecha_hora.strip())
        if not m or not m.group(1).lower().startswith("hoy"):
            return None

        detalle = extraer_hora(m.group(2))
        if detalle == "N/A":
            return None

        return Partido(
            equipo_local=equipo_local,
            equipo_visitante=equipo_visitante,
            estado=EstadoPartido.PROGRAMADO,
            detalle_estado=detalle,
            liga=liga,
            fuente="sportium",
            deporte=deporte,
        )
