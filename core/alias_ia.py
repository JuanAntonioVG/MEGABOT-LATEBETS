"""Integracion con Ollama (IA local) para asistir en el crecimiento del
diccionario de alias de equipos.

Uso pensado: PERIODICO Y MANUAL, no dentro del ciclo automatico de las
00:00. Revisa la cola de partidos que el matcher no pudo emparejar con
confianza (tabla `partidos_no_encontrados`), le pregunta a Ollama si el
candidato mas parecido de Flashscore es en realidad el mismo partido con
los equipos escritos de otra forma, y si es asi guarda los alias
propuestos como PENDIENTES DE APROBACION (`aprobado=0`). Nunca se usan en
el matcher automaticamente — hace falta aprobarlos (por Telegram, cuando
este montado ese panel, o directamente con `core.db.aprobar_alias`).

Por que offline y no en el ciclo automatico de scraping: una llamada a un
LLM tarda ordenes de magnitud mas que una comparacion fuzzy. No tiene
sentido pagar ese coste en la Raspberry Pi a las 00:00 para casos que se
pueden resolver una vez y recordar para siempre.
"""

from __future__ import annotations

import json
from pathlib import Path

import requests

from core import db

PROMPT_SISTEMA = (
    "Eres un asistente que compara partidos deportivos provenientes de dos "
    "fuentes distintas, escritos con posible variacion en el nombre de los "
    "equipos (apodos, abreviaturas, sponsors, idioma, con o sin sufijos de "
    "club). Se te da un partido de una casa de apuestas y el candidato mas "
    "parecido encontrado en Flashscore. Decide si son el MISMO partido "
    "(los mismos dos equipos, aunque esten escritos de forma distinta). "
    "Responde SOLO con JSON, sin texto adicional, con esta forma exacta: "
    '{"mismo_partido": true, "alias": [{"variante": "...", "canonico": "..."}]} '
    "El campo 'alias' debe contener, solo si mismo_partido es true, una "
    "entrada por cada nombre de equipo de la casa que no coincida "
    "textualmente con el de Flashscore (variante = como lo escribe la "
    "casa, en minusculas; canonico = como lo escribe Flashscore, en "
    "minusculas). Si no coinciden ningun par de nombres, 'alias' es una "
    "lista vacia. Si no estas razonablemente seguro de que sea el mismo "
    "partido, responde mismo_partido:false y alias: []."
)

PUNTUACION_MINIMA_PARA_PREGUNTAR = 35


def _preguntar_ollama(
    host: str, modelo: str, partido_casa_texto: str, partido_fs_texto: str, timeout: int = 30
) -> dict | None:
    prompt = (
        f'Partido (casa de apuestas): "{partido_casa_texto}"\n'
        f'Partido (Flashscore, candidato mas parecido): "{partido_fs_texto}"\n'
        "¿Es el mismo partido?"
    )
    try:
        respuesta = requests.post(
            f"{host}/api/generate",
            json={
                "model": modelo,
                "system": PROMPT_SISTEMA,
                "prompt": prompt,
                "stream": False,
                "format": "json",
                "options": {"temperature": 0},
            },
            timeout=timeout,
        )
        respuesta.raise_for_status()
        contenido = respuesta.json().get("response", "")
        return json.loads(contenido)
    except Exception as e:
        print(f"[alias_ia] Error consultando Ollama: {e}")
        return None


def revisar_cola_pendientes(ruta_db: Path, host: str, modelo: str, limite: int = 30) -> tuple[int, int]:
    """Recorre la cola de partidos no emparejados y consulta a Ollama los
    casos con puntuacion suficiente para merecer la pena preguntar.
    Devuelve (revisados, propuestas_de_alias_creadas)."""
    pendientes = db.listar_no_encontrados(ruta_db, resuelto=False)[:limite]
    revisados = 0
    propuestas = 0

    for fila in pendientes:
        candidato = fila["mejor_candidato"]
        puntuacion = fila["puntuacion"] or 0

        if not candidato or puntuacion < PUNTUACION_MINIMA_PARA_PREGUNTAR:
            db.marcar_resuelto(ruta_db, fila["id"])
            continue

        revisados += 1
        texto_casa = f"{fila['equipo_local']} vs {fila['equipo_visitante']}"
        resultado = _preguntar_ollama(host, modelo, texto_casa, candidato)

        if resultado and resultado.get("mismo_partido"):
            for par in resultado.get("alias", []):
                variante = str(par.get("variante", "")).strip()
                canonico = str(par.get("canonico", "")).strip()
                if variante and canonico and variante != canonico:
                    db.guardar_alias(ruta_db, variante, canonico, fuente="ollama", aprobado=False)
                    propuestas += 1

        db.marcar_resuelto(ruta_db, fila["id"])

    return revisados, propuestas
