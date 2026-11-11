"""
etl/orquestador.py   (FC-03, FC-04, apoyado en FA-02)

Coordina todo el ETL con una sola llamada:

    resultado = ejecutar_etl(tipo_carga="COMPLETA")

Secuencia (cada etapa debe terminar bien antes de empezar la siguiente; si una
falla, se detienen las que dependen de ella y se registra el error):

    FC-01_VERIFICACION          lee encabezados de los archivos        (solo lee)
    FA-02_RESPALDO              pg_dump antes de tocar datos           (escribe)
    FC-02_PREPARAR_STAGING      crea lo que falte y aplica el tipo     (escribe)
    FD-03_STAGING_<fuente>      una por fuente (FC-03, ForEach)        (escribe)
    FD-04_TRANSFORMACION        tipos, limpieza y derivadas            (en memoria)
    FD-08_VALIDACION            separa válidos y rechazados            (en memoria)
    FA-01_HOMOLOGACION          municipios contra el DANE              (en memoria)
    FD-06_INTEGRACION           directivo actual y tipo de cobertura   (en memoria)
    FD-09_CARGA_DW              dimensiones y hecho, una transacción   (escribe)

Reanudación (FA-02): con reanudar=True, si la última corrida terminó en error o
se interrumpió, se continúa con el mismo id_corrida. Las etapas que ESCRIBEN y ya
terminaron bien se saltan (nunca se repite el vaciado de una carga completa);
las que solo calculan en memoria se repiten porque Staging es persistente.

Cada etapa queda en la bitácora (staging.etl_log) y la corrida completa con
proceso = 'CORRIDA_ETL'. Los fallos de conexión se reintentan (5, 15, 45 s).
"""
import time
from pathlib import Path

from config.fuentes import fuentes_ordenadas
from config.settings import ARCHIVO_DANE, DATA_ENTRADA
from db import bitacora
from db.conexion import estado_base, inicializar_base, transaccion
from etl import (carga_dw, extraccion, homologacion_geo, integracion, resiliencia,
                 staging, transformacion, validacion, verificacion)
from etl.resiliencia import PROCESO_CORRIDA, con_reintentos
from etl.tipos_carga import es_completa, normalizar_tipo
from utils.logger import get_logger

log = get_logger(__name__)

# Si el respaldo falla en una carga COMPLETA (que vacía la bodega) el proceso se
# detiene. En una INCREMENTAL solo se deja una advertencia y se continúa.
RESPALDO_OBLIGATORIO_EN_COMPLETA = True


class EtapaFallida(Exception):
    """Una etapa falló: detiene las etapas que dependen de ella (FC-04)."""

    def __init__(self, proceso, descripcion, mensaje):
        super().__init__(mensaje)
        self.proceso, self.descripcion, self.mensaje = proceso, descripcion, mensaje


def plan_etapas():
    """Lista [(proceso, descripción)] en orden de ejecución. La interfaz la usa
    para dibujar el avance antes de empezar."""
    plan = [("FC-01_VERIFICACION", "Verificación de los archivos"),
            ("FA-02_RESPALDO", "Respaldo de la base de datos"),
            ("FC-02_PREPARAR_STAGING", "Preparación de Staging")]
    plan += [(f"FD-03_STAGING_{f['nombre']}", f"Carga a Staging: {f['nombre']}")
             for f in fuentes_ordenadas()]
    plan += [("FD-04_TRANSFORMACION", "Conversión de tipos y limpieza"),
             ("FD-08_VALIDACION", "Validación y separación de registros"),
             ("FA-01_HOMOLOGACION", "Homologación geográfica"),
             ("FD-06_INTEGRACION", "Unificación de la información"),
             ("FD-09_CARGA_DW", "Carga a la bodega de datos")]
    return plan


class _Corrida:
    """Estado de una ejecución: ejecuta etapas, las registra y guarda los resultados."""

    def __init__(self, id_corrida, tipo, etapas_ok, reanudada, al_progreso):
        self.id, self.tipo = id_corrida, tipo
        self.etapas_ok, self.reanudada = etapas_ok, reanudada
        self.al_progreso = al_progreso
        self.etapas = []                  # resultado de cada etapa, para la interfaz
        self.resumen = {"staging": {}}
        self.cache_excel = {}
        self.datos = self.validos = self.integrados = None

    # --- avisos a la interfaz y al historial de la corrida
    def _avisar(self, proceso, descripcion, estado, mensaje=""):
        if self.al_progreso:
            try:
                self.al_progreso({"etapa": proceso, "descripcion": descripcion,
                                  "estado": estado, "mensaje": mensaje})
            except Exception:                      # la interfaz nunca debe romper el ETL
                log.exception("Error en la función de progreso")

    def _anotar(self, proceso, descripcion, estado, mensaje):
        self.etapas.append({"etapa": proceso, "descripcion": descripcion,
                            "estado": estado, "mensaje": mensaje})
        self._avisar(proceso, descripcion, estado, mensaje)

    # --- ejecución de una etapa
    def etapa(self, proceso, descripcion, funcion, *, fuente=None, escribe=False,
              solo_primera_vez=False, conteos=None):
        """Ejecuta una etapa: la registra en la bitácora, reintenta fallos de
        conexión y, si falla, lanza EtapaFallida para detener el proceso.
        escribe=True: si ya terminó bien en esta corrida, se salta.
        solo_primera_vez=True: se salta siempre que se esté reanudando."""
        if (escribe and proceso in self.etapas_ok) or (solo_primera_vez and self.reanudada):
            mensaje = "Ya se hizo en la ejecución anterior; se continúa desde el último paso válido."
            id_log = con_reintentos(bitacora.iniciar, self.id, proceso, fuente, self.tipo,
                                    etapa="bitácora")
            con_reintentos(bitacora.finalizar, id_log, estado="OMITIDA", mensaje=mensaje,
                           etapa="bitácora")
            self._anotar(proceso, descripcion, "OMITIDA", mensaje)
            return None

        self._avisar(proceso, descripcion, "INICIO")
        id_log = con_reintentos(bitacora.iniciar, self.id, proceso, fuente, self.tipo,
                                etapa="bitácora")
        try:
            resultado = con_reintentos(funcion, etapa=descripcion)
        except Exception as exc:
            mensaje = str(exc).strip() or type(exc).__name__
            log.exception("Falló la etapa %s", proceso)
            try:
                con_reintentos(bitacora.finalizar, id_log, estado="ERROR",
                               mensaje=mensaje[:2000], etapa="bitácora")
            except Exception:
                log.exception("No se pudo registrar el error de %s en la bitácora", proceso)
            self._anotar(proceso, descripcion, "ERROR", mensaje)
            raise EtapaFallida(proceso, descripcion, mensaje) from exc

        datos_log = dict(conteos(resultado)) if conteos else {}
        estado = datos_log.pop("estado", "OK")
        mensaje = datos_log.pop("mensaje", None)
        con_reintentos(bitacora.finalizar, id_log, estado=estado, mensaje=mensaje,
                       etapa="bitácora", **datos_log)
        self._anotar(proceso, descripcion, estado, mensaje or "")
        return resultado


# ---------------------------------------------------------------
# Auxiliares
# ---------------------------------------------------------------
def _asegurar_base():
    """Crea la base y las tablas si faltan (sin esto no hay bitácora donde escribir).
    Devuelve True si hubo que crear la base ahora."""
    estado = estado_base()
    if estado["lista"]:
        return False
    inicializar_base()
    return True


def _hay_corrida_en_curso():
    """True si otra ejecución está corriendo ahora mismo (evita dos a la vez,
    por ejemplo la app abierta y el Programador de Tareas al mismo tiempo)."""
    resiliencia.cerrar_corridas_abandonadas()
    with transaccion() as cur:
        cur.execute("""SELECT 1 FROM staging.etl_log
                       WHERE proceso = %s AND estado = 'EN_PROCESO' LIMIT 1""",
                    (PROCESO_CORRIDA,))
        return cur.fetchone() is not None


def _suma(resumenes, clave):
    return sum(r.get(clave, 0) for r in resumenes.values())


def _resultado(corrida, ok, inicio, error=None):
    return {"ok": ok, "id_corrida": corrida.id if corrida else None,
            "tipo_carga": corrida.tipo if corrida else None,
            "reanudada": bool(corrida and corrida.reanudada),
            "etapas": corrida.etapas if corrida else [],
            "resumen": corrida.resumen if corrida else {},
            "error": error, "duracion_seg": round(time.time() - inicio, 1)}


# ---------------------------------------------------------------
# Función principal
# ---------------------------------------------------------------
def ejecutar_etl(tipo_carga="INCREMENTAL", carpeta=None, reanudar=False, al_progreso=None):
    """Ejecuta el ETL completo.
      tipo_carga : 'COMPLETA' o 'INCREMENTAL'.
      carpeta    : carpeta con los Excel (por defecto data/entrada).
      reanudar   : True para continuar la última corrida que falló o se interrumpió.
      al_progreso: función opcional que recibe {'etapa','descripcion','estado','mensaje'}
                   en cada cambio (estado: INICIO, OK, OMITIDA o ERROR).
    Devuelve un dict con 'ok', 'id_corrida', 'tipo_carga', 'reanudada', 'etapas',
    'resumen', 'error' ({'etapa','mensaje'} o None) y 'duracion_seg'."""
    inicio = time.time()
    carpeta = Path(carpeta) if carpeta else Path(DATA_ENTRADA)
    tipo = normalizar_tipo(tipo_carga)

    # 0) Base lista y sin otra ejecución en curso
    try:
        base_nueva = con_reintentos(_asegurar_base, etapa="preparar la base de datos")
        if con_reintentos(_hay_corrida_en_curso, etapa="consultar la bitácora"):
            msg = "Ya hay una ejecución del ETL en curso. Espera a que termine."
            log.warning(msg)
            return _resultado(None, False, inicio, {"etapa": "INICIO", "mensaje": msg})
        previa = con_reintentos(resiliencia.corrida_reanudable, etapa="consultar la bitácora") \
            if reanudar else None
    except Exception as exc:
        log.exception("No se pudo iniciar el ETL")
        return _resultado(None, False, inicio, {"etapa": "INICIO", "mensaje": str(exc)})

    if previa:
        id_corrida, etapas_ok = previa["id_corrida"], previa["etapas_ok"]
        if previa["tipo_carga"] and previa["tipo_carga"] != tipo:
            log.warning("Se reanuda una carga %s (no %s), para no repetir el vaciado.",
                        previa["tipo_carga"], tipo)
        tipo = previa["tipo_carga"] or tipo
        log.info("Reanudando la corrida %s. Etapas ya terminadas: %s",
                 id_corrida, ", ".join(sorted(etapas_ok)) or "ninguna")
    else:
        id_corrida, etapas_ok = bitacora.nueva_corrida(), set()

    c = _Corrida(id_corrida, tipo, etapas_ok, reanudada=bool(previa), al_progreso=al_progreso)
    id_log_corrida = con_reintentos(bitacora.iniciar, id_corrida, PROCESO_CORRIDA,
                                    tipo_carga=tipo, etapa="bitácora")
    log.info("ETL %s iniciado (corrida %s)%s", tipo, id_corrida,
             " [reanudación]" if previa else "")

    try:
        _ejecutar_etapas(c, carpeta, base_nueva)
        c.resumen["bodega"] = carga_dw.contar_bodega()
        st, va = c.resumen["staging"], c.resumen.get("validacion", {})
        con_reintentos(
            bitacora.finalizar, id_log_corrida, estado="OK", etapa="bitácora",
            leidos=_suma(st, "leidos"),
            insertados=c.resumen.get("carga", {}).get("hecho", {}).get("nuevos", 0),
            actualizados=c.resumen.get("carga", {}).get("hecho", {}).get("actualizados", 0),
            rechazados=_suma(st, "rechazados") + _suma(va, "rechazados"),
            mensaje=f"Carga {tipo} terminada. Programas en la bodega: "
                    f"{c.resumen['bodega']['hecho_oferta_programa']}.")
        log.info("ETL terminado con éxito en %.1f s", time.time() - inicio)
        return _resultado(c, True, inicio)

    except EtapaFallida as exc:
        error = {"etapa": exc.proceso, "mensaje": exc.mensaje}
        _cerrar_con_error(id_log_corrida, f"Falló la etapa {exc.proceso}: {exc.mensaje}")
        return _resultado(c, False, inicio, error)

    except KeyboardInterrupt:
        _cerrar_con_error(id_log_corrida, "Ejecución interrumpida por el usuario.")
        raise

    except Exception as exc:                  # fallo fuera de una etapa (p. ej. al contar la bodega)
        log.exception("Error inesperado en el ETL")
        _cerrar_con_error(id_log_corrida, f"Error inesperado: {exc}")
        return _resultado(c, False, inicio, {"etapa": "GENERAL", "mensaje": str(exc)})

    finally:
        for libro in c.cache_excel.values():
            try:
                libro.close()
            except Exception:
                pass


def _cerrar_con_error(id_log_corrida, mensaje):
    """Marca la corrida como ERROR (esto enciende la alerta roja persistente)."""
    log.error(mensaje)
    try:
        con_reintentos(bitacora.finalizar, id_log_corrida, estado="ERROR",
                       mensaje=mensaje[:2000], etapa="bitácora")
    except Exception:
        log.exception("No se pudo marcar la corrida como ERROR en la bitácora")


# ---------------------------------------------------------------
# Las etapas, en orden
# ---------------------------------------------------------------
def _ejecutar_etapas(c, carpeta, base_nueva):
    nombres = dict(plan_etapas())

    # FC-01 Verificación de fuentes (solo lee: se repite siempre)
    def verificar():
        r = verificacion.verificar_fuentes(carpeta)
        if not r["ok"]:
            raise ValueError(" | ".join(r["problemas"]))
        return r
    c.etapa("FC-01_VERIFICACION", nombres["FC-01_VERIFICACION"], verificar,
            conteos=lambda r: {"leidos": len(r["detalle"]),
                               "mensaje": "Archivos, hojas y columnas correctos."})

    # FA-02 Respaldo antes de modificar datos
    def respaldar():
        if base_nueva:
            return {"ruta": None, "aviso": "Base nueva: no hay nada que respaldar."}
        try:
            return {"ruta": resiliencia.respaldar_bd(f"antes-{c.tipo.lower()}"), "aviso": None}
        except resiliencia.ErrorRespaldo as exc:
            if es_completa(c.tipo) and RESPALDO_OBLIGATORIO_EN_COMPLETA:
                raise                          # la carga completa vacía la bodega: sin respaldo no sigue
            log.warning("No se pudo hacer el respaldo; se continúa (carga incremental): %s", exc)
            return {"ruta": None, "aviso": f"Respaldo omitido por un fallo: {exc}"}
    def datos_respaldo(r):
        if r["ruta"]:
            c.resumen["respaldo"] = str(r["ruta"])
            return {"mensaje": f"Respaldo: {r['ruta'].name}"}
        return {"estado": "OMITIDA", "mensaje": r["aviso"]}
    c.etapa("FA-02_RESPALDO", nombres["FA-02_RESPALDO"], respaldar,
            escribe=True, solo_primera_vez=True, conteos=datos_respaldo)

    # FC-02 Preparación de Staging (¡una sola vez por corrida!)
    c.etapa("FC-02_PREPARAR_STAGING", nombres["FC-02_PREPARAR_STAGING"],
            lambda: staging.preparar_staging(c.tipo), escribe=True,
            conteos=lambda r: {"mensaje": r["mensaje"]})

    # FC-03 / FD-01 / FD-03: recorre las fuentes (ForEach) y las carga a Staging
    for fuente in fuentes_ordenadas():
        def cargar(fuente=fuente):
            df = extraccion.extraer_fuente(fuente, carpeta, c.cache_excel)
            r = staging.cargar_fuente_staging(fuente, df, c.id)
            c.resumen["staging"][fuente["nombre"]] = r
            return r
        proceso = f"FD-03_STAGING_{fuente['nombre']}"
        c.etapa(proceso, nombres[proceso], cargar, fuente=fuente["nombre"], escribe=True,
                conteos=lambda r: {"leidos": r["leidos"], "insertados": r["insertados"],
                                   "actualizados": r["actualizados"],
                                   "rechazados": r["rechazados"],
                                   "mensaje": f"Sin cambios: {r['sin_cambios']}"})

    # FD-04 / FD-05 Transformación (en memoria)
    def transformar():
        c.datos = transformacion.transformar_todo(c.id)
        return c.datos
    def datos_transformacion(d):
        c.resumen["transformacion"] = {k: len(v) for k, v in d.items()}
        return {"leidos": sum(len(v) for v in d.values())}
    c.etapa("FD-04_TRANSFORMACION", nombres["FD-04_TRANSFORMACION"], transformar,
            conteos=datos_transformacion)

    # FD-08 Validación (en memoria; los rechazos sí se guardan)
    def validar():
        c.validos, resumen = validacion.validar_todo(c.id, c.datos)
        c.resumen["validacion"] = resumen
        return resumen
    c.etapa("FD-08_VALIDACION", nombres["FD-08_VALIDACION"], validar,
            conteos=lambda r: {"leidos": _suma(r, "entrada"), "rechazados": _suma(r, "rechazados")})

    # FA-01 Homologación geográfica (se omite si falta el catálogo DANE)
    def homologar():
        if not hasattr(homologacion_geo, "homologar_todo"):
            return {"omitida": "El módulo de homologación geográfica aún no está instalado."}
        if not ARCHIVO_DANE.exists():
            return {"omitida": f"No se encontró el catálogo DANE ({ARCHIVO_DANE.name}); "
                               "se omite la homologación y se conservan los nombres originales."}
        c.validos, resumen = homologacion_geo.homologar_todo(c.id, c.validos)
        c.resumen["homologacion"] = resumen
        return resumen
    def datos_homologacion(r):
        if isinstance(r, dict) and "omitida" in r:
            return {"estado": "OMITIDA", "mensaje": r["omitida"]}
        return {"mensaje": str(r)[:500]}
    c.etapa("FA-01_HOMOLOGACION", nombres["FA-01_HOMOLOGACION"], homologar,
            conteos=datos_homologacion)

    # FD-06 Integración (en memoria)
    def integrar():
        c.integrados, resumen = integracion.integrar_todo(c.id, c.validos)
        c.resumen["integracion"] = resumen
        return resumen
    c.etapa("FD-06_INTEGRACION", nombres["FD-06_INTEGRACION"], integrar,
            conteos=lambda r: {"mensaje": str(r)[:500]})

    # FD-07 / FD-09 Carga a la bodega (una transacción; no se repite si ya terminó)
    def cargar_bodega():
        r = carga_dw.cargar_dw(c.id, c.integrados)
        c.resumen["carga"] = r
        return r
    c.etapa("FD-09_CARGA_DW", nombres["FD-09_CARGA_DW"], cargar_bodega, escribe=True,
            conteos=lambda r: {"leidos": len(c.integrados["programas"]),
                               "insertados": r["hecho"]["nuevos"],
                               "actualizados": r["hecho"]["actualizados"],
                               "mensaje": f"Hecho sin cambios: {r['hecho']['sin_cambios']}"})