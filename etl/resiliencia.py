"""
etl/resiliencia.py   (FA-02)
Resiliencia del ETL:
  1. Reintentos automáticos SOLO ante fallos de conexión (5, 15 y 45 s).
  2. Respaldos con pg_dump, conservando los últimos 5.
  3. Reanudación de corridas interrumpidas a partir de la bitácora.

Las transacciones atómicas ya viven en db/conexion.py (transaccion()); la
alerta roja persistente vive en ui/alertas.py y usa el log de utils/logger.py.

Importante sobre los reintentos: la función que se reintenta debe ser
"repetible", es decir, abrir su propia transacción (así, si falla a la mitad,
el rollback deja todo como estaba y volver a llamarla no duplica nada).
"""
import functools
import os
import shutil
import subprocess
import time
from datetime import datetime

import psycopg2

from config.settings import (BACKUP_DIR, DB_CONFIG, MAX_RESPALDOS, PG_DUMP_PATH,
                             REINTENTOS_ESPERA)
from db.conexion import transaccion
from utils.logger import get_logger

log = get_logger(__name__)

PROCESO_CORRIDA = "CORRIDA_ETL"       # así se registra la corrida completa en la bitácora
PREFIJO_RESPALDO = "bodega_snies_"
EXTENSION_RESPALDO = ".dump"

# Una corrida en proceso sin actividad en la bitácora por más de estos minutos
# se considera abandonada (por ejemplo, se cerró la ventana o se apagó el equipo).
MINUTOS_INACTIVIDAD = 15


# ---------------------------------------------------------------
# Errores propios
# ---------------------------------------------------------------
class ErrorReintentosAgotados(Exception):
    """Se agotaron los reintentos ante un fallo de conexión."""

    def __init__(self, mensaje, intentos):
        super().__init__(mensaje)
        self.intentos = intentos


class ErrorRespaldo(Exception):
    """No se pudo generar el respaldo con pg_dump."""

    def __init__(self, mensaje, por_conexion=False):
        super().__init__(mensaje)
        self.por_conexion = por_conexion


# ---------------------------------------------------------------
# 1. Reintentos solo ante fallos de conexión
# ---------------------------------------------------------------
# Textos que indican que el servidor no se alcanzó o la conexión se cayó
_TEXTOS_CONEXION = (
    "could not connect", "connection refused", "connection timed out",
    "timeout expired", "server closed the connection", "connection already closed",
    "no connection to the server", "connection reset", "terminating connection",
    "could not translate host name", "the database system is starting up",
    "the database system is shutting down", "ssl syscall error", "eof detected",
    "connection to server", "no such file or directory",
)
# Textos de errores que NO son de red: repetir no sirve
_TEXTOS_NO_RECUPERABLES = (
    "password authentication failed", "authentication failed", "no password supplied",
    "does not exist", "permission denied", "no pg_hba.conf entry",
)
# Códigos SQLSTATE: la clase 08 es "excepción de conexión"; 57P01/02/03 son
# servidor apagándose, cayéndose o iniciando.
_PGCODES_CONEXION = ("57P01", "57P02", "57P03")


def es_fallo_conexion(exc):
    """True solo si el error es de conectividad (red, servidor caído, conexión
    perdida). Errores de datos, de SQL, de contraseña o de permisos devuelven
    False y no se reintentan."""
    actual, visto = exc, set()
    while actual is not None and id(actual) not in visto:     # revisa también la causa
        visto.add(id(actual))
        if isinstance(actual, ErrorRespaldo) and actual.por_conexion:
            return True
        if isinstance(actual, (psycopg2.OperationalError, psycopg2.InterfaceError)):
            texto = str(actual).lower()
            pgcode = getattr(actual, "pgcode", None) or ""
            if any(t in texto for t in _TEXTOS_NO_RECUPERABLES):
                return False
            if pgcode.startswith("08") or pgcode in _PGCODES_CONEXION:
                return True
            if any(t in texto for t in _TEXTOS_CONEXION):
                return True
            if isinstance(actual, psycopg2.InterfaceError):
                return True
        actual = actual.__cause__ or actual.__context__
    return False


def con_reintentos(funcion, *args, etapa="operación", esperas=None, esperar=time.sleep,
                   **kwargs):
    """Ejecuta funcion(*args, **kwargs). Si falla por conexión, espera y la
    vuelve a intentar: tres reintentos con esperas de 5, 15 y 45 segundos
    (REINTENTOS_ESPERA), es decir, hasta 4 llamadas en total.

    Cualquier error que no sea de conexión se propaga de inmediato, sin
    reintentar. Si se agotan los reintentos se lanza ErrorReintentosAgotados.

    esperas y esperar existen para poder probar sin esperar de verdad."""
    esperas = REINTENTOS_ESPERA if esperas is None else esperas
    intento = 0
    while True:
        try:
            return funcion(*args, **kwargs)
        except Exception as exc:
            if not es_fallo_conexion(exc):
                raise                                   # no es de conexión: no se reintenta
            if intento >= len(esperas):
                mensaje = (f"Se perdió la conexión con PostgreSQL en la etapa '{etapa}' y "
                           f"no se recuperó tras {len(esperas)} reintentos. Detalle: {exc}")
                log.error(mensaje)
                raise ErrorReintentosAgotados(mensaje, intento + 1) from exc
            espera = esperas[intento]
            intento += 1
            primera_linea = (str(exc).strip().splitlines() or [repr(exc)])[0]
            log.warning("Fallo de conexión en '%s' (%s). Reintento %d de %d en %s s.",
                        etapa, primera_linea, intento, len(esperas), espera)
            esperar(espera)


def reintentar_conexion(etapa=None):
    """Decorador equivalente a con_reintentos, para funciones repetibles.
        @reintentar_conexion("carga a la bodega")
        def mi_funcion(...): ..."""
    def decorador(funcion):
        @functools.wraps(funcion)
        def envoltura(*args, **kwargs):
            return con_reintentos(funcion, *args, etapa=etapa or funcion.__name__, **kwargs)
        return envoltura
    return decorador


# ---------------------------------------------------------------
# 2. Respaldos con pg_dump (se conservan los últimos MAX_RESPALDOS)
# ---------------------------------------------------------------
def _ruta_pg_dump():
    """Ubica pg_dump: ruta completa de settings o búsqueda en el PATH."""
    if os.path.isabs(PG_DUMP_PATH) and os.path.exists(PG_DUMP_PATH):
        return PG_DUMP_PATH
    encontrado = shutil.which(PG_DUMP_PATH)
    if encontrado:
        return encontrado
    raise ErrorRespaldo(
        "No se encontró pg_dump. Instala las herramientas de cliente de PostgreSQL "
        "o define la ruta completa en la variable de entorno PG_DUMP_PATH "
        "(por ejemplo C:\\Program Files\\PostgreSQL\\16\\bin\\pg_dump.exe).")


def listar_respaldos():
    """Respaldos existentes, del más reciente al más antiguo:
    [{'ruta', 'nombre', 'fecha', 'tamano_mb'}]"""
    if not BACKUP_DIR.exists():
        return []
    archivos = sorted(BACKUP_DIR.glob(f"{PREFIJO_RESPALDO}*{EXTENSION_RESPALDO}"),
                      key=lambda p: p.name, reverse=True)   # el nombre lleva la fecha
    return [{"ruta": p, "nombre": p.name,
             "fecha": datetime.fromtimestamp(p.stat().st_mtime),
             "tamano_mb": round(p.stat().st_size / 1_048_576, 2)} for p in archivos]


def rotar_respaldos(maximo=None):
    """Borra los respaldos más antiguos y deja solo los últimos `maximo`."""
    maximo = MAX_RESPALDOS if maximo is None else maximo
    borrados = []
    for r in listar_respaldos()[maximo:]:
        try:
            r["ruta"].unlink()
            borrados.append(r["nombre"])
        except OSError as exc:
            log.warning("No se pudo borrar el respaldo antiguo %s: %s", r["nombre"], exc)
    if borrados:
        log.info("Respaldos antiguos eliminados: %s", ", ".join(borrados))
    return borrados


def respaldar_bd(etiqueta="manual"):
    """Genera un respaldo completo de la base con pg_dump (formato custom, .dump)
    en backups/ y deja solo los últimos 5. Devuelve la ruta del archivo.
    Lanza ErrorRespaldo si falla (con por_conexion=True si fue por conexión,
    para que con_reintentos pueda repetirlo)."""
    pg_dump = _ruta_pg_dump()
    BACKUP_DIR.mkdir(parents=True, exist_ok=True)

    etiqueta_limpia = "".join(c if c.isalnum() else "-" for c in str(etiqueta)).strip("-")
    marca = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
    destino = BACKUP_DIR / f"{PREFIJO_RESPALDO}{marca}_{etiqueta_limpia}{EXTENSION_RESPALDO}"

    comando = [pg_dump, "-h", str(DB_CONFIG["host"]), "-p", str(DB_CONFIG["port"]),
               "-U", DB_CONFIG["user"], "-d", DB_CONFIG["dbname"],
               "-F", "c", "--no-password", "-f", str(destino)]
    entorno = dict(os.environ, PGPASSWORD=str(DB_CONFIG["password"]))

    log.info("Generando respaldo: %s", destino.name)
    try:
        resultado = subprocess.run(comando, env=entorno, capture_output=True,
                                   text=True, timeout=600)
    except subprocess.TimeoutExpired as exc:
        destino.unlink(missing_ok=True)
        raise ErrorRespaldo("pg_dump tardó más de 10 minutos y se canceló.") from exc

    if resultado.returncode != 0:
        destino.unlink(missing_ok=True)               # no dejar un respaldo a medias
        detalle = (resultado.stderr or "").strip()
        texto = detalle.lower()
        por_conexion = (any(t in texto for t in _TEXTOS_CONEXION)
                        and not any(t in texto for t in _TEXTOS_NO_RECUPERABLES))
        raise ErrorRespaldo(f"pg_dump falló: {detalle}", por_conexion=por_conexion)

    rotar_respaldos()
    log.info("Respaldo listo: %s (%.2f MB)", destino.name, destino.stat().st_size / 1_048_576)
    return destino


# ---------------------------------------------------------------
# 3. Reanudación desde la bitácora
# ---------------------------------------------------------------
def cerrar_corridas_abandonadas(minutos=MINUTOS_INACTIVIDAD):
    """Si una corrida quedó 'EN_PROCESO' sin actividad por más de `minutos`
    (se cerró la ventana, se cayó el equipo), la marca como ERROR para que se
    muestre la alerta roja y se pueda reanudar. Devuelve cuántas cerró."""
    mensaje = ("Ejecución interrumpida (el proceso se detuvo sin terminar). "
               "Se puede reanudar desde el último paso válido.")
    with transaccion() as cur:
        cur.execute(
            """SELECT c.id_corrida FROM staging.etl_log c
               WHERE c.proceso = %s AND c.estado = 'EN_PROCESO'
                 AND (SELECT max(GREATEST(l.fecha_inicio, COALESCE(l.fecha_fin, l.fecha_inicio)))
                      FROM staging.etl_log l WHERE l.id_corrida = c.id_corrida)
                     < now() - make_interval(mins => %s)""",
            (PROCESO_CORRIDA, minutos))
        ids = [str(f[0]) for f in cur.fetchall()]
        for id_corrida in ids:
            cur.execute(
                """UPDATE staging.etl_log
                   SET estado = 'ERROR', fecha_fin = now(),
                       mensaje = COALESCE(mensaje, %s)
                   WHERE id_corrida = %s AND estado = 'EN_PROCESO'""",
                (mensaje, id_corrida))
    if ids:
        log.warning("Corridas interrumpidas marcadas como ERROR: %s", ", ".join(ids))
    return len(ids)


def corrida_reanudable():
    """Devuelve la última corrida si terminó con error o fue interrumpida:
        {'id_corrida', 'tipo_carga', 'mensaje', 'fecha', 'etapas_ok'}
    'etapas_ok' es el conjunto de etapas que ya terminaron bien y que el
    orquestador debe saltarse. Devuelve None si no hay nada que reanudar
    (última corrida exitosa, no hay corridas, o hay una en ejecución ahora)."""
    cerrar_corridas_abandonadas()
    with transaccion() as cur:
        cur.execute(
            """SELECT id_corrida, estado, tipo_carga, mensaje, COALESCE(fecha_fin, fecha_inicio)
               FROM staging.etl_log WHERE proceso = %s
               ORDER BY id_log DESC LIMIT 1""", (PROCESO_CORRIDA,))
        fila = cur.fetchone()
        if not fila or fila[1] != "ERROR":
            return None
        id_corrida = str(fila[0])
        cur.execute("""SELECT DISTINCT proceso FROM staging.etl_log
                       WHERE id_corrida = %s AND estado = 'OK'""", (id_corrida,))
        etapas = {f[0] for f in cur.fetchall()}
    return {"id_corrida": id_corrida, "tipo_carga": fila[2], "mensaje": fila[3],
            "fecha": fila[4], "etapas_ok": etapas}