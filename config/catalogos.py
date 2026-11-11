"""
config/catalogos.py
Catálogos cerrados de homologación y catálogo interno de departamentos DANE.
Un valor que no esté en el catálogo queda como NO INFORMADO y se audita,
así se sabe qué valores nuevos hay que agregar aquí.
"""

CATALOGOS = {
    "estado": ["ACTIVA", "INACTIVA"],
    "principal_seccional": ["PRINCIPAL", "SECCIONAL"],
    "naturaleza_juridica": ["CORPORACIÓN", "FUNDACIÓN", "NACIONAL", "DEPARTAMENTAL", "MUNICIPAL"],
    "sector": ["PRIVADO", "OFICIAL"],
    "caracter_academico": ["UNIVERSIDAD", "INSTITUCIÓN UNIVERSITARIA/ESCUELA TECNOLÓGICA",
                           "INSTITUCIÓN TECNOLÓGICA", "INSTITUCIÓN TÉCNICA PROFESIONAL"],
    "estado_programa": ["ACTIVO", "INACTIVO"],
    "nivel_academico": ["PREGRADO", "POSGRADO"],
    "nivel_formacion": ["UNIVERSITARIO", "ESPECIALIZACIÓN UNIVERSITARIA", "TECNOLÓGICO",
                        "MAESTRÍA", "FORMACIÓN TÉCNICA PROFESIONAL",
                        "ESPECIALIZACIÓN MÉDICO QUIRÚRGICA", "ESPECIALIZACIÓN TECNOLÓGICA",
                        "DOCTORADO", "ESPECIALIZACIÓN TÉCNICO PROFESIONAL"],
    "modalidad": ["PRESENCIAL", "VIRTUAL", "A DISTANCIA", "DUAL",
                  "HÍBRIDA (PRESENCIAL-VIRTUAL)", "PRESENCIAL-VIRTUAL",
                  "HÍBRIDA (A DISTANCIA-VIRTUAL)", "VIRTUAL-A DISTANCIA",
                  "HÍBRIDA (DUAL-VIRTUAL)", "PRESENCIAL-DUAL", "VIRTUAL-DUAL",
                  "PRESENCIAL-A DISTANCIA", "PRESENCIAL-VIRTUAL-A DISTANCIA"],
    "periodicidad": ["SEMESTRAL", "ANUAL", "MENSUAL", "TRIMESTRAL", "PERIODOS", "CUATRIMESTRAL",
                     "SIN DEFINIR", "BIMENSUAL", "BIMESTRAL", "POR COHORTE", "BIANUAL"],
    "tipo_cobertura": ["PRINCIPAL", "EXTENSIÓN", "AMPLIACIÓN COBERTURA", "CENTROS TUTORIALES",
                       "VIRTUAL", "CERES", "CONVENIO", "CONVENIOS EN RED", "SIN DEFINIR"],
}

# Sinónimos que se aceptan para un valor oficial del catálogo
ALIAS_CATALOGOS = {
    "modalidad": {"DISTANCIA": "A DISTANCIA"},
}

# Códigos y nombres oficiales de departamentos (DANE)
DEPARTAMENTOS_DANE = {
    "05": "ANTIOQUIA", "08": "ATLÁNTICO", "11": "BOGOTÁ, D.C.", "13": "BOLÍVAR",
    "15": "BOYACÁ", "17": "CALDAS", "18": "CAQUETÁ", "19": "CAUCA", "20": "CESAR",
    "23": "CÓRDOBA", "25": "CUNDINAMARCA", "27": "CHOCÓ", "41": "HUILA", "44": "LA GUAJIRA",
    "47": "MAGDALENA", "50": "META", "52": "NARIÑO", "54": "NORTE DE SANTANDER",
    "63": "QUINDÍO", "66": "RISARALDA", "68": "SANTANDER", "70": "SUCRE", "73": "TOLIMA",
    "76": "VALLE DEL CAUCA", "81": "ARAUCA", "85": "CASANARE", "86": "PUTUMAYO",
    "88": "ARCHIPIÉLAGO DE SAN ANDRÉS, PROVIDENCIA Y SANTA CATALINA",
    "91": "AMAZONAS", "94": "GUAINÍA", "95": "GUAVIARE", "97": "VAUPÉS", "99": "VICHADA",
}
ALIAS_DEPTO = {
    "BOGOTA": "BOGOTÁ, D.C.", "BOGOTA DC": "BOGOTÁ, D.C.", "SANTAFE DE BOGOTA D C": "BOGOTÁ, D.C.",
    "SAN ANDRES": "ARCHIPIÉLAGO DE SAN ANDRÉS, PROVIDENCIA Y SANTA CATALINA",
    "SAN ANDRES Y PROVIDENCIA": "ARCHIPIÉLAGO DE SAN ANDRÉS, PROVIDENCIA Y SANTA CATALINA",
    "SAN ANDRES PROVIDENCIA Y SANTA CATALINA": "ARCHIPIÉLAGO DE SAN ANDRÉS, PROVIDENCIA Y SANTA CATALINA",
    "VALLE": "VALLE DEL CAUCA", "GUAJIRA": "LA GUAJIRA",
}

# Valores que significan "sin dato" en departamento/municipio
SIN_INFO = {"", "SIN INFORMACION", "NO INFORMADO", "NO APLICA", "N A", "NA"}
