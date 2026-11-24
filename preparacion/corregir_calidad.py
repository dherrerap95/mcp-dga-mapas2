# Corrige los datos de calidad de aguas y registra cada cambio en la bitácora.
import sys

import numpy as np
import pandas as pd

import comun
from corregir_irregulares import normalizar_hora

R = comun.reglas()
E = R["estados"]
CALIFICADORES_VALIDOS = {"=", "<", ">", "", "D"}


# Arma filas de bitácora para un conjunto de filas de calidad.
def filas(t, idx, accion, regla, evidencia, campo="valor", anterior=None, nuevo=None):
    s = t.loc[idx]
    f = s["fecha"].dt.strftime("%Y-%m-%d").to_numpy()
    return pd.DataFrame({
        "codigo": s["codigo_dv"].to_numpy(), "variable": s["cod_param"].to_numpy(), "campo": campo,
        "fecha_original": f, "fecha_corregida": f, "hora": s["hora"].to_numpy(),
        "profundidad": s["profundidad"].to_numpy(),
        "valor_anterior": s["valor"].to_numpy() if anterior is None else anterior,
        "valor_corregido": np.nan if nuevo is None else nuevo,
        "unidad_anterior": s["unidad"].to_numpy(), "unidad_corregida": s["unidad"].to_numpy(),
        "accion": accion, "regla": regla, "evidencia": evidencia,
    })


# Marca filas como dudosas con su motivo.
def marcar(t, mascara, motivo, marcas):
    idx = t.index[np.asarray(mascara) & t["estado"].isin([E["original"], E["dudoso"]]).to_numpy()]
    t.loc[idx, "estado"] = E["dudoso"]
    s = t.loc[idx]
    marcas.append(pd.DataFrame({"codigo": s["codigo_dv"], "variable": s["cod_param"], "fecha": s["fecha"].dt.strftime("%Y-%m-%d"),
                                "hora": s["hora"], "profundidad": s["profundidad"], "valor": s["valor"], "motivo": motivo}))
    print(f"    marcados ({motivo}): {len(idx)}")


# Registra en la bitácora los rótulos de parámetro y unidad corregidos, una fila por parámetro.
def rotulos(cat, t, bit):
    for _, r in cat[cat["regla"] != ""].iterrows():
        n = int((t["cod_param"] == r["cod_param"]).sum())
        for campo, ant, nue in (("parametro", r["nombre_original"], r["nombre"]), ("unidad", r["unidad_original"], r["unidad"])):
            if ant != nue:
                bit.agregar(codigo="todas", variable=r["cod_param"], campo=campo, valor_anterior=ant, valor_corregido=nue,
                            unidad_anterior=r["unidad_original"], unidad_corregida=r["unidad"], accion="corregido",
                            regla=r["regla"], evidencia=f"rótulo aplicado a {n} mediciones")


# Reasigna al código correcto las mediciones registradas con un código equivocado en un período.
def reasignar(t, bit):
    for r in R["calidad"]["reasignar_parametro"]:
        idx = t.index[(t["cod_param"] == r["de"]) & (t["fecha"] < pd.Timestamp(r["antes_de"]))]
        bit.agregar_tabla(filas(t, idx, "corregido", "P2", r["motivo"], campo="parametro", anterior=r["de"], nuevo=r["a"]))
        t.loc[idx, "cod_param"] = r["a"]
        print(f"    reasignadas de {r['de']} a {r['a']}: {len(idx)}")


# Marca las mediciones de códigos cuya unidad no es segura.
def unidades_inciertas(t, marcas):
    for r in R["calidad"]["unidad_incierta"]:
        m = t["cod_param"] == r["cod_param"]
        if "antes_de" in r:
            m &= t["fecha"] < pd.Timestamp(r["antes_de"])
        marcar(t, m.to_numpy(), r["motivo"], marcas)


# Normaliza las horas y registra las mal escritas.
def horas(t, bit):
    t["hora"] = t["hora_texto"].map(normalizar_hora)
    mal = t.index[t["hora_texto"] != t["hora"]]
    for i in mal:
        b = filas(t, [i], "corregido", "F3", f"hora escrita «{t.at[i, 'hora_texto']}»", campo="hora",
                  anterior=[t.at[i, "hora_texto"]], nuevo=[t.at[i, "hora"]])
        bit.agregar_tabla(b)
    print(f"    horas mal escritas: {len(mal)}")


# Elimina mediciones repetidas con el mismo valor y marca las repetidas con valores distintos.
def duplicados(t, bit, marcas):
    clave = ["codigo_dv", "fecha", "hora", "cod_param", "profundidad"]
    copia = t.duplicated(clave + ["valor", "calificador"], keep="first")
    bit.agregar_tabla(filas(t, t.index[copia], "eliminado", "U1", "medición repetida con el mismo valor"))
    t.drop(index=t.index[copia], inplace=True)
    t.reset_index(drop=True, inplace=True)
    print(f"    copias eliminadas: {int(copia.sum())}")
    marcar(t, t.duplicated(clave, keep=False).to_numpy(), "medición repetida con valores distintos", marcas)


# Intenta corregir la coma decimal de un valor con un rango de referencia; devuelve el valor o None.
def candidato(v, ref, lim):
    lo, hi = ref.min(), ref.max()
    cand = [round(v / d, 6) for d in R["coma_decimal"]["divisores"]
            if (lim is None or lim[0] <= v / d <= lim[1]) and lo <= v / d <= hi]
    if len(cand) > 1:
        dist = sorted((abs(c - ref.median()), c) for c in cand)
        cand = [dist[0][1]] if dist[0][0] < dist[1][0] / 3 else []
    return cand[0] if len(cand) == 1 else None


# Corrige o elimina valores fuera de los límites físicos y corrige o marca los valores extremos.
def valores(t, cat, bit, marcas):
    n = {"corr": 0, "elim": 0, "marc": 0}
    cfg = R["calidad"]
    grupos = t.groupby(["codigo_dv", "cod_param"]).indices
    validos = t["estado"] != E["eliminado"]
    for _, r in cat.iterrows():
        c = r["cod_param"]
        sel = t.index[(t["cod_param"] == c).to_numpy() & validos.to_numpy()]
        v = t.loc[sel, "valor"]
        lim = (float(r["limite_min"]), float(r["limite_max"])) if r["limite_min"] != "" else None
        lim_est = cfg["limites_por_estacion"].get(c, {})
        dentro = v.between(*lim) if lim else pd.Series(True, index=v.index, dtype=bool)
        for cod, (a, b_) in lim_est.items():
            m = t.loc[v.index, "codigo_dv"] == cod
            dentro[m] = v[m].between(a, b_)
        sin = cfg["eliminar_sin_correccion"].get(c)
        p01, p99 = v[dentro].quantile([0.01, 0.99])
        dentro = dentro.astype(bool)
        fuera = set(v.index[~dentro.to_numpy()])
        extremos = v.index[dentro & (v > 100 * p99) & (p99 > 0)]
        for i in sorted(fuera) + list(extremos):
            lim_i = tuple(lim_est.get(t.at[i, "codigo_dv"], lim)) if lim else None
            otros = t.loc[grupos[(t.at[i, "codigo_dv"], c)]]
            otros = otros[(otros.index != i) & (otros["estado"] != E["eliminado"])]["valor"]
            if lim_i:
                otros = otros[otros.between(*lim_i)]
            es_fuera = i in fuera
            if es_fuera and sin and lim_i[1] < t.at[i, "valor"] <= sin["hasta"]:
                bit.agregar_tabla(filas(t, [i], "eliminado", "D2", sin["motivo"]))
                t.at[i, "valor"], t.at[i, "estado"] = np.nan, E["eliminado"]
                n["elim"] += 1
                continue
            if not es_fuera and (len(otros) < 5 or t.at[i, "valor"] <= 10 * otros.max()):
                continue
            ref = otros if len(otros) >= 5 else v[dentro & v.between(p01, p99)]
            nuevo = candidato(t.at[i, "valor"], ref, lim_i)
            ev = (f"{'fuera del rango físico' if es_fuera else 'más de cien veces el percentil 99'}; "
                  f"referencia {'de la estación' if len(otros) >= 5 else 'del parámetro'} entre {ref.min():g} y {ref.max():g}")
            if nuevo is not None:
                bit.agregar_tabla(filas(t, [i], "corregido", "D1", ev, nuevo=[nuevo]))
                t.at[i, "valor"], t.at[i, "estado"] = nuevo, E["corregido"]
                n["corr"] += 1
            elif es_fuera:
                bit.agregar_tabla(filas(t, [i], "eliminado", "D2", ev))
                t.at[i, "valor"], t.at[i, "estado"] = np.nan, E["eliminado"]
                n["elim"] += 1
            else:
                marcar(t, t.index == i, "valor más de cien veces el percentil 99 del parámetro", marcas)
                n["marc"] += 1
    print(f"    valores: {n['corr']} corregidos, {n['elim']} eliminados, {n['marc']} marcados")


# Reemplaza los calificadores sin significado por vacío y marca esos valores.
def calificadores(t, bit, marcas):
    raros = t.index[~t["calificador"].isin(CALIFICADORES_VALIDOS)]
    for cal, g in t.loc[raros].groupby("calificador"):
        bit.agregar_tabla(filas(t, g.index, "corregido", "P6", f"calificador «{cal}» sin significado", campo="calificador",
                                anterior=cal, nuevo=""))
    t.loc[raros, "calificador"] = ""
    marcar(t, t.index.isin(raros), "calificador original sin significado", marcas)
    print(f"    calificadores sin significado: {len(raros)}")


# Ejecuta la corrección de calidad de aguas.
def main():
    print("  calidad")
    t = pd.read_pickle(comun.ruta("intermedios") / "calidad.pkl")
    t["estado"] = np.uint8(E["original"])
    cat = pd.read_csv(comun.RAIZ / "parametros" / "parametros_calidad.csv", sep=";", dtype=str,
                      keep_default_na=False, encoding="utf-8-sig")
    bit, marcas = comun.Bitacora("calidad"), []
    rotulos(cat, t, bit)
    horas(t, bit)
    reasignar(t, bit)
    duplicados(t, bit, marcas)
    valores(t, cat, bit, marcas)
    calificadores(t, bit, marcas)
    unidades_inciertas(t, marcas)
    cond = cat.loc[cat["nombre_original"].str.startswith("Conductividad"), "cod_param"]
    marcar(t, (t["cod_param"].isin(cond) & (t["valor"] == 0)).to_numpy(), "conductividad igual a 0", marcas)
    t = t.merge(cat[["cod_param", "nombre", "unidad"]].rename(columns={"unidad": "unidad_corregida"}), on="cod_param", how="left")
    excep = R["calidad"]["limites_por_estacion"]
    for _, r in cat[cat["limite_min"] != ""].iterrows():
        x = t[(t["cod_param"] == r["cod_param"]) & (t["estado"] != E["eliminado"])]
        x = x[~x["codigo_dv"].isin(excep.get(r["cod_param"], {}))]["valor"]
        assert x.between(float(r["limite_min"]), float(r["limite_max"])).all(), f"{r['cod_param']}: valores fuera de límite"
    b = bit.tabla()
    n_camb = int((t["estado"] == E["corregido"]).sum() + (t["estado"] == E["eliminado"]).sum())
    assert n_camb == int(b["regla"].isin(["D1", "D2"]).sum()), "valores cambiados sin bitácora"
    print("  Controles superados")
    destino, revision = comun.ruta("intermedios"), comun.ruta("revision")
    cols = ["codigo", "codigo_dv", "cod_param", "nombre", "unidad_corregida", "fecha", "hora", "profundidad",
            "valor", "calificador", "estado"]
    t[cols].rename(columns={"unidad_corregida": "unidad"}).to_pickle(destino / "calidad_corregido.pkl")
    m = pd.concat(marcas, ignore_index=True)
    b.to_pickle(destino / "bitacora_calidad.pkl")
    m.to_pickle(destino / "marcas_calidad.pkl")
    comun.a_csv(b, revision / "bitacora_calidad.csv")
    comun.a_csv(m, revision / "marcas_calidad.csv")
    resumen = b.groupby(["regla", "accion"]).size().rename("casos").reset_index()
    resumen["descripcion"] = resumen["regla"].map(R["reglas"])
    comun.a_csv(resumen, revision / "resumen_calidad.csv")
    print(resumen.to_string(index=False))


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    main()
