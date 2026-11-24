# Lee los informes originales de Mapas DGA y los guarda normalizados como tablas intermedias.
import sys

import numpy as np
import pandas as pd

import comun


# Cuenta los decimales escritos en cada valor de texto.
def decimales(texto):
    t = texto.str.strip()
    pos = t.str.find(".")
    return np.where(pos >= 0, t.str.len() - pos - 1, 0).astype("int8")


# Lee un informe diario y lo deja como codigo, fecha, valor y decimales escritos.
def leer_diario(nombre, inf):
    meta, obs = comun.leer_informe(inf["archivo"], [inf["fecha"], inf["valor"], "Unidad"])
    unidades = obs["Unidad"].str.strip().value_counts().to_dict()
    t = pd.DataFrame({
        "codigo": obs["codigo"].astype("category"),
        "fecha": comun.leer_fechas(obs[inf["fecha"]]),
        "valor": pd.to_numeric(obs[inf["valor"]].str.strip(), errors="coerce"),
        "decimales": decimales(obs[inf["valor"]]),
    })
    malas = int(t["fecha"].isna().sum())
    no_num = int((t["valor"].isna() & (obs[inf["valor"]].str.strip() != "")).sum())
    print(f"  {nombre}: {len(t)} filas, {len(meta)} estaciones, unidades {unidades}, "
          f"fechas ilegibles {malas}, valores no numéricos {no_num}")
    return meta, t


# Lee un informe de mediciones irregulares (pozos o sedimentos integrados) conservando el orden del archivo.
def leer_irregular(nombre, inf):
    extra = ["Observacion"] if nombre == "pozos" else []
    meta, obs = comun.leer_informe(inf["archivo"], [inf["fecha"], inf["valor"], "Unidad"] + extra)
    t = pd.DataFrame({
        "codigo": obs["codigo"],
        "fila": np.arange(len(obs)),
        "fecha_texto": obs[inf["fecha"]].str.strip(),
        "fecha": comun.leer_fechas(obs[inf["fecha"]]),
        "valor": pd.to_numeric(obs[inf["valor"]].str.strip(), errors="coerce"),
        "unidad": obs["Unidad"].str.strip(),
    })
    if extra:
        t["observacion"] = obs["Observacion"].str.strip()
    print(f"  {nombre}: {len(t)} filas, {len(meta)} estaciones, unidades {t['unidad'].value_counts().to_dict()}, "
          f"fechas ilegibles {int(t['fecha'].isna().sum())}, sin valor {int(t['valor'].isna().sum())}")
    return meta, t


# Lee el muestreo rutinario de sedimentos, con la fecha en tres columnas y la hora tal como viene.
def leer_rutinario(nombre, inf):
    meta, obs = comun.leer_informe(inf["archivo"], ["Día", "Mes", "Año", "Hora", inf["valor"], "Unidad"])
    texto = obs["Año"].str.strip() + "-" + obs["Mes"].str.strip().str.zfill(2) + "-" + obs["Día"].str.strip().str.zfill(2)
    t = pd.DataFrame({
        "codigo": obs["codigo"],
        "fila": np.arange(len(obs)),
        "fecha": pd.to_datetime(texto, format="%Y-%m-%d", errors="coerce"),
        "hora_texto": obs["Hora"],
        "valor": pd.to_numeric(obs[inf["valor"]].str.strip(), errors="coerce"),
        "unidad": obs["Unidad"].str.strip(),
    })
    print(f"  {nombre}: {len(t)} filas, {len(meta)} estaciones, fechas ilegibles {int(t['fecha'].isna().sum())}, "
          f"sin valor {int(t['valor'].isna().sum())}")
    return meta, t


# Lee el informe de calidad de aguas, con sus propios metadatos de estación y parámetros.
def leer_calidad(nombre, inf):
    metas, obs = [], []
    cols_meta = {"COD. ESTACIÓN": "codigo_dv", "ESTACIÓN": "nombre", "REGIÓN": "region", "PROVINCIA": "provincia",
                 "COMUNA": "comuna", "CUENCA": "cuenca", "SUB CUENCA": "subcuenca", "SUB SUB CUENCA": "subsubcuenca",
                 "LATITUD": "lat", "LONGITUD": "lon", "ALTITUD": "altitud"}
    for b in pd.read_csv(comun.abrir(inf["archivo"]), dtype=str, keep_default_na=False, chunksize=500_000, encoding="utf-8"):
        b.columns = [c.strip() for c in b.columns]
        b = b.apply(lambda c: c.str.strip())
        m = b[list(cols_meta)].rename(columns=cols_meta).drop_duplicates("codigo_dv")
        metas.append(m)
        obs.append(pd.DataFrame({
            "codigo_dv": b["COD. ESTACIÓN"],
            "cod_param": b["COD. PARAMETRO"],
            "parametro": b["PARAMETRO"],
            "unidad": b["UNIDAD"],
            "fecha": comun.leer_fechas(b["FECHA MEDICION"], "%d-%m-%Y"),
            "hora_texto": b["HORA MEDICION"],
            "valor": pd.to_numeric(b["VALOR"], errors="coerce"),
            "calificador": b["CALIDAD"],
            "profundidad": pd.to_numeric(b["AguProfundidad"], errors="coerce"),
        }))
    meta = pd.concat(metas).drop_duplicates("codigo_dv")
    meta["codigo"] = meta["codigo_dv"].str.split("-").str[0]
    t = pd.concat(obs, ignore_index=True)
    t["codigo"] = t["codigo_dv"].str.split("-").str[0]
    t["fila"] = np.arange(len(t))
    print(f"  {nombre}: {len(t)} filas, {len(meta)} estaciones, {t['cod_param'].nunique()} parámetros, "
          f"fechas ilegibles {int(t['fecha'].isna().sum())}, sin valor {int(t['valor'].isna().sum())}")
    return meta.set_index("codigo"), t


LECTORES = {"diario": leer_diario, "irregular": leer_irregular, "rutinario": leer_rutinario, "calidad": leer_calidad}


# Ejecuta la lectura de los informes pedidos o de todos.
def main():
    par = comun.parametros()
    destino = comun.ruta("intermedios")
    nombres = sys.argv[1:] or list(par["informes"])
    for nombre in nombres:
        inf = par["informes"][nombre]
        meta, t = LECTORES[inf["formato"]](nombre, inf)
        meta.to_pickle(destino / f"estaciones_{nombre}.pkl")
        t.to_pickle(destino / f"{nombre}.pkl")


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    main()
