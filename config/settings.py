"""
config/settings.py
Parámetros generales del sistema ETL SNIES: conexión, rutas, reintentos,
umbrales de homologación y respaldos. Los valores sensibles se pueden
sobrescribir con variables de entorno (PGHOST, PGPASSWORD, etc.).
"""
import os
from pathlib import Path

# ---------------------------------------------------------------
# Rutas del proyecto
# ---------------------------------------------------------------
BASE_DIR = Path(__file__).resolve().parent.parent
DATA_ENTRADA = BASE_DIR / "data" / "entrada"        # archivos SNIES cargados
DATA_CATALOGOS = BASE_DIR / "data" / "catalogos"    # catálogo DANE
BACKUP_DIR = BASE_DIR / "backups"
LOG_DIR = BASE_DIR / "logs"
SQL_DIR = BASE_DIR / "db" / "sql"

# ---------------------------------------------------------------
# Conexión a PostgreSQL
# ---------------------------------------------------------------
DB_CONFIG = {
    "host": os.getenv("PGHOST", "localhost"),
    "port": os.getenv("PGPORT", "5432"),
    "dbname": os.getenv("PGDATABASE", "bodega_snies"),
    "user": os.getenv("PGUSER", "postgres"),
    "password": os.getenv("PGPASSWORD", "123"),
}

# Base de mantenimiento de PostgreSQL: se usa solo para crear bodega_snies si no existe
DB_MANTENIMIENTO = "postgres"

# Esquemas de la base de datos
ESQUEMA_STAGING = "staging"
ESQUEMA_DW = "dw"

# ---------------------------------------------------------------
# FA-02: resiliencia y respaldos
# ---------------------------------------------------------------
REINTENTOS_ESPERA = (5, 15, 45)   # segundos entre reintentos (3 intentos)
MAX_RESPALDOS = 5                 # se conservan los últimos 5 respaldos
PG_DUMP_PATH = os.getenv("PG_DUMP_PATH", "pg_dump")  # ruta completa si no está en el PATH

# ---------------------------------------------------------------
# FA-01: umbrales de homologación geográfica (rapidfuzz, 0 a 100)
# ---------------------------------------------------------------
UMBRAL_AUTOMATICO = 90   # >= 90: se acepta automáticamente
UMBRAL_REVISION = 75     # entre 75 y 89: requiere revisión del usuario
                         # < 75: advertencia, queda como NO INFORMADO
ARCHIVO_DANE = DATA_CATALOGOS / "dane_municipios.csv"

# ---------------------------------------------------------------
# Constantes de negocio
# ---------------------------------------------------------------
NO_INFORMADO = "NO INFORMADO"
TIPOS_CARGA = ("COMPLETA", "INCREMENTAL")
