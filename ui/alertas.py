"""
ui/alertas.py   (FA-02)
Alerta visual persistente en Streamlit: se muestra en rojo mientras la última
ejecución del ETL haya terminado con error, hasta que una nueva termine bien.
"""
import streamlit as st

from db.bitacora import alerta_pendiente
from utils.logger import ARCHIVO_LOG


def mostrar_alerta_persistente():
    """Llamar al inicio de cada pantalla. Devuelve True si hay alerta activa."""
    try:
        alerta = alerta_pendiente()
    except Exception:
        return False     # sin base de datos todavía: la pantalla de inicio lo explica
    if not alerta:
        return False
    st.error(
        f"La última ejecución del ETL terminó con error ({alerta['fecha']:%Y-%m-%d %H:%M}). "
        f"Motivo: {alerta['mensaje']}\n\n"
        f"Puede volver a ejecutar el proceso: continuará desde el último paso válido. "
        f"Detalle técnico en: {ARCHIVO_LOG}")
    return True
