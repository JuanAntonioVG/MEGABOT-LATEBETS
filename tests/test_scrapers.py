"""Tests de la logica PURA de parseo de los scrapers (sin tocar la red) —
sobre todo para las reglas de "solo hoy, no otro dia" que son faciles de
verificar con HTML simulado y que no dependen de que la web este viva."""

from datetime import datetime, timedelta

from bs4 import BeautifulSoup

from scrapers.betfair import BetfairScraper


def _html_partido_betfair(fecha_js: str) -> BeautifulSoup:
    html = f"""
    <a class="_1af1df63fd08a1c6-fixtureHeader">
        <span class="_443c5e6894fef559-teamName">Equipo Local</span>
        <span class="_443c5e6894fef559-teamName">Equipo Visitante</span>
        <time class="_73836e21fc8d6105-datetime" datetime="{fecha_js}">
            <span>21:30</span>
        </time>
    </a>
    """
    return BeautifulSoup(html, "lxml").select_one("a")


def test_betfair_acepta_partido_de_hoy():
    hoy_js = datetime.now().strftime("%a %b %d %Y 21:30:00 GMT+0200 (hora de verano)")
    elemento = _html_partido_betfair(hoy_js)
    partido = BetfairScraper._parsear_partido(elemento, "Liga Test", "futbol")
    assert partido is not None
    assert partido.equipo_local == "Equipo Local"


def test_betfair_descarta_partido_de_manana():
    """Caso real detectado en produccion: Betfair mezcla partidos de
    'mañana' en su pagina de 'hoy' sin avisar en el texto visible."""
    manana = datetime.now() + timedelta(days=1)
    manana_js = manana.strftime("%a %b %d %Y 17:00:00 GMT+0200 (hora de verano)")
    elemento = _html_partido_betfair(manana_js)
    partido = BetfairScraper._parsear_partido(elemento, "Liga Test", "futbol")
    assert partido is None


def test_betfair_no_descarta_si_el_formato_de_fecha_es_inesperado():
    """Si algun dia Betfair cambia el formato del atributo datetime, no
    debe empezar a descartar TODOS los partidos por error de parseo —
    mejor un falso positivo que perder datos reales silenciosamente."""
    elemento = _html_partido_betfair("2026-08-22T21:30:00Z")  # formato ISO, no el JS esperado
    partido = BetfairScraper._parsear_partido(elemento, "Liga Test", "futbol")
    assert partido is not None
