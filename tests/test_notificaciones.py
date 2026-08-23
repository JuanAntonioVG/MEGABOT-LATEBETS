from datetime import datetime

from core.models import Discrepancia, ResultadoEjecucion, TiempoEtapa
from telegram_bot.notificaciones import formatear_resumen, formatear_resumen_discrepancias


def _discrepancia(prioridad: str = "alta") -> Discrepancia:
    return Discrepancia(
        casa_id="bwin",
        casa_nombre="Bwin",
        deporte="futbol",
        liga="Liga Casa",
        equipo_local_casa="Equipo Local",
        equipo_visitante_casa="Equipo Visitante",
        detalle_casa="21:00",
        equipo_local_fs="Equipo Local FS",
        equipo_visitante_fs="Equipo Visitante FS",
        detalle_fs="19:00",
        similitud=90.0,
        prioridad=prioridad,
    )


def test_sin_discrepancias_no_se_llama_en_la_practica():
    # formatear_resumen_discrepancias no se usa con lista vacia (enviar_resultado
    # ya comprueba `if resultado.discrepancias` antes de llamarla), pero no debe
    # reventar si algun dia se le pasa una.
    texto = formatear_resumen_discrepancias([])
    assert "0" in texto


def test_singular_cuando_hay_una_sola():
    texto = formatear_resumen_discrepancias([_discrepancia("alta")])
    assert "1 oportunidad detectada" in texto
    assert "Todas de alta prioridad" in texto


def test_desglose_por_prioridad():
    discrepancias = [_discrepancia("alta"), _discrepancia("alta"), _discrepancia("baja")]
    texto = formatear_resumen_discrepancias(discrepancias)
    assert "3 oportunidades detectadas" in texto
    assert "2 de alta prioridad" in texto
    assert "1 de baja prioridad" in texto


def test_todas_baja_prioridad():
    texto = formatear_resumen_discrepancias([_discrepancia("baja"), _discrepancia("baja")])
    assert "Todas de baja prioridad" in texto
    assert "alta prioridad" not in texto.split("Todas")[0]


def test_no_expone_detalle_de_equipos_ni_horas():
    # A proposito: el detalle vive en el panel, no en este mensaje corto.
    texto = formatear_resumen_discrepancias([_discrepancia("alta")])
    assert "Equipo Local" not in texto
    assert "21:00" not in texto


def _resultado_con_tiempos(tiempos: list[TiempoEtapa]) -> ResultadoEjecucion:
    r = ResultadoEjecucion(inicio=datetime.now(), host="PC-TEST", paralelismo=2)
    r.fin = datetime.now()
    r.tiempos = tiempos
    r.total_partidos = sum(t.partidos for t in tiempos)
    return r


def test_resumen_muestra_tasa_de_verificacion_por_casa():
    """Pedido directo: recuperar lo que mostraba el bot anterior — de
    los partidos de una casa, cuantos se pudieron verificar de verdad
    contra Flashscore."""
    resultado = _resultado_con_tiempos(
        [
            TiempoEtapa("flashscore/futbol", 10.0, 462),
            TiempoEtapa("winamax/futbol", 20.0, 200, verificados=180, cobertura=90.0),
        ]
    )
    texto = formatear_resumen(resultado, verboso=False)
    assert "200" in texto
    assert "180 verificados (90%)" in texto


def test_resumen_flashscore_no_muestra_verificados():
    resultado = _resultado_con_tiempos([TiempoEtapa("flashscore/futbol", 10.0, 462)])
    texto = formatear_resumen(resultado, verboso=False)
    assert "verificados" not in texto


def test_resumen_casa_sin_partidos_no_muestra_0_verificados():
    resultado = _resultado_con_tiempos([TiempoEtapa("winamax/futbol", 5.0, 0)])
    texto = formatear_resumen(resultado, verboso=False)
    assert "verificados" not in texto
