"""Puente entre el bot y la Mini App de Telegram (panel web que se abre
DENTRO de Telegram, alojado como pagina estatica en GitHub Pages —
ver docs/panel.html).

La pagina es estatica y no tiene forma de leer la base de datos por su
cuenta, asi que el estado actual (toggles, paralelismo, hora, verboso)
se le manda EMBEBIDO en la propia URL cada vez que se abre. Cuando el
usuario pulsa "Guardar cambios" dentro de la Mini App, Telegram entrega
esos cambios al bot como un mensaje especial (`web_app_data`), que se
decodifica y aplica aqui.
"""

from __future__ import annotations

import base64
import json
from pathlib import Path

from config.catalogo_casas import CATALOGO_CASAS
from core import db
from telegram_bot.notificaciones import EMOJIS_DEPORTE

# GitHub Pages del repo — ver README para como activarlo (Settings > Pages).
URL_BASE_PANEL = "https://juanantoniovg.github.io/MEGABOT-LATEBETS/panel.html"


def construir_url_panel(ruta_db: Path) -> str:
    """URL de la Mini App con el estado actual embebido en base64."""
    casas = []
    for casa in CATALOGO_CASAS.values():
        deportes = [
            {
                "id": deporte,
                "n": deporte.capitalize(),
                "e": EMOJIS_DEPORTE.get(deporte, "❓"),
                "a": db.esta_activo(ruta_db, casa.id, deporte),
            }
            for deporte in casa.deportes
        ]
        casas.append({"id": casa.id, "n": casa.nombre_legible, "d": deportes})

    estado = {
        "c": casas,
        "p": db.get_setting_int(ruta_db, "paralelismo", 2),
        "h": db.get_setting(ruta_db, "hora_ejecucion", "00:00"),
        "v": db.get_setting_bool(ruta_db, "verboso", False),
    }
    crudo = json.dumps(estado, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    payload = base64.urlsafe_b64encode(crudo).decode("ascii")
    return f"{URL_BASE_PANEL}?estado={payload}"


def aplicar_cambios(ruta_db: Path, datos_json: str) -> tuple[str, str | None]:
    """Aplica a la base de datos los cambios mandados desde la Mini App.

    Devuelve (resumen_legible, nueva_hora_o_None) — nueva_hora solo
    viene rellena si el usuario cambio la hora de ejecucion, para que
    quien llame pueda reprogramar el scheduler (esta funcion no conoce
    el Programador, eso es responsabilidad de telegram_bot/bot.py).
    """
    cambios = json.loads(datos_json)
    partes: list[str] = []
    nueva_hora: str | None = None

    toggles = cambios.get("toggles", [])
    for t in toggles:
        db.set_toggle(ruta_db, t["c"], t["d"], bool(t["a"]))
    if toggles:
        partes.append(f"{len(toggles)} casa(s)/deporte(s) actualizados")

    if "paralelismo" in cambios:
        db.set_setting(ruta_db, "paralelismo", str(int(cambios["paralelismo"])))
        partes.append(f"paralelismo → {cambios['paralelismo']}")

    if "hora" in cambios and cambios["hora"]:
        nueva_hora = str(cambios["hora"])
        db.set_setting(ruta_db, "hora_ejecucion", nueva_hora)
        partes.append(f"hora → {nueva_hora}")

    if "verboso" in cambios:
        db.set_setting(ruta_db, "verboso", "1" if cambios["verboso"] else "0")
        partes.append(f"verbose → {'on' if cambios['verboso'] else 'off'}")

    resumen = ", ".join(partes) if partes else "sin cambios"
    return resumen, nueva_hora
