"""Punto de entrada unico del bot.

Uso:
    python run.py            Arranca el proceso persistente (Telegram +
                              scheduler). Pensado para dejarlo corriendo
                              en el PC de pruebas, o como servicio en la
                              Raspberry Pi. Se controla todo por Telegram.

    python run.py --once     Ejecuta UN ciclo de scraping + auditoria de
                              inmediato y termina. Util para probar sin
                              esperar a la hora programada ni necesitar
                              el bot de Telegram configurado.

    python run.py --once --deportes futbol [otro_deporte ...]
                              Igual, pero limitado a los deportes
                              indicados en vez de todos los activos.
"""

from __future__ import annotations

import asyncio
import logging
import sys

from config.settings import cargar_settings
from core import db


def _configurar_logging(nivel: str) -> None:
    logging.basicConfig(
        level=getattr(logging, nivel.upper(), logging.INFO),
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )


def _run_once() -> None:
    from core.orquestador import ejecutar_ciclo

    settings = cargar_settings()
    db.inicializar_db(settings.db_path)

    # --deportes futbol baloncesto ... limita el ciclo a esos deportes,
    # igual que el --deportes del bot anterior. Sin esta bandera se
    # procesan TODOS los deportes activos, que puede tardar bastante.
    deportes_filtro: list[str] | None = None
    if "--deportes" in sys.argv:
        idx = sys.argv.index("--deportes")
        deportes_filtro = [a for a in sys.argv[idx + 1 :] if not a.startswith("--")] or None

    resultado = asyncio.run(
        ejecutar_ciclo(settings.db_path, deportes_filtro=deportes_filtro, headless=settings.headless)
    )

    print(f"\nCiclo completado en {resultado.duracion_segundos:.1f}s (host={resultado.host})")
    print(f"Partidos procesados: {resultado.total_partidos}")
    print(f"Discrepancias encontradas: {len(resultado.discrepancias)}")
    for d in resultado.discrepancias:
        print(
            f"  [{d.prioridad}] {d.casa_nombre} {d.deporte} ({d.liga}): "
            f"{d.equipo_local_casa} vs {d.equipo_visitante_casa} "
            f"-> casa={d.detalle_casa} fs={d.detalle_fs} (similitud {d.similitud:.0f}%)"
        )
    if resultado.errores:
        print("\nErrores durante el ciclo:")
        for e in resultado.errores:
            print(f"  - {e}")

    print("\nTiempos por etapa:")
    for t in sorted(resultado.tiempos, key=lambda x: -x.segundos):
        print(f"  {t.etiqueta}: {t.segundos:.1f}s")


def _run_bot() -> None:
    from telegram_bot.bot import construir_app

    settings = cargar_settings()
    if not settings.telegram_bot_token:
        print("ERROR: falta TELEGRAM_BOT_TOKEN en .env")
        sys.exit(1)
    if not settings.telegram_admin_id:
        print("ERROR: falta TELEGRAM_ADMIN_ID en .env (tu user_id numérico de Telegram, para la whitelist)")
        sys.exit(1)

    app = construir_app(settings)
    app.run_polling()


def main() -> None:
    settings = cargar_settings()
    _configurar_logging(settings.log_level)

    if "--once" in sys.argv:
        _run_once()
    else:
        _run_bot()


if __name__ == "__main__":
    main()
