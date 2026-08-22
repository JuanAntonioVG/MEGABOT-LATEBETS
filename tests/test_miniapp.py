import base64
import json

from core import db
from telegram_bot.miniapp import URL_BASE_PANEL, aplicar_cambios, construir_url_panel


def test_construir_url_panel_incluye_estado_valido(tmp_path):
    ruta = tmp_path / "test.sqlite3"
    db.inicializar_db(ruta)
    db.set_toggle(ruta, "bwin", "futbol", False)
    db.set_setting(ruta, "paralelismo", "3")
    db.set_setting(ruta, "hora_ejecucion", "01:15")
    db.set_setting(ruta, "verboso", "1")

    url = construir_url_panel(ruta)
    assert url.startswith(URL_BASE_PANEL + "?estado=")

    payload_b64 = url.split("estado=", 1)[1]
    estado = json.loads(base64.urlsafe_b64decode(payload_b64))

    assert estado["p"] == 3
    assert estado["h"] == "01:15"
    assert estado["v"] is True

    casa_bwin = next(c for c in estado["c"] if c["id"] == "bwin")
    deporte_futbol = next(d for d in casa_bwin["d"] if d["id"] == "futbol")
    assert deporte_futbol["a"] is False  # lo desactivamos arriba

    # Una casa/deporte que nunca se toco sigue activa por defecto.
    deporte_baloncesto = next(d for d in casa_bwin["d"] if d["id"] == "baloncesto")
    assert deporte_baloncesto["a"] is True


def test_aplicar_cambios_toggles_y_ajustes(tmp_path):
    ruta = tmp_path / "test.sqlite3"
    db.inicializar_db(ruta)

    payload = json.dumps(
        {
            "toggles": [
                {"c": "bwin", "d": "futbol", "a": False},
                {"c": "winamax", "d": "tenis", "a": True},
            ],
            "paralelismo": 4,
            "hora": "02:30",
            "verboso": True,
        }
    )

    resumen, nueva_hora = aplicar_cambios(ruta, payload)

    assert db.esta_activo(ruta, "bwin", "futbol") is False
    assert db.esta_activo(ruta, "winamax", "tenis") is True
    assert db.get_setting_int(ruta, "paralelismo", 2) == 4
    assert db.get_setting(ruta, "hora_ejecucion") == "02:30"
    assert db.get_setting_bool(ruta, "verboso", False) is True
    assert nueva_hora == "02:30"
    assert "2 casa" in resumen


def test_aplicar_cambios_sin_nada_no_rompe(tmp_path):
    ruta = tmp_path / "test.sqlite3"
    db.inicializar_db(ruta)

    resumen, nueva_hora = aplicar_cambios(ruta, "{}")

    assert resumen == "sin cambios"
    assert nueva_hora is None
