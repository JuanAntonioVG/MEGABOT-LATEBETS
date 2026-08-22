import base64
import json

from core import db
from core.models import Discrepancia
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
    assert estado["pp"] is False  # por defecto, la programación no está pausada

    casa_bwin = next(c for c in estado["c"] if c["id"] == "bwin")
    deporte_futbol = next(d for d in casa_bwin["d"] if d["id"] == "futbol")
    assert deporte_futbol["a"] is False  # lo desactivamos arriba

    # Una casa/deporte que nunca se toco sigue activa por defecto.
    deporte_baloncesto = next(d for d in casa_bwin["d"] if d["id"] == "baloncesto")
    assert deporte_baloncesto["a"] is True

    # Alias, reportes y ejecuciones van siempre presentes, aunque vacios.
    assert estado["al"] == {"pend": [], "apr": []}
    assert estado["r"] == []
    assert estado["ej"] == []


def test_construir_url_panel_con_tab_no_por_defecto_lo_anade_a_la_url(tmp_path):
    ruta = tmp_path / "test.sqlite3"
    db.inicializar_db(ruta)

    assert "&tab=" not in construir_url_panel(ruta)  # "casas" es la pestaña por defecto
    assert construir_url_panel(ruta, tab="reportes").endswith("&tab=reportes")


def test_construir_url_panel_incluye_alias_y_reportes(tmp_path):
    ruta = tmp_path / "test.sqlite3"
    db.inicializar_db(ruta)

    db.guardar_alias(ruta, "barça", "fc barcelona", fuente="ollama", aprobado=False)
    db.guardar_alias(ruta, "atleti", "atletico de madrid", fuente="manual", aprobado=True)

    discrepancia = Discrepancia(
        casa_id="bwin",
        casa_nombre="Bwin",
        deporte="futbol",
        liga="LaLiga",
        liga_fs="LaLiga EA Sports",
        equipo_local_casa="Real Madrid",
        equipo_visitante_casa="Barcelona",
        detalle_casa="21:00",
        equipo_local_fs="Real Madrid",
        equipo_visitante_fs="FC Barcelona",
        detalle_fs="21:30",
        similitud=88.0,
        prioridad="alta",
    )
    db.guardar_discrepancia(ruta, discrepancia)
    ejecucion_id = db.crear_ejecucion(ruta, host="PC-JUAN", paralelismo=2)
    db.cerrar_ejecucion(
        ruta, ejecucion_id, total_partidos=100, total_discrepancias=1, errores=[], duracion_segundos=42.0
    )

    estado = json.loads(base64.urlsafe_b64decode(construir_url_panel(ruta).split("estado=", 1)[1]))

    assert len(estado["al"]["pend"]) == 1
    assert estado["al"]["pend"][0]["va"] == "barça"
    assert len(estado["al"]["apr"]) == 1
    assert estado["al"]["apr"][0]["fu"] == "manual"

    assert len(estado["r"]) == 1
    assert estado["r"][0]["cs"] == "Bwin"
    assert estado["r"][0]["ec"] == ["Real Madrid", "Barcelona"]
    assert estado["r"][0]["ef"] == ["Real Madrid", "FC Barcelona"]

    assert len(estado["ej"]) == 1
    assert estado["ej"][0]["h"] == "PC-JUAN"
    assert estado["ej"][0]["pt"] == 100
    assert estado["ej"][0]["er"] == 0


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
            "programacion_pausada": True,
        }
    )

    resumen, nueva_hora, acciones = aplicar_cambios(ruta, payload)

    assert db.esta_activo(ruta, "bwin", "futbol") is False
    assert db.esta_activo(ruta, "winamax", "tenis") is True
    assert db.get_setting_int(ruta, "paralelismo", 2) == 4
    assert db.get_setting(ruta, "hora_ejecucion") == "02:30"
    assert db.get_setting_bool(ruta, "verboso", False) is True
    assert db.get_setting_bool(ruta, "programacion_pausada", False) is True
    assert nueva_hora == "02:30"
    assert acciones == []
    assert "2 casa" in resumen
    assert "pausada" in resumen


def test_aplicar_cambios_alias_decisiones_y_nuevos(tmp_path):
    ruta = tmp_path / "test.sqlite3"
    db.inicializar_db(ruta)

    db.guardar_alias(ruta, "barça", "fc barcelona", fuente="ollama", aprobado=False)
    pendiente_id = db.listar_alias_pendientes(ruta)[0]["id"]
    db.guardar_alias(ruta, "atleti", "atletico de madrid", fuente="manual", aprobado=True)
    aprobado_id = db.listar_alias_aprobados(ruta)[0]["id"]

    payload = json.dumps(
        {
            "alias_decisiones": [
                {"id": pendiente_id, "accion": "aprobar"},
                {"id": aprobado_id, "accion": "rechazar"},
            ],
            "alias_nuevos": [{"variante": "el sub 21", "canonico": "españa sub-21"}],
        }
    )

    resumen, _nueva_hora, _acciones = aplicar_cambios(ruta, payload)

    aprobados = db.obtener_alias_aprobados(ruta)
    assert aprobados["barça"] == "fc barcelona"  # la propuesta pendiente quedo aprobada
    assert "atleti" not in aprobados  # el aprobado se elimino
    assert aprobados["el sub 21"] == "españa sub-21"  # el nuevo se guardo ya aprobado
    assert "1 alias aprobado" in resumen
    assert "1 alias eliminado" in resumen
    assert "1 alias nuevo" in resumen


def test_aplicar_cambios_acciones_validas_se_devuelven_y_las_invalidas_no(tmp_path):
    ruta = tmp_path / "test.sqlite3"
    db.inicializar_db(ruta)

    payload = json.dumps({"acciones": ["ejecutar_ciclo", "borrar_todo"]})
    resumen, _nueva_hora, acciones = aplicar_cambios(ruta, payload)

    assert acciones == ["ejecutar_ciclo"]
    assert "ciclo manual lanzado" in resumen


def test_aplicar_cambios_sin_nada_no_rompe(tmp_path):
    ruta = tmp_path / "test.sqlite3"
    db.inicializar_db(ruta)

    resumen, nueva_hora, acciones = aplicar_cambios(ruta, "{}")

    assert resumen == "sin cambios"
    assert nueva_hora is None
    assert acciones == []
