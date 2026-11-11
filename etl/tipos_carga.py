"""
etl/tipos_carga.py   (FD-02, apoya FC-02)
Define el comportamiento de cada tipo de carga:
  COMPLETA    : vacía staging y la bodega y recarga todo desde cero.
  INCREMENTAL : no borra nada; staging y la bodega usan UPSERT, así solo se
                insertan los registros nuevos y se actualizan los modificados.
La bitácora (etl_log, rechazos, auditoría) nunca se borra: es el historial.
"""
from config.settings import NO_INFORMADO, TIPOS_CARGA

TABLAS_STAGING = [
    "staging.instituciones_raw", "staging.directivos_raw",
    "staging.programas_raw", "staging.cobertura_raw",
]
TABLAS_DW = [
    "dw.hecho_oferta_programa", "dw.dim_programa", "dw.dim_tiempo",
    "dw.dim_estado_calidad", "dw.dim_modalidad", "dw.dim_area_conocimiento",
    "dw.dim_ubicacion", "dw.dim_institucion",
]


def normalizar_tipo(tipo):
    """Valida el tipo de carga y lo devuelve en mayúsculas."""
    t = (tipo or "").strip().upper()
    if t not in TIPOS_CARGA:
        raise ValueError(f"Tipo de carga no válido: '{tipo}'. Use: {', '.join(TIPOS_CARGA)}.")
    return t


def es_completa(tipo):
    return normalizar_tipo(tipo) == "COMPLETA"


def preparar_para_carga(cur, tipo):
    """Deja staging y la bodega listos según el tipo de carga.
    Debe llamarse dentro de una transacción (si algo falla, no se borra nada).
    Devuelve un texto con lo realizado, para la bitácora."""
    if not es_completa(tipo):
        return "Carga incremental: no se borra información existente."

    cur.execute("TRUNCATE " + ", ".join(TABLAS_STAGING) + " RESTART IDENTITY")
    cur.execute("TRUNCATE " + ", ".join(TABLAS_DW) + " RESTART IDENTITY CASCADE")
    # Fila de referencia que usa el hecho cuando un programa no tiene cobertura
    cur.execute("""INSERT INTO dw.dim_ubicacion (departamento, municipio, tipo_cobertura)
                   VALUES (%s, %s, %s) ON CONFLICT DO NOTHING""",
                (NO_INFORMADO, NO_INFORMADO, NO_INFORMADO))
    return "Carga completa: staging y bodega vaciados antes de recargar."
