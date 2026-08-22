from core.matcher import (
    UMBRAL_CONFIANZA_MINIMA,
    _detectar_discrepancia_horario,
    detectar_categoria,
    encontrar_mejor_coincidencia,
    normalizar_hora,
    normalizar_nombre,
)
from core.models import EstadisticasAuditoria, EstadoPartido, Partido


def _partido(
    local, visitante, estado=EstadoPartido.PROGRAMADO, detalle="20:00", liga="Liga Test", fuente="test"
):
    return Partido(
        equipo_local=local,
        equipo_visitante=visitante,
        estado=estado,
        detalle_estado=detalle,
        liga=liga,
        fuente=fuente,
        deporte="futbol",
    )


def _auditar_sin_db(casa, flashscore):
    """Replica `core.matcher.auditar` sin tocar la base de datos, para
    tests unitarios puros (la cola de no-encontrados en SQLite se prueba
    aparte, con una base de datos real, en test_db.py)."""
    disponibles = list(flashscore)
    discrepancias = []
    verificados = 0
    for partido_casa in casa:
        mejor, puntuacion = encontrar_mejor_coincidencia(partido_casa, disponibles, {})
        if mejor and puntuacion >= UMBRAL_CONFIANZA_MINIMA:
            verificados += 1
            d = _detectar_discrepancia_horario("bwin", "Bwin", "futbol", partido_casa, mejor, puntuacion)
            if d:
                discrepancias.append(d)
            disponibles.remove(mejor)
    stats = EstadisticasAuditoria(
        total_casa=len(casa),
        verificados=verificados,
        cobertura=(verificados / len(casa) * 100) if casa else 0.0,
    )
    return discrepancias, stats


def test_normalizar_nombre_quita_sufijos_comunes():
    assert normalizar_nombre("Athletic Club FC", {}) == "athletic club"
    assert normalizar_nombre("Real Madrid C.F.", {}) == "real madrid cf"


def test_normalizar_nombre_aplica_alias_aprobado():
    alias = {"barça": "futbol club barcelona"}
    assert normalizar_nombre("Barça", alias) == "futbol club barcelona"


def test_encontrar_mejor_coincidencia_elige_el_mas_parecido():
    casa = _partido("Athletic de Bilbao", "Sevilla")
    candidatos = [
        _partido("Real Madrid", "Valencia"),
        _partido("Athletic Club", "Sevilla FC"),
    ]
    mejor, puntuacion = encontrar_mejor_coincidencia(casa, candidatos, {})
    assert mejor is candidatos[1]
    assert puntuacion >= UMBRAL_CONFIANZA_MINIMA


def test_auditar_detecta_discrepancia_de_horario():
    casa = [_partido("Athletic Club", "Sevilla", detalle="21:00")]
    flashscore = [_partido("Athletic de Bilbao", "Sevilla FC", detalle="19:00", fuente="flashscore")]

    discrepancias, stats = _auditar_sin_db(casa, flashscore)

    assert stats.verificados == 1
    assert len(discrepancias) == 1
    assert discrepancias[0].prioridad == "alta"  # 21:00 > 19:00 -> la casa va "por delante"


def test_auditar_no_marca_discrepancia_si_coincide_hora():
    casa = [_partido("Athletic Club", "Sevilla", detalle="19:00")]
    flashscore = [_partido("Athletic de Bilbao", "Sevilla FC", detalle="19:00", fuente="flashscore")]
    discrepancias, stats = _auditar_sin_db(casa, flashscore)
    assert stats.verificados == 1
    assert discrepancias == []


def test_auditar_ignora_partidos_en_vivo():
    casa = [_partido("Athletic Club", "Sevilla", estado=EstadoPartido.EN_VIVO, detalle="45'")]
    flashscore = [
        _partido(
            "Athletic de Bilbao",
            "Sevilla FC",
            estado=EstadoPartido.EN_VIVO,
            detalle="46'",
            fuente="flashscore",
        )
    ]
    discrepancias, _stats = _auditar_sin_db(casa, flashscore)
    assert discrepancias == []


def test_detectar_categoria():
    assert detectar_categoria("Guadalajara Sub-21") == "sub21"
    assert detectar_categoria("Guadalajara U21") == "sub21"
    assert detectar_categoria("Sassuolo Sub-20") == "sub20"
    assert detectar_categoria("Badalona Women") == "femenino"
    assert detectar_categoria("Barcelona (W)") == "femenino"
    assert detectar_categoria("Myjava F") == "femenino"  # "F" suelta al final, sin parentesis
    assert detectar_categoria("Atlético de Madrid B") == "reservas"
    assert detectar_categoria("Wrexham II") == "reservas"
    assert detectar_categoria("Athletic Club") is None
    assert detectar_categoria("Real Madrid") is None


def test_encontrar_mejor_coincidencia_descarta_categoria_distinta():
    """Caso real detectado en produccion: 'Guadalajara vs Tijuana' (casa,
    equipo absoluto) NO debe emparejarse con 'Guadalajara Sub-21 vs
    Tijuana Sub-21' (Flashscore) aunque el texto sea casi identico."""
    casa = _partido("Guadalajara", "Tijuana")
    candidatos = [_partido("Guadalajara Sub-21", "Tijuana Sub-21", fuente="flashscore")]
    mejor, puntuacion = encontrar_mejor_coincidencia(casa, candidatos, {})
    assert mejor is None
    assert puntuacion == -1.0


def test_encontrar_mejor_coincidencia_no_descarta_misma_categoria():
    """Si AMBOS lados son de la misma categoria, se sigue encontrando el
    match con normalidad -- el filtro no debe generar falsos negativos."""
    casa = _partido("Guadalajara Sub-21", "Tijuana Sub-21")
    candidatos = [
        _partido("Real Madrid", "Barcelona", fuente="flashscore"),
        _partido("Guadalajara Sub 21", "Tijuana Sub 21", fuente="flashscore"),
    ]
    mejor, puntuacion = encontrar_mejor_coincidencia(casa, candidatos, {})
    assert mejor is candidatos[1]
    assert puntuacion >= UMBRAL_CONFIANZA_MINIMA


def test_auditar_no_confunde_barcelona_con_barcelona_sc():
    """Caso real detectado en produccion: 'Barcelona SC' (club ecuatoriano)
    no debe confundirse con un 'Barcelona' distinto solo por el nombre."""
    casa = [_partido("Barcelona SC", "Orense SC", detalle="19:00")]
    flashscore = [_partido("Badalona Women", "Barcelona B", detalle="21:00", fuente="flashscore")]
    discrepancias, stats = _auditar_sin_db(casa, flashscore)
    assert stats.verificados == 0
    assert discrepancias == []


def test_encontrar_mejor_coincidencia_no_confunde_dos_partidos_que_comparten_un_equipo():
    """Caso real detectado en produccion la noche del 22-23 de agosto:
    'Skanderborg AGF Haandbold vs TMS Ringsted' (casa) se emparejo con
    'Aalborg vs Skanderborg AGF' (Flashscore) — son DOS PARTIDOS REALES
    Y DISTINTOS que solo comparten un equipo, no el mismo partido escrito
    de otra forma. Comparar "local+visitante" como una sola bolsa de
    palabras dejaba que ese equipo compartido subiera la puntuacion por
    encima del umbral aunque el otro equipo no tuviera nada que ver."""
    casa = [_partido("Skanderborg AGF Haandbold", "TMS Ringsted", detalle="18:00")]
    flashscore = [_partido("Aalborg", "Skanderborg AGF", detalle="16:00", fuente="flashscore")]
    discrepancias, stats = _auditar_sin_db(casa, flashscore)
    assert stats.verificados == 0  # no se da por bueno el emparejamiento
    assert discrepancias == []  # y por tanto no se avisa de una "discrepancia" inventada


def test_encontrar_mejor_coincidencia_sigue_encontrando_partidos_reales_con_nombres_distintos():
    """El arreglo del caso de arriba no debe volverse tan estricto que
    dos formas legitimas de escribir el MISMO partido dejen de
    emparejarse (caso real, misma noche: mismo partido, un lado
    abrevia el segundo equipo)."""
    casa = _partido("Sydney Olympic", "Marconi Stallions FC")
    candidatos = [_partido("Sydney Olympic", "Marconi", fuente="flashscore")]
    mejor, puntuacion = encontrar_mejor_coincidencia(casa, candidatos, {})
    assert mejor is candidatos[0]
    assert puntuacion >= UMBRAL_CONFIANZA_MINIMA


def test_normalizar_hora_ignora_texto_sin_hora():
    assert normalizar_hora("En Vivo") == "n/a"
    assert normalizar_hora("Comienza en 5 min") == "n/a"
    assert normalizar_hora("17:5") == "n/a"  # los minutos deben tener 2 digitos


def test_normalizar_hora_normaliza_con_ceros():
    assert normalizar_hora("9:05") == "09:05"
