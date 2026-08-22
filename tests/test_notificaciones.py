from core.models import Discrepancia
from telegram_bot.notificaciones import LIMITE_CARACTERES_MENSAJE, formatear_mensajes_discrepancias


def _discrepancia(indice: int) -> Discrepancia:
    return Discrepancia(
        casa_id="bwin",
        casa_nombre="Bwin",
        deporte="futbol",
        liga=f"Liga Casa {indice}",
        liga_fs=f"Liga Flashscore {indice}",
        equipo_local_casa=f"Equipo Local {indice}",
        equipo_visitante_casa=f"Equipo Visitante {indice}",
        detalle_casa="21:00",
        equipo_local_fs=f"Equipo Local FS {indice}",
        equipo_visitante_fs=f"Equipo Visitante FS {indice}",
        detalle_fs="19:00",
        similitud=90.0,
        prioridad="alta",
    )


def test_sin_discrepancias_no_genera_mensajes():
    assert formatear_mensajes_discrepancias([]) == []


def test_pocas_discrepancias_caben_en_un_mensaje():
    mensajes = formatear_mensajes_discrepancias([_discrepancia(1), _discrepancia(2)])
    assert len(mensajes) == 1
    assert "#1" in mensajes[0]
    assert "#2" in mensajes[0]


def test_muchas_discrepancias_se_reparten_en_varios_mensajes_dentro_del_limite():
    discrepancias = [_discrepancia(i) for i in range(1, 60)]
    mensajes = formatear_mensajes_discrepancias(discrepancias)
    assert len(mensajes) > 1
    for mensaje in mensajes:
        assert len(mensaje) <= LIMITE_CARACTERES_MENSAJE

    # Ninguna discrepancia se pierde ni se duplica en el reparto.
    texto_completo = "\n".join(mensajes)
    for i in range(1, 60):
        assert f">#{i}<" in texto_completo


def test_orden_por_similitud_descendente():
    baja = _discrepancia(1)
    baja.similitud = 70.0
    alta = _discrepancia(2)
    alta.similitud = 95.0
    mensajes = formatear_mensajes_discrepancias([baja, alta])
    # La de mayor similitud (95%, indice original 2) debe listarse como #1.
    assert mensajes[0].index("#1") < mensajes[0].index("#2")
    assert "95%" in mensajes[0].split("#1")[1].split("#2")[0]
