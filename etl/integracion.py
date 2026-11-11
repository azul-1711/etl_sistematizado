"""
etl/integracion.py   (FD-06)

Unifica la información de las distintas fuentes para el modelo dimensional:

  1) Instituciones + Directivos -> 'directivo_actual' de dim_institucion.
     Se toma el directivo vigente de cada institución. Si hay varios se prioriza
     el Rector; luego otros cargos que empiezan por Rector (menos el suplente);
     luego cualquier otro cargo. Entre cargos iguales gana el que empezó más
     recientemente. Sin directivo vigente -> 'NO APLICA'.
     Vigente = su periodo ya empezó y no ha terminado (sin fecha 'hasta', o con
     fecha 'hasta' que aún no llega). Una fecha 'hasta' escrita pero ilegible
     se considera periodo terminado.

  2) Programas + Cobertura -> 'tipo_cobertura' de cada programa, cruzando por
     código SNIES + departamento + municipio (comparados sin tildes ni
     mayúsculas). Un programa puede tener varios tipos en el mismo lugar: se
     elige el de mayor prioridad (Principal primero, orden del catálogo).
     Sin coincidencia -> 'NO INFORMADO', porque no todos los programas tienen
     registro de cobertura.

Recibe los DataFrames válidos (salida de validacion) y devuelve:
  instituciones : con la columna directivo_actual
  programas     : con la columna tipo_cobertura
y un resumen con conteos. No escribe nada en la base salvo la auditoría.
"""
from datetime import date

import pandas as pd

from config.catalogos import CATALOGOS
from config.settings import NO_INFORMADO
from etl import limpieza as L
from etl.limpieza import AUD
from utils.logger import get_logger

log = get_logger(__name__)

SIN_DIRECTIVO = "NO APLICA"
PRIORIDAD_TIPO = {t: i for i, t in enumerate(CATALOGOS["tipo_cobertura"])}


# ---------------------------------------------------------------
# 1) Directivo actual de cada institución
# ---------------------------------------------------------------
def _prioridad_cargo(cargo):
    cargo = cargo or ""
    if cargo == "RECTOR":
        return 1
    if cargo.startswith("RECTOR") and "SUPLENTE" not in cargo:
        return 2
    return 3


def _es_vigente(fila, hoy):
    """Periodo ya iniciado y no terminado."""
    desde, hasta = fila["fecha_desde"], fila["fecha_hasta"]
    if fila["hasta_informada"] and (hasta is None or hasta < hoy):
        return False
    if desde is not None and desde > hoy:
        return False
    return True


def directivo_vigente_por_institucion(directivos, hoy=None):
    """{codigo_institucion: 'NOMBRE APELLIDO'} del directivo vigente prioritario."""
    hoy = hoy or date.today()
    mejor = {}
    for i, fila in enumerate(directivos.to_dict("records")):
        if not _es_vigente(fila, hoy):
            continue
        persona = " ".join(p for p in (fila["nombre"], fila["apellido"]) if p)
        if not persona:
            continue
        desde = fila["fecha_desde"]
        orden = (_prioridad_cargo(fila["cargo"]),
                 0 if desde else 1,
                 -desde.toordinal() if desde else 0,
                 i)
        cod = fila["codigo_institucion"]
        if cod not in mejor or orden < mejor[cod][0]:
            mejor[cod] = (orden, persona)
    return {cod: persona for cod, (_, persona) in mejor.items()}


def asignar_directivo_actual(instituciones, directivos):
    vigentes = directivo_vigente_por_institucion(directivos)
    out = instituciones.copy()
    valores = []
    for cod in out["codigo_institucion"]:
        valor = vigentes.get(cod, SIN_DIRECTIVO)
        AUD.reg("dim_institucion", "directivo_actual", "CRUCE_DIRECTIVOS_RECTOR_VIGENTE",
                "(sin cruce)", valor)
        valores.append(valor)
    out["directivo_actual"] = valores
    con_directivo = sum(1 for v in valores if v != SIN_DIRECTIVO)
    return out, con_directivo


# ---------------------------------------------------------------
# 2) Tipo de cobertura de cada programa
# ---------------------------------------------------------------
def _con_claves(df):
    """Agrega las columnas de comparación (sin tildes ni mayúsculas)."""
    out = df.copy()
    for col, clave in (("departamento", "k_dep"), ("municipio", "k_mun")):
        cache = {v: L._clave(v) for v in out[col].unique()}
        out[clave] = out[col].map(cache)
    return out


def asignar_tipo_cobertura(programas, cobertura):
    cob = _con_claves(cobertura)
    cob["prioridad"] = cob["tipo_cobertura"].map(PRIORIDAD_TIPO).fillna(99)
    cob = (cob.sort_values("prioridad", kind="stable")
              .drop_duplicates(subset=["codigo_snies", "k_dep", "k_mun"], keep="first")
              [["codigo_snies", "k_dep", "k_mun", "tipo_cobertura"]])

    prog = _con_claves(programas)
    unido = prog.merge(cob, on=["codigo_snies", "k_dep", "k_mun"], how="left",
                       suffixes=("", "_cob"), validate="many_to_one")
    sin_cruce = unido["tipo_cobertura"].isna()
    unido["tipo_cobertura"] = unido["tipo_cobertura"].where(~sin_cruce, NO_INFORMADO)
    unido = unido.drop(columns=["k_dep", "k_mun"])

    # Auditoría: cuántos programas no encontraron cobertura
    for _ in range(int(sin_cruce.sum())):
        AUD.reg("dim_ubicacion", "tipo_cobertura",
                "CRUCE_COBERTURA_SIN_COINCIDENCIA_A_NO_INFORMADO", None, NO_INFORMADO)
    return unido.astype(object), int((~sin_cruce).sum())


# ---------------------------------------------------------------
# Etapa completa
# ---------------------------------------------------------------
def integrar_todo(id_corrida, validos):
    """validos: salida de validacion.validar_todo (y de homologacion_geo si aplica).
    Devuelve ({'instituciones','programas'}, resumen)."""
    AUD.datos = {}
    instituciones, con_directivo = asignar_directivo_actual(
        validos["instituciones"], validos["directivos"])
    programas, con_cobertura = asignar_tipo_cobertura(
        validos["programas"], validos["cobertura"])
    AUD.volcar(id_corrida)

    resumen = {
        "instituciones": len(instituciones),
        "instituciones_con_directivo": con_directivo,
        "instituciones_sin_directivo": len(instituciones) - con_directivo,
        "programas": len(programas),
        "programas_con_cobertura": con_cobertura,
        "programas_sin_cobertura": len(programas) - con_cobertura,
    }
    log.info("Integración: %s", resumen)
    return {"instituciones": instituciones, "programas": programas}, resumen
