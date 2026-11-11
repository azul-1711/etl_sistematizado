"""
etl/verificacion.py   (FC-01)
Verifica los archivos SNIES antes de procesar: que existan, que se puedan abrir,
que tengan las hojas esperadas y las columnas requeridas. Solo lee los
encabezados, por eso es rápido aun con archivos grandes.
"""
import warnings
from pathlib import Path

import pandas as pd
from openpyxl import load_workbook

from config.fuentes import fuentes_ordenadas
from config.settings import DATA_ENTRADA

warnings.filterwarnings("ignore", category=UserWarning, module="openpyxl")
EXTENSIONES_VALIDAS = {".xlsx", ".xlsm", ".csv"}


def _encabezados_excel(ruta):
    """Devuelve {hoja: [columnas]} leyendo solo la primera fila de cada hoja."""
    wb = load_workbook(ruta, read_only=True)
    try:
        resultado = {}
        for ws in wb.worksheets:
            primera = next(ws.iter_rows(min_row=1, max_row=1, values_only=True), ())
            resultado[ws.title] = [str(c).strip() for c in primera if c is not None]
        return resultado
    finally:
        wb.close()


def verificar_fuentes(carpeta=DATA_ENTRADA):
    """Revisa todas las fuentes configuradas.
    Devuelve {'ok': bool, 'problemas': [texto], 'detalle': [dict por fuente]}.
    Si 'ok' es False el ETL no debe continuar."""
    carpeta = Path(carpeta)
    problemas, detalle = [], []
    cache = {}   # un archivo se abre una sola vez aunque tenga varias hojas

    for fuente in fuentes_ordenadas():
        nombre, archivo = fuente["nombre"], fuente["archivo"]
        ruta = carpeta / archivo
        estado = {"fuente": nombre, "archivo": archivo, "hoja": fuente["hoja"],
                  "ok": False, "motivo": ""}

        if not ruta.exists():
            estado["motivo"] = f"No se encontró el archivo {archivo}."
        elif ruta.suffix.lower() not in EXTENSIONES_VALIDAS:
            estado["motivo"] = f"Formato no compatible ({ruta.suffix}). Use Excel (.xlsx) o CSV."
        else:
            try:
                if ruta.suffix.lower() == ".csv":
                    columnas = {fuente["hoja"]: list(pd.read_csv(ruta, nrows=0).columns)}
                else:
                    if ruta not in cache:
                        cache[ruta] = _encabezados_excel(ruta)
                    columnas = cache[ruta]

                hoja = fuente["hoja"]
                if hoja not in columnas:
                    estado["motivo"] = f"El archivo {archivo} no tiene la hoja '{hoja}'."
                else:
                    faltan = [c for c in fuente["columnas"] if c not in columnas[hoja]]
                    if faltan:
                        estado["motivo"] = (f"En {archivo} / {hoja} faltan las columnas: "
                                            + ", ".join(faltan))
                    else:
                        estado["ok"] = True
            except Exception as exc:
                estado["motivo"] = f"No se pudo leer {archivo}: {exc}"

        if not estado["ok"]:
            problemas.append(estado["motivo"])
        detalle.append(estado)

    return {"ok": not problemas, "problemas": problemas, "detalle": detalle}
