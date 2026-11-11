"""
etl/carga_dw.py   (FD-09)

Carga los registros validados a la bodega de datos (esquema dw), respetando el
orden y las relaciones del modelo dimensional:

  1) dimensiones   (etl/dimensiones.py: registra lo nuevo y obtiene las sk_*)
  2) hecho_oferta_programa  (una fila por programa, indicador_oferta = 1)

Todo ocurre en UNA sola transacción: si algo falla (por ejemplo, una clave
foránea o la conexión), se revierte completo y la bodega queda como estaba.
El hecho usa UPSERT por sk_programa: inserta los programas nuevos, actualiza los
que cambiaron y deja intactos los que están igual, así una carga repetida o
incremental no genera duplicados.

El orquestador decide si la carga es COMPLETA o INCREMENTAL antes de llamar
aquí (tipos_carga.preparar_para_carga vacía la bodega en la completa); esta
etapa no borra nada.

Función principal:
    cargar_dw(id_corrida, integrados) -> resumen
"""
import psycopg2.extras

from db.conexion import transaccion
from etl.dimensiones import cargar_dimensiones
from utils.logger import get_logger

log = get_logger(__name__)

TABLA_HECHO = "dw.hecho_oferta_programa"
COLS_HECHO = ["sk_programa", "sk_institucion", "sk_ubicacion", "sk_area_conocimiento",
              "sk_modalidad", "sk_estado_calidad", "sk_tiempo_resolucion",
              "sk_tiempo_ejecutoria", "sk_tiempo_registro_snies", "indicador_oferta",
              "creditos", "duracion_periodos", "vigencia_anios", "costo_matricula"]


def _consulta_hecho():
    cambiables = [c for c in COLS_HECHO if c != "sk_programa"]
    asignar = ", ".join(f"{c} = EXCLUDED.{c}" for c in cambiables)
    actuales = ", ".join(f"t.{c}" for c in cambiables)
    nuevos = ", ".join(f"EXCLUDED.{c}" for c in cambiables)
    return (f"INSERT INTO {TABLA_HECHO} AS t ({', '.join(COLS_HECHO)}) VALUES %s "
            f"ON CONFLICT (sk_programa) DO UPDATE SET {asignar} "
            f"WHERE ({actuales}) IS DISTINCT FROM ({nuevos}) "
            f"RETURNING (xmax = 0)")


def cargar_hecho(cur, hecho):
    """UPSERT del hecho. Devuelve {'nuevos', 'actualizados', 'sin_cambios'}."""
    filas = list(hecho[COLS_HECHO].itertuples(index=False, name=None))
    nuevos = actualizados = 0
    if filas:
        tocadas = psycopg2.extras.execute_values(
            cur, _consulta_hecho(), filas, page_size=2000, fetch=True)
        nuevos = sum(1 for (nueva,) in tocadas if nueva)
        actualizados = len(tocadas) - nuevos
    return {"nuevos": nuevos, "actualizados": actualizados,
            "sin_cambios": len(filas) - nuevos - actualizados}


def contar_bodega():
    """Filas de cada tabla de la bodega (para mostrar en la app y comprobar la carga)."""
    tablas = ["dim_institucion", "dim_programa", "dim_ubicacion", "dim_area_conocimiento",
              "dim_modalidad", "dim_estado_calidad", "dim_tiempo", "hecho_oferta_programa"]
    resultado = {}
    with transaccion() as cur:
        for t in tablas:
            cur.execute(f"SELECT count(*) FROM dw.{t}")
            resultado[t] = cur.fetchone()[0]
    return resultado


def cargar_dw(id_corrida, integrados):
    """Carga dimensiones y hecho en una sola transacción.
    integrados: salida de integracion.integrar_todo ({'instituciones','programas'}).
    Devuelve {'dimensiones': {...}, 'hecho': {...}} con nuevos/actualizados."""
    with transaccion() as cur:
        hecho, resumen_dims = cargar_dimensiones(
            cur, integrados["instituciones"], integrados["programas"], id_corrida)
        resumen_hecho = cargar_hecho(cur, hecho)
    log.info("Carga a la bodega: hecho %s", resumen_hecho)
    return {"dimensiones": resumen_dims, "hecho": resumen_hecho}