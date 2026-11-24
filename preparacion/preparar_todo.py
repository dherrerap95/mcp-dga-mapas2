# Ejecuta en orden la lectura, la corrección y la unificación de estaciones de Mapas DGA.
import subprocess
import sys
from pathlib import Path

PASOS = ["leer_mapas2.py", "corregir_diarias.py", "corregir_irregulares.py", "corregir_calidad.py", "estaciones.py",
         "convertir_mapas2.py", "validar_mapas2.py", "muestra_mapas2.py"]


# Corre cada paso y se detiene ante el primer error.
def main():
    carpeta = Path(__file__).resolve().parent
    for paso in PASOS:
        print(f"== {paso}", flush=True)
        subprocess.run([sys.executable, str(carpeta / paso)], check=True)


if __name__ == "__main__":
    main()
