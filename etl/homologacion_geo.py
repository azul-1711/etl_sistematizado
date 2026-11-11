"""
etl/homologacion_geo.py   (FA-01)

Homologación aproximada y normalización geográfica contra el catálogo del DANE
(data/catalogos/dane_municipios.csv).

Qué hace con cada departamento y municipio de Programas y Cobertura:
  1. Coincidencia EXACTA (sin tildes, mayúsculas ni signos)      -> puntaje 100
  2. Si no hay exacta, similitud con rapidfuzz (WRatio, 0 a 100)
  3. El municipio se busca SOLO entre los municipios del departamento ya
     homologado: así se valida que el municipio pertenezca a ese departamento.
  4. Clasificación por umbrales (config/settings.py):
        >= 90      AUTOMATICO   se reemplaza por el nombre oficial del DANE
        75 a 89    REVISION     se conserva el valor original hasta que el usuario
                                lo apruebe en la app (ui/homologacion.py)
        < 75       ADVERTENCIA  no hay coincidencia confiable
     Si las dos mejores opciones empatan (diferencia menor a MARGEN_AMBIGUEDAD),
     un caso AUTOMATICO baja a REVISION, porque no se sabe cuál es la correcta.
  5. Cada valor distinto queda en staging.homologacion_geografica (trazabilidad).

Decisiones del usuario: cuando alguien aprueba o rechaza un caso en la app, la
decisión queda en esa misma tabla (estado_revision) y se aplica sola en las
corridas siguientes. La tabla no se borra en las cargas completas.

Uso desde el orquestador:
    validos, resumen = homologar_todo(id_corrida, validos)

Prueba sin base de datos (lee Programas.xlsx y muestra qué pasaría):
    python -m etl.homologacion_geo [ruta/a/Programas.xlsx]
"""
from functools import lru_cache

import pandas as pd
from rapidfuzz import fuzz, process

from config.settings import (ARCHIVO_DANE, NO_INFORMADO, UMBRAL_AUTOMATICO,
                             UMBRAL_REVISION)
from etl import limpieza as L
from utils.logger import get_logger

log = get_logger(__name__)

# Si las dos mejores opciones se diferencian en menos de estos puntos, el caso
# se manda a revisión aunque el puntaje sea alto.
MARGEN_AMBIGUEDAD = 2.0

# Qué hacer con un valor sin coincidencia confiable (< UMBRAL_REVISION):
#   False -> se conserva el valor original (no se pierde información; queda la advertencia)
#   True  -> se reemplaza por NO INFORMADO
ADVERTENCIA_A_NO_INFORMADO = False

COLUMNAS_DANE = ("codigo_departamento", "departamento", "codigo_municipio", "municipio")

NIVEL_DEP, NIVEL_MUN = "DEPARTAMENTO", "MUNICIPIO"
AUTOMATICO, REVISION, ADVERTENCIA = "AUTOMATICO", "REVISION", "ADVERTENCIA"
PENDIENTE, APROBADA, RECHAZADA, NO_APLICA = "PENDIENTE", "APROBADA", "RECHAZADA", "NO_APLICA"


class ErrorCatalogoDane(ValueError):
    """El catálogo DANE falta o no tiene el formato esperado."""


# ---------------------------------------------------------------
# Catálogo DANE
# ---------------------------------------------------------------
class CatalogoDane:
    """Catálogo en memoria, indexado por claves sin tildes (para comparar)."""

    def __init__(self, df):
        self.deptos = {}        # clave depto -> {"nombre", "codigo"}
        self.municipios = {}    # clave depto -> {clave municipio -> {"nombre", "codigo"}}
        for f in df.to_dict("records"):
            kd, km = L._clave(f["departamento"]), L._clave(f["municipio"])
            self.deptos.setdefault(kd, {"nombre": f["departamento"],
                                        "codigo": f["codigo_departamento"]})
            self.municipios.setdefault(kd, {})[km] = {"nombre": f["municipio"],
                                                      "codigo": f["codigo_municipio"]}
        self.claves_deptos = list(self.deptos)
        self.claves_municipios = {kd: list(m) for kd, m in self.municipios.items()}


def cargar_catalogo_dane(ruta=None):
    """Lee dane_municipios.csv (columnas: codigo_departamento, departamento,
    codigo_municipio, municipio)."""
    ruta = ruta or ARCHIVO_DANE
    try:
        df = pd.read_csv(ruta, dtype=str, encoding="utf-8-sig", keep_default_na=False)
    except FileNotFoundError as exc:
        raise ErrorCatalogoDane(
            f"No se encontró el catálogo DANE en {ruta}. Debe llamarse "
            "dane_municipios.csv y estar en data/catalogos/.") from exc
    df.columns = [c.strip().lower() for c in df.columns]
    faltan = [c for c in COLUMNAS_DANE if c not in df.columns]
    if faltan:
        raise ErrorCatalogoDane(f"Al catálogo DANE le faltan las columnas: {', '.join(faltan)}.")
    for c in COLUMNAS_DANE:
        df[c] = df[c].str.strip()
    df = df[(df["departamento"] != "") & (df["municipio"] != "")]
    if df.empty:
        raise ErrorCatalogoDane("El catálogo DANE está vacío.")
    return CatalogoDane(df)


@lru_cache(maxsize=1)
def _catalogo_en_cache():
    return cargar_catalogo_dane()


# ---------------------------------------------------------------
# Coincidencia de un valor contra una lista de claves
# ---------------------------------------------------------------
def _clasificar(puntaje, ambiguo=False):
    if puntaje >= UMBRAL_AUTOMATICO:
        return REVISION if ambiguo else AUTOMATICO
    if puntaje >= UMBRAL_REVISION:
        return REVISION
    return ADVERTENCIA


def _mejor_similitud(clave, claves_candidatas):
    """(clave_mejor, puntaje, ambiguo) con rapidfuzz. ambiguo=True si la segunda
    mejor opción también es buena y queda casi empatada."""
    if not claves_candidatas:
        return None, 0.0, False
    top = process.extract(clave, claves_candidatas, scorer=fuzz.WRatio, limit=2)
    mejor = top[0]
    ambiguo = (len(top) > 1 and top[1][1] >= UMBRAL_REVISION
               and (mejor[1] - top[1][1]) < MARGEN_AMBIGUEDAD)
    return mejor[0], round(float(mejor[1]), 2), ambiguo


def _resultado(nivel, original, ref=None):
    return {"nivel": nivel, "valor_original": original, "departamento_ref": ref,
            "valor_homologado": None, "codigo_dane": None, "metodo": "SIN_COINCIDENCIA",
            "puntaje": 0.0, "clasificacion": ADVERTENCIA}


def resolver_departamento(valor, cat):
    r = _resultado(NIVEL_DEP, valor)
    k = L._clave(valor)
    if k in cat.deptos:
        d = cat.deptos[k]
        r.update(valor_homologado=d["nombre"], codigo_dane=d["codigo"], metodo="EXACTA",
                 puntaje=100.0, clasificacion=AUTOMATICO)
        return r
    k_mejor, puntaje, ambiguo = _mejor_similitud(k, cat.claves_deptos)
    if k_mejor is not None and puntaje >= UMBRAL_REVISION:
        d = cat.deptos[k_mejor]
        r.update(valor_homologado=d["nombre"], codigo_dane=d["codigo"], metodo="SIMILITUD",
                 puntaje=puntaje, clasificacion=_clasificar(puntaje, ambiguo))
    else:
        r["puntaje"] = puntaje
    return r


def resolver_municipio(valor, clave_depto, cat, techo_revision=False):
    """Busca el municipio SOLO entre los del departamento de referencia.
    techo_revision=True: el departamento aún no está confirmado, así que ningún
    municipio puede quedar como AUTOMATICO."""
    ref = cat.deptos[clave_depto]["nombre"] if clave_depto else None
    r = _resultado(NIVEL_MUN, valor, ref)
    if clave_depto is None:
        return r                                   # sin departamento válido: no se puede validar
    k = L._clave(valor)
    munis = cat.municipios[clave_depto]
    if k in munis:
        m = munis[k]
        r.update(valor_homologado=m["nombre"], codigo_dane=m["codigo"], metodo="EXACTA",
                 puntaje=100.0, clasificacion=AUTOMATICO)
    else:
        k_mejor, puntaje, ambiguo = _mejor_similitud(k, cat.claves_municipios[clave_depto])
        if k_mejor is not None and puntaje >= UMBRAL_REVISION:
            m = munis[k_mejor]
            r.update(valor_homologado=m["nombre"], codigo_dane=m["codigo"], metodo="SIMILITUD",
                     puntaje=puntaje, clasificacion=_clasificar(puntaje, ambiguo))
        else:
            r["puntaje"] = puntaje
    if techo_revision and r["clasificacion"] == AUTOMATICO:
        r["clasificacion"] = REVISION
    return r


# ---------------------------------------------------------------
# Decisiones del usuario y valor final
# ---------------------------------------------------------------
def _clave_decision(r):
    return (r["nivel"], r["valor_original"], r["departamento_ref"] or "")


def _aplicar_decision(r, decisiones, cat):
    """Pone estado_revision y, si el usuario ya decidió este caso, lo respeta."""
    r["estado_revision"] = NO_APLICA if r["clasificacion"] == AUTOMATICO else PENDIENTE
    d = decisiones.get(_clave_decision(r))
    if not d:
        return r
    r["estado_revision"] = d["estado_revision"]
    if d["estado_revision"] == APROBADA and d.get("valor_homologado"):
        r["valor_homologado"] = d["valor_homologado"]
        r["codigo_dane"] = _codigo_de(r, cat)
    return r


def _codigo_de(r, cat):
    k = L._clave(r["valor_homologado"])
    if r["nivel"] == NIVEL_DEP:
        return cat.deptos.get(k, {}).get("codigo")
    kd = L._clave(r["departamento_ref"]) if r["departamento_ref"] else None
    return cat.municipios.get(kd, {}).get(k, {}).get("codigo")


def valor_final(r):
    """Qué valor pasa a la bodega según la clasificación y la decisión del usuario."""
    original = r["valor_original"]
    if r["estado_revision"] == RECHAZADA:
        return original
    if r["estado_revision"] == APROBADA or r["clasificacion"] == AUTOMATICO:
        return r["valor_homologado"] or original
    if r["clasificacion"] == REVISION:
        return original                             # espera la revisión del usuario
    return NO_INFORMADO if ADVERTENCIA_A_NO_INFORMADO else original


def _confirmado(r):
    """True si el departamento quedó confirmado (automático o aprobado por el usuario)."""
    return r["estado_revision"] == APROBADA or (
        r["clasificacion"] == AUTOMATICO and r["estado_revision"] != RECHAZADA)


# ---------------------------------------------------------------
# Núcleo (sin base de datos)
# ---------------------------------------------------------------
def homologar_pares(pares, cat, decisiones=None):
    """pares: pares (departamento, municipio) ya limpios, con repetidos.
    Devuelve (traza, mapa):
      traza: un dict por valor distinto (lo que va a homologacion_geografica)
      mapa : {(departamento, municipio): (departamento_final, municipio_final)}"""
    decisiones = decisiones or {}
    traza, mapa = {}, {}
    cache_dep, cache_mun = {}, {}

    def registrar(r):
        traza.setdefault(_clave_decision(r), r)

    for dep, mun in set(pares):
        # --- departamento
        if dep == NO_INFORMADO:
            dep_final, k_ref, techo = dep, None, False
        else:
            if dep not in cache_dep:
                cache_dep[dep] = _aplicar_decision(resolver_departamento(dep, cat), decisiones, cat)
            rd = cache_dep[dep]
            registrar(rd)
            dep_final = valor_final(rd)
            # Se valida el municipio contra el departamento propuesto si hay uno
            # razonable (automático, en revisión o aprobado) y no fue rechazado.
            usable = (rd["valor_homologado"] is not None and rd["estado_revision"] != RECHAZADA
                      and rd["clasificacion"] != ADVERTENCIA) or rd["estado_revision"] == APROBADA
            k_ref = L._clave(rd["valor_homologado"]) if usable else None
            techo = usable and not _confirmado(rd)
        # --- municipio
        if mun == NO_INFORMADO:
            mun_final = mun
        else:
            llave = (mun, k_ref, techo)
            if llave not in cache_mun:
                cache_mun[llave] = _aplicar_decision(
                    resolver_municipio(mun, k_ref, cat, techo), decisiones, cat)
            rm = cache_mun[llave]
            registrar(rm)
            mun_final = valor_final(rm)
        mapa[(dep, mun)] = (dep_final, mun_final)
    return list(traza.values()), mapa


def _resumen_traza(traza):
    cuenta = {c: sum(1 for r in traza if r["clasificacion"] == c)
              for c in (AUTOMATICO, REVISION, ADVERTENCIA)}
    return {"valores_unicos": len(traza),
            "automatico": cuenta[AUTOMATICO], "revision": cuenta[REVISION],
            "advertencia": cuenta[ADVERTENCIA],
            "pendientes": sum(1 for r in traza if r["estado_revision"] == PENDIENTE),
            "decisiones_aplicadas": sum(1 for r in traza
                                        if r["estado_revision"] in (APROBADA, RECHAZADA))}


def _aplicar_mapa(df, mapa):
    """Devuelve (df nuevo, cantidad de filas cuyo departamento o municipio cambió)."""
    pares = list(zip(df["departamento"], df["municipio"]))
    nuevos = [mapa.get(p, p) for p in pares]
    out = df.copy()
    out["departamento"] = [n[0] for n in nuevos]
    out["municipio"] = [n[1] for n in nuevos]
    cambiadas = sum(1 for a, b in zip(pares, nuevos) if a != b)
    return out, cambiadas


# ---------------------------------------------------------------
# Base de datos: trazabilidad y decisiones (staging.homologacion_geografica)
# ---------------------------------------------------------------
def leer_decisiones(cur):
    """Última decisión del usuario por caso: {(nivel, valor_original, depto_ref): {...}}."""
    cur.execute("""SELECT DISTINCT ON (nivel, valor_original, COALESCE(departamento_ref, ''))
                          nivel, valor_original, COALESCE(departamento_ref, ''),
                          valor_homologado, estado_revision
                   FROM staging.homologacion_geografica
                   WHERE estado_revision IN ('APROBADA', 'RECHAZADA')
                   ORDER BY nivel, valor_original, COALESCE(departamento_ref, ''),
                            id_homologacion DESC""")
    return {(n, v, ref): {"valor_homologado": h, "estado_revision": e}
            for n, v, ref, h, e in cur.fetchall()}


def guardar_traza(cur, id_corrida, traza):
    """Reemplaza la trazabilidad de la corrida (así repetir la etapa no duplica)."""
    from psycopg2.extras import execute_values
    cur.execute("DELETE FROM staging.homologacion_geografica WHERE id_corrida = %s",
                (str(id_corrida),))
    filas = [(str(id_corrida), r["nivel"], r["valor_original"], r["departamento_ref"],
              r["valor_homologado"], r["codigo_dane"], r["metodo"], r["puntaje"],
              r["clasificacion"], r["estado_revision"]) for r in traza]
    if filas:
        execute_values(cur, """INSERT INTO staging.homologacion_geografica
                (id_corrida, nivel, valor_original, departamento_ref, valor_homologado,
                 codigo_dane, metodo, puntaje, clasificacion, estado_revision) VALUES %s""",
                       filas)


# ---------------------------------------------------------------
# Etapa FA-01 completa
# ---------------------------------------------------------------
def homologar_todo(id_corrida, validos):
    """validos: salida de validacion.validar_todo. Devuelve (validos_nuevos, resumen).
    Solo cambia departamento y municipio de 'programas' y 'cobertura'."""
    from db.conexion import transaccion
    cat = cargar_catalogo_dane()

    pares = []
    for tabla in ("programas", "cobertura"):
        df = validos[tabla]
        pares += list(zip(df["departamento"], df["municipio"]))

    with transaccion() as cur:
        decisiones = leer_decisiones(cur)           # antes de borrar lo de esta corrida
        traza, mapa = homologar_pares(pares, cat, decisiones)
        guardar_traza(cur, id_corrida, traza)

    nuevos = dict(validos)
    resumen = _resumen_traza(traza)
    for tabla in ("programas", "cobertura"):
        nuevos[tabla], cambiadas = _aplicar_mapa(validos[tabla], mapa)
        resumen[f"filas_{tabla}_cambiadas"] = cambiadas
    log.info("Homologación geográfica: %s", resumen)
    if resumen["pendientes"]:
        log.warning("Hay %d valores geográficos por revisar en la app.", resumen["pendientes"])
    return nuevos, resumen


# ---------------------------------------------------------------
# Apoyo para la pantalla de revisión (ui/homologacion.py)
# ---------------------------------------------------------------
def consultar_traza(id_corrida=None, clasificacion=None, estado=None, limite=5000):
    """Filas de la trazabilidad, con filtros opcionales. Lista de dicts."""
    from db.conexion import transaccion
    filtros, params = [], []
    for columna, valor in (("id_corrida", id_corrida), ("clasificacion", clasificacion),
                           ("estado_revision", estado)):
        if valor:
            filtros.append(f"{columna} = %s")
            params.append(str(valor))
    donde = ("WHERE " + " AND ".join(filtros)) if filtros else ""
    with transaccion() as cur:
        cur.execute(f"""SELECT id_homologacion, id_corrida, nivel, valor_original,
                               departamento_ref, valor_homologado, codigo_dane, metodo,
                               puntaje, clasificacion, estado_revision, fecha_registro
                        FROM staging.homologacion_geografica {donde}
                        ORDER BY clasificacion, puntaje DESC, id_homologacion LIMIT %s""",
                    params + [limite])
        cols = [d[0] for d in cur.description]
        return [dict(zip(cols, f)) for f in cur.fetchall()]


def candidatos(nivel, valor, departamento_ref=None, n=5):
    """Mejores opciones del catálogo para un valor: [(nombre, puntaje)].
    Sirve para que el usuario escoja otra opción distinta a la propuesta."""
    cat = _catalogo_en_cache()
    k = L._clave(valor)
    if nivel == NIVEL_DEP:
        pool = {kk: d["nombre"] for kk, d in cat.deptos.items()}
    else:
        kd = L._clave(departamento_ref) if departamento_ref else None
        pool = {kk: m["nombre"] for kk, m in cat.municipios.get(kd, {}).items()}
    if not pool:
        return []
    return [(pool[c], round(float(p), 2))
            for c, p, _ in process.extract(k, list(pool), scorer=fuzz.WRatio, limit=n)]


def resolver_revision(id_homologacion, estado, valor_homologado=None):
    """Guarda la decisión del usuario: estado APROBADA, RECHAZADA o PENDIENTE.
    Con APROBADA se puede indicar otro valor_homologado (debe existir en el DANE).
    La decisión se aplica en la siguiente ejecución del ETL."""
    from db.conexion import transaccion
    if estado not in (APROBADA, RECHAZADA, PENDIENTE):
        raise ValueError("Estado no válido. Use APROBADA, RECHAZADA o PENDIENTE.")
    cat = _catalogo_en_cache()
    with transaccion() as cur:
        cur.execute("""SELECT nivel, departamento_ref, valor_homologado
                       FROM staging.homologacion_geografica WHERE id_homologacion = %s""",
                    (id_homologacion,))
        fila = cur.fetchone()
        if not fila:
            raise ValueError(f"No existe el caso {id_homologacion}.")
        nivel, ref, propuesto = fila
        if estado == APROBADA:
            valor = valor_homologado or propuesto
            if not valor:
                raise ValueError("No hay un valor del DANE para aprobar; elija uno.")
            r = {"nivel": nivel, "departamento_ref": ref, "valor_homologado": valor}
            codigo = _codigo_de(r, cat)
            if codigo is None:
                raise ValueError(f"'{valor}' no existe en el catálogo DANE"
                                 f"{' para ' + ref if ref else ''}.")
            cur.execute("""UPDATE staging.homologacion_geografica
                           SET estado_revision = %s, valor_homologado = %s, codigo_dane = %s
                           WHERE id_homologacion = %s""",
                        (estado, valor, codigo, id_homologacion))
        else:
            cur.execute("""UPDATE staging.homologacion_geografica SET estado_revision = %s
                           WHERE id_homologacion = %s""", (estado, id_homologacion))


# ---------------------------------------------------------------
# Prueba por consola, sin base de datos
# ---------------------------------------------------------------
def _simulacion(ruta_excel):
    cat = cargar_catalogo_dane()
    libro = pd.ExcelFile(ruta_excel)
    pares = []
    for hoja, c_dep, c_mun in (("Programas", "DEPARTAMENTO_OFERTA_PROGRAMA",
                                "MUNICIPIO_OFERTA_PROGRAMA"),
                               ("Cobertura", "DEPARTAMENTO", "MUNICIPIO")):
        df = libro.parse(hoja, dtype=str, usecols=[c_dep, c_mun]).dropna()
        pares += [(L.normalizar_ubicacion("sim", "departamento", d),
                   L.normalizar_ubicacion("sim", "municipio", m))
                  for d, m in zip(df[c_dep], df[c_mun])]
    traza, mapa = homologar_pares(pares, cat)
    print("Resumen:", _resumen_traza(traza))
    print(f"Pares distintos: {len(mapa)}  |  cambian: "
          f"{sum(1 for k, v in mapa.items() if k != v)}")
    for clase in (REVISION, ADVERTENCIA):
        casos = [r for r in traza if r["clasificacion"] == clase]
        print(f"\n--- {clase} ({len(casos)}) ---")
        for r in sorted(casos, key=lambda x: -x["puntaje"]):
            print(f"{r['nivel'][:3]} | {r['valor_original']:<34} -> "
                  f"{str(r['valor_homologado']):<30} {r['puntaje']:>6} | {r['departamento_ref']}")


if __name__ == "__main__":
    import sys
    from config.settings import DATA_ENTRADA
    _simulacion(sys.argv[1] if len(sys.argv) > 1 else DATA_ENTRADA / "Programas.xlsx")