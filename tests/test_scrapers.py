"""Tests de la logica PURA de parseo de los scrapers (sin tocar la red) —
sobre todo para las reglas de "solo hoy, no otro dia" que son faciles de
verificar con HTML simulado y que no dependen de que la web este viva."""

from datetime import datetime, timedelta

from bs4 import BeautifulSoup

from core.models import EstadoPartido
from scrapers.betfair import BetfairScraper
from scrapers.bwin import _es_fecha_de_hoy as _bwin_es_fecha_de_hoy
from scrapers.flashscore import FlashscoreScraper
from scrapers.pokerstars import PokerstarsScraper
from scrapers.sportium import SportiumScraper
from scrapers.williamhill import WilliamHillScraper, _es_fecha_de_hoy
from scrapers.winamax import _es_de_otro_dia


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


def test_betfair_detecta_en_vivo_por_el_grid_de_tenis_sin_status_ni_datetime():
    """Bug real detectado en vivo el 2026-08-23 escaneando tenis: un
    partido de Challenger/ITF en directo no tiene ni div[class*="-status"]
    con texto ni time[class*="-datetime"] — el unico indicador es un grid
    de sets/juegos/puntos con clases "...-inPlay"/"...-serving". Sin el
    patron de respaldo, 11 de 21 partidos de esa pagina se perdian
    enteros en vez de marcarse en vivo."""
    html = """
    <a class="_1af1df63fd08a1c6-fixtureHeader">
        <span class="_443c5e6894fef559-teamName">Equipo Local</span>
        <span class="_443c5e6894fef559-teamName">Equipo Visitante</span>
        <div class="_90346fd614c6253a-square _90346fd614c6253a-inPlay">15</div>
    </a>
    """
    elemento = BeautifulSoup(html, "lxml").select_one("a")
    partido = BetfairScraper._parsear_partido(elemento, "Liga Test", "tenis")
    assert partido is not None
    assert partido.estado == EstadoPartido.EN_VIVO


def test_betfair_sin_status_ni_datetime_ni_marcador_en_vivo_descarta_el_partido():
    html = """
    <a class="_1af1df63fd08a1c6-fixtureHeader">
        <span class="_443c5e6894fef559-teamName">Equipo Local</span>
        <span class="_443c5e6894fef559-teamName">Equipo Visitante</span>
    </a>
    """
    elemento = BeautifulSoup(html, "lxml").select_one("a")
    assert BetfairScraper._parsear_partido(elemento, "Liga Test", "tenis") is None


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


def _html_partido_williamhill_clasico_normal(en_vivo: bool = False) -> BeautifulSoup:
    """Patron real de la plantilla "clasica" (baloncesto) para deportes que
    no son de EEUU: nombres en dos <span> dentro de .btmarket__link-name,
    visto en vivo el 2026-08-23 contra 'Amistosos internacionales'."""
    clase_live = "wh-label btmarket__live go area-livescore event__status"
    if not en_vivo:
        clase_live += " displayNone"
    html = f"""
    <div class="event" id="OB_EV1" data-startdatetime="2026-08-23T15:15:00.000Z">
        <div class="btmarket__content">
            <label class="{clase_live}">00:00</label>
            <div class="btmarket__link-name btmarket__link-name--2-rows">
                <span>Georgia</span> <span>Israel</span>
            </div>
            <time datetime="2026-08-23T15:15:00+00:00">23 ago. 17:15</time>
        </div>
    </div>
    """
    return BeautifulSoup(html, "lxml").select_one("div.event")


def _html_partido_williamhill_clasico_us() -> BeautifulSoup:
    """Patron real de la plantilla "clasica" para deportes de EEUU (WNBA):
    nombres en <p> dentro de .event__team, no en .btmarket__link-name."""
    html = """
    <section class="markettable__event event" id="OB_EV2" data-startdatetime="2026-08-23T20:00:00.000Z">
        <div class="event__team"><p>Seattle Storm</p></div>
        <div class="event__team"><p>Dallas Wings</p></div>
        <time datetime="2026-08-23T20:00:00+00:00">23 ago. 22:00</time>
    </section>
    """
    return BeautifulSoup(html, "lxml").select_one("section")


def test_williamhill_clasico_extrae_nombres_del_patron_normal():
    elemento = _html_partido_williamhill_clasico_normal()
    partido = WilliamHillScraper._parsear_partido_clasico(elemento, "Amistosos internacionales", "baloncesto")
    assert partido is not None
    assert partido.equipo_local == "Georgia"
    assert partido.equipo_visitante == "Israel"
    assert partido.estado == EstadoPartido.PROGRAMADO
    assert partido.detalle_estado == "17:15"


def test_williamhill_clasico_extrae_nombres_del_patron_us():
    """Regresion directa del bug real: sin el patron de respaldo
    '.event__team > p', WNBA devolvia 0 partidos pese a estar presentes
    en la pagina (los nombres no viven en .btmarket__link-name aqui)."""
    elemento = _html_partido_williamhill_clasico_us()
    partido = WilliamHillScraper._parsear_partido_clasico(elemento, "WNBA", "baloncesto")
    assert partido is not None
    assert partido.equipo_local == "Seattle Storm"
    assert partido.equipo_visitante == "Dallas Wings"


def test_williamhill_clasico_detecta_en_vivo_por_la_clase_displaynone():
    """Bug real evitado: el label del marcador en vivo SIEMPRE esta en el
    DOM, solo se distingue por si tiene la clase 'displayNone' o no."""
    elemento = _html_partido_williamhill_clasico_normal(en_vivo=True)
    partido = WilliamHillScraper._parsear_partido_clasico(elemento, "Liga Test", "baloncesto")
    assert partido is not None
    assert partido.estado == EstadoPartido.EN_VIVO
    assert partido.detalle_estado == "00:00"


def test_williamhill_clasico_sin_ninguno_de_los_dos_patrones_devuelve_none():
    html = (
        '<div class="event" id="OB_EV3"><time datetime="2026-08-23T17:15:00+00:00">23 ago. 17:15</time></div>'
    )
    elemento = BeautifulSoup(html, "lxml").select_one("div.event")
    assert WilliamHillScraper._parsear_partido_clasico(elemento, "Liga Test", "baloncesto") is None


def _html_evento_pokerstars_programado() -> BeautifulSoup:
    html = """
    <li data-testid="event">
        <span class="_ee287b1"><span>-</span>Portugal (F)</span>
        <span class="_ee287b1">Bélgica (F)</span>
        <time datetime="13:30">13:30</time>
    </li>
    """
    return BeautifulSoup(html, "lxml").select_one("li")


def _html_evento_pokerstars_en_vivo() -> BeautifulSoup:
    """Patron real de un partido EN VIVO visto navegando voleibol
    competicion a competicion el 2026-08-23: no usa span._ee287b1 en
    absoluto, sino [data-testid="scoreboard-participant-name"], y no
    tiene time[datetime] — bug real: sin este patron de respaldo el
    partido se perdia entero en vez de marcarse en vivo."""
    html = """
    <li data-testid="event">
        <span data-testid="scoreboard-participant-name">Japón (F)</span>
        <span data-testid="scoreboard-participant-name">Corea del Sur (F)</span>
    </li>
    """
    return BeautifulSoup(html, "lxml").select_one("li")


def test_pokerstars_evento_lista_patron_programado():
    elemento = _html_evento_pokerstars_programado()
    partido = PokerstarsScraper._parsear_evento_lista(elemento, "Liga Test", "voleibol")
    assert partido is not None
    assert partido.equipo_local == "Portugal (F)"
    assert partido.equipo_visitante == "Bélgica (F)"
    assert partido.estado == EstadoPartido.PROGRAMADO


def test_pokerstars_evento_lista_patron_en_vivo():
    elemento = _html_evento_pokerstars_en_vivo()
    partido = PokerstarsScraper._parsear_evento_lista(elemento, "Liga Test", "voleibol")
    assert partido is not None
    assert partido.equipo_local == "Japón (F)"
    assert partido.equipo_visitante == "Corea del Sur (F)"
    assert partido.estado == EstadoPartido.EN_VIVO


def test_pokerstars_evento_lista_sin_ningun_patron_devuelve_none():
    html = '<li data-testid="event"><time datetime="13:30">13:30</time></li>'
    elemento = BeautifulSoup(html, "lxml").select_one("li")
    assert PokerstarsScraper._parsear_evento_lista(elemento, "Liga Test", "voleibol") is None


def test_pokerstars_enlaces_competiciones_filtra_por_ruta_base():
    """Solo deben aceptarse enlaces que cuelguen de la misma ruta que la
    URL de partida — asi no hace falta depender de las clases CSS
    ofuscadas del contenedor de competiciones."""
    html = """
    <details data-testid="sports-expandable-accordion">
        <ul>
            <li><a href="/sports/voleibol/998917/campeonato-de-europa-femenino/12370950/">Campeonato de Europa</a></li>
            <li><a href="/sports/futbol/1/otra-cosa/999/">No es de este deporte</a></li>
        </ul>
    </details>
    """
    soup = BeautifulSoup(html, "lxml")
    enlaces = PokerstarsScraper._enlaces_competiciones(
        soup, "https://www.pokerstars.es/sports/voleibol/998917/"
    )
    assert len(enlaces) == 1
    assert enlaces[0][1] == "Campeonato de Europa"
    assert enlaces[0][0] == (
        "https://www.pokerstars.es/sports/voleibol/998917/campeonato-de-europa-femenino/12370950/"
    )


def test_bwin_fecha_de_hoy_acepta_dia_y_mes_correctos():
    hoy = datetime.now()
    texto = f"martes - {hoy.day}/{hoy.month}/26"
    assert _bwin_es_fecha_de_hoy(texto) is True


def test_bwin_fecha_de_otro_dia_se_descarta():
    """Caso real detectado en vivo el 2026-08-23: sin partidos de hockey
    para hoy, Bwin agrupaba por fecha (en vez de por liga) y mostraba
    partidos de pretemporada de la NHL de mas de un mes de distancia como
    si fueran de "hoy" — 5 partidos con liga "Desconocida" que en
    realidad eran del 29/9, no del dia de la prueba."""
    manana = datetime.now() + timedelta(days=1)
    texto = f"miércoles - {manana.day}/{manana.month}/26"
    assert _bwin_es_fecha_de_hoy(texto) is False


def test_bwin_fecha_no_interpretable_no_descarta_por_las_dudas():
    assert _bwin_es_fecha_de_hoy("") is True
    assert _bwin_es_fecha_de_hoy("formato totalmente distinto") is True


def _html_tarjeta_winamax(con_etiqueta_fecha: bool) -> BeautifulSoup:
    etiqueta = '<div class="sc-faHdxz bssBit">3 sep.</div>' if con_etiqueta_fecha else ""
    html = f"""
    <div data-testid="match-card-1">
        <span class="sc-huGNkN">Liga de Campeones</span>
        <div class="sc-brsddS">Tappara Tampere</div>
        <div class="sc-hbWFOe bfVxfn">
            {etiqueta}
            <div class="sc-hmMbRg lIZsQ">17:30</div>
        </div>
        <div class="sc-brsddS">Bili Tygri Liberec</div>
    </div>
    """
    return BeautifulSoup(html, "lxml").select_one("div[data-testid]")


def test_winamax_tarjeta_de_hoy_no_tiene_etiqueta_de_fecha():
    elemento = _html_tarjeta_winamax(con_etiqueta_fecha=False)
    assert _es_de_otro_dia(elemento) is False


def test_winamax_tarjeta_con_etiqueta_de_fecha_es_de_otro_dia():
    """Bug real detectado en vivo el 2026-08-23 escaneando hockey: la
    etiqueta de fecha (`div.sc-faHdxz`) esta ANIDADA dentro de la
    tarjeta, no como hermano en el DOM — tratarla como un separador
    aparte dejaba colar la primera tarjeta de un dia futuro (aqui,
    "3 sep.") como si fuera de hoy, porque su propia etiqueta solo se
    veia DESPUES de haber aceptado ya la tarjeta."""
    elemento = _html_tarjeta_winamax(con_etiqueta_fecha=True)
    assert _es_de_otro_dia(elemento) is True
