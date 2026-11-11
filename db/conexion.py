"""
db/conexion.py
Conexión a PostgreSQL con psycopg2, transacciones atómicas y creación
automática de la base de datos (para quien no sabe usar pgAdmin).

Funciones principales:
  conectar()               conexión a bodega_snies
  transaccion()            cursor con commit/rollback automático (FA-02)
  inicializar_base()       crea la base si falta y ejecuta los DDL (FC-02)
  estado_base()            informa qué existe y qué falta (para la interfaz)
"""
from contextlib import contextmanager

import psycopg2
from psycopg2 import sql

from config.settings import DB_CONFIG, DB_MANTENIMIENTO, SQL_DIR
from utils.logger import get_logger

log = get_logger(__name__)

# Orden de ejecución de los scripts de estructura
SCRIPTS_DDL = ["ddl_staging.sql", "ddl_control.sql", "ddl_dw.sql"]

# Tablas que deben existir para considerar la base lista
TABLAS_ESPERADAS = [
    "staging.instituciones_raw", "staging.directivos_raw",
    "staging.programas_raw", "staging.cobertura_raw",
    "staging.etl_log", "staging.rechazos_etl",
    "staging.auditoria_transformacion", "staging.homologacion_geografica",
    "dw.dim_institucion", "dw.dim_ubicacion", "dw.dim_area_conocimiento",
    "dw.dim_modalidad", "dw.dim_estado_calidad", "dw.dim_tiempo",
    "dw.dim_programa", "dw.hecho_oferta_programa",
]


class ErrorBaseDatos(Exception):
    """Error de conexión o de creación de la base, con mensaje entendible."""


def _mensaje_amigable(exc):
    """Traduce los errores típicos de PostgreSQL a un mensaje claro."""
    texto = str(exc).lower()
    if "password authentication failed" in texto:
        return ("La contraseña de PostgreSQL no es correcta. "
                "Revísala en config/settings.py o en la variable PGPASSWORD.")
    if "could not connect" in texto or "connection refused" in texto or "timeout" in texto:
        return ("No se pudo conectar con PostgreSQL. Verifica que esté instalado, "
                "que el servicio esté iniciado y que el host y el puerto sean correctos.")
    if "does not exist" in texto and "database" in texto:
        return "La base de datos no existe todavía."
    return f"Error de PostgreSQL: {exc}"


def conectar(dbname=None):
    """Abre una conexión nueva. Quien la use debe cerrarla.
    Sin argumentos se conecta a la base del proyecto (bodega_snies)."""
    config = dict(DB_CONFIG)
    if dbname:
        config["dbname"] = dbname
    return psycopg2.connect(**config)


@contextmanager
def transaccion():
    """Entrega un cursor dentro de una transacción atómica.
    Commit al salir sin errores; rollback si hay cualquier excepción."""
    conn = conectar()
    try:
        cur = conn.cursor()
        yield cur
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def ejecutar_script(ruta_sql):
    """Ejecuta un archivo .sql completo dentro de una transacción."""
    with open(ruta_sql, encoding="utf-8") as f:
        contenido = f.read()
    with transaccion() as cur:
        cur.execute(contenido)
    log.info("Script ejecutado: %s", ruta_sql.name)


def probar_conexion():
    """Devuelve (True, '') si hay conexión a bodega_snies; (False, motivo) si no."""
    try:
        conectar().close()
        return True, ""
    except Exception as exc:
        return False, _mensaje_amigable(exc)


# ---------------------------------------------------------------
# Creación automática de la base de datos
# ---------------------------------------------------------------
def base_existe():
    """True si la base bodega_snies ya existe en el servidor."""
    nombre = DB_CONFIG["dbname"]
    try:
        conn = conectar(DB_MANTENIMIENTO)
    except Exception as exc:
        raise ErrorBaseDatos(_mensaje_amigable(exc)) from exc
    try:
        cur = conn.cursor()
        cur.execute("SELECT 1 FROM pg_database WHERE datname = %s", (nombre,))
        return cur.fetchone() is not None
    finally:
        conn.close()


def crear_base_si_no_existe():
    """Crea la base bodega_snies si no existe. Devuelve True si la creó.
    CREATE DATABASE no puede ir dentro de una transacción, por eso autocommit."""
    if base_existe():
        return False
    nombre = DB_CONFIG["dbname"]
    conn = conectar(DB_MANTENIMIENTO)
    try:
        conn.autocommit = True
        conn.cursor().execute(
            sql.SQL("CREATE DATABASE {} ENCODING 'UTF8'").format(sql.Identifier(nombre)))
    except Exception as exc:
        raise ErrorBaseDatos(
            f"No se pudo crear la base '{nombre}'. Es posible que el usuario "
            f"'{DB_CONFIG['user']}' no tenga permiso para crear bases de datos. "
            f"Detalle: {exc}") from exc
    finally:
        conn.close()
    log.info("Base de datos creada: %s", nombre)
    return True


def crear_estructuras():
    """Ejecuta los scripts DDL (staging, control y bodega). Se puede repetir
    sin problema: todos usan IF NOT EXISTS y no borran datos."""
    for nombre in SCRIPTS_DDL:
        ruta = SQL_DIR / nombre
        if not ruta.exists() or ruta.stat().st_size == 0:
            raise ErrorBaseDatos(f"Falta el script de estructura o está vacío: {nombre}")
        try:
            ejecutar_script(ruta)
        except Exception as exc:
            raise ErrorBaseDatos(f"Falló la ejecución de {nombre}: {exc}") from exc


def inicializar_base():
    """Deja el sistema listo en una sola acción: crea la base si falta y
    crea esquemas y tablas. Devuelve un resumen para mostrar al usuario."""
    creada = crear_base_si_no_existe()
    crear_estructuras()
    estado = estado_base()
    estado["base_creada_ahora"] = creada
    return estado


def estado_base():
    """Resumen del estado de la base, pensado para mostrarlo en la interfaz:
    {'servidor_ok', 'base_existe', 'tablas_faltantes', 'lista', 'mensaje'}"""
    estado = {"servidor_ok": False, "base_existe": False,
              "tablas_faltantes": list(TABLAS_ESPERADAS), "lista": False, "mensaje": ""}
    try:
        estado["base_existe"] = base_existe()
        estado["servidor_ok"] = True
    except ErrorBaseDatos as exc:
        estado["mensaje"] = str(exc)
        return estado

    if not estado["base_existe"]:
        estado["mensaje"] = "La base de datos aún no existe."
        return estado

    with transaccion() as cur:
        cur.execute("""SELECT table_schema || '.' || table_name
                       FROM information_schema.tables
                       WHERE table_schema IN ('staging', 'dw')""")
        existentes = {fila[0] for fila in cur.fetchall()}
    estado["tablas_faltantes"] = [t for t in TABLAS_ESPERADAS if t not in existentes]
    estado["lista"] = not estado["tablas_faltantes"]
    estado["mensaje"] = ("La base está lista." if estado["lista"]
                         else f"Faltan {len(estado['tablas_faltantes'])} tablas por crear.")
    return estado
