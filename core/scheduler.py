"""Envoltorio fino sobre APScheduler para poder reprogramar la hora de
ejecucion diaria EN CALIENTE (ej. cuando se cambia por Telegram con
/hora) sin tener que reiniciar el proceso."""

from __future__ import annotations

from collections.abc import Awaitable, Callable

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger

ID_JOB_CICLO_DIARIO = "ciclo_diario"


class Programador:
    def __init__(self) -> None:
        self._scheduler = AsyncIOScheduler()

    def iniciar(self) -> None:
        if not self._scheduler.running:
            self._scheduler.start()

    def programar_ciclo_diario(self, funcion: Callable[[], Awaitable[None]], hora: int, minuto: int) -> None:
        """Anade el job diario, o lo REPROGRAMA si ya existia (no crea
        duplicados)."""
        trigger = CronTrigger(hour=hora, minute=minuto)
        if self._scheduler.get_job(ID_JOB_CICLO_DIARIO):
            self._scheduler.reschedule_job(ID_JOB_CICLO_DIARIO, trigger=trigger)
        else:
            self._scheduler.add_job(
                funcion,
                trigger,
                id=ID_JOB_CICLO_DIARIO,
                misfire_grace_time=3600,
            )

    def proxima_ejecucion(self):
        job = self._scheduler.get_job(ID_JOB_CICLO_DIARIO)
        return job.next_run_time if job else None
