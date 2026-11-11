"""
app.py   (FC-05)
Aplicación Streamlit del ETL SNIES. Se inicia con:

    streamlit run app.py

Pantallas:
  Inicio y base de datos   estado de PostgreSQL y botón «Preparar base de datos»
  Carga de archivos        los dos cargadores y la verificación FC-01
  Ejecución del ETL        ejecutar, reanudar, historial y respaldos
  Errores y rechazos       errores por etapa y filas rechazadas
  Homologación geográfica  revisión de los casos de FA-01
  Programación             cómo programar ejecuciones con el Programador de Tareas
"""
import sys

import streamlit as st

from config.settings import BASE_DIR, DB_CONFIG
from db.conexion import estado_base, inicializar_base
from etl.verificacion import verificar_fuentes
from ui.alertas import mostrar_alerta_persistente
from ui.carga import mostrar_carga
from ui.ejecucion import mostrar_ejecucion
from ui.errores import mostrar_errores
from ui.homologacion import mostrar_homologacion

st.set_page_config(page_title="ETL SNIES", page_icon="🎓", layout="wide")

PANTALLAS = ["Inicio y base de datos", "Carga de archivos", "Ejecución del ETL",
             "Errores y rechazos", "Homologación geográfica", "Programación"]


# ---------------------------------------------------------------
# Apoyo
# ---------------------------------------------------------------
def _estado():
    """Estado de la base, o un estado de error si no se puede consultar."""
    try:
        return estado_base()
    except Exception as exc:
        return {"servidor_ok": False, "base_existe": False, "tablas_faltantes": [],
                "lista": False, "mensaje": str(exc)}


def _archivos_ok():
    """Resultado de la verificación FC-01; la hace una vez si aún no existe."""
    if "verificacion" not in st.session_state:
        st.session_state["verificacion"] = verificar_fuentes()
    return st.session_state["verificacion"]["ok"]


def comando_programacion(tipo, frecuencia, hora):
    """Comando schtasks para programar run_etl.py en el Programador de Tareas."""
    # run_etl.py calcula sus rutas a partir de su propia ubicación, así que no
    # necesita carpeta de trabajo. Las comillas \" protegen rutas con espacios.
    script = BASE_DIR / "run_etl.py"
    accion = f'\\"{sys.executable}\\" \\"{script}\\" --tipo {tipo}'
    return (f'schtasks /Create /TN "ETL SNIES {tipo.title()}" /SC {frecuencia} '
            f'/ST {hora} /TR "{accion}" /F')


# ---------------------------------------------------------------
# Pantallas propias de app.py
# ---------------------------------------------------------------
def pantalla_inicio(estado):
    st.header("ETL SNIES")
    mostrar_alerta_persistente()
    st.write("Sistema ETL sistematizado para el SNIES: lee los archivos de Instituciones y "
             "Programas, los limpia, los valida y los carga a la bodega de datos en PostgreSQL.")

    st.subheader("Base de datos")
    st.caption(f"Servidor {DB_CONFIG['host']}:{DB_CONFIG['port']} · base {DB_CONFIG['dbname']} · "
               f"usuario {DB_CONFIG['user']}")
    if estado["lista"]:
        st.success("La base está lista: existen todas las tablas necesarias.")
    elif not estado["servidor_ok"]:
        st.error(estado["mensaje"])
    else:
        st.warning(estado["mensaje"])

    if not estado["lista"]:
        if st.button("Preparar base de datos", type="primary", disabled=not estado["servidor_ok"]):
            try:
                with st.spinner("Creando la base y las tablas..."):
                    resultado = inicializar_base()
            except Exception as exc:
                st.error(f"No se pudo preparar la base: {exc}")
            else:
                st.success("Base de datos preparada." if resultado["lista"]
                           else f"Quedó incompleta: {resultado['mensaje']}")
                st.rerun()
    elif st.button("Volver a comprobar"):
        st.rerun()

    st.subheader("Cómo usarlo")
    st.markdown("1. **Carga de archivos:** sube `Instituciones.xlsx` y `Programas.xlsx`.\n"
                "2. **Ejecución del ETL:** elige el tipo de carga y pulsa ejecutar.\n"
                "3. **Errores y rechazos** y **Homologación geográfica:** revisa lo que quedó "
                "por corregir.")


def pantalla_programacion():
    st.header("Programación")
    mostrar_alerta_persistente()
    st.write("El Programador de Tareas de Windows puede ejecutar el ETL sin abrir la app. "
             "Usa los archivos que estén en `data/entrada/` en ese momento, así que deja ahí "
             "los más recientes.")
    c1, c2, c3 = st.columns(3)
    tipo = c1.selectbox("Tipo de carga", ["INCREMENTAL", "COMPLETA"])
    frecuencia = c2.selectbox("Frecuencia", ["DAILY", "WEEKLY", "MONTHLY"],
                              format_func={"DAILY": "Diaria", "WEEKLY": "Semanal",
                                           "MONTHLY": "Mensual"}.get)
    hora = c3.text_input("Hora (24 h)", "06:00")
    st.write("Abre el Símbolo del sistema de Windows y pega este comando:")
    st.code(comando_programacion(tipo, frecuencia, hora), language="bat")
    st.caption("Si una ejecución falla, la próxima vez que abras la app verás la alerta roja y "
               "podrás reanudarla desde «Ejecución del ETL». También se puede lanzar a mano con "
               "`python run_etl.py --tipo INCREMENTAL` (agrega `--reanudar` para continuar una "
               "ejecución que falló).")


# ---------------------------------------------------------------
# Menú
# ---------------------------------------------------------------
def main():
    estado = _estado()
    st.sidebar.title("ETL SNIES")
    pantalla = st.sidebar.radio("Pantalla", PANTALLAS)
    st.sidebar.caption("Base lista ✅" if estado["lista"] else "Base sin preparar ⚠️")

    if pantalla == "Inicio y base de datos":
        pantalla_inicio(estado)
    elif pantalla == "Programación":
        pantalla_programacion()
    elif not estado["lista"]:
        st.warning("Primero prepara la base de datos en «Inicio y base de datos».")
    elif pantalla == "Carga de archivos":
        mostrar_carga()
    elif pantalla == "Ejecución del ETL":
        mostrar_ejecucion(_archivos_ok())
    elif pantalla == "Errores y rechazos":
        mostrar_errores()
    elif pantalla == "Homologación geográfica":
        mostrar_homologacion()


main()