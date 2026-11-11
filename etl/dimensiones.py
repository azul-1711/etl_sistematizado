"""
etl/dimensiones.py   (FD-07)

Mapeo dimensional y asignación de claves subrogadas.

Recibe los datos ya integrados y, dentro de la transacción que le entrega
carga_dw.py (un cursor):
  1) Registra en cada dimensión los elementos nuevos y actualiza los que
     cambiaron. Lo que ya está igual no se toca (sin duplicados):
       dim_institucion       UPSERT por codigo_institucion
       dim_programa          UPSERT por codigo_snies
       dim_ubicacion         departamento + municipio + tipo_cobertura
       dim_area_conocimiento área + nbc + CINE-F (3 niveles)
       dim_modalidad         nivel, formación, modalidad, periodicidades, ciclos
       dim_estado_calidad    estado del programa + reconocimiento
       dim_tiempo            una fila por fecha, con anio, trimestre, mes y
                             dia_semana calculados (no vienen del Excel)
  2) Consulta las claves subrogadas (sk_*) y se las asigna a cada programa.
     dim_tiempo se usa con 3 roles: resolución, ejecutoria y registro SNIES.

Devuelve el DataFrame del hecho (una fila por programa, con sus sk_* y medidas)
y un resumen {dimensión: {'nuevos', 'actualizados'}}.
Si a algún programa le falta una clave obligatoria se lanza un error: la
transacción se revierte y no queda nada a medias.
"""
import pandas as pd
import psycopg2.extras

from etl.limpieza import AUD
from utils.logger import get_logger

log = get_logger(__name__)

DIAS = ["LUNES", "MARTES", "MIÉRCOLES", "JUEVES", "VIERNES", "SÁBADO", "DOMINGO"]
ROLES_TIEMPO = {                      # columna del programa -> columna sk del hecho
    "fecha_resolucion": "sk_tiempo_resolucion",
    "fecha_ejecutoria": "sk_tiempo_ejecutoria",
    "fecha_registro_snies": "sk_tiempo_registro_snies",
}

COLS_INST = ["codigo_institucion", "codigo_ies_padre", "nombre", "estado", "nit",
             "principal_seccional", "naturaleza_juridica", "sector", "caracter_academico",
             "pagina_web", "acreditacion", "fecha_acreditacion", "vigencia_acreditacion",
             "directivo_actual"]
COLS_PROG = ["codigo_snies", "codigo_icfes", "registro_unico", "nombre_programa",
             "titulo_otorgado", "resolucion_aprobacion"]
COLS_UBIC = ["departamento", "municipio", "tipo_cobertura"]
COLS_AREA = ["area_conocimiento", "nbc", "cine_f_amplio", "cine_f_especifico",
             "cine_f_detallado"]
COLS_MODA = ["nivel_academico", "nivel_formacion", "modalidad", "periodicidad",
             "periodicidad_admisiones", "ciclos_propedeuticos"]
COLS_ESTA = ["estado_programa", "reconocimiento_ministerio"]
MEDIDAS = ["indicador_oferta", "creditos", "duracion_periodos", "vigencia_anios",
           "costo_matricula"]


# ---------------------------------------------------------------
# Inserciones
# ---------------------------------------------------------------
def _filas_unicas(df, columnas):
    """Combinaciones distintas de 'columnas', como lista de tuplas."""
    return list(df[columnas].drop_duplicates().itertuples(index=False, name=None))


def _insertar_nuevos(cur, tabla, columnas, filas):
    """INSERT ... ON CONFLICT DO NOTHING. Devuelve cuántas filas eran nuevas."""
    if not filas:
        return 0
    consulta = (f"INSERT INTO {tabla} ({', '.join(columnas)}) VALUES %s "
                f"ON CONFLICT DO NOTHING RETURNING 1")
    nuevas = psycopg2.extras.execute_values(cur, consulta, filas, page_size=2000, fetch=True)
    return len(nuevas)


def _upsert(cur, tabla, columnas, llave, filas):
    """INSERT ... ON CONFLICT (llave) DO UPDATE solo si algo cambió.
    Devuelve (nuevos, actualizados)."""
    if not filas:
        return 0, 0
    cambiables = [c for c in columnas if c != llave]
    asignar = ", ".join(f"{c} = EXCLUDED.{c}" for c in cambiables)
    actuales = ", ".join(f"t.{c}" for c in cambiables)
    nuevos = ", ".join(f"EXCLUDED.{c}" for c in cambiables)
    consulta = (f"INSERT INTO {tabla} AS t ({', '.join(columnas)}) VALUES %s "
                f"ON CONFLICT ({llave}) DO UPDATE SET {asignar} "
                f"WHERE ({actuales}) IS DISTINCT FROM ({nuevos}) "
                f"RETURNING (xmax = 0)")
    tocadas = psycopg2.extras.execute_values(cur, consulta, filas, page_size=2000, fetch=True)
    ins = sum(1 for (nueva,) in tocadas if nueva)
    return ins, len(tocadas) - ins


def _filas_tiempo(programas):
    """Una fila por fecha distinta (de los 3 roles) con sus campos derivados."""
    fechas = set()
    for col in ROLES_TIEMPO:
        fechas.update(f for f in programas[col] if f is not None)
    filas = [(f, f.year, (f.month - 1) // 3 + 1, f.month, DIAS[f.weekday()])
             for f in sorted(fechas)]
    return filas


# ---------------------------------------------------------------
# Consulta de claves subrogadas
# ---------------------------------------------------------------
def _mapa_sk(cur, tabla, sk, columnas):
    """{(valores de columnas): sk} de una dimensión."""
    cur.execute(f"SELECT {sk}, {', '.join(columnas)} FROM {tabla}")
    return {tuple(f[1:]): f[0] for f in cur.fetchall()}


def _asignar(df, columnas, mapa):
    """Lista con la sk de cada fila (None si la combinación no existe)."""
    return [mapa.get(tuple(f)) for f in df[columnas].itertuples(index=False, name=None)]


# ---------------------------------------------------------------
# Etapa completa
# ---------------------------------------------------------------
def cargar_dimensiones(cur, instituciones, programas, id_corrida=None):
    """Registra los elementos dimensionales y devuelve (hecho, resumen).
    Debe ejecutarse dentro de una transacción (cur)."""
    resumen = {}

    # --- dimensiones con UPSERT (tienen llave de negocio única) ---
    filas = list(instituciones[COLS_INST].itertuples(index=False, name=None))
    resumen["dim_institucion"] = dict(zip(
        ("nuevos", "actualizados"),
        _upsert(cur, "dw.dim_institucion", COLS_INST, "codigo_institucion", filas)))

    filas = list(programas[COLS_PROG].itertuples(index=False, name=None))
    resumen["dim_programa"] = dict(zip(
        ("nuevos", "actualizados"),
        _upsert(cur, "dw.dim_programa", COLS_PROG, "codigo_snies", filas)))

    # --- dimensiones por combinación de atributos (solo se agregan las nuevas) ---
    for tabla, columnas in (("dim_ubicacion", COLS_UBIC), ("dim_area_conocimiento", COLS_AREA),
                            ("dim_modalidad", COLS_MODA), ("dim_estado_calidad", COLS_ESTA)):
        nuevos = _insertar_nuevos(cur, f"dw.{tabla}", columnas,
                                  _filas_unicas(programas, columnas))
        resumen[tabla] = {"nuevos": nuevos, "actualizados": 0}

    filas_tiempo = _filas_tiempo(programas)
    nuevos = _insertar_nuevos(cur, "dw.dim_tiempo",
                              ["fecha", "anio", "trimestre", "mes", "dia_semana"], filas_tiempo)
    resumen["dim_tiempo"] = {"nuevos": nuevos, "actualizados": 0}
    if nuevos:                                  # campos derivados: no vienen del Excel
        AUD.reg("dim_tiempo", "anio/trimestre/mes/dia_semana", "DERIVADOS_DE_FECHA",
                None, "calculados a partir de fecha")
        if id_corrida:
            AUD.volcar(id_corrida)

    # --- claves subrogadas ---
    sk_inst = _mapa_sk(cur, "dw.dim_institucion", "sk_institucion", ["codigo_institucion"])
    sk_prog = _mapa_sk(cur, "dw.dim_programa", "sk_programa", ["codigo_snies"])
    sk_ubic = _mapa_sk(cur, "dw.dim_ubicacion", "sk_ubicacion", COLS_UBIC)
    sk_area = _mapa_sk(cur, "dw.dim_area_conocimiento", "sk_area_conocimiento", COLS_AREA)
    sk_moda = _mapa_sk(cur, "dw.dim_modalidad", "sk_modalidad", COLS_MODA)
    sk_esta = _mapa_sk(cur, "dw.dim_estado_calidad", "sk_estado_calidad", COLS_ESTA)
    cur.execute("SELECT sk_tiempo, fecha FROM dw.dim_tiempo")
    sk_tiempo = {f: sk for sk, f in cur.fetchall()}

    hecho = pd.DataFrame({
        "codigo_snies": programas["codigo_snies"].values,
        "sk_institucion": _asignar(programas, ["codigo_institucion"], sk_inst),
        "sk_ubicacion": _asignar(programas, COLS_UBIC, sk_ubic),
        "sk_area_conocimiento": _asignar(programas, COLS_AREA, sk_area),
        "sk_modalidad": _asignar(programas, COLS_MODA, sk_moda),
        "sk_programa": _asignar(programas, ["codigo_snies"], sk_prog),
        "sk_estado_calidad": _asignar(programas, COLS_ESTA, sk_esta),
    }, dtype=object)
    for col, sk_col in ROLES_TIEMPO.items():          # los 3 roles de dim_tiempo
        hecho[sk_col] = pd.Series(
            [sk_tiempo.get(f) if f is not None else None for f in programas[col]],
            index=hecho.index, dtype=object)       # object: conserva enteros y None
    for medida in MEDIDAS:
        hecho[medida] = programas[medida].values

    # Las 6 claves obligatorias no pueden quedar vacías
    obligatorias = ["sk_institucion", "sk_ubicacion", "sk_area_conocimiento",
                    "sk_modalidad", "sk_programa", "sk_estado_calidad"]
    faltan = hecho[obligatorias].isna().any(axis=1)
    if faltan.any():
        ejemplo = hecho.loc[faltan, "codigo_snies"].head(5).tolist()
        raise ValueError(f"{int(faltan.sum())} programas quedaron sin alguna clave "
                         f"subrogada obligatoria (ej. SNIES {ejemplo}).")

    log.info("Dimensiones: %s", {k: v for k, v in resumen.items()})
    return hecho.drop(columns=["codigo_snies"]), resumen