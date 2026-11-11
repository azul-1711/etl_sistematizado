"""
utils/logger.py
Logger del sistema: escribe en consola y en logs/etl_snies.log.
El archivo de log es el que referencia la alerta visual de FA-02.
"""
import logging
from logging.handlers import RotatingFileHandler

from config.settings import LOG_DIR

ARCHIVO_LOG = LOG_DIR / "etl_snies.log"


def get_logger(nombre="etl_snies"):
    logger = logging.getLogger(nombre)
    if logger.handlers:          # evita duplicar handlers si se llama varias veces
        return logger
    logger.setLevel(logging.INFO)
    formato = logging.Formatter("%(asctime)s | %(levelname)s | %(name)s | %(message)s")

    LOG_DIR.mkdir(parents=True, exist_ok=True)
    archivo = RotatingFileHandler(ARCHIVO_LOG, maxBytes=2_000_000, backupCount=3, encoding="utf-8")
    archivo.setFormatter(formato)
    consola = logging.StreamHandler()
    consola.setFormatter(formato)

    logger.addHandler(archivo)
    logger.addHandler(consola)
    return logger
