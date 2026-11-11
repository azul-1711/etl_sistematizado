"""
config/fuentes.py
Configuración centralizada de las fuentes SNIES (FC-03).
El orquestador recorre esta lista (ForEach): para agregar una fuente nueva
basta con agregar una entrada, sin tocar el resto del código.

Cada fuente define:
  nombre         : identificador para bitácora y mensajes
  archivo        : nombre del archivo dentro de data/entrada
  hoja           : hoja de Excel a leer
  tabla_staging  : tabla destino en staging
  clave          : columnas de staging que identifican un registro (UPSERT)
  columnas       : {columna del Excel: columna en staging}
                   (también sirve como lista de columnas requeridas, FC-01)
  orden          : orden de ejecución dentro del ETL
"""

FUENTES = [
    {
        "nombre": "Instituciones",
        "archivo": "Instituciones.xlsx",
        "hoja": "Instituciones",
        "tabla_staging": "staging.instituciones_raw",
        "clave": ["codigo_institucion"],
        "orden": 1,
        "columnas": {
            "CÓDIGO_INSTITUCIÓN": "codigo_institucion",
            "CÓDIGO_IES_PADRE": "codigo_ies_padre",
            "NOMBRE_INSTITUCIÓN": "nombre",
            "ESTADO": "estado",
            "NÚM_IDENTIFIC_TRIBUTARIA_NIT": "nit",
            "PRINCIPAL_SECCIONAL": "principal_seccional",
            "NATURALEZA_JURÍDICA": "naturaleza_juridica",
            "SECTOR": "sector",
            "CARÁCTER_ACADÉMICO": "caracter_academico",
            "PÁGINA_WEB": "pagina_web",
            "ACREDITADA_ALTA_CALIDAD": "acreditacion",
            "FECHA_ACREDITACIÓN": "fecha_acreditacion",
            "VIGENCIA_DE_LA_ACREDITACIÓN": "vigencia_acreditacion",
        },
    },
    {
        "nombre": "Directivos",
        "archivo": "Instituciones.xlsx",
        "hoja": "Directivos",
        "tabla_staging": "staging.directivos_raw",
        "clave": ["codigo_institucion", "cargo", "nombre", "apellido", "fecha_desde"],
        "orden": 2,
        "columnas": {
            "CÓDIGO_INSTITUCIÓN": "codigo_institucion",
            "CARGO": "cargo",
            "NOMBRE": "nombre",
            "APELLIDO": "apellido",
            "FECHA_DESDE": "fecha_desde",
            "FECHA_HASTA": "fecha_hasta",
        },
    },
    {
        "nombre": "Programas",
        "archivo": "Programas.xlsx",
        "hoja": "Programas",
        "tabla_staging": "staging.programas_raw",
        "clave": ["codigo_snies"],
        "orden": 3,
        "columnas": {
            "CÓDIGO_SNIES_DEL_PROGRAMA": "codigo_snies",
            "CÓDIGO_ANTERIOR_ICFES": "codigo_icfes",
            "REGISTRO_UNICO": "registro_unico",
            "NOMBRE_DEL_PROGRAMA": "nombre_programa",
            "TITULO_OTORGADO": "titulo_otorgado",
            "RESOLUCIÓN_DE_APROBACIÓN": "resolucion_aprobacion",
            "CÓDIGO_INSTITUCIÓN": "codigo_institucion",
            "DEPARTAMENTO_OFERTA_PROGRAMA": "departamento_oferta",
            "MUNICIPIO_OFERTA_PROGRAMA": "municipio_oferta",
            "ÁREA_DE_CONOCIMIENTO": "area_conocimiento",
            "NÚCLEO_BÁSICO_DEL_CONOCIMIENTO": "nbc",
            "CINE_F_2013_AC_CAMPO_AMPLIO": "cine_f_amplio",
            "CINE_F_2013_AC_CAMPO_ESPECÍFIC": "cine_f_especifico",
            "CINE_F_2013_AC_CAMPO_DETALLADO": "cine_f_detallado",
            "NIVEL_ACADÉMICO": "nivel_academico",
            "NIVEL_DE_FORMACIÓN": "nivel_formacion",
            "MODALIDAD": "modalidad",
            "PERIODICIDAD": "periodicidad",
            "PERIODICIDAD_ADMISIONES": "periodicidad_admisiones",
            "SE_OFRECE_POR_CICLOS_PROPEDÉUT": "ciclos_propedeuticos",
            "ESTADO_PROGRAMA": "estado_programa",
            "RECONOCIMIENTO_DEL_MINISTERIO": "reconocimiento_ministerio",
            "FECHA_DE_RESOLUCIÓN": "fecha_resolucion",
            "FECHA_EJECUTORIA": "fecha_ejecutoria",
            "FECHA_DE_REGISTRO_EN_SNIES": "fecha_registro_snies",
            "NÚMERO_CRÉDITOS": "creditos",
            "NÚMERO_PERIODOS_DE_DURACIÓN": "duracion_periodos",
            "VIGENCIA_AÑOS": "vigencia_anios",
            "COSTO_MATRÍCULA_ESTUD_NUEVOS": "costo_matricula",
        },
    },
    {
        "nombre": "Cobertura",
        "archivo": "Programas.xlsx",
        "hoja": "Cobertura",
        "tabla_staging": "staging.cobertura_raw",
        "clave": ["codigo_snies", "departamento", "municipio", "tipo_cobertura"],
        "orden": 4,
        "columnas": {
            "CÓDIGO_SNIES_DEL_PROGRAMA": "codigo_snies",
            "DEPARTAMENTO": "departamento",
            "MUNICIPIO": "municipio",
            "TIPO_CUBRIMIENTO": "tipo_cobertura",
        },
    },
]

# Archivos que el usuario debe cargar (se deducen de las fuentes)
ARCHIVOS_REQUERIDOS = sorted({f["archivo"] for f in FUENTES})


def fuentes_ordenadas():
    """Devuelve las fuentes en el orden de ejecución."""
    return sorted(FUENTES, key=lambda f: f["orden"])
