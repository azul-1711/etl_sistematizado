"""
db/rechazos.py
Registro y consulta de filas rechazadas (staging.rechazos_etl) con la fila
completa en JSON y el motivo (FC-06, FD-08). Usa transacción propia para que
los rechazos queden guardados aunque la carga principal falle.
"""
import json

import psycopg2.extras

from db.conexion import transaccion


def _limpiar(fila):
    """Convierte NaN/NaT a None para poder guardar la fila como JSON."""
    return {k: (None if (v is None or (isinstance(v, float) and v != v)) else v)
            for k, v in dict(fila).items()}


def _fila_sql(id_corrida, tabla_origen, motivo, fila, codigo_snies, codigo_institucion):
    return (str(id_corrida), tabla_origen,
            str(codigo_snies)[:50] if codigo_snies else None,
            str(codigo_institucion)[:50] if codigo_institucion else None,
            motivo, json.dumps(_limpiar(fila), ensure_ascii=False, default=str))


def registrar(id_corrida, tabla_origen, motivo, fila,
              codigo_snies=None, codigo_institucion=None):
    """Registra un rechazo individual."""
    registrar_lote(id_corrida, tabla_origen,
                   [(motivo, fila, codigo_snies, codigo_institucion)])


def registrar_lote(id_corrida, tabla_origen, items):
    """Registra varios rechazos de una vez.
    items: lista de (motivo, fila_dict, codigo_snies, codigo_institucion)."""
    if not items:
        return 0
    filas = [_fila_sql(id_corrida, tabla_origen, m, f, s, i) for m, f, s, i in items]
    with transaccion() as cur:
        psycopg2.extras.execute_values(
            cur,
            """INSERT INTO staging.rechazos_etl
                   (id_corrida, tabla_origen, codigo_snies, codigo_institucion,
                    motivo_rechazo, fila_completa) VALUES %s""",
            filas)
    return len(filas)


def eliminar_por_prefijo(id_corrida, prefijo_motivo):
    """Borra los rechazos de una corrida cuyo motivo empieza con 'prefijo_motivo'.
    Sirve para repetir una etapa al reanudar (FA-02) sin dejar rechazos duplicados."""
    with transaccion() as cur:
        cur.execute("""DELETE FROM staging.rechazos_etl
                       WHERE id_corrida = %s AND left(motivo_rechazo, %s) = %s""",
                    (str(id_corrida), len(prefijo_motivo), prefijo_motivo))
        return cur.rowcount


def contar(id_corrida):
    with transaccion() as cur:
        cur.execute("SELECT count(*) FROM staging.rechazos_etl WHERE id_corrida = %s",
                    (str(id_corrida),))
        return cur.fetchone()[0]


def consultar(id_corrida=None, limite=500):
    """Rechazos más recientes (de una corrida o de todas), para mostrarlos en la app."""
    sql = """SELECT id_rechazo, id_corrida, tabla_origen, codigo_snies, codigo_institucion,
                    motivo_rechazo, fecha_rechazo, fila_completa
             FROM staging.rechazos_etl """
    params = []
    if id_corrida:
        sql += "WHERE id_corrida = %s "
        params.append(str(id_corrida))
    sql += "ORDER BY id_rechazo DESC LIMIT %s"
    params.append(limite)
    with transaccion() as cur:
        cur.execute(sql, params)
        columnas = [d[0] for d in cur.description]
        return [dict(zip(columnas, f)) for f in cur.fetchall()]
