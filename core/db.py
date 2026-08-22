"""Capa de acceso a la base de datos SQLite unica del proyecto.

Guarda: configuracion editable (toggles ON/OFF, ajustes sueltos como la
hora de ejecucion o el paralelismo), el diccionario de alias de equipos
(asistido por Ollama), la cola de partidos sin emparejar, el historial
de discrepancias y el historial de ejecuciones con tiempos por etapa
(para poder comparar velocidad entre el PC y la Raspberry Pi).

Se usan funciones sueltas (no una clase) recibiendo siempre la ruta de
la base de datos: es facil de testear (se le pasa una ruta temporal) y
evita mantener conexiones abiertas de mas.
"""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path

from core.models import Discrepancia, TiempoEtapa

SCHEMA = """
CREATE TABLE IF NOT EXISTS settings (
    clave TEXT PRIMARY KEY,
    valor TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS toggles (
    casa TEXT NOT NULL,
    deporte TEXT NOT NULL,
    activo INTEGER NOT NULL DEFAULT 1,
    PRIMARY KEY (casa, deporte)
);

CREATE TABLE IF NOT EXISTS alias_equipos (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    variante TEXT NOT NULL UNIQUE,
    canonico TEXT NOT NULL,
    fuente TEXT NOT NULL DEFAULT 'manual',
    aprobado INTEGER NOT NULL DEFAULT 1,
    creado_en TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS partidos_no_encontrados (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    casa TEXT NOT NULL,
    deporte TEXT NOT NULL,
    equipo_local TEXT NOT NULL,
    equipo_visitante TEXT NOT NULL,
    liga TEXT,
    mejor_candidato TEXT,
    puntuacion REAL,
    creado_en TEXT NOT NULL,
    resuelto INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS discrepancias (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ejecucion_id INTEGER,
    casa TEXT NOT NULL,
    casa_nombre TEXT NOT NULL,
    deporte TEXT NOT NULL,
    liga TEXT,
    liga_fs TEXT,
    equipo_local_casa TEXT,
    equipo_visitante_casa TEXT,
    detalle_casa TEXT,
    equipo_local_fs TEXT,
    equipo_visitante_fs TEXT,
    detalle_fs TEXT,
    similitud REAL,
    prioridad TEXT,
    creado_en TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS ejecuciones (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    host TEXT,
    iniciado_en TEXT NOT NULL,
    finalizado_en TEXT,
    duracion_segundos REAL,
    paralelismo INTEGER,
    total_partidos INTEGER,
    total_discrepancias INTEGER,
    errores TEXT
);

CREATE TABLE IF NOT EXISTS tiempos_etapa (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ejecucion_id INTEGER NOT NULL,
    etiqueta TEXT NOT NULL,
    segundos REAL NOT NULL,
    partidos INTEGER NOT NULL DEFAULT 0,
    FOREIGN KEY (ejecucion_id) REFERENCES ejecuciones (id)
);
"""


def inicializar_db(ruta: Path) -> None:
    ruta.parent.mkdir(parents=True, exist_ok=True)
    with _conectar(ruta) as con:
        con.executescript(SCHEMA)
        _migrar_columnas_viejas(con)


def _migrar_columnas_viejas(con: sqlite3.Connection) -> None:
    """`CREATE TABLE IF NOT EXISTS` no anade columnas nuevas a una tabla
    que ya existia de una version anterior — hace falta un ALTER TABLE
    aparte. Bug real: `tiempos_etapa.partidos` no existia en las bases
    de datos creadas antes de este cambio, asi que el conteo de
    partidos por casa que SI se calculaba en cada ciclo nunca se
    guardaba (se perdia en cuanto se mandaba el mensaje de Telegram)."""
    columnas = {fila["name"] for fila in con.execute("PRAGMA table_info(tiempos_etapa)")}
    if "partidos" not in columnas:
        con.execute("ALTER TABLE tiempos_etapa ADD COLUMN partidos INTEGER NOT NULL DEFAULT 0")


@contextmanager
def _conectar(ruta: Path) -> Iterator[sqlite3.Connection]:
    con = sqlite3.connect(ruta, timeout=10)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA journal_mode=WAL;")
    con.execute("PRAGMA foreign_keys=ON;")
    try:
        yield con
        con.commit()
    finally:
        con.close()


# --------------------------------------------------------------- settings
def get_setting(ruta: Path, clave: str, default: str | None = None) -> str | None:
    with _conectar(ruta) as con:
        fila = con.execute("SELECT valor FROM settings WHERE clave = ?", (clave,)).fetchone()
        return fila["valor"] if fila else default


def set_setting(ruta: Path, clave: str, valor: str) -> None:
    with _conectar(ruta) as con:
        con.execute(
            "INSERT INTO settings (clave, valor) VALUES (?, ?) "
            "ON CONFLICT(clave) DO UPDATE SET valor = excluded.valor",
            (clave, valor),
        )


def get_setting_int(ruta: Path, clave: str, default: int) -> int:
    valor = get_setting(ruta, clave)
    return int(valor) if valor is not None else default


def get_setting_bool(ruta: Path, clave: str, default: bool) -> bool:
    valor = get_setting(ruta, clave)
    if valor is None:
        return default
    return valor == "1"


# --------------------------------------------------------------- toggles
def set_toggle(ruta: Path, casa: str, deporte: str, activo: bool) -> None:
    with _conectar(ruta) as con:
        con.execute(
            "INSERT INTO toggles (casa, deporte, activo) VALUES (?, ?, ?) "
            "ON CONFLICT(casa, deporte) DO UPDATE SET activo = excluded.activo",
            (casa, deporte, int(activo)),
        )


def esta_activo(ruta: Path, casa: str, deporte: str, default: bool = True) -> bool:
    with _conectar(ruta) as con:
        fila = con.execute(
            "SELECT activo FROM toggles WHERE casa = ? AND deporte = ?", (casa, deporte)
        ).fetchone()
        return bool(fila["activo"]) if fila else default


def listar_toggles(ruta: Path) -> dict[tuple[str, str], bool]:
    with _conectar(ruta) as con:
        filas = con.execute("SELECT casa, deporte, activo FROM toggles").fetchall()
        return {(f["casa"], f["deporte"]): bool(f["activo"]) for f in filas}


# --------------------------------------------------------------- alias de equipos
def guardar_alias(
    ruta: Path, variante: str, canonico: str, fuente: str = "manual", aprobado: bool = True
) -> None:
    with _conectar(ruta) as con:
        con.execute(
            "INSERT INTO alias_equipos (variante, canonico, fuente, aprobado, creado_en) "
            "VALUES (?, ?, ?, ?, ?) "
            "ON CONFLICT(variante) DO UPDATE SET canonico = excluded.canonico, aprobado = excluded.aprobado",
            (
                variante.lower().strip(),
                canonico.lower().strip(),
                fuente,
                int(aprobado),
                datetime.now().isoformat(),
            ),
        )


def obtener_alias_aprobados(ruta: Path) -> dict[str, str]:
    with _conectar(ruta) as con:
        filas = con.execute("SELECT variante, canonico FROM alias_equipos WHERE aprobado = 1").fetchall()
        return {f["variante"]: f["canonico"] for f in filas}


def listar_alias_pendientes(ruta: Path, limite: int = 500) -> list[sqlite3.Row]:
    with _conectar(ruta) as con:
        return con.execute(
            "SELECT * FROM alias_equipos WHERE aprobado = 0 ORDER BY creado_en LIMIT ?", (limite,)
        ).fetchall()


def listar_alias_aprobados(ruta: Path, limite: int = 500) -> list[sqlite3.Row]:
    """A diferencia de `obtener_alias_aprobados` (pensada para el matcher:
    solo variante->canonico), esta devuelve la fila completa — incluido
    `id`, que hace falta para poder borrar un alias concreto desde el
    panel.

    El limite por defecto (500) es solo una cota de seguridad para no
    escanear una tabla sin fin — quien necesite embeber esto en la URL
    de la Mini App (telegram_bot/miniapp.py) pasa un limite MUCHO mas
    bajo explicitamente, esto no es ese limite."""
    with _conectar(ruta) as con:
        return con.execute(
            "SELECT * FROM alias_equipos WHERE aprobado = 1 ORDER BY creado_en DESC LIMIT ?", (limite,)
        ).fetchall()


def aprobar_alias(ruta: Path, alias_id: int) -> None:
    with _conectar(ruta) as con:
        con.execute("UPDATE alias_equipos SET aprobado = 1 WHERE id = ?", (alias_id,))


def rechazar_alias(ruta: Path, alias_id: int) -> None:
    """Descarta una propuesta PENDIENTE (aprobado=0) — borrado."""
    with _conectar(ruta) as con:
        con.execute("DELETE FROM alias_equipos WHERE id = ?", (alias_id,))


def eliminar_alias(ruta: Path, alias_id: int) -> None:
    """Borra un alias ya APROBADO (ej. uno que resulto ser un error, o que
    el usuario quiere retirar del diccionario). Misma operacion que
    `rechazar_alias` a nivel de base de datos — se mantienen como dos
    funciones separadas porque cada nombre deja claro, en el sitio donde
    se llama, sobre que tipo de alias se esta actuando."""
    with _conectar(ruta) as con:
        con.execute("DELETE FROM alias_equipos WHERE id = ?", (alias_id,))


# --------------------------------------------------------------- cola de no encontrados
def encolar_no_encontrado(
    ruta: Path,
    casa: str,
    deporte: str,
    equipo_local: str,
    equipo_visitante: str,
    liga: str,
    mejor_candidato: str | None,
    puntuacion: float,
) -> None:
    with _conectar(ruta) as con:
        con.execute(
            "INSERT INTO partidos_no_encontrados "
            "(casa, deporte, equipo_local, equipo_visitante, liga, mejor_candidato, puntuacion, creado_en) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (
                casa,
                deporte,
                equipo_local,
                equipo_visitante,
                liga,
                mejor_candidato,
                puntuacion,
                datetime.now().isoformat(),
            ),
        )


def listar_no_encontrados(ruta: Path, resuelto: bool = False) -> list[sqlite3.Row]:
    with _conectar(ruta) as con:
        return con.execute(
            "SELECT * FROM partidos_no_encontrados WHERE resuelto = ? ORDER BY creado_en",
            (int(resuelto),),
        ).fetchall()


def marcar_resuelto(ruta: Path, id_: int) -> None:
    with _conectar(ruta) as con:
        con.execute("UPDATE partidos_no_encontrados SET resuelto = 1 WHERE id = ?", (id_,))


# --------------------------------------------------------------- discrepancias
def guardar_discrepancia(ruta: Path, d: Discrepancia, ejecucion_id: int | None = None) -> None:
    with _conectar(ruta) as con:
        con.execute(
            "INSERT INTO discrepancias (ejecucion_id, casa, casa_nombre, deporte, liga, liga_fs, "
            "equipo_local_casa, equipo_visitante_casa, detalle_casa, "
            "equipo_local_fs, equipo_visitante_fs, detalle_fs, similitud, prioridad, creado_en) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                ejecucion_id,
                d.casa_id,
                d.casa_nombre,
                d.deporte,
                d.liga,
                d.liga_fs,
                d.equipo_local_casa,
                d.equipo_visitante_casa,
                d.detalle_casa,
                d.equipo_local_fs,
                d.equipo_visitante_fs,
                d.detalle_fs,
                d.similitud,
                d.prioridad,
                datetime.now().isoformat(),
            ),
        )


def listar_discrepancias_recientes(ruta: Path, limite: int = 20) -> list[sqlite3.Row]:
    with _conectar(ruta) as con:
        return con.execute(
            "SELECT * FROM discrepancias ORDER BY creado_en DESC LIMIT ?", (limite,)
        ).fetchall()


# --------------------------------------------------------------- ejecuciones
def crear_ejecucion(ruta: Path, host: str, paralelismo: int) -> int:
    with _conectar(ruta) as con:
        cur = con.execute(
            "INSERT INTO ejecuciones (host, iniciado_en, paralelismo) VALUES (?, ?, ?)",
            (host, datetime.now().isoformat(), paralelismo),
        )
        return cur.lastrowid


def cerrar_ejecucion(
    ruta: Path,
    ejecucion_id: int,
    total_partidos: int,
    total_discrepancias: int,
    errores: list[str],
    duracion_segundos: float,
) -> None:
    with _conectar(ruta) as con:
        con.execute(
            "UPDATE ejecuciones SET finalizado_en = ?, duracion_segundos = ?, "
            "total_partidos = ?, total_discrepancias = ?, errores = ? WHERE id = ?",
            (
                datetime.now().isoformat(),
                duracion_segundos,
                total_partidos,
                total_discrepancias,
                json.dumps(errores, ensure_ascii=False),
                ejecucion_id,
            ),
        )


def guardar_tiempo_etapa(ruta: Path, ejecucion_id: int, tiempo: TiempoEtapa) -> None:
    with _conectar(ruta) as con:
        con.execute(
            "INSERT INTO tiempos_etapa (ejecucion_id, etiqueta, segundos, partidos) VALUES (?, ?, ?, ?)",
            (ejecucion_id, tiempo.etiqueta, tiempo.segundos, tiempo.partidos),
        )


def listar_ejecuciones_recientes(ruta: Path, limite: int = 10) -> list[sqlite3.Row]:
    with _conectar(ruta) as con:
        return con.execute(
            "SELECT * FROM ejecuciones ORDER BY iniciado_en DESC LIMIT ?", (limite,)
        ).fetchall()


def listar_tiempos_de_ejecucion(ruta: Path, ejecucion_id: int) -> list[sqlite3.Row]:
    with _conectar(ruta) as con:
        return con.execute(
            "SELECT * FROM tiempos_etapa WHERE ejecucion_id = ? ORDER BY id", (ejecucion_id,)
        ).fetchall()
