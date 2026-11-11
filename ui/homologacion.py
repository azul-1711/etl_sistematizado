"""
ui/homologacion.py   (FA-01, FC-05)
Pantalla de Streamlit para revisar la homologación geográfica contra el DANE.

El usuario ve los valores que el sistema no pudo resolver solo (REVISION y
ADVERTENCIA), y para cada uno puede:
  - Aprobar la opción propuesta,
  - Escoger otra opción del catálogo DANE y aprobarla, o
  - Rechazar (se conserva el valor original).
La decisión queda guardada y se aplica en la siguiente ejecución del ETL.

Uso desde app.py:
    from ui.homologacion import mostrar_homologacion
    mostrar_homologacion()
"""
import pandas as pd
import streamlit as st

from config.settings import ARCHIVO_DANE
from etl import homologacion_geo as H
from ui.alertas import mostrar_alerta_persistente

POR_PAGINA = 15


def _cargar_traza():
    """Trazabilidad de la última corrida que homologó. Devuelve (id_corrida, filas)."""
    filas = H.consultar_traza(limite=20000)
    if not filas:
        return None, []
    id_corrida = max(filas, key=lambda f: f["id_homologacion"])["id_corrida"]
    return id_corrida, [f for f in filas if f["id_corrida"] == id_corrida]


def _tarjeta_caso(caso):
    """Una fila por revisar, con sus botones."""
    id_h = caso["id_homologacion"]
    with st.container(border=True):
        c1, c2, c3 = st.columns([3, 3, 1])
        c1.markdown(f"**{caso['nivel'].capitalize()}:** {caso['valor_original']}")
        if caso["departamento_ref"]:
            c1.caption(f"Dentro del departamento: {caso['departamento_ref']}")
        propuesta = caso["valor_homologado"]
        c2.markdown(f"**Propuesta del DANE:** {propuesta or 'sin propuesta'}")
        c3.metric("Puntaje", f"{caso['puntaje']:.0f}")

        opciones = [n for n, _ in H.candidatos(caso["nivel"], caso["valor_original"],
                                               caso["departamento_ref"], n=6)]
        if propuesta and propuesta not in opciones:
            opciones.insert(0, propuesta)
        indice = opciones.index(propuesta) if propuesta in opciones else 0
        elegido = st.selectbox("Valor del catálogo DANE", opciones, index=indice,
                               key=f"op_{id_h}") if opciones else None

        b1, b2, _ = st.columns([1, 1, 3])
        if b1.button("Aprobar", key=f"ok_{id_h}", type="primary", disabled=not elegido):
            _guardar(id_h, H.APROBADA, elegido)
        if b2.button("Rechazar", key=f"no_{id_h}",
                     help="Se conserva el valor original tal como viene en el archivo."):
            _guardar(id_h, H.RECHAZADA)


def _guardar(id_h, estado, valor=None):
    try:
        H.resolver_revision(id_h, estado, valor)
    except Exception as exc:
        st.error(f"No se pudo guardar la decisión: {exc}")
        return
    st.rerun()


def mostrar_homologacion():
    st.header("Homologación geográfica")
    mostrar_alerta_persistente()

    if not ARCHIVO_DANE.exists():
        st.warning(f"No se encontró el catálogo DANE ({ARCHIVO_DANE.name}) en data/catalogos/. "
                   "Sin él, el ETL omite la homologación.")
        return

    try:
        id_corrida, filas = _cargar_traza()
    except Exception as exc:
        st.info("Todavía no hay información de homologación. Ejecuta el ETL primero. "
                f"(Detalle: {exc})")
        return
    if not filas:
        st.info("Todavía no hay información de homologación. Ejecuta el ETL primero.")
        return

    def n(clase):
        return sum(1 for f in filas if f["clasificacion"] == clase)
    pendientes = [f for f in filas if f["estado_revision"] == H.PENDIENTE]

    m = st.columns(5)
    m[0].metric("Valores revisados", len(filas))
    m[1].metric("Automáticos", n(H.AUTOMATICO))
    m[2].metric("En revisión", n(H.REVISION))
    m[3].metric("Advertencias", n(H.ADVERTENCIA))
    m[4].metric("Pendientes", len(pendientes))
    st.caption(f"Última ejecución: {id_corrida}. Las decisiones que tomes aquí se aplican "
               "en la siguiente ejecución del ETL.")

    tab_rev, tab_todo = st.tabs(["Por revisar", "Trazabilidad completa"])

    with tab_rev:
        if not pendientes:
            st.success("No hay casos pendientes de revisión.")
        else:
            f1, f2 = st.columns(2)
            clase = f1.selectbox("Clasificación", ["Todas", H.REVISION, H.ADVERTENCIA])
            nivel = f2.selectbox("Nivel", ["Todos", H.NIVEL_DEP, H.NIVEL_MUN])
            vista = [f for f in pendientes
                     if clase in ("Todas", f["clasificacion"])
                     and nivel in ("Todos", f["nivel"])]
            paginas = max(1, -(-len(vista) // POR_PAGINA))
            pagina = st.number_input(f"Página (de {paginas})", 1, paginas, 1) if paginas > 1 else 1
            ini = (pagina - 1) * POR_PAGINA
            st.write(f"{len(vista)} casos")
            for caso in vista[ini:ini + POR_PAGINA]:
                _tarjeta_caso(caso)

    with tab_todo:
        df = pd.DataFrame(filas).drop(columns=["id_corrida"])
        st.dataframe(df, use_container_width=True, hide_index=True)
        st.download_button("Descargar CSV", df.to_csv(index=False).encode("utf-8-sig"),
                           file_name="homologacion_geografica.csv", mime="text/csv")