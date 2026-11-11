"""
etl/transformacion.py   (FD-04, FD-05)

FD-04  Conversión de tipos y derivación de columnas.
FD-05  Limpieza y estandarización (TRIM, mayúsculas, catálogos cerrados,
       fechas, números, caracteres dañados, vacíos -> NO INFORMADO).

Lee Staging (datos casi crudos), aplica las reglas 3.2 con las funciones de
etl/limpieza.py y entrega DataFrames ya limpios y con sus tipos finales
(texto, int, Decimal, date). NO rechaza filas: lo que no se puede convertir
queda en None y la validación (etl/validacion.py) decide qué se rechaza.
Cada fila conserva su 'id_carga' de Staging para poder rastrearla.

Cada cambio queda auditado en staging.auditoria_transformacion (campo, regla,
cuántos valores cambió y un ejemplo). Como Staging es persistente, esta etapa
se puede repetir completa al reanudar una corrida (FA-02) sin efectos
secundarios: lo único que escribe es la auditoría de la corrida, y antes la
borra para no duplicarla.

Funciones principales:
    transformar_todo(id_corrida)  -> {'instituciones','directivos','programas','cobertura'}
    leer_staging()                -> DataFrames crudos de las 4 tablas de Staging
"""
from datetime import date
from decimal import Decimal

import pandas as pd

from config.settings import NO_INFORMADO
from db.conexion import transaccion
from etl import limpieza as L
from etl.limpieza import AUD, T, T_nombre, T_texto, T_texto_info, T_vigencia
from utils.logger import get_logger

log = get_logger(__name__)

# Tablas de Staging por nombre de fuente
TABLAS = {
    "instituciones": "staging.instituciones_raw",
    "directivos": "staging.directivos_raw",
    "programas": "staging.programas_raw",
    "cobertura": "staging.cobertura_raw",
}

# Nombres con que se audita cada grupo de campos (tabla destino del modelo)
D_INST = "dim_institucion"
D_UBIC = "dim_ubicacion"
D_AREA = "dim_area_conocimiento"
D_MODA = "dim_modalidad"
D_ESTA = "dim_estado_calidad"
D_PROG = "dim_programa"
D_HECHO = "hecho_oferta_programa"


# ---------------------------------------------------------------
# Lectura de Staging
# ---------------------------------------------------------------
def leer_tabla(tabla):
    """Una tabla de Staging completa como DataFrame (columnas de texto)."""
    with transaccion() as cur:
        cur.execute(f"SELECT * FROM {tabla} ORDER BY id_carga")
        columnas = [d[0] for d in cur.description]
        filas = cur.fetchall()
    return pd.DataFrame(filas, columns=columnas).astype(object)


def leer_staging():
    """Devuelve {'instituciones','directivos','programas','cobertura'} crudos."""
    return {nombre: leer_tabla(tabla) for nombre, tabla in TABLAS.items()}


def leer_filas_crudas(nombre, ids):
    """Filas crudas de Staging por id_carga (para guardar la fila completa en rechazos)."""
    ids = [int(i) for i in ids]
    if not ids:
        return {}
    with transaccion() as cur:
        cur.execute(f"SELECT * FROM {TABLAS[nombre]} WHERE id_carga = ANY(%s)", (ids,))
        columnas = [d[0] for d in cur.description]
        return {f[0]: dict(zip(columnas, f)) for f in cur.fetchall()}


# ---------------------------------------------------------------
# Conversión de tipos (texto limpio -> tipo final)
# ---------------------------------------------------------------
def _a_date(texto_iso):
    return date.fromisoformat(texto_iso) if texto_iso else None


def _a_int(texto):
    return int(texto) if texto is not None else None


def _a_decimal(texto):
    return Decimal(texto) if texto is not None else None


def _texto_o_no_informado(valor):
    """Texto que no es de catálogo (resolución): TRIM y vacío -> NO INFORMADO."""
    return L._codigo(valor) or NO_INFORMADO


# ---------------------------------------------------------------
# Instituciones  ->  dim_institucion
# ---------------------------------------------------------------
def transformar_instituciones(df):
    filas = []
    for r in df.to_dict("records"):
        cod = L._codigo(r["codigo_institucion"])
        T(D_INST, "codigo_institucion", "TRIM", r["codigo_institucion"], cod)
        padre = T(D_INST, "codigo_ies_padre", "TRIM_VACIO_A_NULL",
                  r["codigo_ies_padre"], L._codigo(r["codigo_ies_padre"]))
        nit = T(D_INST, "nit", "SOLO_DIGITOS_SIN_PUNTOS_GUIONES_ESPACIOS_O_NO_INFORMADO",
                r["nit"], L.limpiar_nit(r["nit"]) or NO_INFORMADO)
        fecha_acred = T(D_INST, "fecha_acreditacion", "FECHA_A_YYYY_MM_DD",
                        r["fecha_acreditacion"], L.limpiar_fecha(r["fecha_acreditacion"]))
        vigencia = T(D_INST, "vigencia_acreditacion", "TEXTO_A_ENTERO",
                     r["vigencia_acreditacion"], L.a_entero(r["vigencia_acreditacion"]))
        filas.append({
            "id_carga": r["id_carga"],
            "codigo_institucion": cod,
            "codigo_ies_padre": padre,
            "nombre": T_nombre(D_INST, "nombre", r["nombre"]),
            "estado": L.homologar_catalogo(D_INST, "estado", r["estado"]),
            "nit": nit,
            "principal_seccional": L.homologar_catalogo(
                D_INST, "principal_seccional", r["principal_seccional"]),
            "naturaleza_juridica": L.homologar_catalogo(
                D_INST, "naturaleza_juridica", r["naturaleza_juridica"]),
            "sector": L.homologar_catalogo(D_INST, "sector", r["sector"]),
            "caracter_academico": L.homologar_catalogo(
                D_INST, "caracter_academico", r["caracter_academico"]),
            "pagina_web": T(D_INST, "pagina_web", "URL_INVALIDA_A_NULL_O_PREFIJO_HTTP",
                            r["pagina_web"], L.limpiar_url(r["pagina_web"])),
            "acreditacion": T(D_INST, "acreditacion", "HOMOLOGACION_SI_NO",
                              r["acreditacion"], L.homologar_si_no(r["acreditacion"])),
            "fecha_acreditacion": _a_date(fecha_acred),
            "vigencia_acreditacion": _a_int(vigencia),
        })
    return pd.DataFrame(filas, dtype=object)


# ---------------------------------------------------------------
# Directivos  ->  insumo del cruce 'directivo_actual'
# ---------------------------------------------------------------
def transformar_directivos(df):
    filas = []
    for r in df.to_dict("records"):
        # Aquí el nombre vacío queda en None (no 'NO INFORMADO'): así el cruce
        # puede ignorar directivos sin nombre en vez de mostrar 'NO INFORMADO'.
        desde = L.limpiar_fecha(r["fecha_desde"])
        hasta = L.limpiar_fecha(r["fecha_hasta"])
        filas.append({
            "id_carga": r["id_carga"],
            "codigo_institucion": L._codigo(r["codigo_institucion"]),
            "cargo": L.limpiar_nombre(r["cargo"], default=None),
            "nombre": L.limpiar_nombre(r["nombre"], default=None),
            "apellido": L.limpiar_nombre(r["apellido"], default=None),
            "fecha_desde": _a_date(desde),
            "fecha_hasta": _a_date(hasta),
            # Si la fecha 'hasta' venía escrita pero no se pudo leer, el periodo
            # no se considera vigente (igual que en el ETL anterior).
            "hasta_informada": L._trim(r["fecha_hasta"]) is not None,
        })
    return pd.DataFrame(filas, dtype=object)


# ---------------------------------------------------------------
# Programas  ->  dim_programa, dim_ubicacion, dim_area_conocimiento,
#                dim_modalidad, dim_estado_calidad, hecho_oferta_programa
# ---------------------------------------------------------------
def transformar_programas(df):
    filas = []
    for r in df.to_dict("records"):
        # t(): aplica una función de limpieza y deja la regla en la auditoría
        def t(tabla, campo, regla, col, fn):
            return T(tabla, campo, regla, r[col], fn(r[col]))

        snies = t(D_PROG, "codigo_snies", "SOLO_DIGITOS_ENTERO_NO_NULO",
                  "codigo_snies", L.a_codigo_snies)
        inst = t(D_HECHO, "codigo_institucion", "TRIM", "codigo_institucion", L._codigo)

        f_resol = t(D_HECHO, "fecha_resolucion", "FECHA_A_YYYY_MM_DD",
                    "fecha_resolucion", L.limpiar_fecha)
        f_ejec = t(D_HECHO, "fecha_ejecutoria", "FECHA_A_YYYY_MM_DD",
                   "fecha_ejecutoria", L.limpiar_fecha)
        f_reg = t(D_HECHO, "fecha_registro_snies", "FECHA_A_YYYY_MM_DD",
                  "fecha_registro_snies", L.limpiar_fecha)
        creditos = t(D_HECHO, "creditos", "TEXTO_A_ENTERO_NO_CONVERTIBLE_A_NULL",
                     "creditos", L.a_entero)
        duracion = t(D_HECHO, "duracion_periodos", "TEXTO_A_ENTERO_MAYOR_A_CERO",
                     "duracion_periodos", lambda x: L.a_entero(x, minimo=1))
        costo = t(D_HECHO, "costo_matricula", "LIMPIAR_SIMBOLOS_COMAS_A_DECIMAL",
                  "costo_matricula", L.a_moneda)

        filas.append({
            "id_carga": r["id_carga"],
            # dim_programa
            "codigo_snies": snies,
            "codigo_icfes": t(D_PROG, "codigo_icfes", "TRIM_VACIO_A_NULL",
                              "codigo_icfes", L._codigo),
            "registro_unico": t(D_PROG, "registro_unico", "TRIM_VACIO_A_NULL",
                                "registro_unico", L._codigo),
            "nombre_programa": T_nombre(D_PROG, "nombre_programa", r["nombre_programa"]),
            "titulo_otorgado": T_nombre(D_PROG, "titulo_otorgado", r["titulo_otorgado"]),
            "resolucion_aprobacion": t(D_PROG, "resolucion_aprobacion",
                                       "TRIM_VACIO_A_NO_INFORMADO",
                                       "resolucion_aprobacion", _texto_o_no_informado),
            # llave hacia dim_institucion
            "codigo_institucion": inst,
            # dim_ubicacion (FA-01 puede afinar luego departamento y municipio)
            "departamento": L.normalizar_ubicacion(
                D_UBIC, "departamento", r["departamento_oferta"]),
            "municipio": L.normalizar_ubicacion(
                D_UBIC, "municipio", r["municipio_oferta"]),
            # dim_area_conocimiento
            "area_conocimiento": T_texto_info(D_AREA, "area_conocimiento", r["area_conocimiento"]),
            "nbc": T_texto(D_AREA, "nbc", r["nbc"]),
            "cine_f_amplio": T_texto_info(D_AREA, "cine_f_amplio", r["cine_f_amplio"]),
            "cine_f_especifico": T_texto_info(D_AREA, "cine_f_especifico", r["cine_f_especifico"]),
            "cine_f_detallado": T_texto_info(D_AREA, "cine_f_detallado", r["cine_f_detallado"]),
            # dim_modalidad
            "nivel_academico": L.homologar_catalogo(D_MODA, "nivel_academico", r["nivel_academico"]),
            "nivel_formacion": L.homologar_catalogo(D_MODA, "nivel_formacion", r["nivel_formacion"]),
            "modalidad": L.homologar_catalogo(D_MODA, "modalidad", r["modalidad"]),
            "periodicidad": L.homologar_catalogo(D_MODA, "periodicidad", r["periodicidad"]),
            "periodicidad_admisiones": L.homologar_catalogo(
                D_MODA, "periodicidad_admisiones", r["periodicidad_admisiones"], "periodicidad"),
            "ciclos_propedeuticos": t(D_MODA, "ciclos_propedeuticos", "HOMOLOGACION_SI_NO",
                                      "ciclos_propedeuticos", L.homologar_si_no),
            # dim_estado_calidad (reconocimiento NO es SI/NO: trae Registro calificado,
            # Acreditación de alta calidad, etc., y así lo guarda el DDL)
            "estado_programa": L.homologar_catalogo(
                D_ESTA, "estado_programa", r["estado_programa"]),
            "reconocimiento_ministerio": T_texto(
                D_ESTA, "reconocimiento_ministerio", r["reconocimiento_ministerio"]),
            # hecho_oferta_programa: fechas de los 3 roles de dim_tiempo y medidas
            "fecha_resolucion": _a_date(f_resol),
            "fecha_ejecutoria": _a_date(f_ejec),
            "fecha_registro_snies": _a_date(f_reg),
            "creditos": _a_int(creditos),
            "duracion_periodos": _a_int(duracion),
            "vigencia_anios": _a_decimal(
                T_vigencia(D_HECHO, "vigencia_anios", r["vigencia_anios"])),
            "costo_matricula": _a_decimal(costo),
            "indicador_oferta": 1,            # derivado: medida constante
        })

    # Derivado del ETL, no viene del Excel: se audita una sola vez con su cantidad
    if filas:
        AUD.datos[(D_HECHO, "indicador_oferta", "CONSTANTE_1_DERIVADA_DEL_ETL")] = \
            [len(filas), None, "1"]
    return pd.DataFrame(filas, dtype=object)


# ---------------------------------------------------------------
# Cobertura  ->  insumo del cruce 'tipo_cobertura' de dim_ubicacion
# ---------------------------------------------------------------
def transformar_cobertura(df):
    filas = []
    for r in df.to_dict("records"):
        filas.append({
            "id_carga": r["id_carga"],
            "codigo_snies": T("cobertura", "codigo_snies", "SOLO_DIGITOS_ENTERO_NO_NULO",
                              r["codigo_snies"], L.a_codigo_snies(r["codigo_snies"])),
            "departamento": L.normalizar_ubicacion(
                "cobertura", "departamento", r["departamento"]),
            "municipio": L.normalizar_ubicacion("cobertura", "municipio", r["municipio"]),
            "tipo_cobertura": L.homologar_catalogo(
                D_UBIC, "tipo_cobertura", r["tipo_cobertura"]),
        })
    return pd.DataFrame(filas, dtype=object)


# ---------------------------------------------------------------
# Orquestación de la etapa
# ---------------------------------------------------------------
def _borrar_auditoria_previa(id_corrida):
    """Al repetir la etapa (reanudación) la auditoría de la corrida se rehace."""
    with transaccion() as cur:
        cur.execute("DELETE FROM staging.auditoria_transformacion WHERE id_corrida = %s",
                    (str(id_corrida),))


def transformar_todo(id_corrida, crudos=None):
    """Transforma las 4 fuentes desde Staging.
    Devuelve {'instituciones','directivos','programas','cobertura'} limpios.
    La auditoría de transformaciones queda guardada con el id de la corrida."""
    _borrar_auditoria_previa(id_corrida)
    AUD.datos = {}
    crudos = crudos or leer_staging()
    vacias = [nombre for nombre, df in crudos.items() if df.empty]
    if vacias:
        raise ValueError("Staging no tiene datos de: " + ", ".join(vacias) +
                         ". Cargue los archivos a Staging antes de transformar.")

    # Vocabulario para reparar caracteres dañados (DISE¿O -> DISEÑO)
    L.fijar_vocabulario(L.construir_vocabulario(
        crudos["programas"]["nombre_programa"], crudos["programas"]["titulo_otorgado"],
        crudos["instituciones"]["nombre"], crudos["directivos"]["nombre"],
        crudos["directivos"]["apellido"]))

    resultado = {
        "instituciones": transformar_instituciones(crudos["instituciones"]),
        "directivos": transformar_directivos(crudos["directivos"]),
        "programas": transformar_programas(crudos["programas"]),
        "cobertura": transformar_cobertura(crudos["cobertura"]),
    }
    AUD.volcar(id_corrida)
    log.info("Transformación lista: %s",
             ", ".join(f"{k} {len(v)}" for k, v in resultado.items()))
    return resultado
