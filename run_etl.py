"""
run_etl.py   (FC-05)
Entrada por consola del ETL. Es el script que ejecuta el Programador de Tareas
de Windows para las actualizaciones programadas, sin necesidad de abrir la app.

Ejemplos:
    python run_etl.py                       (carga incremental)
    python run_etl.py --tipo COMPLETA
"""
import argparse
import sys

from etl.orquestador import ejecutar_etl


def main():
    parser = argparse.ArgumentParser(description="ETL SNIES -> Bodega de datos")
    parser.add_argument("--tipo", default="INCREMENTAL",
                        choices=["COMPLETA", "INCREMENTAL"], type=str.upper,
                        help="Tipo de carga (por defecto: INCREMENTAL)")
    parser.add_argument("--reanudar", action="store_true",
                        help="Continuar la última ejecución que falló o se interrumpió")
    args = parser.parse_args()

    resultado = ejecutar_etl(tipo_carga=args.tipo, reanudar=args.reanudar)
    sys.exit(0 if resultado.get("ok") else 1)


if __name__ == "__main__":
    main()