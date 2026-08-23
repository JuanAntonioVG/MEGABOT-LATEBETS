from core import db
from core.models import Discrepancia, TiempoEtapa


def test_settings_roundtrip(tmp_path):
    ruta = tmp_path / "test.sqlite3"
    db.inicializar_db(ruta)

    assert db.get_setting(ruta, "hora_ejecucion") is None
    assert db.get_setting(ruta, "hora_ejecucion", "00:00") == "00:00"

    db.set_setting(ruta, "hora_ejecucion", "00:30")
    assert db.get_setting(ruta, "hora_ejecucion") == "00:30"

    db.set_setting(ruta, "paralelismo", "5")
    assert db.get_setting_int(ruta, "paralelismo", 3) == 5

    db.set_setting(ruta, "verboso", "1")
    assert db.get_setting_bool(ruta, "verboso", False) is True


def test_toggles_por_defecto_activo_hasta_que_se_apague(tmp_path):
    ruta = tmp_path / "test.sqlite3"
    db.inicializar_db(ruta)

    # Sin tocar nada, una combinacion nueva se considera activa.
    assert db.esta_activo(ruta, "bwin", "futbol") is True

    db.set_toggle(ruta, "bwin", "futbol", False)
    assert db.esta_activo(ruta, "bwin", "futbol") is False

    db.set_toggle(ruta, "bwin", "futbol", True)
    assert db.esta_activo(ruta, "bwin", "futbol") is True

    toggles = db.listar_toggles(ruta)
    assert toggles[("bwin", "futbol")] is True


def test_alias_aprobar_y_rechazar(tmp_path):
    ruta = tmp_path / "test.sqlite3"
    db.inicializar_db(ruta)

    db.guardar_alias(ruta, "barça", "fc barcelona", fuente="ollama", aprobado=False)
    pendientes = db.listar_alias_pendientes(ruta)
    assert len(pendientes) == 1
    assert db.obtener_alias_aprobados(ruta) == {}

    db.aprobar_alias(ruta, pendientes[0]["id"])
    assert db.obtener_alias_aprobados(ruta) == {"barça": "fc barcelona"}

    db.guardar_alias(ruta, "otro", "otro canonico", fuente="ollama", aprobado=False)
    pendiente_2 = db.listar_alias_pendientes(ruta)[0]
    db.rechazar_alias(ruta, pendiente_2["id"])
    assert db.obtener_alias_aprobados(ruta) == {"barça": "fc barcelona"}
    assert db.listar_alias_pendientes(ruta) == []


def test_alias_listar_pendientes_y_aprobados_respetan_limite(tmp_path):
    ruta = tmp_path / "test.sqlite3"
    db.inicializar_db(ruta)

    for i in range(5):
        db.guardar_alias(ruta, f"variante {i}", f"canonico {i}", fuente="ollama", aprobado=False)
        db.guardar_alias(ruta, f"variante apr {i}", f"canonico apr {i}", fuente="manual", aprobado=True)

    assert len(db.listar_alias_pendientes(ruta)) == 5  # limite por defecto (500) no molesta con pocos datos
    assert len(db.listar_alias_pendientes(ruta, limite=2)) == 2
    assert len(db.listar_alias_aprobados(ruta, limite=3)) == 3


def test_alias_listar_aprobados_y_eliminar(tmp_path):
    ruta = tmp_path / "test.sqlite3"
    db.inicializar_db(ruta)

    db.guardar_alias(ruta, "barça", "fc barcelona", fuente="ollama", aprobado=True)
    db.guardar_alias(ruta, "atleti", "atletico de madrid", fuente="manual", aprobado=True)

    aprobados = db.listar_alias_aprobados(ruta)
    assert len(aprobados) == 2
    assert {f["fuente"] for f in aprobados} == {"ollama", "manual"}

    id_a_borrar = next(f["id"] for f in aprobados if f["variante"] == "barça")
    db.eliminar_alias(ruta, id_a_borrar)

    restantes = db.listar_alias_aprobados(ruta)
    assert len(restantes) == 1
    assert restantes[0]["variante"] == "atleti"


def test_cola_no_encontrados(tmp_path):
    ruta = tmp_path / "test.sqlite3"
    db.inicializar_db(ruta)

    db.encolar_no_encontrado(ruta, "bwin", "futbol", "Equipo A", "Equipo B", "Liga X", "Candidato", 42.0)
    pendientes = db.listar_no_encontrados(ruta, resuelto=False)
    assert len(pendientes) == 1

    db.marcar_resuelto(ruta, pendientes[0]["id"])
    assert db.listar_no_encontrados(ruta, resuelto=False) == []
    assert len(db.listar_no_encontrados(ruta, resuelto=True)) == 1


def test_ejecuciones_y_tiempos(tmp_path):
    ruta = tmp_path / "test.sqlite3"
    db.inicializar_db(ruta)

    ejecucion_id = db.crear_ejecucion(ruta, host="PC-JUAN", paralelismo=3)
    db.guardar_tiempo_etapa(ruta, ejecucion_id, TiempoEtapa("bwin/futbol", 4.2))
    db.guardar_tiempo_etapa(ruta, ejecucion_id, TiempoEtapa("winamax/futbol", 7.8))

    discrepancia = Discrepancia(
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
    )
    db.guardar_discrepancia(ruta, discrepancia, ejecucion_id)

    db.cerrar_ejecucion(
        ruta, ejecucion_id, total_partidos=50, total_discrepancias=1, errores=[], duracion_segundos=12.0
    )

    ejecuciones = db.listar_ejecuciones_recientes(ruta)
    assert len(ejecuciones) == 1
    assert ejecuciones[0]["host"] == "PC-JUAN"
    assert ejecuciones[0]["total_discrepancias"] == 1

    tiempos = db.listar_tiempos_de_ejecucion(ruta, ejecucion_id)
    assert {t["etiqueta"] for t in tiempos} == {"bwin/futbol", "winamax/futbol"}

    discrepancias = db.listar_discrepancias_recientes(ruta)
    assert len(discrepancias) == 1
    assert discrepancias[0]["casa_nombre"] == "Bwin"


def _discrepancia(prioridad: str, equipo_local: str = "A") -> Discrepancia:
    return Discrepancia(
        casa_id="bwin",
        casa_nombre="Bwin",
        deporte="futbol",
        liga="LaLiga",
        equipo_local_casa=equipo_local,
        equipo_visitante_casa="B",
        detalle_casa="21:00",
        equipo_local_fs=equipo_local,
        equipo_visitante_fs="B",
        detalle_fs="19:00",
        similitud=90.0,
        prioridad=prioridad,
    )


def test_listar_discrepancias_pone_alta_prioridad_primero(tmp_path):
    """Pedido directo del usuario: quiere ver antes lo urgente. Se
    guarda a proposito la de baja prioridad ANTES (mas antigua), para
    comprobar que el orden es de verdad por prioridad y no solo por
    fecha."""
    ruta = tmp_path / "test.sqlite3"
    db.inicializar_db(ruta)
    db.guardar_discrepancia(ruta, _discrepancia("baja", equipo_local="Baja"))
    db.guardar_discrepancia(ruta, _discrepancia("alta", equipo_local="Alta"))

    discrepancias = db.listar_discrepancias_recientes(ruta)
    assert [d["equipo_local_casa"] for d in discrepancias] == ["Alta", "Baja"]


def test_descartar_discrepancia_la_oculta_de_la_lista(tmp_path):
    """Pedido directo del usuario: poder "eliminar" (descartar, sin
    perder el historial en la base de datos) las alertas que ya no le
    sirven, para llevar un orden."""
    ruta = tmp_path / "test.sqlite3"
    db.inicializar_db(ruta)
    db.guardar_discrepancia(ruta, _discrepancia("alta"))
    id_ = db.listar_discrepancias_recientes(ruta)[0]["id"]

    db.descartar_discrepancia(ruta, id_)

    assert db.listar_discrepancias_recientes(ruta) == []
