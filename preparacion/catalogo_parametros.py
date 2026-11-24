# Genera el catálogo editable de parámetros de calidad de aguas con nombres y unidades normalizados.
import sys

import pandas as pd

import comun

UNIDADES = {
    "Mg/L Al": ("mg/l Al", "P1"),
    "mhos/cm": ("µS/cm", "P1"),
    "Grad. C": ("°C", "P1"),
    "unid. ph": ("unidad de pH", "P1"),
    "nmp/100": ("NMP/100 ml", "P1"),
    "u. nefel": ("UNT", "P1"),
    "U. Pt": ("U Pt-Co", "P1"),
    "metros": ("m", "P1"),
}
NOMBRES = {"6001": ("Oxígeno disuelto (porcentaje de saturación)", "%", "P2")}
LIMITES = {"6020": (0, 14), "5140": (-2, 45), "6000": (0, 25), "6001": (0, 300)}
UNIDADES_DICCIONARIO = {"mg/L": "mg/l", "ug/L": "µg/l", "uS/cm": "µS/cm", "NMP/100mL": "NMP/100 ml", "NTU": "UNT"}
UNIDAD_INCIERTA = {"9900": "incierta (µg/l o mg/l)"}


# Lee el diccionario de códigos de parámetros de la DGA.
def diccionario():
    d = pd.read_csv(comun.RAIZ / "parametros" / "diccionario_parametros_dga.csv", sep=";", dtype=str,
                    keep_default_na=False, encoding="utf-8-sig")
    return d.set_index("cod_param")


# Arma nombre y unidad de un código a partir del diccionario.
def rotulo_diccionario(r):
    nombre = r["parametro"].strip()
    nombre = nombre[0].upper() + nombre[1:]
    unidad = UNIDADES_DICCIONARIO.get(r["unidad"].strip(), r["unidad"].strip())
    if r["expresion"] not in ("", "NA", "-", "Unidad de pH", "CE"):
        unidad = f"{unidad} {r['expresion']}"
    return nombre, unidad


# Arma el catálogo desde los datos leídos y lo escribe en parámetros.
def main():
    t = pd.read_pickle(comun.ruta("intermedios") / "calidad.pkl")
    g = t.groupby("cod_param").agg(nombre_original=("parametro", lambda x: x.mode().iloc[0]),
                                   unidad_original=("unidad", lambda x: x.mode().iloc[0]),
                                   filas=("valor", "size")).reset_index()
    g["nombre"], g["unidad"], g["regla"] = g["nombre_original"], g["unidad_original"], ""
    dic = diccionario()
    g["metodologia"] = g["cod_param"].map(lambda c: dic.at[c, "metodologia"] if c in dic.index else "")
    g["nota_diccionario"] = g["cod_param"].map(
        lambda c: "; ".join(x for x in (dic.at[c, "parametro"], dic.at[c, "unidad"], dic.at[c, "expresion"], dic.at[c, "comentario"]) if x)
        if c in dic.index else "")
    for i, r in g.iterrows():
        if r["unidad_original"] in UNIDADES:
            g.at[i, "unidad"], g.at[i, "regla"] = UNIDADES[r["unidad_original"]]
        if r["cod_param"] in NOMBRES:
            g.at[i, "nombre"], g.at[i, "unidad"], g.at[i, "regla"] = NOMBRES[r["cod_param"]]
        if r["nombre_original"] == "" and r["cod_param"] in dic.index:
            g.at[i, "nombre"], g.at[i, "unidad"] = rotulo_diccionario(dic.loc[r["cod_param"]])
            g.at[i, "regla"] = "P4"
        elif r["nombre_original"] == "":
            g.at[i, "nombre"], g.at[i, "regla"] = f"Parámetro sin identificar (código {r['cod_param']})", "P4"
        if r["cod_param"] in UNIDAD_INCIERTA:
            g.at[i, "unidad"] = UNIDAD_INCIERTA[r["cod_param"]]
    g["limite_min"] = g["cod_param"].map(lambda c: LIMITES.get(c, ("", ""))[0])
    g["limite_max"] = g["cod_param"].map(lambda c: LIMITES.get(c, ("", ""))[1])
    g["identificado"] = ~g["nombre"].str.startswith("Parámetro sin identificar")
    destino = comun.RAIZ / "parametros" / "parametros_calidad.csv"
    if destino.exists() and "--forzar" not in sys.argv:
        actual = pd.read_csv(destino, sep=";", dtype=str, keep_default_na=False, encoding="utf-8-sig")
        nuevos = g[~g["cod_param"].isin(actual["cod_param"])].astype(str)
        if nuevos.empty:
            print(f"  {destino.name} sin códigos nuevos; se conserva tal como está")
            return
        g = pd.concat([actual, nuevos[actual.columns]], ignore_index=True)
        print(f"  {len(nuevos)} códigos nuevos agregados al final de lo editado: {', '.join(nuevos['cod_param'])}")
    g.sort_values("cod_param", key=lambda c: c.astype(int)).to_csv(destino, sep=";", index=False, encoding="utf-8-sig")
    print(f"  {len(g)} parámetros, {int((g['regla'] != '').sum())} con rótulo corregido, "
          f"{int((g['regla'] == 'P4').sum())} sin nombre en los datos, {int((~g['identificado']).sum())} sin identificar")


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    main()
