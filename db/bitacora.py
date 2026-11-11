"""
db/bitacora.py
Bitácora de ejecuciones (staging.etl_log). Cada función usa su propia
transacción corta: así el registro de una etapa o de un error queda guardado
aunque la transacción principal del ETL se revierta (necesario para FC-04 y FA-02).
"""
import uuid

from db.conexion import transaccion


def nueva_corrida():
    """Identificador único de una ejecución completa del ETL."""
    return str(uuid.uuid4())


def iniciar(id_corrida, proceso, fuente=None, tipo_carga=None):
    """Registra el inicio de una etapa. Devuelve id_log."""
    with transaccion() as cur:
        cur.execute(
            """INSERT INTO staging.etl_log (id_corrida, proceso, fuente, tipo_carga, estado)
               VALUES (%s, %s, %s, %s, 'EN_PROCESO') RETURNING id_log""",
            (id_corrida, proceso, fuente, tipo_carga))
        return cur.fetchone()[0]


def finalizar(id_log, leidos=0, insertados=0, actualizados=0, rechazados=0,
              estado="OK", mensaje=None):
    """Cierra una etapa con sus conteos. estado: OK / ERROR / OMITIDA."""
    with transaccion() as cur:
        cur.execute(
            """UPDATE staging.etl_log
               SET fecha_fin = now(), registros_leidos = %s, registros_insertados = %s,
                   registros_actualizados = %s, registros_rechazados = %s,
                   estado = %s, mensaje = %s
               WHERE id_log = %s""",
            (leidos, insertados, actualizados, rechazados, estado, mensaje, id_log))


def registrar_error(id_corrida, proceso, mensaje, fuente=None, tipo_carga=None):
    """Deja constancia de un error: etapa (proceso) y causa (mensaje)."""
    with transaccion() as cur:
        cur.execute(
            """INSERT INTO staging.etl_log
                   (id_corrida, proceso, fuente, tipo_carga, fecha_fin, estado, mensaje)
               VALUES (%s, %s, %s, %s, now(), 'ERROR', %s)""",
            (id_corrida, proceso, fuente, tipo_carga, str(mensaje)[:2000]))


def etapas_ok(id_corrida):
    """Conjunto de etapas ya terminadas con éxito en una corrida (para reanudar, FA-02)."""
    with transaccion() as cur:
        cur.execute("""SELECT DISTINCT proceso FROM staging.etl_log
                       WHERE id_corrida = %s AND estado = 'OK'""", (id_corrida,))
        return {f[0] for f in cur.fetchall()}


def _filas_a_dict(cur):
    columnas = [d[0] for d in cur.description]
    return [dict(zip(columnas, fila)) for fila in cur.fetchall()]


def historial(limite=50):
    """Últimos registros de la bitácora (historial de ejecuciones, FD-02)."""
    with transaccion() as cur:
        cur.execute("""SELECT id_log, id_corrida, proceso, fuente, tipo_carga, fecha_inicio,
                              fecha_fin, registros_leidos, registros_insertados,
                              registros_actualizados, registros_rechazados, estado, mensaje
                       FROM staging.etl_log ORDER BY id_log DESC LIMIT %s""", (limite,))
        return _filas_a_dict(cur)


def alerta_pendiente():
    """Si la última corrida completa terminó con error, devuelve sus datos
    (para la alerta persistente de FA-02); si no, None.
    La corrida completa se registra con proceso = 'CORRIDA_ETL'."""
    with transaccion() as cur:
        cur.execute("""SELECT id_corrida, estado, mensaje, fecha_fin
                       FROM staging.etl_log WHERE proceso = 'CORRIDA_ETL'
                       ORDER BY id_log DESC LIMIT 1""")
        fila = cur.fetchone()
    if fila and fila[1] == "ERROR":
        return {"id_corrida": fila[0], "estado": fila[1], "mensaje": fila[2], "fecha": fila[3]}
    return None
