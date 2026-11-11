"""
ui/carga.py   (FC-01, FC-05)
Pantalla de carga de archivos. Tiene exactamente DOS cargadores:
  - Instituciones.xlsx  (hojas Instituciones y Directivos)
  - Programas.xlsx      (hojas Programas y Cobertura)

Cada archivo se guarda en data/entrada/ con el nombre que espera el ETL y se
verifica de inmediato con etl.verificacion (FC-01): que se pueda abrir, que
tenga las hojas y las columnas requeridas. El catálogo DANE no se sube aquí:
viaja dentro del proyecto en data/catalogos/.

Uso desde app.py:
    from ui.carga import mostrar_carga
    archivos_ok = mostrar_carga()      # True si los dos archivos están listos
"""
from datetime import datetime

import streamlit as st

from config.settings import DATA_ENTRADA
from etl.verificacion import verificar_fuentes
from ui.alertas import mostrar_alerta_persistente

# (archivo esperado, texto de ayuda) en el orden en que se muestran
CARGADORES = [
    ("Instituciones.xlsx", "Debe tener las hojas Instituciones y Directivos."),
    ("Programas.xlsx", "Debe tener las hojas Programas y Cobertura."),
]


def _estado_en_disco(nombre):
    """Texto con la fecha del archivo que ya está en data/entrada, o None."""
    ruta = DATA_ENTRADA / nombre
    if not ruta.exists():
        return None
    fecha = datetime.fromtimestamp(ruta.stat().st_mtime)
    return f"{nombre} ({ruta.stat().st_size / 1_048_576:.1f} MB, guardado el {fecha:%Y-%m-%d %H:%M})"


def _guardar(archivo_subido, nombre):
    """Guarda el archivo subido con el nombre esperado. Solo si es nuevo,
    porque Streamlit devuelve el mismo archivo en cada recarga de la pantalla."""
    firma = (archivo_subido.name, archivo_subido.size)
    if st.session_state.get(f"firma_{nombre}") == firma:
        return False
    DATA_ENTRADA.mkdir(parents=True, exist_ok=True)
    (DATA_ENTRADA / nombre).write_bytes(archivo_subido.getbuffer())
    st.session_state[f"firma_{nombre}"] = firma
    return True


def _verificar():
    with st.spinner("Verificando archivos..."):
        st.session_state["verificacion"] = verificar_fuentes(DATA_ENTRADA)


def mostrar_carga():
    """Dibuja la pantalla. Devuelve True si la verificación FC-01 fue exitosa."""
    st.header("Carga de archivos")
    mostrar_alerta_persistente()
    st.write("Sube los dos archivos del SNIES. Se guardan en el proyecto y se revisan "
             "antes de poder ejecutar el ETL.")

    hubo_cambio = False
    columnas = st.columns(len(CARGADORES))
    for col, (nombre, ayuda) in zip(columnas, CARGADORES):
        with col:
            subido = st.file_uploader(nombre, type=["xlsx"], key=f"up_{nombre}", help=ayuda)
            st.caption(ayuda)
            if subido is not None:
                if subido.name != nombre:
                    st.caption(f"Se guardará como {nombre}.")
                try:
                    hubo_cambio |= _guardar(subido, nombre)
                except OSError as exc:
                    st.error(f"No se pudo guardar {nombre}: {exc}")
            en_disco = _estado_en_disco(nombre)
            if en_disco:
                st.success(en_disco)
            else:
                st.warning(f"Falta {nombre}.")

    st.divider()
    if hubo_cambio or st.button("Verificar archivos"):
        _verificar()

    resultado = st.session_state.get("verificacion")
    if resultado is None:
        st.info("Aún no se han verificado los archivos.")
        return False

    for d in resultado["detalle"]:
        if d["ok"]:
            st.write(f"✅ {d['archivo']} / {d['hoja']}: hoja y columnas correctas.")
        else:
            st.write(f"❌ {d['motivo']}")
    if resultado["ok"]:
        st.success("Archivos correctos. Ya puedes ejecutar el ETL.")
    else:
        st.error("Corrige los archivos indicados y vuelve a cargarlos. El ETL no se ejecutará "
                 "hasta que la verificación sea correcta.")
    return resultado["ok"]