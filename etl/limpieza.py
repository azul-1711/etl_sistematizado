"""
etl/limpieza.py
Biblioteca de funciones de limpieza y estandarización (reglas 3.2 del documento).
Cada función recibe un valor crudo y devuelve el valor limpio; los módulos de
transformación y validación las aplican columna por columna.

Reglas cubiertas: TRIM, mayúsculas, espacios dobles, caracteres dañados (¿),
códigos, NIT, URL, SI/NO, fechas, enteros, vigencia con coma decimal, moneda,
catálogos cerrados y normalización de departamentos contra el DANE.
"""
import re
import unicodedata
from collections import Counter, defaultdict
from datetime import date
from decimal import Decimal, InvalidOperation

import pandas as pd
import psycopg2.extras

from config.catalogos import (ALIAS_CATALOGOS, ALIAS_DEPTO, CATALOGOS,
                              DEPARTAMENTOS_DANE, SIN_INFO)
from config.settings import NO_INFORMADO as NO_INF
from db.conexion import transaccion


# ================================================================
# Auditoría: cuántos valores cambió cada regla (con un ejemplo)
# ================================================================
class Auditor:
    """Acumula por (tabla, campo, regla) cuántos valores cambió cada
    transformación y guarda un ejemplo original -> final."""

    def __init__(self):
        self.datos = {}

    def reg(self, tabla, campo, regla, original, final):
        o = None if (original is None or (not isinstance(original, str) and pd.isna(original))) \
            else str(original)
        f = None if final is None else str(final)
        if o == f:
            return
        clave = (tabla, campo, regla)
        if clave not in self.datos:
            self.datos[clave] = [0, o, f]
        self.datos[clave][0] += 1

    def volcar(self, id_corrida):
        """Guarda lo acumulado en staging.auditoria_transformacion."""
        filas = [(str(id_corrida), t, c, r, n, o, f)
                 for (t, c, r), (n, o, f) in self.datos.items()]
        if filas:
            with transaccion() as cur:
                psycopg2.extras.execute_values(
                    cur,
                    """INSERT INTO staging.auditoria_transformacion
                           (id_corrida, tabla_destino, campo, regla, cantidad,
                            ejemplo_original, ejemplo_final) VALUES %s""",
                    filas)
        self.datos = {}


AUD = Auditor()


# ================================================================
# Funciones de limpieza básicas
# ================================================================
def _trim(v):
    if v is None or (not isinstance(v, str) and pd.isna(v)):
        return None
    s = str(v).strip()
    return s if s != "" else None


def _clave(s):
    """Clave de comparación: sin tildes, mayúsculas, solo letras/dígitos."""
    s = unicodedata.normalize("NFD", str(s))
    s = "".join(c for c in s if unicodedata.category(c) != "Mn")
    return re.sub(r"[^A-Za-z0-9]+", " ", s).upper().strip()


GRAVE_A_AGUDO = str.maketrans("ÀÈÌÒÙ", "ÁÉÍÓÚ")
LETRAS_ES = set("ÁÉÍÓÚÜÑ")


def limpiar_texto(val, es_mayus=True, default=NO_INF):
    """Texto general: TRIM, NFC, espacios dobles, mayúsculas. Vacío -> default."""
    v = _trim(val)
    if not v:
        return default
    v = unicodedata.normalize("NFC", v)
    v = re.sub(r"\s+", " ", v)
    if es_mayus:
        v = v.upper().translate(GRAVE_A_AGUDO)
    return v


def _codigo(val):
    """Código como texto: TRIM y sin el '.0' que a veces deja Excel."""
    v = _trim(val)
    if v is None:
        return None
    return re.sub(r"\.0+$", "", v)


def codigo_institucion_valido(cod):
    """Los códigos de institución del SNIES son enteros (ej. '1101'). Cualquier otra
    cosa, como la nota al pie con que terminan los Excel, no es una institución."""
    return bool(cod) and re.fullmatch(r"\d+", cod) is not None


def homologar_si_no(val):
    v = _trim(val)
    if not v:
        return NO_INF
    n = _clave(v)
    if n in ("SI", "S", "TRUE", "1", "YES"):
        return "SI"
    if n in ("NO", "N", "FALSE", "0"):
        return "NO"
    return NO_INF


def limpiar_url(val):
    v = _trim(val)
    if not v:
        return None
    if re.match(r"^https?://", v, re.IGNORECASE):
        return v
    if re.match(r"^[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}", v):
        return f"http://{v}"
    return None


def limpiar_nit(val):
    """Solo dígitos: quita espacios, guiones y puntos (899.999.063-3 -> 8999990633)."""
    v = _trim(val)
    if not v:
        return None
    return re.sub(r"[\s.\-]", "", v) or None


# ---------------------------- fechas y números --------------------------
def limpiar_fecha(val):
    """'2021-08-25 00:00:00' -> '2021-08-25'. Acepta también dd/mm/aaaa.
    Si no se puede convertir -> None."""
    v = _trim(val)
    if not v:
        return None
    m = re.match(r"^(\d{4})-(\d{1,2})-(\d{1,2})", v)
    if m:
        a, mes, d = map(int, m.groups())
    else:
        m = re.match(r"^(\d{1,2})[/-](\d{1,2})[/-](\d{4})", v)
        if not m:
            return None
        d, mes, a = map(int, m.groups())
    try:
        return date(a, mes, d).isoformat()
    except ValueError:
        return None


def a_entero(val, minimo=0):
    """Texto -> entero (como texto). '120', '120.0' o '$ 120' -> '120'.
    No convertible, negativo o menor que 'minimo' -> None."""
    v = _trim(val)
    if not v:
        return None
    v = re.sub(r"[\s$]", "", v)
    m = re.fullmatch(r"(\d{1,9})(?:[.,]0+)?", v)
    if not m:
        return None
    n = int(m.group(1))
    return str(n) if n >= minimo else None


def a_vigencia(val):
    """Vigencia en años: acepta '7' y '8,5' (coma decimal). Máximo 99.9
    (el hecho es DECIMAL(4,1) y hay un dato de origen con 1603)."""
    v = _trim(val)
    if not v:
        return None
    v = re.sub(r"\s", "", v).replace(",", ".")
    if not re.fullmatch(r"\d+(\.\d+)?", v):
        return None
    d = Decimal(v).quantize(Decimal("0.1"))
    if d > Decimal("99.9"):
        return None
    return str(int(d)) if d == d.to_integral() else str(d)


def a_moneda(val):
    """Quita $, espacios y comas y deja un decimal con punto ('$ 10,259,485' ->
    '10259485'). Máximo 9,999,999,999.99 (columna DECIMAL(12,2))."""
    v = _trim(val)
    if not v:
        return None
    v = re.sub(r"[$\s,]", "", v)
    if re.fullmatch(r"\d{1,3}(\.\d{3})+", v):      # 10.259.485 (puntos de miles)
        v = v.replace(".", "")
    if not re.fullmatch(r"\d+(\.\d+)?", v):
        return None
    try:
        d = Decimal(v).quantize(Decimal("0.01"))
    except InvalidOperation:
        return None
    if d > Decimal("9999999999.99"):
        return None
    return str(int(d)) if d == d.to_integral() else str(d)


def a_codigo_snies(val):
    """Entero no nulo: quita letras y símbolos ('NOTA:' -> None, '0019' -> '19')."""
    v = _trim(val)
    if not v:
        return None
    v = re.sub(r"\.0+$", "", v)
    digitos = re.sub(r"\D", "", v)
    return str(int(digitos)) if digitos else None


# ---------------------------- nombres y caracteres dañados -------------
def construir_vocabulario(*series):
    """Palabras 'sanas' (sin ¿) de todos los nombres, con su frecuencia.
    Sirve para reparar palabras dañadas: DISE¿O -> DISEÑO."""
    vocab = Counter()
    for s in series:
        for v in s.dropna():
            v = unicodedata.normalize("NFC", str(v)).upper().translate(GRAVE_A_AGUDO)
            for t in re.findall(r"[\w¿]+", v):
                if "¿" not in t:
                    vocab[t] += 1
    return vocab


VOCAB = Counter()
_VOCAB_POR_LARGO = {}
_CACHE_REPARACION = {}
# Respaldo cuando la palabra sana no aparece en ningún otro nombre
_SUFIJOS = [(r"CI¿N$", "CIÓN"), (r"SI¿N$", "SIÓN"), (r"¿A$", "ÍA")]


def fijar_vocabulario(vocab):
    global VOCAB, _VOCAB_POR_LARGO, _CACHE_REPARACION
    VOCAB = vocab
    _CACHE_REPARACION = {}
    _VOCAB_POR_LARGO = defaultdict(list)
    for p in vocab:
        _VOCAB_POR_LARGO[len(p)].append(p)


def _reparar_token(t):
    if t in _CACHE_REPARACION:
        return _CACHE_REPARACION[t]
    res = None
    if t != "¿":
        mejor, mejor_n = None, -1
        for g in _VOCAB_POR_LARGO.get(len(t), []):
            if all(a == b or (a == "¿" and b in LETRAS_ES) for a, b in zip(t, g)):
                if VOCAB[g] > mejor_n:
                    mejor, mejor_n = g, VOCAB[g]
        res = mejor
        if res is None:
            for patron, reemplazo in _SUFIJOS:
                if re.search(patron, t):
                    res = re.sub(patron, reemplazo, t)
                    break
    if res is None:                       # no se pudo reparar: se elimina el ¿
        res = t.replace("¿", "")
    _CACHE_REPARACION[t] = res
    return res


def _sin_diacriticos_extranjeros(v):
    """Ë, Ï, Ô, Ö... -> E, I, O, O. Conserva Á É Í Ó Ú Ü Ñ."""
    out = []
    for c in v:
        if c in LETRAS_ES or ord(c) < 128:
            out.append(c)
        else:
            base = "".join(x for x in unicodedata.normalize("NFD", c)
                           if unicodedata.category(x) != "Mn")
            out.append(base)
    return "".join(out)


def limpiar_nombre(val, default=NO_INF):
    """Nombres (institución, programa, título, personas): mayúsculas, espacios
    dobles, repara o elimina caracteres dañados (¿) y quita caracteres
    especiales. Permitidos: letras, dígitos, espacio y . , & ( ) / : ' -"""
    v = _trim(val)
    if not v:
        return default
    v = unicodedata.normalize("NFC", v).upper()
    v = v.replace("\xad", "").replace("–", "-").replace("—", "-")
    v = re.sub(r"\s+", " ", v)
    if "¿" in v:
        v = re.sub(r"[\w¿]+", lambda m: _reparar_token(m.group(0)) if "¿" in m.group(0)
                   else m.group(0), v)
    v = v.translate(GRAVE_A_AGUDO)
    v = _sin_diacriticos_extranjeros(v)
    v = re.sub(r"[^A-ZÁÉÍÓÚÜÑ0-9 .,&()/:'\-]", "", v)
    v = re.sub(r"\s+", " ", v).strip()
    return v or default


# ---------------------------- catálogos cerrados -----------------------
_MAPA_CAT = {c: {_clave(v): v for v in vals} for c, vals in CATALOGOS.items()}
for _campo, _alias in ALIAS_CATALOGOS.items():
    for _k, _v in _alias.items():
        _MAPA_CAT[_campo][_clave(_k)] = _v

_MAPA_DEPTO = {_clave(n): n for n in DEPARTAMENTOS_DANE.values()}
_MAPA_DEPTO.update({_clave(k): v for k, v in ALIAS_DEPTO.items()})
_SIN_INFO = {_clave(x) for x in SIN_INFO}


def homologar_catalogo(tabla, campo, val, catalogo=None):
    """Valor -> valor oficial del catálogo cerrado. Vacío -> NO INFORMADO.
    Un valor que no está en el catálogo -> NO INFORMADO y queda auditado."""
    original = val
    v = _trim(val)
    if not v:
        final = NO_INF
        AUD.reg(tabla, campo, "VACIO_A_NO_INFORMADO", original, final)
        return final
    final = _MAPA_CAT[catalogo or campo].get(_clave(v))
    if final is None:
        final = NO_INF
        AUD.reg(tabla, campo, "CATALOGO_NO_RECONOCIDO_A_NO_INFORMADO", original, final)
        return final
    AUD.reg(tabla, campo, "HOMOLOGACION_CATALOGO_CERRADO", original, final)
    return final


def normalizar_ubicacion(tabla, campo, val):
    """Departamento contra el diccionario DANE interno (tildes, mayúsculas, alias).
    Municipio: solo forma (mayúsculas, espacios). 'Sin información' o vacío -> NO INFORMADO."""
    v = _trim(val)
    k = _clave(v) if v else ""
    if k in _SIN_INFO:
        final = NO_INF
        AUD.reg(tabla, campo, "VACIO_O_SIN_INFORMACION_A_NO_INFORMADO", val, final)
        return final
    if campo.startswith("departamento"):
        final = _MAPA_DEPTO.get(k)
        if final is not None:
            AUD.reg(tabla, campo, "NORMALIZACION_CATALOGO_DANE", val, final)
            return final
    final = limpiar_texto(v)
    AUD.reg(tabla, campo, "MAYUSCULAS_ESPACIOS", val, final)
    return final


# ---------------------------- helper de auditoría ----------------------
def T(tabla, campo, regla, original, final):
    """Registra la transformación y devuelve el valor final."""
    AUD.reg(tabla, campo, regla, original, final)
    return final


def T_texto(tabla, campo, original):
    """limpiar_texto + auditoría (distingue vacío de simple cambio de formato)."""
    final = limpiar_texto(original)
    regla = "VACIO_A_NO_INFORMADO" if not _trim(original) else "MAYUSCULAS_ESPACIOS"
    return T(tabla, campo, regla, original, final)


def T_texto_info(tabla, campo, original):
    """Como T_texto, pero el literal 'Sin información' (comparado con _clave, sin
    tildes) también cuenta como vacío -> NO INFORMADO. Para área de conocimiento y
    campos CINE-F. No se usa en nbc: 'SIN CLASIFICAR' es una categoría real del SNIES."""
    o = _trim(original)
    if o and _clave(o) == "SIN INFORMACION":
        return T(tabla, campo, "VACIO_O_SIN_INFORMACION_A_NO_INFORMADO", original, NO_INF)
    return T_texto(tabla, campo, original)


def T_nombre(tabla, campo, original):
    """limpiar_nombre + auditoría (distingue reparación de caracteres dañados)."""
    final = limpiar_nombre(original)
    o = _trim(original)
    if not o:
        regla = "VACIO_A_NO_INFORMADO"
    elif "¿" in o:
        regla = "CARACTERES_DANADOS_REPARADOS_O_ELIMINADOS"
    else:
        regla = "MAYUSCULAS_CARACTERES_ESPECIALES_ESPACIOS"
    return T(tabla, campo, regla, original, final)


def T_vigencia(tabla, campo, original):
    final = a_vigencia(original)
    o = _trim(original)
    if o and final is None:
        regla = "FUERA_DE_RANGO_O_NO_NUMERICO_A_NULL"
    else:
        regla = "COMA_DECIMAL_A_PUNTO"
    return T(tabla, campo, regla, original, final)
