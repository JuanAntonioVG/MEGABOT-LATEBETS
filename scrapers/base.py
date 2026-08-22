"""Interfaz comun para todos los scrapers (casas de apuestas y Flashscore).

Cada scraper es una clase que hereda de ScraperBase, se registra con
`@registrar`, y solo tiene que implementar `extraer`. El orquestador no
sabe nada de CSS ni de Playwright mas alla de esta interfaz: anadir una
casa nueva es escribir un fichero nuevo aqui dentro, nada mas.
"""

from __future__ import annotations

import re
from abc import ABC, abstractmethod
from collections.abc import Callable
from typing import ClassVar

from bs4 import BeautifulSoup
from bs4.element import Tag
from playwright.async_api import Page

from core.models import Partido

REGISTRO_SCRAPERS: dict[str, type[ScraperBase]] = {}


def registrar(cls: type[ScraperBase]) -> type[ScraperBase]:
    if not getattr(cls, "id", None):
        raise ValueError(f"{cls.__name__} debe definir un atributo de clase 'id'.")
    REGISTRO_SCRAPERS[cls.id] = cls
    return cls


class ScraperBase(ABC):
    id: ClassVar[str]
    nombre_legible: ClassVar[str]

    @abstractmethod
    async def extraer(self, page: Page, url: str, deporte: str) -> list[Partido]:
        """Navega a `url` y devuelve la lista de partidos encontrados."""
        raise NotImplementedError


# --------------------------------------------------------------------------
# Utilidades compartidas por varios scrapers
# --------------------------------------------------------------------------

SELECTORES_COOKIES_HABITUALES = [
    "#onetrust-accept-btn-handler",
    "#tarteaucitronPersonalize2",
    "button[aria-label='Cerrar']",
]


async def aceptar_cookies(page: Page, selectores: list[str] | None = None, timeout_ms: int = 4000) -> bool:
    """Intenta cerrar el banner de cookies probando varios selectores conocidos.
    Nunca lanza excepcion: si no encuentra nada, simplemente sigue (la web
    puede no tener banner, o ya estar aceptado de una sesion anterior)."""
    for selector in selectores or SELECTORES_COOKIES_HABITUALES:
        try:
            await page.locator(selector).first.click(timeout=timeout_ms)
            return True
        except Exception:
            continue
    return False


async def scroll_hasta_estabilizar(
    page: Page,
    selector_partido: str,
    max_scrolls_sin_cambios: int = 5,
    pausa_ms: int = 700,
    max_scrolls_total: int = 60,
) -> None:
    """Hace scroll hacia abajo repetidamente hasta que el numero de elementos
    que casan `selector_partido` deja de crecer. Pensado para paginas con
    carga perezosa (Bwin, Winamax)."""
    sin_cambios = 0
    anterior = -1
    for _ in range(max_scrolls_total):
        actual = await page.locator(selector_partido).count()
        if actual <= anterior:
            sin_cambios += 1
            if sin_cambios >= max_scrolls_sin_cambios:
                break
        else:
            sin_cambios = 0
        anterior = actual
        await page.mouse.wheel(0, 2600)
        await page.wait_for_timeout(pausa_ms)


def agrupar_por_liga(
    soup: BeautifulSoup,
    selector_header: str,
    selector_partido: str,
    extraer_titulo_liga: Callable[[Tag], str],
) -> list[tuple[Tag, str]]:
    """Recorre el documento en orden y empareja cada partido con la liga del
    ultimo 'header' de liga visto antes que el — el patron habitual en estas
    webs es: cabecera de liga/competicion, seguida de sus partidos, como
    hermanos (o primos) en el DOM, en orden de aparicion.

    `extraer_titulo_liga` es una funcion (Tag) -> str que sabe leer el texto
    de un elemento 'header'. Si un partido aparece antes de cualquier header
    (no deberia pasar, pero por si acaso), se le asigna "Desconocida".
    """
    combinados = soup.select(f"{selector_header}, {selector_partido}")
    ids_header = {id(el) for el in soup.select(selector_header)}

    liga_actual = "Desconocida"
    resultado: list[tuple[Tag, str]] = []
    for el in combinados:
        if id(el) in ids_header:
            try:
                titulo = extraer_titulo_liga(el)
                if titulo:
                    liga_actual = titulo
            except Exception:
                pass
        else:
            resultado.append((el, liga_actual))
    return resultado


def extraer_hora(texto: str) -> str:
    """Busca HH:MM en un texto libre y lo devuelve normalizado con ceros
    iniciales. Devuelve 'N/A' si no encuentra ninguna hora (ej. 'Ahora',
    'Comienza en 5 min', etc. — evita falsos positivos en el matcher)."""
    match = re.search(r"(\d{1,2}):(\d{2})", texto)
    if match:
        return f"{int(match.group(1)):02d}:{match.group(2)}"
    return "N/A"
