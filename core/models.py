"""Modelos de datos tipados usados en todo el proyecto.

Se usan dataclasses estandar de Python (no pydantic) a proposito: son
mas ligeras de instalar en la Raspberry Pi (sin depender del nucleo en
Rust de pydantic-core) y aqui no necesitamos validacion compleja, solo
estructura clara y con tipos.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum


class EstadoPartido(StrEnum):
    PROGRAMADO = "Programado"
    EN_VIVO = "En Vivo"
    FINALIZADO = "Finalizado"
    DESCONOCIDO = "Desconocido"


@dataclass(slots=True)
class Partido:
    """Un partido tal y como lo extrae un scraper, desde UNA unica fuente
    (una casa de apuestas concreta, o Flashscore)."""

    equipo_local: str
    equipo_visitante: str
    estado: EstadoPartido
    detalle_estado: str
    liga: str = "Desconocida"
    fuente: str = ""  # id de la casa, o "flashscore"
    deporte: str = ""

    def nombre(self) -> str:
        return f"{self.equipo_local} vs {self.equipo_visitante}"


@dataclass(slots=True)
class Discrepancia:
    """Una discrepancia de horario detectada entre una casa y Flashscore."""

    casa_id: str
    casa_nombre: str
    deporte: str
    liga: str  # liga tal y como la escribe la CASA
    equipo_local_casa: str
    equipo_visitante_casa: str
    detalle_casa: str
    equipo_local_fs: str
    equipo_visitante_fs: str
    detalle_fs: str
    similitud: float
    prioridad: str  # "alta" | "baja"
    liga_fs: str = "Desconocida"  # liga tal y como la escribe FLASHSCORE


@dataclass(slots=True)
class EstadisticasAuditoria:
    total_casa: int
    verificados: int
    cobertura: float


@dataclass(slots=True)
class TiempoEtapa:
    """Cuanto tardo una etapa concreta (una casa+deporte), cuantos
    partidos trajo, y cuantos de esos se pudieron verificar de verdad
    contra Flashscore. Es la base de la telemetria que permite comparar
    velocidad PC vs Pi, ver si alguna casa trae menos partidos de lo
    esperado, Y ver la tasa de acierto del emparejamiento (lo que el
    bot anterior mostraba como "cobertura de auditoría") — `verificados`
    y `cobertura` se quedan a 0 para la etapa de Flashscore, que no se
    audita contra si misma."""

    etiqueta: str
    segundos: float
    partidos: int = 0
    verificados: int = 0
    cobertura: float = 0.0


@dataclass(slots=True)
class ResultadoEjecucion:
    inicio: datetime
    fin: datetime | None = None
    host: str = ""
    paralelismo: int = 1
    discrepancias: list[Discrepancia] = field(default_factory=list)
    tiempos: list[TiempoEtapa] = field(default_factory=list)
    total_partidos: int = 0
    errores: list[str] = field(default_factory=list)

    @property
    def duracion_segundos(self) -> float:
        if not self.fin:
            return 0.0
        return (self.fin - self.inicio).total_seconds()
