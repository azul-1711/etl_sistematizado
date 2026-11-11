"""
etl/staging.py   (FC-02, FD-03)

FC-02  preparar_staging(tipo)
       Comprueba que la base y las tablas Staging existan (si faltan, las crea)
       y deja las tablas listas según el tipo de carga (COMPLETA / INCREMENTAL).

FD-03  cargar_fuente_staging(fuente, df, id_corrida)
       Guarda en Staging los datos casi crudos de una fuente usando UPSERT:
       inserta lo nuevo, actualiza lo que cambió y deja igual lo que no cambió.
       Todo ocurre en una sola transacción: si algo falla no queda nada a medias.

Importante para la reanudación (FA-02): preparar_staging con carga COMPLETA
vacía Staging y la bodega. El orquestador debe llamarla una sola vez por
corrida y NO repetirla al reanudar una corrida interrumpida.
"""
import psycopg2.extras
from psycopg2 import sql

from db import rechazos
from db.bitacora import nueva_corrida
from db.conexion import estado_base, inicializar_base, transaccion
from etl.tipos_carga import TABLAS_STAGING, normalizar_tipo, preparar_para_carga
from utils.logger import get_logger

log = get_logger(__name__)

TAMANO_LOTE = 1000

# Columnas de la llave que pueden venir vacías: el índice único de la tabla las
# trata como texto vacío (COALESCE), así una carga repetida no duplica la fila.
CLAVE_ADMITE_VACIO = {
    "staging.directivos_raw": {"fecha_desde"},
}


# ---------------------------------------------------------------
# FC-02: preparación del área Staging
# ---------------------------------------------------------------
def preparar_staging(tipo_carga):
    """Deja el área Staging lista para recibir datos.
    1) Si falta la base o alguna tabla, las crea (no hay que usar pgAdmin).
    2) Aplica el tipo de carga: COMPLETA vacía Staging y la bodega;
       INCREMENTAL no borra nada.
    Devuelve {'tipo_carga', 'estructuras_creadas', 'mensaje'}."""
    tipo = normalizar_tipo(tipo_carga)

    creadas = False
    estado = estado_base()
    if not estado["lista"]:
        log.info("Faltan estructuras (%s). Se crean automáticamente.", estado["mensaje"])
        inicializar_base()
        creadas = True

    with transaccion() as cur:
        mensaje = preparar_para_carga(cur, tipo)
    log.info("Staging preparado. %s", mensaje)
    return {"tipo_carga": tipo, "estructuras_creadas": creadas, "mensaje": mensaje}


def contar_filas():
    """Cantidad de filas de cada tabla de Staging: {tabla: filas}."""
    resultado = {}
    with transaccion() as cur:
        for tabla in TABLAS_STAGING:
            cur.execute(sql.SQL("SELECT count(*) FROM {}").format(
                sql.Identifier(*tabla.split("."))))
            resultado[tabla] = cur.fetchone()[0]
    return resultado


# ---------------------------------------------------------------
# FD-03: carga a Staging con UPSERT
# ---------------------------------------------------------------
def _vacio(valor):
    return valor is None or (isinstance(valor, str) and valor.strip() == "")


def _separar_filas(fuente, df):
    """Recorre el DataFrame y separa:
      validas   : {llave: fila} (una por llave; si se repite gana la última)
      rechazos  : filas que no se pueden cargar, con su motivo
      vacias    : filas completamente vacías (se ignoran, no tienen información)
      exactos   : filas repetidas idénticas (se descartan sin más)"""
    tabla = fuente["tabla_staging"]
    columnas = list(fuente["columnas"].values())
    clave = fuente["clave"]
    admite_vacio = CLAVE_ADMITE_VACIO.get(tabla, set())
    obligatorias = [c for c in clave if c not in admite_vacio]

    validas, rechazados = {}, []
    vacias = exactos = 0

    for fila in df[columnas].to_dict("records"):
        codigos = (fila.get("codigo_snies"), fila.get("codigo_institucion"))

        if all(_vacio(fila[c]) for c in columnas):
            vacias += 1
            continue

        faltan = [c for c in obligatorias if _vacio(fila[c])]
        if faltan:
            rechazados.append((
                "Falta un dato obligatorio de la llave del registro: " + ", ".join(faltan),
                fila, *codigos))
            continue

        llave = tuple(fila[c] if fila[c] is not None else "" for c in clave)
        previa = validas.get(llave)
        if previa is not None and previa != fila:
            rechazados.append((
                "Llave repetida en el archivo con datos distintos; se conservó la última fila",
                previa, *codigos))
        elif previa is not None:
            exactos += 1
        validas[llave] = fila

    return validas, rechazados, vacias, exactos


def _consulta_upsert(fuente, columnas):
    """Arma el INSERT ... ON CONFLICT. Solo se actualiza una fila si algún dato
    realmente cambió; las filas iguales no se tocan. Devuelve si fue insertada."""
    tabla = fuente["tabla_staging"]
    clave = fuente["clave"]
    admite_vacio = CLAVE_ADMITE_VACIO.get(tabla, set())
    cambiables = [c for c in columnas if c not in clave]

    destino = sql.Identifier(*tabla.split("."))
    lista_cols = sql.SQL(", ").join(sql.Identifier(c) for c in columnas)
    objetivo = sql.SQL(", ").join(
        sql.SQL("(COALESCE({}, ''))").format(sql.Identifier(c)) if c in admite_vacio
        else sql.Identifier(c)
        for c in clave)

    if cambiables:
        nuevos = sql.SQL(", ").join(sql.SQL("EXCLUDED.{}").format(sql.Identifier(c))
                                    for c in cambiables)
        actuales = sql.SQL(", ").join(sql.SQL("t.{}").format(sql.Identifier(c))
                                      for c in cambiables)
        asignaciones = sql.SQL(", ").join(
            sql.SQL("{0} = EXCLUDED.{0}").format(sql.Identifier(c)) for c in cambiables)
        accion = sql.SQL("DO UPDATE SET {} WHERE ({}) IS DISTINCT FROM ({})").format(
            asignaciones, actuales, nuevos)
    else:
        accion = sql.SQL("DO NOTHING")

    return sql.SQL(
        "INSERT INTO {destino} AS t ({cols}) VALUES %s "
        "ON CONFLICT ({objetivo}) {accion} RETURNING (xmax = 0)"
    ).format(destino=destino, cols=lista_cols, objetivo=objetivo, accion=accion)


def cargar_fuente_staging(fuente, df, id_corrida=None):
    """Carga una fuente (DataFrame ya extraído) a su tabla de Staging.
    Devuelve un resumen con: fuente, tabla, leidos, insertados, actualizados,
    sin_cambios, rechazados, filas_vacias y duplicados_exactos."""
    columnas = list(fuente["columnas"].values())
    id_corrida = id_corrida or nueva_corrida()

    validas, rechazados, vacias, exactos = _separar_filas(fuente, df)
    filas = [tuple(f[c] for c in columnas) for f in validas.values()]

    insertados = actualizados = 0
    if filas:
        with transaccion() as cur:
            consulta = _consulta_upsert(fuente, columnas).as_string(cur)
            tocadas = psycopg2.extras.execute_values(
                cur, consulta, filas, page_size=TAMANO_LOTE, fetch=True)
        insertados = sum(1 for (nueva,) in tocadas if nueva)
        actualizados = len(tocadas) - insertados

    # Los rechazos se guardan al final: si la carga falló y se reintenta,
    # no quedan rechazos repetidos.
    rechazos.registrar_lote(id_corrida, fuente["tabla_staging"], rechazados)

    resumen = {
        "fuente": fuente["nombre"], "tabla": fuente["tabla_staging"],
        "leidos": len(df), "insertados": insertados, "actualizados": actualizados,
        "sin_cambios": len(filas) - insertados - actualizados,
        "rechazados": len(rechazados), "filas_vacias": vacias,
        "duplicados_exactos": exactos,
    }
    log.info("Staging %(fuente)s: leídos %(leidos)s, nuevos %(insertados)s, "
             "actualizados %(actualizados)s, sin cambios %(sin_cambios)s, "
             "rechazados %(rechazados)s", resumen)
    return resumen
