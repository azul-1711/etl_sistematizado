"""
ui/errores.py   (FC-06)
Pantalla para consultar:
  - Errores por etapa: qué etapa falló y por qué (bitácora, estado ERROR).
  - Filas rechazadas: la fila completa tal como estaba en Staging y el motivo
    del rechazo (staging.rechazos_etl).

Uso desde app.py:
    from ui.errores import mostrar_errores
    mostrar_errores()
"""
import json

import pandas as pd
import streamlit as st

from db import bitacora, rechazos
from etl.resiliencia import PROCESO_CORRIDA
from ui.alertas import mostrar_alerta_persistente


def _opciones_corrida():
    """[(texto, id_corrida o None)] con 'Todas' y las últimas corridas."""
    opciones = [("Todas las ejecuciones", None)]
    for f in bitacora.historial(limite=1000):
        if f["proceso"] == PROCESO_CORRIDA:
            texto = (f"{f['fecha_inicio']:%Y-%m-%d %H:%M} · {f['tipo_carga'] or '?'} · "
                     f"{f['estado']} · {str(f['id_corrida'])[:8]}")
            opciones.append((texto, str(f["id_corrida"])))
    return opciones[:21]


def _tabla_errores(id_corrida):
    filas = [f for f in bitacora.historial(limite=1000)
             if f["estado"] == "ERROR" and (id_corrida is None or str(f["id_corrida"]) == id_corrida)]
    if not filas:
        st.success("No hay errores registrados.")
        return
    df = pd.DataFrame(filas)[["fecha_fin", "proceso", "fuente", "mensaje", "id_corrida"]]
    df = df.rename(columns={"proceso": "etapa", "mensaje": "causa"})
    st.dataframe(df, use_container_width=True, hide_index=True)


def _df_rechazos(filas):
    """DataFrame legible; la fila completa se convierte a texto JSON."""
    df = pd.DataFrame(filas)
    df["fila_completa"] = df["fila_completa"].map(
        lambda v: json.dumps(v, ensure_ascii=False) if v is not None else "")
    return df


def _tabla_rechazos(id_corrida):
    limite = st.number_input("Máximo de filas a cargar", 100, 50000, 2000, step=500)
    filas = rechazos.consultar(id_corrida, limite=int(limite))
    if not filas:
        st.success("No hay filas rechazadas.")
        return
    df = _df_rechazos(filas)

    st.markdown("**Resumen por motivo**")
    resumen = (df.groupby(["tabla_origen", "motivo_rechazo"]).size()
                 .reset_index(name="filas").sort_values("filas", ascending=False))
    st.dataframe(resumen, use_container_width=True, hide_index=True)

    f1, f2 = st.columns(2)
    tabla = f1.selectbox("Tabla de origen", ["Todas"] + sorted(df["tabla_origen"].unique()))
    texto = f2.text_input("Buscar en el motivo o en la fila")
    vista = df if tabla == "Todas" else df[df["tabla_origen"] == tabla]
    if texto:
        t = texto.lower()
        vista = vista[vista["motivo_rechazo"].str.lower().str.contains(t, regex=False)
                      | vista["fila_completa"].str.lower().str.contains(t, regex=False)]

    st.caption(f"{len(vista)} de {len(df)} filas")
    st.dataframe(vista.drop(columns=["id_corrida"]), use_container_width=True, hide_index=True)
    st.download_button("Descargar CSV", vista.to_csv(index=False).encode("utf-8-sig"),
                       file_name="filas_rechazadas.csv", mime="text/csv")

    if not vista.empty:
        elegido = st.selectbox("Ver la fila completa del rechazo", vista["id_rechazo"].tolist())
        original = next(f for f in filas if f["id_rechazo"] == elegido)
        st.write(f"**Motivo:** {original['motivo_rechazo']}")
        st.json(original["fila_completa"])


def mostrar_errores():
    st.header("Errores y filas rechazadas")
    mostrar_alerta_persistente()

    try:
        opciones = _opciones_corrida()
    except Exception as exc:
        st.info(f"Todavía no hay información para mostrar. ({exc})")
        return
    texto = st.selectbox("Ejecución", [o[0] for o in opciones])
    id_corrida = dict(opciones)[texto]

    tab_err, tab_rech = st.tabs(["Errores por etapa", "Filas rechazadas"])
    with tab_err:
        _tabla_errores(id_corrida)
    with tab_rech:
        _tabla_rechazos(id_corrida)