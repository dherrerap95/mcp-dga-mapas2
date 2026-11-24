# Reúne pistas para identificar los códigos de parámetro de calidad de aguas que vienen sin nombre ni unidad.
import sys

import numpy as np
import pandas as pd

import comun


# Calcula para cada código sin nombre su rango, períodos, códigos vecinos y parámetros medidos en las mismas muestras.
def main():
    t = pd.read_pickle(comun.ruta("intermedios") / "calidad_corregido.pkl")
    t = t[t["estado"] != comun.reglas()["estados"]["eliminado"]]
    cat = pd.read_csv(comun.RAIZ / "parametros" / "parametros_calidad.csv", sep=";", dtype=str,
                      keep_default_na=False, encoding="utf-8-sig")
    nombrados = cat[cat["identificado"] == "True"].copy()
    nombrados["num"] = nombrados["cod_param"].astype(int)
    sin = cat[cat["identificado"] == "False"]["cod_param"].tolist()
    t["muestra"] = t["codigo_dv"] + "|" + t["fecha"].dt.strftime("%Y-%m-%d") + "|" + t["profundidad"].astype(str)
    piv = t[t["calificador"] == "="].pivot_table(index="muestra", columns="cod_param", values="valor", aggfunc="median")
    filas = []
    for c in sin:
        s = t[t["cod_param"] == c]
        v = s.loc[s["calificador"] == "=", "valor"]
        n = int(c)
        vec = nombrados.iloc[(nombrados["num"] - n).abs().argsort()[:3]]
        muestras = set(s["muestra"])
        co = t[t["muestra"].isin(muestras) & (t["cod_param"] != c)].groupby("nombre")["muestra"].nunique()
        co = (co / max(len(muestras), 1)).sort_values(ascending=False).head(6)
        razones = []
        if c in piv:
            for _, r in vec.iterrows():
                if r["cod_param"] in piv:
                    par = piv[[c, r["cod_param"]]].dropna()
                    par = par[par[r["cod_param"]] > 0]
                    if len(par) >= 10:
                        razones.append(f"{r['cod_param']}: mediana de la razón {np.median(par[c] / par[r['cod_param']]):.3g} (n={len(par)})")
        filas.append({
            "cod_param": c, "filas": len(s), "estaciones": s["codigo_dv"].nunique(),
            "desde": s["fecha"].min().date(), "hasta": s["fecha"].max().date(),
            "pct_bajo_limite": round(100 * (s["calificador"] == "<").mean(), 1),
            "p10": v.quantile(0.1), "mediana": v.median(), "p90": v.quantile(0.9), "maximo": v.max(),
            "codigos_vecinos": "; ".join(f"{r['cod_param']} {r['nombre']} ({r['unidad']})" for _, r in vec.iterrows()),
            "razon_con_vecinos": "; ".join(razones),
            "medido_junto_a": "; ".join(f"{k} {100 * x:.0f} %" for k, x in co.items()),
        })
    p = pd.DataFrame(filas).sort_values("filas", ascending=False)
    comun.a_csv(p, comun.ruta("revision") / "pistas_parametros_sin_nombre.csv")
    with pd.option_context("display.width", 250, "display.max_colwidth", 120, "display.max_rows", 100):
        print(p[["cod_param", "filas", "estaciones", "desde", "hasta", "pct_bajo_limite", "mediana", "p90",
                 "codigos_vecinos", "razon_con_vecinos"]].to_string(index=False))


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    main()
