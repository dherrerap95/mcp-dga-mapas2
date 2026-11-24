# Funciones comunes de la preparación de Mapas DGA: rutas, lectura, metadatos y bitácora.
import json
import zipfile
from pathlib import Path

import numpy as np
import pandas as pd

RAIZ = Path(__file__).resolve().parent.parent
META = ["codigo", "nombre", "cod_region", "region", "comuna", "lat", "lon", "utm_e", "utm_n", "altitud"]
COLUMNAS_BITACORA = [
    "informe", "codigo", "variable", "campo", "fecha_original", "fecha_corregida", "hora", "profundidad",
    "valor_anterior", "valor_corregido", "unidad_anterior", "unidad_corregida", "accion", "regla", "evidencia",
]


# Lee el archivo de parámetros de la fuente.
def parametros():
    with open(RAIZ / "parametros" / "mapas2_dga.json", encoding="utf-8") as f:
        return json.load(f)


# Lee el archivo de reglas de corrección.
def reglas():
    with open(RAIZ / "parametros" / "correcciones_mapas2.json", encoding="utf-8") as f:
        return json.load(f)


# Devuelve una ruta de los parámetros resuelta desde la raíz del repositorio.
def ruta(clave):
    p = (RAIZ / parametros()[clave]).resolve()
    p.mkdir(parents=True, exist_ok=True)
    return p


# Abre un informe original, comprimido o no, como flujo binario.
def abrir(archivo):
    base = ruta("originales")
    if archivo.endswith(".zip"):
        zf = zipfile.ZipFile(base / archivo)
        return zf.open(zf.namelist()[0])
    return open(base / archivo, "rb")


# Lee un informe por bloques como texto y entrega los metadatos de estación y las columnas pedidas.
def leer_informe(archivo, columnas, tam=1_000_000):
    metas, obs = [], []
    for b in pd.read_csv(abrir(archivo), dtype=str, keep_default_na=False, chunksize=tam, encoding="utf-8"):
        b.columns = [c.strip() for c in b.columns]
        m = b.iloc[:, :10].copy()
        m.columns = META
        metas.append(m.drop_duplicates("codigo"))
        o = b[columnas].copy()
        o.insert(0, "codigo", m["codigo"].str.strip())
        obs.append(o)
    meta = pd.concat(metas).drop_duplicates("codigo")
    for c in META:
        meta[c] = meta[c].str.strip()
    return meta.set_index("codigo"), pd.concat(obs, ignore_index=True)


# Convierte coordenadas 0GGMMSS a grados decimales negativos y registra los minutos o segundos iguales a 60.
def gms_a_decimal(texto):
    t = str(texto).strip()
    if not t.isdigit():
        return np.nan, False
    t = t.zfill(7)
    g, m, s = int(t[:-4]), int(t[-4:-2]), int(t[-2:])
    sesenta = m >= 60 or s >= 60
    return -(g + m / 60 + s / 3600), sesenta


# Convierte fechas dd/mm/aaaa o dd-mm-aaaa a fechas de pandas.
def leer_fechas(serie, formato="%d/%m/%Y"):
    return pd.to_datetime(serie.str.strip(), format=formato, errors="coerce")


# Acumula filas de bitácora en una lista y las entrega como tabla.
class Bitacora:
    def __init__(self, informe):
        self.informe = informe
        self.filas = []

    # Agrega una fila de bitácora.
    def agregar(self, **campos):
        fila = {c: "" for c in COLUMNAS_BITACORA}
        fila["informe"] = self.informe
        fila.update({k: v for k, v in campos.items() if v is not None})
        self.filas.append(fila)

    # Agrega muchas filas a partir de una tabla con las columnas de la bitácora.
    def agregar_tabla(self, t):
        if len(t):
            t = t.copy()
            t["informe"] = self.informe
            for c in COLUMNAS_BITACORA:
                if c not in t.columns:
                    t[c] = ""
            self.filas.extend(t[COLUMNAS_BITACORA].to_dict("records"))

    # Devuelve la bitácora como tabla.
    def tabla(self):
        return pd.DataFrame(self.filas, columns=COLUMNAS_BITACORA)


# Escribe un CSV para Excel: separador punto y coma y coma decimal.
def a_csv(df, destino):
    df.to_csv(destino, sep=";", decimal=",", index=False, encoding="utf-8-sig")


# Formatea una fecha como texto ISO o vacío.
def iso(f):
    return "" if pd.isna(f) else pd.Timestamp(f).date().isoformat()
