"""
ui/ejecucion.py   (FC-05, FC-04, FA-02)
Pantalla para ejecutar el ETL completo con una sola acción:
  - elegir el tipo de carga (COMPLETA o INCREMENTAL),
  - ejecutar, viendo el avance etapa por etapa,
  - reanudar una ejecución que falló o se interrumpió,
  - ver el resultado, el historial de ejecuciones y los respaldos.

Uso desde app.py:
    from ui.ejecucion import mostrar_ejecucion
    mostrar_ejecucion(archivos_ok)     # archivos_ok viene de ui.carga.mostrar_carga()
"""
import pandas as pd
import streamlit as st

from db import bitacora
from etl.orquestador import ejecutar_etl, plan_etapas
from etl.resiliencia import PROCESO_CORRIDA, corrida_reanudable, listar_respaldos
from ui.alertas import mostrar_alerta_persistente

ICONOS = {"INICIO": "⏳", "OK": "✅", "OMITIDA": "⏭️", "ERROR": "❌", "PENDIENTE": "⬜"}

DESCRIPCION_TIPO = {
    "INCREMENTAL": "Inserta solo los registros nuevos y actualiza los que cambiaron. "
                   "No borra nada.",
    "COMPLETA": "Vacía Staging y la bodega y vuelve a cargar todo desde cero. "
                "Antes se genera un respaldo.",
}


def _lineas_avance(plan, estados):
    """Una línea de texto por etapa, con su icono y el mensaje si lo hay."""
    lineas = []
    for proceso, descripcion in plan:
        estado, mensaje = estados.get(proceso, ("PENDIENTE", ""))
        linea = f"{ICONOS.get(estado, '⬜')} {descripcion}"
        if mensaje and estado in ("OK", "OMITIDA", "ERROR"):
            linea += f" — {mensaje}"
        lineas.append(linea)
    return lineas


def _ejecutar(tipo, reanudar):
    """Corre el ETL mostrando el avance en vivo y guarda el resultado."""
    plan = plan_etapas()
    estados = {}
    barra = st.progress(0.0, text="Iniciando...")
    zona = st.empty()

    def al_progreso(ev):
        estados[ev["etapa"]] = (ev["estado"], ev["mensaje"])
        terminadas = sum(1 for e, _ in estados.values() if e in ("OK", "OMITIDA"))
        barra.progress(min(terminadas / len(plan), 1.0), text=ev["descripcion"])
        zona.markdown("\n\n".join(_lineas_avance(plan, estados)))

    resultado = ejecutar_etl(tipo_carga=tipo, reanudar=reanudar, al_progreso=al_progreso)
    barra.progress(1.0 if resultado["ok"] else barra_valor(estados, plan),
                   text="Terminado" if resultado["ok"] else "Detenido por un error")
    st.session_state["resultado_etl"] = resultado
    st.session_state["avance_etl"] = _lineas_avance(plan, estados)


def barra_valor(estados, plan):
    terminadas = sum(1 for e, _ in estados.values() if e in ("OK", "OMITIDA"))
    return min(terminadas / len(plan), 1.0)


def _mostrar_resultado():
    resultado = st.session_state.get("resultado_etl")
    if not resultado:
        return
    st.subheader("Resultado de la última ejecución")
    if resultado["ok"]:
        st.success(f"ETL {resultado['tipo_carga']} terminado en {resultado['duracion_seg']} s"
                   + (" (reanudado)." if resultado["reanudada"] else "."))
        bodega = resultado["resumen"].get("bodega")
        if bodega:
            cols = st.columns(min(len(bodega), 4))
            for i, (tabla, cantidad) in enumerate(bodega.items()):
                cols[i % len(cols)].metric(tabla.replace("_", " ").capitalize(), f"{cantidad:,}")
    else:
        error = resultado["error"] or {}
        st.error(f"El ETL se detuvo en la etapa {error.get('etapa', '?')}: "
                 f"{error.get('mensaje', 'error desconocido')}\n\n"
                 "Cuando corrijas el problema, usa «Reanudar» para continuar desde el último "
                 "paso válido.")
    with st.expander("Avance por etapa"):
        st.markdown("\n\n".join(st.session_state.get("avance_etl", [])))
    with st.expander("Detalle de la corrida"):
        st.json(resultado["resumen"], expanded=False)


def _historial():
    st.subheader("Historial de ejecuciones")
    try:
        filas = bitacora.historial(limite=500)
    except Exception as exc:
        st.info(f"No se pudo leer la bitácora todavía. ({exc})")
        return
    corridas = [f for f in filas if f["proceso"] == PROCESO_CORRIDA][:10]
    if not corridas:
        st.caption("Aún no hay ejecuciones registradas.")
        return
    df = pd.DataFrame(corridas)[["fecha_inicio", "tipo_carga", "estado", "registros_leidos",
                                 "registros_insertados", "registros_actualizados",
                                 "registros_rechazados", "mensaje"]]
    st.dataframe(df, use_container_width=True, hide_index=True)


def _respaldos():
    with st.expander("Respaldos de la base de datos (últimos 5)"):
        respaldos = listar_respaldos()
        if not respaldos:
            st.caption("Todavía no hay respaldos. Se generan automáticamente antes de cada carga.")
        for r in respaldos:
            st.write(f"{r['nombre']} — {r['tamano_mb']} MB — {r['fecha']:%Y-%m-%d %H:%M}")


def mostrar_ejecucion(archivos_ok=True):
    st.header("Ejecución del ETL")
    mostrar_alerta_persistente()

    try:
        pendiente = corrida_reanudable()
    except Exception:
        pendiente = None          # sin base de datos todavía: la pantalla de inicio lo explica

    # --- Reanudar una corrida que falló
    if pendiente:
        with st.container(border=True):
            st.warning(f"Hay una ejecución {pendiente['tipo_carga'] or ''} sin terminar "
                       f"({pendiente['fecha']:%Y-%m-%d %H:%M}). Ya completó: "
                       f"{len(pendiente['etapas_ok'])} etapas. No se repetirá el trabajo hecho.")
            if st.button("Reanudar desde el último paso válido", type="primary",
                         disabled=not archivos_ok):
                _ejecutar(pendiente["tipo_carga"], reanudar=True)

    # --- Nueva ejecución
    st.subheader("Nueva ejecución")
    if not archivos_ok:
        st.info("Primero carga y verifica los dos archivos en la pantalla «Carga de archivos».")
    tipo = st.radio("Tipo de carga", ["INCREMENTAL", "COMPLETA"], horizontal=True,
                    format_func=str.capitalize)
    st.caption(DESCRIPCION_TIPO[tipo])
    confirma = True
    if tipo == "COMPLETA":
        confirma = st.checkbox("Entiendo que se borrará el contenido actual de Staging y la bodega.")
    if st.button("Ejecutar ETL", type="primary" if not pendiente else "secondary",
                 disabled=not (archivos_ok and confirma)):
        _ejecutar(tipo, reanudar=False)

    _mostrar_resultado()
    st.divider()
    _historial()
    _respaldos()