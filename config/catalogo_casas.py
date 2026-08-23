"""Catalogo ESTATICO de casas de apuestas y deportes soportados.

Esto es solo el "que existe" (estructura + URLs). El "que esta activo
ahora mismo" vive en la base de datos (tabla `toggles`) y se edita por
Telegram — nunca hay que tocar este fichero para encender/apagar algo.

Si una casa no tiene URL para un deporte, es que esa casa NO SOPORTA ese
deporte: no aparecera como opcion en el menu de Telegram en absoluto
(distinto de "soportado pero apagado", que es lo que gestiona la tabla
`toggles`).

Anadir una casa nueva = anadir una entrada aqui + su scraper en
`scrapers/`. El orquestador no necesita ningun cambio.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True, slots=True)
class CasaApuestas:
    id: str
    nombre_legible: str
    scraper_id: str
    deportes: dict[str, str] = field(default_factory=dict)


FLASHSCORE = CasaApuestas(
    id="flashscore",
    nombre_legible="Flashscore",
    scraper_id="flashscore",
    deportes={
        "futbol": "https://www.flashscore.es/futbol/",
        "baloncesto": "https://www.flashscore.es/baloncesto/",
        "voleibol": "https://www.flashscore.es/voleibol/",
        "waterpolo": "https://www.flashscore.es/waterpolo/",
        "futsal": "https://www.flashscore.es/futbol-sala/",
        "balonmano": "https://www.flashscore.es/balonmano/",
        "tenis": "https://www.flashscore.es/tenis/",
        "hockey": "https://www.flashscore.es/hockey/",
    },
)

CATALOGO_CASAS: dict[str, CasaApuestas] = {
    "pokerstars": CasaApuestas(
        id="pokerstars",
        nombre_legible="Pokerstars",
        scraper_id="pokerstars",
        deportes={
            "futbol": "https://www.pokerstars.es/sports/futbol/1/matches/",
            "baloncesto": "https://www.pokerstars.es/sports/baloncesto/7522/matches/",
            # OJO: esta URL es la pagina "landing" del deporte (solo lista
            # competiciones, sin sufijo /matches/ que da error aqui — ver
            # docstring de scrapers/pokerstars.py), no una lista de
            # partidos como el resto. El scraper lo detecta solo y visita
            # cada competicion por separado; verificado en vivo el
            # 2026-08-23: 9 partidos reales (8 programados + 1 en vivo).
            "voleibol": "https://www.pokerstars.es/sports/voleibol/998917/",
            # Mismo tipo de URL "landing" que voleibol (sin /matches/) —
            # se sospecha el mismo arreglo aplicaria aqui tal cual, pero
            # no se ha verificado en vivo todavia (fuera del alcance de
            # la sesion en la que se arreglo voleibol).
            "balonmano": "https://www.pokerstars.es/sports/balonmano/468328/",
            "tenis": "https://www.pokerstars.es/sports/tenis/2/matches/",
            # Verificado en vivo el 2026-08-23: 0 partidos ese dia, pero
            # confirmado que NO es un fallo — Pokerstars solo cubre 6
            # competiciones de hockey (Alemania-DEL, Eslovaquia-Extraliga,
            # Champions Hockey League, Rep. Checa-Extraliga, SHL Sueca,
            # Suiza-NLA, todas comprobadas en la pestaña "Competiciones"),
            # ninguna con temporada empezada todavia, y ningun mercado de
            # "amistosos de clubes" en absoluto — a diferencia de William
            # Hill/Sportium/Bwin/Winamax, que si ofrecen ese pool.
            "hockey": "https://www.pokerstars.es/sports/hockey-hielo/7524/matches/",
            # NO hay waterpolo: comprobado en vivo el 2026-08-23 que no
            # aparece en el menu de deportes de Pokerstars Sports (17
            # deportes listados, sin Waterpolo) — no lo ofrecen.
            # Mismo tipo de URL "landing" que voleibol (id 15826206, el
            # mismo que usa Betfair para futsal — comparten plataforma).
            # Verificado en vivo el 2026-08-23: 0 partidos reales ese dia
            # (la unica competicion, "Brasil - LNF", redirige a su pagina
            # de "outrights" por no tener ningun partido programado
            # todavia — coincide con Bwin/Betfair, tambien en 0 ese dia).
            "futsal": "https://www.pokerstars.es/sports/futbol-sala/15826206/",
        },
    ),
    "bwin": CasaApuestas(
        id="bwin",
        nombre_legible="Bwin",
        scraper_id="bwin",
        deportes={
            "futbol": "https://www.bwin.es/es/sports/f%C3%BAtbol-4/hoy",
            "baloncesto": "https://www.bwin.es/es/sports/baloncesto-7/cupones/hoy-863",
            "voleibol": "https://www.bwin.es/es/sports/voleibol-18/apuestas?tab=matches",
            "waterpolo": "https://www.bwin.es/es/sports/waterpolo-52/apuestas?tab=matches",
            "futsal": "https://www.bwin.es/es/sports/f%C3%BAtbol-sala-70/apuestas?tab=matches",
            "balonmano": "https://www.bwin.es/es/sports/balonmano-16/apuestas?tab=matches",
            "tenis": "https://www.bwin.es/es/sports/tenis-5/hoy",
            # BUG REAL arreglado el 2026-08-23: la URL en si es correcta,
            # pero el scraper devolvia partidos de pretemporada de la NHL
            # de mas de un mes de distancia (finales de septiembre) como
            # si fueran de "hoy" — ver el docstring de scrapers/bwin.py
            # para la causa (agrupacion por fecha en vez de por liga
            # cuando no hay nada para hoy) y el arreglo. Verificado en
            # vivo tras el arreglo: 0 partidos ese dia (correcto, la NHL
            # no ha empezado la temporada).
            "hockey": "https://www.bwin.es/es/sports/hockey-sobre-hielo-12/apuestas?tab=matches",
        },
    ),
    "winamax": CasaApuestas(
        id="winamax",
        nombre_legible="Winamax",
        scraper_id="winamax",
        deportes={
            "futbol": "https://www.winamax.es/apuestas-deportivas/sports/1",
            "baloncesto": "https://www.winamax.es/apuestas-deportivas/sports/2",
            "voleibol": "https://www.winamax.es/apuestas-deportivas/sports/23",
            "waterpolo": "https://www.winamax.es/apuestas-deportivas/sports/26",
            "futsal": "https://www.winamax.es/apuestas-deportivas/sports/29",
            "balonmano": "https://www.winamax.es/apuestas-deportivas/sports/6",
            "tenis": "https://www.winamax.es/apuestas-deportivas/sports/5",
            # BUG REAL arreglado el 2026-08-23: el scraper devolvia 1
            # "partido de hoy" que en realidad era del 3 de septiembre —
            # ver el docstring de scrapers/winamax.py para la causa
            # (bug generalizable, no especifico de hockey: solo se
            # manifiesta cuando NINGUN partido es de verdad de hoy).
            # Verificado en vivo tras el arreglo: 0 partidos ese dia.
            "hockey": "https://www.winamax.es/apuestas-deportivas/sports/4",
        },
    ),
    "betfair": CasaApuestas(
        id="betfair",
        nombre_legible="Betfair",
        scraper_id="betfair",
        deportes={
            "futbol": "https://www.betfair.es/sport/football",
            "baloncesto": "https://www.betfair.es/sport/basketball",
            "voleibol": "https://www.betfair.es/sport/volleyball",
            # BUG REAL arreglado el 2026-08-23: "https://www.betfair.es/sport/futsal"
            # (la URL que habia aqui antes) NO es una redireccion valida —
            # cae en la portada generica (www.betfair.es/apuestas/), y el
            # scraper se comia sus "Partidos Destacados" (?futbol normal,
            # La Liga) como si fueran partidos de futsal. Detectado
            # comparando contra Flashscore: "Atlético de Madrid vs
            # Villarreal" apareciendo en la categoria de futsal no cuadraba
            # ni de broma. El slug correcto (encontrado navegando el menu
            # de deportes real) es "fútbol-sala" con un id numerico propio,
            # NO "futsal" — mismo id (15826206) que usa Pokerstars, que
            # comparte plataforma con Betfair.
            "futsal": "https://www.betfair.es/apuestas/f%C3%BAtbol-sala/s-15826206",
            "balonmano": "https://www.betfair.es/sport/handball",
            "tenis": "https://www.betfair.es/sport/tennis",
            # Verificado en vivo el 2026-08-23: redirige al mismo id que
            # Pokerstars (s-7524, comparten plataforma) y da 0 partidos
            # por el mismo motivo — mismas 6 competiciones sin temporada
            # empezada, sin mercado de amistosos de clubes. No es un bug.
            "hockey": "https://www.betfair.es/sport/ice-hockey",
            # NO hay waterpolo aqui: comprobado en vivo el 2026-08-23 que
            # Betfair.es sencillamente no lo ofrece como deporte — ni en
            # el menu principal ni en "Apuestas especiales" (listado
            # completo de ~26 deportes revisado a mano, sin Waterpolo).
            # No es un descuido, es que no existe URL a la que apuntar.
        },
    ),
    "williamhill": CasaApuestas(
        id="williamhill",
        nombre_legible="William Hill",
        scraper_id="williamhill",
        deportes={
            "futbol": "https://sports.williamhill.es/betting/es-es/f%C3%BAtbol/partidos/competici%C3%B3n/hoy/",
            # Verificado en vivo el 2026-08-23: mismo patron de URL que
            # futbol, pero la pagina usa una plantilla completamente
            # distinta ("clasica") — ver el docstring de
            # scrapers/williamhill.py. 13 partidos reales encontrados
            # (mas 2 de "eBasketball" simulado, descartados a proposito).
            "baloncesto": "https://sports.williamhill.es/betting/es-es/baloncesto/partidos/competici%C3%B3n/hoy/",
            # Verificado en vivo el 2026-08-23: la rama "clasica" añadida
            # para baloncesto generaliza tal cual (mismos selectores,
            # ningun cambio de codigo). 12 partidos reales, incluidos 4
            # en vivo detectados correctamente.
            "voleibol": "https://sports.williamhill.es/betting/es-es/voleibol/partidos/competici%C3%B3n/hoy/",
            # NO hay waterpolo: comprobado en vivo el 2026-08-23 que no
            # aparece en el listado "Deportes A-Z" (31 deportes, completo,
            # sin Waterpolo) — William Hill.es no lo ofrece. La URL con
            # el mismo patron (".../waterpolo/partidos/...") ni siquiera
            # carga (timeout de navegacion en vez de un 404 limpio).
            # Verificado en vivo el 2026-08-23: tenis SI usa la plantilla
            # "moderna" (la misma que futbol, no la "clasica" de
            # baloncesto/voleibol) — cero cambios de codigo, 54 partidos.
            "tenis": "https://sports.williamhill.es/betting/es-es/tenis/partidos/competici%C3%B3n/hoy/",
            # NO hay futsal: comprobado en vivo el 2026-08-23 que "sala"/
            # "futsal" no aparece por ningun sitio en la web (ni en el
            # listado A-Z de deportes) — William Hill.es no lo ofrece,
            # igual que Waterpolo. Mismo sintoma que Waterpolo: la URL
            # con el mismo patron ni siquiera carga (timeout).
            # Verificado en vivo el 2026-08-23: plantilla "clasica" (como
            # baloncesto/voleibol, NO la moderna de futbol/tenis), cero
            # cambios de codigo. 9 partidos reales — contrastados uno a
            # uno contra los 24 "Amistosos de Clubs" de Flashscore ese
            # dia: son un subconjunto genuino (los equipos profesionales
            # de Alemania/Austria/Suiza/Escandinavia/Francia), sin los
            # amistosos de cantera/juveniles rusos y alemanes de 3ª que
            # Flashscore si agrega — no es un fallo de scraper, es
            # alcance real del catalogo de apuestas de William Hill.
            "hockey": "https://sports.williamhill.es/betting/es-es/hockey-hielo/partidos/competici%C3%B3n/hoy/",
        },
    ),
    "sportium": CasaApuestas(
        id="sportium",
        nombre_legible="Sportium",
        scraper_id="sportium",
        deportes={
            "futbol": "https://www.sportium.es/apuestas/sports/soccer/matches/today",
            # Verificado en vivo el 2026-08-23: mismo patron de URL que
            # futbol, y el scraper generico ya vale tal cual (10 partidos,
            # ninguna rama especial hizo falta — a diferencia de William
            # Hill, aqui SI se reutiliza la misma plantilla que futbol).
            "baloncesto": "https://www.sportium.es/apuestas/sports/basketball/matches/today",
            # Igual que baloncesto: mismo patron de URL, scraper generico
            # sin cambios. 7 partidos verificados en vivo.
            "voleibol": "https://www.sportium.es/apuestas/sports/volleyball/matches/today",
            # NO hay waterpolo: comprobado en vivo el 2026-08-23 que no
            # aparece en el listado completo de deportes de Sportium (24
            # deportes, sin Waterpolo).
            # Verificado en vivo el 2026-08-23: 59 partidos, sin cambios
            # de codigo (mismo patron que futbol/baloncesto/voleibol).
            "tenis": "https://www.sportium.es/apuestas/sports/tennis/matches/today",
            # Igual que el resto: mismo patron de URL, scraper generico
            # sin cambios. Verificado en vivo el 2026-08-23: 2 partidos.
            "futsal": "https://www.sportium.es/apuestas/sports/futsal/matches/today",
            # OJO con el slug: es "ice_hockey" con GUION BAJO, no
            # "ice-hockey" con guion normal como el resto de deportes en
            # ingles de Sportium — confirmado navegando el menu real.
            # Verificado en vivo el 2026-08-23: 2 partidos (Nottingham
            # Panthers-Angers Ducs y Tabor-Dresdner Eislöwen, ambos
            # tambien presentes en Flashscore/William Hill ese dia), sin
            # cambios de codigo.
            "hockey": "https://www.sportium.es/apuestas/sports/ice_hockey/matches/today",
        },
    ),
}


# Vive aqui (dato de catalogo, no de formato) para que tanto
# telegram_bot/notificaciones.py como telegram_bot/miniapp.py puedan
# importarlo sin que uno dependa del otro — notificaciones.py necesita
# construir URLs de la Mini App (miniapp.py) y antes ese import cruzado
# habria creado un ciclo.
EMOJIS_DEPORTE = {
    "futbol": "⚽",
    "baloncesto": "🏀",
    "tenis": "🎾",
    "voleibol": "🏐",
    "balonmano": "🤾",
    "hockey": "🏒",
    "waterpolo": "🤽",
    "futsal": "🥅",
}


def todas_las_combinaciones() -> list[tuple[str, str, str]]:
    """Devuelve (casa_id, deporte, url) para cada combinacion que existe
    en el catalogo (independientemente de si esta encendida o apagada)."""
    combinaciones = []
    for casa in CATALOGO_CASAS.values():
        for deporte, url in casa.deportes.items():
            if url:
                combinaciones.append((casa.id, deporte, url))
    return combinaciones


def todos_los_deportes() -> list[str]:
    """Todos los deportes que soporta AL MENOS una casa, ordenados
    alfabeticamente. Para el menu de Telegram "por deporte"."""
    deportes = {deporte for casa in CATALOGO_CASAS.values() for deporte, url in casa.deportes.items() if url}
    return sorted(deportes)


def casas_que_soportan(deporte: str) -> list[CasaApuestas]:
    """Casas del catalogo que tienen URL para ese deporte concreto."""
    return [casa for casa in CATALOGO_CASAS.values() if casa.deportes.get(deporte)]
