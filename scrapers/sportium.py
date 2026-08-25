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

BUG REAL detectado en vivo el 2026-08-25 (reportado por el usuario:
"hay 108 partidos hoy y el bot solo ha cogido 50" — confirmado a mano
con Ctrl+F contando "hoy" en la pagina real). La causa de fondo NO era
el click de expandir (esa fue la sospecha inicial, descartada tras
medir) sino algo anterior: la propia LISTA DE GRUPOS (ligas) tambien
llega por detras en varias tandas, igual que los partidos de cada
grupo — el primer grupo puede aparecer mucho antes de que esten todos.
Con la espera fija que habia antes (500ms tras el primer grupo),
`n_grupos` se contaba demasiado pronto: en pruebas repetidas contra la
misma carga, unas veces salian los 34 grupos reales y otras solo 7 — el
resto (y sus partidos, ni siquiera renderizados todavia en ese
instante) se perdian enteros sin ningun error. Arreglado esperando a
que el NUMERO de grupos deje de crecer antes de contarlos (igual que
`scroll_hasta_estabilizar` en base.py, pero sin scroll — la carga aqui
es async pura). El margen de "sin cambios" es generoso a proposito (3s)
porque se detecto una tanda con una pausa real de mas de 1s antes de
seguir cargando mas grupos; un margen mas corto daba el numero por
estable antes de tiempo. Verificado en vivo: 34/34 grupos, 108
partidos, en 5 ejecuciones seguidas (frente a resultados entre 48 y 108
segun cuando se contara, antes del arreglo).

De paso, el click de expandir en si SI se cambio a JavaScript (mismo
patron que en Flashscore, `_expandir_ligas_colapsadas` — se salta las
comprobaciones de "visible/estable/sin animacion" de Playwright, que
pueden colgarse mas de la cuenta), pero con cuidado: un primer intento
de reforzarlo aun mas con un REINTENTO (repetir el click si tras
esperar seguia sin haber partidos) resulto ser peor que el problema
original — verificado en vivo pulsando dos veces seguidas el mismo
header en la consola que el boton es un TOGGLE (0 a 3 partidos con el
primer click, de vuelta a 0 con el segundo), asi que cualquier lectura
de conteo que llegara un poco antes de que el primer click terminara de
renderizar disparaba un segundo click que RE-COLAPSABA el grupo (14 y 9
partidos en pruebas, peor que los 50 originales). Con un solo click y
sondeando sin volver a tocar el header, sin reintento, es seguro.
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

        # RAIZ REAL del bug del usuario ("108 partidos hoy, el bot solo
        # cogio 50"): la LISTA DE GRUPOS (ligas) tambien llega por
        # detras EN VARIAS TANDAS, no de una vez — el primer grupo puede
        # aparecer mucho antes de que esten los demas. Verificado en
        # vivo contando cuantos grupos habia disponibles tras la espera
        # fija de 500ms que habia antes: unas veces 34 (los de verdad),
        # otras solo 7 — el resto de grupos (y sus partidos, aun no
        # renderizados en ese momento) se perdian enteros sin ningun
        # error, simplemente porque `n_grupos` se contaba demasiado
        # pronto. Se espera ahora a que el NUMERO de grupos deje de
        # crecer (igual que `scroll_hasta_estabilizar` en base.py, pero
        # sin scroll — aqui no hace falta, la carga es async pura) antes
        # de contar cuantos hay que expandir. Verificado en vivo tras el
        # arreglo: 34/34 grupos, 108 partidos, en 5 ejecuciones seguidas
        # (frente a resultados entre 48 y 108 antes, segun cuando se
        # contara). El margen de espera "sin cambios" es generoso a
        # proposito (3s) porque se detecto una tanda con una pausa real
        # de mas de 1s antes de seguir cargando mas grupos — un margen
        # mas corto daba por estabilizado un numero que en realidad
        # todavia iba a seguir creciendo.
        grupos_anterior = -1
        sin_cambios = 0
        for _ in range(60):
            grupos_actual = await page.locator(SELECTOR_GRUPO_LIGA).count()
            if grupos_actual <= grupos_anterior:
                sin_cambios += 1
                if sin_cambios >= 12:
                    break
            else:
                sin_cambios = 0
            grupos_anterior = grupos_actual
            await page.wait_for_timeout(250)

        n_grupos = await page.locator(SELECTOR_GRUPO_LIGA).count()
        for i in range(min(n_grupos, MAX_GRUPOS_A_EXPANDIR)):
            grupo = page.locator(SELECTOR_GRUPO_LIGA).nth(i)
            if await grupo.locator(SELECTOR_PARTIDO).count() > 0:
                continue  # ya tiene partidos cargados, no hace falta expandir
            header = grupo.locator(SELECTOR_HEADER).first
            # BUG REAL detectado en vivo el 2026-08-25 (reportado por el
            # usuario: solo 50 de 108 partidos de hoy): `header.click()`
            # de Playwright (con sus comprobaciones de que el elemento
            # este visible/estable/sin animacion antes de pulsar) fallaba
            # en silencio para una parte de los grupos, y el
            # `except Exception: continue` se comia el fallo sin dejar
            # rastro. Mismo sintoma que ya se vio en Flashscore
            # (`_expandir_ligas_colapsadas`): click por JavaScript, que
            # se salta esas comprobaciones.
            #
            # OJO — intento anterior de arreglo (reintentar el click si
            # tras esperar seguia sin haber partidos) resulto CONTRAPRODUCENTE
            # y verificado en vivo que lo era: el boton es un TOGGLE (un
            # segundo click vuelve a COLAPSAR el grupo, confirmado
            # pulsando dos veces seguidas sobre el mismo header — de 0 a
            # 3 partidos con el primer click, de vuelta a 0 con el
            # segundo). Por eso aqui NO se reintenta con otro click si
            # parece que no ha funcionado: se hace UN click y se espera
            # sondeando (sin volver a tocar el header) hasta 2s — mas
            # que suficiente margen, y sin el riesgo de deshacer un
            # click que en realidad SI habia funcionado pero iba mas
            # lento que la espera.
            try:
                await header.evaluate("el => { el.scrollIntoView({block: 'center'}); el.click(); }")
            except Exception:
                continue
            for _ in range(10):
                await page.wait_for_timeout(200)
                if await grupo.locator(SELECTOR_PARTIDO).count() > 0:
                    break

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
