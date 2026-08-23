import base64
import json

from core import db
from core.models import Discrepancia
from telegram_bot.miniapp import (
    LIMITE_ALIAS,
    LIMITE_REPORTES,
    LIMITE_TEXTO_REPORTE,
    URL_BASE_PANEL,
    aplicar_cambios,
    construir_url_panel,
)


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


def test_construir_url_panel_omite_ef_cuando_coincide_con_ec(tmp_path):
    """ "ef" (nombres de Flashscore) solo debe viajar en la URL cuando de
    verdad difiere de "ec" — es el caso normal tras el emparejamiento, y
    mandarlo siempre duplicaba bytes sin aportar nada. El panel usa "ec"
    como valor por defecto cuando "ef" no viene (ver docs/panel.html)."""
    ruta = tmp_path / "test.sqlite3"
    db.inicializar_db(ruta)

    db.guardar_discrepancia(
        ruta,
        Discrepancia(
            casa_id="bwin",
            casa_nombre="Bwin",
            deporte="futbol",
            liga="LaLiga",
            liga_fs="LaLiga EA Sports",
            equipo_local_casa="Real Madrid",
            equipo_visitante_casa="Barcelona",
            detalle_casa="21:00",
            equipo_local_fs="Real Madrid",
            equipo_visitante_fs="Barcelona",
            detalle_fs="19:00",
            similitud=100.0,
            prioridad="alta",
        ),
    )

    estado = json.loads(base64.urlsafe_b64decode(construir_url_panel(ruta).split("estado=", 1)[1]))
    assert "ef" not in estado["r"][0]


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

    resumen, nueva_hora, acciones, _siguiente_offset = aplicar_cambios(ruta, payload)

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

    resumen, _nueva_hora, _acciones, _siguiente_offset = aplicar_cambios(ruta, payload)

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
    resumen, _nueva_hora, acciones, _siguiente_offset = aplicar_cambios(ruta, payload)

    assert acciones == ["ejecutar_ciclo"]
    assert "ciclo manual lanzado" in resumen


def test_construir_url_panel_recorta_textos_largos(tmp_path):
    ruta = tmp_path / "test.sqlite3"
    db.inicializar_db(ruta)

    nombre_largo = "Real Club Deportivo Muy Larguísimo De Verdad"
    assert len(nombre_largo) > LIMITE_TEXTO_REPORTE

    discrepancia = Discrepancia(
        casa_id="bwin",
        casa_nombre="Bwin",
        deporte="futbol",
        liga=nombre_largo,
        equipo_local_casa=nombre_largo,
        equipo_visitante_casa="B",
        detalle_casa="21:00",
        equipo_local_fs="A",
        equipo_visitante_fs="B",
        detalle_fs="19:00",
        similitud=90.0,
        prioridad="alta",
    )
    db.guardar_discrepancia(ruta, discrepancia)

    estado = json.loads(base64.urlsafe_b64decode(construir_url_panel(ruta).split("estado=", 1)[1]))
    reporte = estado["r"][0]
    assert len(reporte["lc"]) <= LIMITE_TEXTO_REPORTE
    assert len(reporte["ec"][0]) <= LIMITE_TEXTO_REPORTE
    assert reporte["lc"].endswith("…")  # se ve que esta cortado, no parece el nombre completo


def test_construir_url_panel_marca_cuando_hay_mas_reportes_de_los_mostrados(tmp_path):
    ruta = tmp_path / "test.sqlite3"
    db.inicializar_db(ruta)

    def _discrepancia(i: int) -> Discrepancia:
        return Discrepancia(
            casa_id="bwin",
            casa_nombre="Bwin",
            deporte="futbol",
            liga="LaLiga",
            equipo_local_casa=f"Equipo {i}",
            equipo_visitante_casa="B",
            detalle_casa="21:00",
            equipo_local_fs="A",
            equipo_visitante_fs="B",
            detalle_fs="19:00",
            similitud=90.0,
            prioridad="alta",
        )

    for i in range(LIMITE_REPORTES - 1):
        db.guardar_discrepancia(ruta, _discrepancia(i))
    estado = json.loads(base64.urlsafe_b64decode(construir_url_panel(ruta).split("estado=", 1)[1]))
    assert estado["rm"] is False  # por debajo del limite: seguro que no falta ninguna

    # Al llegar exactamente al limite ya no se puede distinguir "hay
    # justo tantas" de "hay mas" sin una consulta aparte — se avisa por
    # el mismo motivo que el resto del proyecto prefiere falsos
    # positivos a esconder algo: es mejor un aviso de mas que de menos.
    db.guardar_discrepancia(ruta, _discrepancia(LIMITE_REPORTES - 1))
    estado = json.loads(base64.urlsafe_b64decode(construir_url_panel(ruta).split("estado=", 1)[1]))
    assert len(estado["r"]) == LIMITE_REPORTES  # la vista sigue acotada
    assert estado["rm"] is True


def test_construir_url_panel_respeta_limite_alias(tmp_path):
    ruta = tmp_path / "test.sqlite3"
    db.inicializar_db(ruta)

    for i in range(LIMITE_ALIAS + 5):
        db.guardar_alias(ruta, f"variante pendiente {i}", f"canonico {i}", fuente="ollama", aprobado=False)
        db.guardar_alias(ruta, f"variante aprobada {i}", f"canonico {i}", fuente="manual", aprobado=True)

    estado = json.loads(base64.urlsafe_b64decode(construir_url_panel(ruta).split("estado=", 1)[1]))
    assert len(estado["al"]["pend"]) == LIMITE_ALIAS
    assert len(estado["al"]["apr"]) == LIMITE_ALIAS


def test_url_panel_no_supera_un_tamano_seguro_ni_en_el_peor_caso(tmp_path):
    """Regresion directa del bug real: con LIMITE_REPORTES=20 y sin
    recortar texto, la URL llego a 9323 caracteres contra la base de
    datos real del proyecto y GitHub Pages la rechazo con
    "414 URI Too Long". Este test satura deliberadamente TODAS las
    listas (reportes, ejecuciones, alias) con textos largos para que
    una regresion futura (subir un limite, quitar un _recortar) no
    pueda colarse en silencio."""
    ruta = tmp_path / "test.sqlite3"
    db.inicializar_db(ruta)

    nombre_largo = "Real Club Deportivo Muy Larguísimo De Verdad " * 2
    # Nombre de Flashscore DISTINTO al de la casa a proposito: "ef" solo
    # se manda cuando difiere de "ec" (ver construir_url_panel), asi que
    # con el mismo texto en los dos el peor caso real quedaria
    # subestimado — esto fuerza a que "ef" SI viaje en la URL.
    nombre_largo_fs = "Nombre De Flashscore Completamente Distinto XYZ " * 2

    for _i in range(LIMITE_REPORTES + 5):
        db.guardar_discrepancia(
            ruta,
            Discrepancia(
                casa_id="bwin",
                casa_nombre=nombre_largo,
                deporte="futbol",
                liga=nombre_largo,
                liga_fs=nombre_largo,
                equipo_local_casa=nombre_largo,
                equipo_visitante_casa=nombre_largo,
                detalle_casa="21:00",
                equipo_local_fs=nombre_largo_fs,
                equipo_visitante_fs=nombre_largo_fs,
                detalle_fs="19:00",
                similitud=90.0,
                prioridad="alta",
            ),
        )
    for _i in range(10):
        eid = db.crear_ejecucion(ruta, host="RASPBERRYPI-MUY-LARGO" * 2, paralelismo=2)
        db.cerrar_ejecucion(
            ruta,
            eid,
            total_partidos=999,
            total_discrepancias=99,
            errores=["error"] * 5,
            duracion_segundos=999.9,
        )
    for i in range(LIMITE_ALIAS + 5):
        db.guardar_alias(
            ruta, f"{nombre_largo} {i}", f"{nombre_largo} canonico {i}", fuente="ollama", aprobado=False
        )
        db.guardar_alias(
            ruta,
            f"{nombre_largo} apr {i}",
            f"{nombre_largo} canonico apr {i}",
            fuente="manual",
            aprobado=True,
        )

    url = construir_url_panel(ruta, tab="reportes")
    # Margen bajo los 9323 que rompieron de verdad — ver el comentario
    # junto a LIMITE_REPORTES en telegram_bot/miniapp.py.
    #
    # OJO: el catalogo de casas (config/catalogo_casas.py) tambien se
    # serializa entero en la URL (seccion "c", independientemente de que
    # este activo o no — ver construir_url_panel), asi que este numero
    # sube un poco cada vez que se añade una casa o un deporte nuevo al
    # catalogo. Reajustado el 2026-08-23 (3ª vez, al añadir el "id" de
    # cada discrepancia para poder descartarla y subir LIMITE_REPORTES
    # de 9 a 10): peor caso forzado ~8612 caracteres. Si este assert
    # vuelve a saltar por seguir ampliando el catalogo (no por un
    # recorte real quitado), basta con volver a subir el umbral, no
    # bajar LIMITE_REPORTES/ALIAS.
    assert len(url) < 8900, f"URL de {len(url)} caracteres — demasiado cerca del limite real de GitHub Pages"


def test_aplicar_cambios_descarta_reportes(tmp_path):
    """Pedido directo del usuario: poder "eliminar" (descartar, sin
    perder el historial) las alertas ya revisadas, para llevar un
    orden."""
    ruta = tmp_path / "test.sqlite3"
    db.inicializar_db(ruta)

    db.guardar_discrepancia(
        ruta,
        Discrepancia(
            casa_id="bwin",
            casa_nombre="Bwin",
            deporte="futbol",
            liga="LaLiga",
            equipo_local_casa="A",
            equipo_visitante_casa="B",
            detalle_casa="21:00",
            equipo_local_fs="A",
            equipo_visitante_fs="B",
            detalle_fs="19:00",
            similitud=90.0,
            prioridad="alta",
        ),
    )
    id_ = db.listar_discrepancias_recientes(ruta)[0]["id"]

    payload = json.dumps({"reportes_descartados": [id_]})
    resumen, _nueva_hora, _acciones, _siguiente_offset = aplicar_cambios(ruta, payload)

    assert db.listar_discrepancias_recientes(ruta) == []
    assert "1 alerta" in resumen


def test_aplicar_cambios_sin_nada_no_rompe(tmp_path):
    ruta = tmp_path / "test.sqlite3"
    db.inicializar_db(ruta)

    resumen, nueva_hora, acciones, siguiente_offset = aplicar_cambios(ruta, "{}")

    assert resumen == "sin cambios"
    assert nueva_hora is None
    assert acciones == []
    assert siguiente_offset is None


def test_aplicar_cambios_ver_mas_reportes_devuelve_el_offset_pedido(tmp_path):
    """Pedido directo del usuario: quiere poder ver todo el historial
    de alertas, no solo las mas recientes — el boton "Ver más" del
    panel manda el offset de la siguiente tanda, que aqui solo se
    valida y se devuelve (mandar el mensaje nuevo con esa pagina es
    responsabilidad de telegram_bot/bot.py, no de esta funcion)."""
    ruta = tmp_path / "test.sqlite3"
    db.inicializar_db(ruta)

    payload = json.dumps({"reportes_offset_siguiente": 10})
    _resumen, _nueva_hora, _acciones, siguiente_offset = aplicar_cambios(ruta, payload)

    assert siguiente_offset == 10


def test_construir_url_panel_con_offset_reportes_salta_los_primeros(tmp_path):
    ruta = tmp_path / "test.sqlite3"
    db.inicializar_db(ruta)

    for i in range(3):
        db.guardar_discrepancia(
            ruta,
            Discrepancia(
                casa_id="bwin",
                casa_nombre="Bwin",
                deporte="futbol",
                liga="LaLiga",
                equipo_local_casa=f"Equipo {i}",
                equipo_visitante_casa="B",
                detalle_casa="21:00",
                equipo_local_fs="A",
                equipo_visitante_fs="B",
                detalle_fs="19:00",
                similitud=90.0,
                prioridad="alta",
            ),
        )

    estado = json.loads(
        base64.urlsafe_b64decode(construir_url_panel(ruta, offset_reportes=2).split("estado=", 1)[1])
    )
    assert len(estado["r"]) == 1  # solo queda 1 de las 3 tras saltarse las 2 primeras
    assert estado["ro"] == 2

    # El offset por defecto (0) no se manda — es el caso normal, no hace
    # falta pagar el byte de mas en cada URL.
    estado_normal = json.loads(base64.urlsafe_b64decode(construir_url_panel(ruta).split("estado=", 1)[1]))
    assert "ro" not in estado_normal
