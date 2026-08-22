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
            "voleibol": "https://www.pokerstars.es/sports/voleibol/998917/",
            "balonmano": "https://www.pokerstars.es/sports/balonmano/468328/",
            "tenis": "https://www.pokerstars.es/sports/tenis/2/matches/",
            "hockey": "https://www.pokerstars.es/sports/hockey-hielo/7524/matches/",
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
            "futsal": "https://www.betfair.es/sport/futsal",
            "balonmano": "https://www.betfair.es/sport/handball",
            "tenis": "https://www.betfair.es/sport/tennis",
            "hockey": "https://www.betfair.es/sport/ice-hockey",
        },
    ),
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
