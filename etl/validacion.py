"""
etl/validacion.py   (FD-08, apoya FC-06)

Comprueba las reglas de calidad e integridad sobre los datos ya transformados
y separa:
  válidos    -> continúan a integración y carga a la bodega
  rechazados -> se guardan en staging.rechazos_etl con la FILA COMPLETA tal como
                estaba en Staging (JSON) y el motivo, para consultarlos en la app

Reglas:
  Instituciones : código obligatorio y numérico; sin repetidos tras la limpieza.
  Directivos    : código de institución numérico y que exista en Instituciones.
  Programas     : código SNIES entero y no nulo; código de institución obligatorio,
                  numérico y existente en Instituciones; sin SNIES repetidos.
  Cobertura     : código SNIES entero y no nulo (aquí cae la fila 'NOTA:') y que el
                  programa exista entre los programas válidos.

Los valores que no se pueden convertir pero no son llave (créditos, costo, fechas)
NO rechazan la fila: quedan en NULL, según las reglas 3.2.
Todos los motivos empiezan con '[Validación]' para distinguirlos de los de Staging.
Repetir la etapa (reanudación) borra primero los rechazos de validación de la
corrida, así no quedan duplicados.
"""
import pandas as pd

from db import rechazos
from etl.transformacion import TABLAS, leer_filas_crudas
from utils.logger import get_logger

log = get_logger(__name__)

PREFIJO = "[Validación] "

M_INST_VACIO = PREFIJO + "Código de institución vacío"
M_INST_NO_NUM = (PREFIJO + "Código de institución no numérico "
                 "(puede ser una nota al pie del archivo)")
M_INST_REPETIDO = PREFIJO + "Código de institución repetido tras la limpieza; se conservó la primera fila"
M_DIR_SIN_INST = PREFIJO + "La institución del directivo no existe en Instituciones"
M_SNIES_INVALIDO = PREFIJO + "Código SNIES vacío o no numérico"
M_SNIES_REPETIDO = PREFIJO + "Código SNIES repetido tras la limpieza; se conservó la primera fila"
M_PROG_SIN_INST = PREFIJO + "La institución del programa no existe en Instituciones"
M_COB_SIN_PROG = PREFIJO + "El programa de la cobertura no existe entre los programas válidos"


def _vacio(serie):
    return serie.isna() | (serie.astype(str).str.strip() == "")


def _numerico(serie):
    return serie.astype(str).str.fullmatch(r"\d+").fillna(False)


def _asignar(motivos, mascara, texto):
    """Pone el motivo solo a las filas que aún no tienen uno (gana la primera regla)."""
    libres = motivos.isna() & mascara
    motivos[libres] = texto


def _marcar_codigo_institucion(df, motivos, columna="codigo_institucion"):
    _asignar(motivos, _vacio(df[columna]), M_INST_VACIO)
    _asignar(motivos, ~_numerico(df[columna]), M_INST_NO_NUM)


def _separar(df, motivos):
    return df[motivos.isna()].reset_index(drop=True), df[motivos.notna()]


def _guardar_rechazos(id_corrida, nombre, rechazadas, motivos):
    """Guarda las filas rechazadas (fila cruda de Staging + motivo)."""
    if rechazadas.empty:
        return 0
    crudas = leer_filas_crudas(nombre, rechazadas["id_carga"].tolist())
    items = []
    for _, fila in rechazadas.iterrows():
        cruda = crudas.get(int(fila["id_carga"]), {})
        items.append((motivos[fila.name], cruda,
                      cruda.get("codigo_snies"), cruda.get("codigo_institucion")))
    return rechazos.registrar_lote(id_corrida, TABLAS[nombre], items)


def _resumen(df_entrada, validos, rechazadas):
    return {"entrada": len(df_entrada), "validos": len(validos), "rechazados": len(rechazadas)}


def validar_todo(id_corrida, datos):
    """Valida las 4 fuentes transformadas.
    datos: salida de transformacion.transformar_todo.
    Devuelve (validos, resumen):
      validos : {'instituciones','directivos','programas','cobertura'} (DataFrames)
      resumen : {fuente: {'entrada','validos','rechazados'}} (+ 'duplicados_descartados'
                en cobertura)"""
    rechazos.eliminar_por_prefijo(id_corrida, PREFIJO)
    validos, resumen = {}, {}

    # ---------------- Instituciones ----------------
    df = datos["instituciones"]
    motivos = pd.Series(None, index=df.index, dtype=object)
    _marcar_codigo_institucion(df, motivos)
    _asignar(motivos, df["codigo_institucion"].duplicated(keep="first"), M_INST_REPETIDO)
    validos["instituciones"], rech = _separar(df, motivos)
    _guardar_rechazos(id_corrida, "instituciones", rech, motivos)
    resumen["Instituciones"] = _resumen(df, validos["instituciones"], rech)
    codigos_inst = set(validos["instituciones"]["codigo_institucion"])

    # ---------------- Directivos ----------------
    df = datos["directivos"]
    motivos = pd.Series(None, index=df.index, dtype=object)
    _marcar_codigo_institucion(df, motivos)
    _asignar(motivos, ~df["codigo_institucion"].isin(codigos_inst), M_DIR_SIN_INST)
    validos["directivos"], rech = _separar(df, motivos)
    _guardar_rechazos(id_corrida, "directivos", rech, motivos)
    resumen["Directivos"] = _resumen(df, validos["directivos"], rech)

    # ---------------- Programas ----------------
    df = datos["programas"]
    motivos = pd.Series(None, index=df.index, dtype=object)
    _asignar(motivos, _vacio(df["codigo_snies"]), M_SNIES_INVALIDO)
    _marcar_codigo_institucion(df, motivos)
    _asignar(motivos, ~df["codigo_institucion"].isin(codigos_inst), M_PROG_SIN_INST)
    repetidos = df["codigo_snies"].notna() & df["codigo_snies"].duplicated(keep="first")
    _asignar(motivos, repetidos, M_SNIES_REPETIDO)
    validos["programas"], rech = _separar(df, motivos)
    _guardar_rechazos(id_corrida, "programas", rech, motivos)
    resumen["Programas"] = _resumen(df, validos["programas"], rech)
    codigos_snies = set(validos["programas"]["codigo_snies"])

    # ---------------- Cobertura ----------------
    df = datos["cobertura"]
    motivos = pd.Series(None, index=df.index, dtype=object)
    _asignar(motivos, _vacio(df["codigo_snies"]), M_SNIES_INVALIDO)
    _asignar(motivos, ~df["codigo_snies"].isin(codigos_snies), M_COB_SIN_PROG)
    ok, rech = _separar(df, motivos)
    _guardar_rechazos(id_corrida, "cobertura", rech, motivos)
    # Filas iguales una vez limpiadas (mismo programa, lugar y tipo): no son error,
    # simplemente se descartan.
    llave = ["codigo_snies", "departamento", "municipio", "tipo_cobertura"]
    sin_repetir = ok.drop_duplicates(subset=llave, keep="first").reset_index(drop=True)
    validos["cobertura"] = sin_repetir
    resumen["Cobertura"] = _resumen(df, sin_repetir, rech)
    resumen["Cobertura"]["duplicados_descartados"] = len(ok) - len(sin_repetir)

    for fuente, r in resumen.items():
        log.info("Validación %s: entrada %d, válidos %d, rechazados %d",
                 fuente, r["entrada"], r["validos"], r["rechazados"])
    return validos, resumen
