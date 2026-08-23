"""Tests de la logica PURA de parseo de los scrapers (sin tocar la red) —
sobre todo para las reglas de "solo hoy, no otro dia" que son faciles de
verificar con HTML simulado y que no dependen de que la web este viva."""

from datetime import datetime, timedelta

from bs4 import BeautifulSoup

from scrapers.betfair import BetfairScraper
from scrapers.flashscore import FlashscoreScraper
from scrapers.sportium import SportiumScraper
from scrapers.williamhill import WilliamHillScraper, _es_fecha_de_hoy


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


def _html_partido_flashscore_futbol() -> BeautifulSoup:
    """Patron real de futbol: nombre DENTRO de un div .event__xxxParticipant,
    en un span con testid — visto en vivo el 2026-08-22."""
    html = """
    <div class="event__match">
        <div class="event__homeParticipant">
            <span data-testid="wcl-scores-simple-text-01">Real Madrid</span>
        </div>
        <div class="event__awayParticipant">
            <span data-testid="wcl-scores-simple-text-01">Barcelona</span>
        </div>
        <div class="event__time">20:00</div>
    </div>
    """
    return BeautifulSoup(html, "lxml").select_one("div.event__match")


def _html_partido_flashscore_baloncesto() -> BeautifulSoup:
    """Patron real de baloncesto: nombre DIRECTO dentro de
    div.event__participant--xxx, sin envoltorio ni span con testid — bug
    real detectado en vivo el 2026-08-22 (0 de 27 partidos se
    reconocian con el patron de futbol de arriba)."""
    html = """
    <div class="event__match">
        <div class="event__participant event__participant--home">Warwick Senators F</div>
        <div class="event__participant event__participant--away fontExtraBold">
            Sturt Sabres F
            <svg class="winner-ico"><title></title></svg>
        </div>
        <div class="event__stage"><div class="event__stage--block">Finalizado</div></div>
    </div>
    """
    return BeautifulSoup(html, "lxml").select_one("div.event__match")


def test_flashscore_extrae_nombres_con_patron_futbol():
    elemento = _html_partido_flashscore_futbol()
    partido = FlashscoreScraper._parsear_partido(elemento, "LaLiga", "futbol")
    assert partido is not None
    assert partido.equipo_local == "Real Madrid"
    assert partido.equipo_visitante == "Barcelona"


def test_flashscore_extrae_nombres_con_patron_baloncesto():
    """Regresion directa del bug real: sin el patron 'plano' de
    respaldo, esto devolvia None y Flashscore/baloncesto daba 0
    partidos pese a haberlos de verdad en la pagina."""
    elemento = _html_partido_flashscore_baloncesto()
    partido = FlashscoreScraper._parsear_partido(elemento, "NBL1", "baloncesto")
    assert partido is not None
    assert partido.equipo_local == "Warwick Senators F"
    # El icono SVG de "ganador" (sin texto) no debe colarse en el nombre.
    assert partido.equipo_visitante == "Sturt Sabres F"


def test_flashscore_sin_ninguno_de_los_dos_patrones_devuelve_none():
    html = '<div class="event__match"><div class="event__time">20:00</div></div>'
    elemento = BeautifulSoup(html, "lxml").select_one("div.event__match")
    assert FlashscoreScraper._parsear_partido(elemento, "Liga Test", "futbol") is None


def _html_partido_williamhill(nombres: str, hora: str = "17:00") -> BeautifulSoup:
    html = f"""
    <article data-testid="event-ob-ev1">
        <div class="sp-o-market__title"><a><span class="sp-betName">{nombres}</span></a></div>
        <div class="sp-o-market__clock"><span class="sp-o-market__clock__time">{hora}</span></div>
    </article>
    """
    return BeautifulSoup(html, "lxml").select_one("article")


def test_williamhill_separa_equipos_por_el_caracter_especial():
    """El separador NO es un guion normal "-" sino el caracter Unicode
    "₋" (U+208B) — con un guion normal, split() no separaria nada y el
    partido se descartaria entero."""
    elemento = _html_partido_williamhill("At. Madrid ₋ Villarreal")
    partido = WilliamHillScraper._parsear_partido(elemento, "LaLiga", "futbol")
    assert partido is not None
    assert partido.equipo_local == "At. Madrid"
    assert partido.equipo_visitante == "Villarreal"


def test_williamhill_con_guion_normal_no_separa_nada():
    elemento = _html_partido_williamhill("At. Madrid - Villarreal")
    assert WilliamHillScraper._parsear_partido(elemento, "LaLiga", "futbol") is None


def test_williamhill_fecha_de_hoy_acepta_dia_y_mes_correctos():
    hoy = datetime.now()
    texto = f"dom, {hoy.day} {['ene', 'feb', 'mar', 'abr', 'may', 'jun', 'jul', 'ago', 'sep', 'oct', 'nov', 'dic'][hoy.month - 1]}"
    assert _es_fecha_de_hoy(texto) is True


def test_williamhill_fecha_de_otro_dia_se_descarta():
    """Caso real detectado en vivo: la pestaña 'Hoy' mezclaba partidos
    bajo una cabecera fechada 'Lun, 24 Ago' (mañana) junto a los de hoy."""
    manana = datetime.now() + timedelta(days=1)
    meses = ["ene", "feb", "mar", "abr", "may", "jun", "jul", "ago", "sep", "oct", "nov", "dic"]
    texto = f"lun, {manana.day} {meses[manana.month - 1]}"
    # Si por casualidad "mañana" cae el mismo dia+mes que hoy (no puede
    # pasar salvo fin de año raro), el test no tendria sentido — no es
    # el caso normal, se ignora esa arista.
    assert _es_fecha_de_hoy(texto) is False


def test_williamhill_fecha_no_interpretable_no_descarta_por_las_dudas():
    assert _es_fecha_de_hoy("") is True
    assert _es_fecha_de_hoy("formato totalmente distinto") is True


def _html_partido_sportium(con_fecha: bool = True) -> BeautifulSoup:
    """Reproduce la estructura real: la fecha/hora vive FUERA del <a>
    con los nombres de equipo, como hermano suyo dentro de la fila
    ta-EventListItem — no dentro de el."""
    fecha_html = "<div>Hoy, 17:00</div>" if con_fecha else ""
    html = f"""
    <div class="ta-FlexPane ta-EventListItem">
        {fecha_html}
        <a class="ta-Button EventListItemDetails ta-EventListItemDetails">
            <div class="ta-participantName">At. Madrid</div>
            <div class="ta-participantName">Villarreal</div>
        </a>
    </div>
    """
    return BeautifulSoup(html, "lxml").select_one("div.ta-EventListItem")


def test_sportium_encuentra_la_fecha_fuera_del_enlace():
    """Bug real detectado en produccion: buscar la fecha DENTRO del <a>
    de nombres de equipo (en vez de en toda la fila) descartaba el
    partido entero pese a que los nombres se leian bien — la pagina
    real da 0 partidos con ese fallo aunque las ligas se expandan
    correctamente."""
    elemento = _html_partido_sportium(con_fecha=True)
    partido = SportiumScraper._parsear_partido(elemento, "LaLiga", "futbol")
    assert partido is not None
    assert partido.equipo_local == "At. Madrid"
    assert partido.equipo_visitante == "Villarreal"
    assert partido.detalle_estado == "17:00"


def test_sportium_sin_fecha_descarta_el_partido():
    elemento = _html_partido_sportium(con_fecha=False)
    assert SportiumScraper._parsear_partido(elemento, "LaLiga", "futbol") is None
