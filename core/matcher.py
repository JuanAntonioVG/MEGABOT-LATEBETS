"""Motor de comparacion (auditor): empareja los partidos de una casa de
apuestas contra los de Flashscore y detecta discrepancias de horario.

Dos capas de normalizacion de nombres de equipo, a proposito separadas:

1. `REGLAS_LIMPIEZA`: reglas genericas y estables (quitar "FC", ".", "-",
   normalizar sub-23/femenino/equipos B...). Son universales, no dependen
   de que equipo concreto sea, y no necesitan mantenimiento por IA.
2. El diccionario de ALIAS de la base de datos (tabla `alias_equipos`):
   traducciones de nombres propios concretos (ej. "barça" -> "barcelona").
   Este es el que crece con ayuda de Ollama a partir de la cola de
   partidos sin emparejar (ver `core/alias_ia.py`), y el que tu apruebas
   antes de que se use.

El resto de la logica (fuzzy matching con thefuzz, deteccion de
discrepancias de horario) es la misma que ya funcionaba en el bot
anterior, adaptada a los modelos tipados nuevos y con el campo de liga
incluido en cada discrepancia.
"""

from __future__ import annotations

import re
from pathlib import Path

from thefuzz import fuzz

from core import db
from core.models import Discrepancia, EstadisticasAuditoria, Partido

UMBRAL_CONFIANZA_MINIMA = 70

REGLAS_LIMPIEZA: dict[str, str] = {
    " fc": "",
    " cf": "",
    " sc": "",
    " sk": "",
    " fk": "",
    "spor kulubu": "",
    " genclikspor": "",
    " belediyespor": "",
    " united": "",
    " city": "",
    " town": "",
    " wanderers": "",
    " athletic": "",
    " rangers": "",
    ".": "",
    "'": "",
    "-": " ",
    " (w)": "",
    " (f)": "",
    " women": "",
    " (muj)": "",
    " u23": " sub-23",
    " u21": " sub-21",
    " u20": " sub-20",
    " u19": " sub-19",
    " sub23": " sub-23",
    "reserves": "",
    "reservas": "",
    " (res)": "",
    " ii": " 2",
    " b": " 2",
    " srb": "",
    " fra": "",
}


def normalizar_nombre(nombre: str, alias: dict[str, str]) -> str:
    """Aplica limpieza generica + alias aprobados, en ese orden."""
    texto = nombre.lower().strip()
    for viejo, nuevo in REGLAS_LIMPIEZA.items():
        texto = texto.replace(viejo, nuevo)
    for variante, canonico in alias.items():
        if variante in texto:
            texto = texto.replace(variante, canonico)
    return " ".join(texto.split())


def _similitud_nombre(a: str, b: str, alias: dict[str, str]) -> float:
    return float(fuzz.token_set_ratio(normalizar_nombre(a, alias), normalizar_nombre(b, alias)))


def _puntuacion_partido(casa: Partido, candidato: Partido, alias: dict[str, str]) -> float:
    """Compara equipo local CONTRA equipo local y visitante CONTRA
    visitante POR SEPARADO, y se queda con el PEOR de los dos.

    Bug real detectado en produccion la noche del 22 al 23 de agosto:
    antes se comparaba "local+visitante" como una sola bolsa de
    palabras (token_set_ratio sobre el texto junto), y eso deja que un
    solo equipo compartido empuje la puntuacion por encima del umbral
    aunque el OTRO equipo no tenga nada que ver — ej. "Skanderborg AGF
    Haandbold vs TMS Ringsted" emparejado con "Aalborg vs Skanderborg
    AGF" (79% con la bolsa junta): son dos partidos reales y DISTINTOS
    que solo comparten un equipo, no el mismo partido escrito distinto.
    Verificado contra los 9 casos reales de esa noche: este cambio
    rechaza los 5 que eran falsos emparejamientos (bajan de 70-83% a
    20-46%) sin perder ninguno de los 4 que si eran discrepancias
    reales (se quedan en 77-100%, alguno incluso sube)."""
    return min(
        _similitud_nombre(casa.equipo_local, candidato.equipo_local, alias),
        _similitud_nombre(casa.equipo_visitante, candidato.equipo_visitante, alias),
    )


# El fuzzy matching (token_set_ratio) puntua muy alto dos nombres aunque uno
# tenga palabras "de mas" que el otro no tiene — eso es justo lo que hace
# que confunda "Barcelona B" con "Barcelona", o "Sevilla Sub-21" con
# "Sevilla": son partidos DISTINTOS (categorias/sexo distintos), no el
# mismo partido escrito de forma distinta. Se detecta la categoria en el
# nombre ORIGINAL (antes de que REGLAS_LIMPIEZA la limpie/normalice, que es
# precisamente lo que borraria la señal) y solo se aceptan como candidatos
# los pares cuya categoria coincide en AMBOS equipos. Esto no sube el
# umbral de confianza general -- solo descarta candidatos que con
# seguridad no son el mismo partido, así que nunca deja de encontrar un
# partido real de la misma categoria que sí lo sea.
_PATRON_SUB = re.compile(r"\bsub[\s-]?(\d{1,2})\b|\bu[\s-]?(\d{1,2})\b", re.IGNORECASE)
# \bf$ / \bw$: Flashscore marca varios partidos femeninos con una "F" (o
# "W") suelta al final del nombre, sin parentesis (ej. "Myjava F") -- se
# comprueba con la cadena ya recortada (strip), asi que $ es de verdad el
# final del nombre.
_PATRON_FEMENINO = re.compile(r"\bwomen\b|\bfemenino\b|\(w\)|\(f\)|\(muj\)|\bf$|\bw$", re.IGNORECASE)
_PATRON_RESERVAS = re.compile(r"\breserves?\b|\breservas\b|\(res\)|\bii\b|\bb\b", re.IGNORECASE)


def detectar_categoria(nombre_equipo: str) -> str | None:
    """Devuelve 'sub19'/'sub21'/'femenino'/'reservas' si el nombre lleva
    esa marca, o None si parece un equipo absoluto normal."""
    texto = nombre_equipo.lower().strip()
    m = _PATRON_SUB.search(texto)
    if m:
        return f"sub{m.group(1) or m.group(2)}"
    if _PATRON_FEMENINO.search(texto):
        return "femenino"
    if _PATRON_RESERVAS.search(texto):
        return "reservas"
    return None


def _categorias_compatibles(casa: Partido, candidato: Partido) -> bool:
    return detectar_categoria(casa.equipo_local) == detectar_categoria(
        candidato.equipo_local
    ) and detectar_categoria(casa.equipo_visitante) == detectar_categoria(candidato.equipo_visitante)


def normalizar_hora(detalle: str) -> str:
    """Busca HH:MM en el texto y lo estandariza. 'n/a' si no hay hora
    valida (evita falsos positivos con 'En Vivo', 'Finalizado', etc.)."""
    texto = str(detalle).lower()
    match = re.search(r"(\d{1,2}):(\d{2})", texto)
    if match:
        return f"{int(match.group(1)):02d}:{match.group(2)}"
    return "n/a"


def encontrar_mejor_coincidencia(
    partido_casa: Partido, candidatos_fs: list[Partido], alias: dict[str, str]
) -> tuple[Partido | None, float]:
    mejor_partido: Partido | None = None
    mejor_puntuacion = -1.0
    for candidato in candidatos_fs:
        if not _categorias_compatibles(partido_casa, candidato):
            continue
        puntuacion = _puntuacion_partido(partido_casa, candidato, alias)
        if puntuacion > mejor_puntuacion:
            mejor_puntuacion = puntuacion
            mejor_partido = candidato
    return mejor_partido, mejor_puntuacion


def auditar(
    ruta_db: Path,
    casa_id: str,
    casa_nombre: str,
    deporte: str,
    partidos_casa: list[Partido],
    partidos_flashscore: list[Partido],
) -> tuple[list[Discrepancia], EstadisticasAuditoria]:
    """Compara los partidos de una casa contra Flashscore. Guarda en la
    base de datos, para revision offline (asistida por Ollama), los que
    no se pudieron emparejar con confianza suficiente."""
    alias = db.obtener_alias_aprobados(ruta_db)
    disponibles = list(partidos_flashscore)
    discrepancias: list[Discrepancia] = []
    verificados = 0

    for partido_casa in partidos_casa:
        if not partido_casa.equipo_local or not partido_casa.equipo_visitante:
            continue

        mejor_match, puntuacion = encontrar_mejor_coincidencia(partido_casa, disponibles, alias)

        if mejor_match and puntuacion >= UMBRAL_CONFIANZA_MINIMA:
            verificados += 1
            discrepancia = _detectar_discrepancia_horario(
                casa_id, casa_nombre, deporte, partido_casa, mejor_match, puntuacion
            )
            if discrepancia:
                discrepancias.append(discrepancia)
            disponibles.remove(mejor_match)
        else:
            db.encolar_no_encontrado(
                ruta_db,
                casa_id,
                deporte,
                partido_casa.equipo_local,
                partido_casa.equipo_visitante,
                partido_casa.liga,
                mejor_match.nombre() if mejor_match else None,
                puntuacion,
            )

    stats = EstadisticasAuditoria(
        total_casa=len(partidos_casa),
        verificados=verificados,
        cobertura=(verificados / len(partidos_casa) * 100) if partidos_casa else 0.0,
    )
    return discrepancias, stats


def _detectar_discrepancia_horario(
    casa_id: str,
    casa_nombre: str,
    deporte: str,
    partido_casa: Partido,
    partido_fs: Partido,
    puntuacion: float,
) -> Discrepancia | None:
    if partido_casa.estado != "Programado" or partido_fs.estado != "Programado":
        return None

    texto_casa = partido_casa.detalle_estado.lower()
    if any(palabra in texto_casa for palabra in ["comienza", "ahora"]):
        return None

    hora_casa = normalizar_hora(partido_casa.detalle_estado)
    hora_fs = normalizar_hora(partido_fs.detalle_estado)

    if hora_casa == "n/a" or hora_fs == "n/a" or hora_casa == hora_fs:
        return None

    prioridad = "alta" if hora_casa > hora_fs else "baja"

    return Discrepancia(
        casa_id=casa_id,
        casa_nombre=casa_nombre,
        deporte=deporte,
        liga=partido_casa.liga,
        liga_fs=partido_fs.liga,
        equipo_local_casa=partido_casa.equipo_local,
        equipo_visitante_casa=partido_casa.equipo_visitante,
        detalle_casa=partido_casa.detalle_estado,
        equipo_local_fs=partido_fs.equipo_local,
        equipo_visitante_fs=partido_fs.equipo_visitante,
        detalle_fs=partido_fs.detalle_estado,
        similitud=puntuacion,
        prioridad=prioridad,
    )
