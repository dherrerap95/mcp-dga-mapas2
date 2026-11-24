# Corrige las series diarias de caudal, precipitación y temperatura y registra cada cambio en la bitácora.
import sys

import numpy as np
import pandas as pd

import comun

R = comun.reglas()
E = R["estados"]
DIARIAS = ["caudal", "prep", "tmin", "tmax", "tmed"]


# Carga una serie intermedia ordenada por estación y fecha, con su columna de estado.
def cargar(nombre):
    t = pd.read_pickle(comun.ruta("intermedios") / f"{nombre}.pkl")
    t["codigo"] = t["codigo"].astype(str)
    t = t.sort_values(["codigo", "fecha"], kind="stable").reset_index(drop=True)
    t["estado"] = np.uint8(E["original"])
    return t


# Arma filas de bitácora para un conjunto de filas de una serie.
def filas(t, idx, variable, accion, regla, nuevo=None, evidencia=""):
    s = t.loc[idx]
    return pd.DataFrame({
        "codigo": s["codigo"].to_numpy(),
        "variable": variable,
        "campo": "valor",
        "fecha_original": s["fecha"].dt.strftime("%Y-%m-%d").to_numpy(),
        "fecha_corregida": s["fecha"].dt.strftime("%Y-%m-%d").to_numpy(),
        "valor_anterior": s["valor"].to_numpy(),
        "valor_corregido": np.nan if nuevo is None else nuevo,
        "accion": accion,
        "regla": regla,
        "evidencia": evidencia,
    })


# Elimina valores dejando la fila con NaN y estado eliminado.
def eliminar(t, idx, variable, regla, evidencia, bit):
    if len(idx) == 0:
        return
    bit.agregar_tabla(filas(t, idx, variable, "eliminado", regla, evidencia=evidencia))
    t.loc[idx, "valor"] = np.nan
    t.loc[idx, "estado"] = E["eliminado"]


# Reemplaza un valor por su corrección y lo registra.
def corregir(t, i, variable, nuevo, regla, evidencia, bit):
    bit.agregar_tabla(filas(t, [i], variable, "corregido", regla, nuevo=[nuevo], evidencia=[evidencia]))
    t.loc[i, "valor"] = nuevo
    t.loc[i, "estado"] = E["corregido"]


# Resuelve fechas repetidas: copias iguales, diferencias de redondeo y valores incompatibles.
def resolver_duplicados(t, variable, bit):
    tol = R["duplicados"]["tolerancia_redondeo_rel"]
    rep = t[t.duplicated(["codigo", "fecha"], keep=False)]
    quitar = []
    for _, g in rep.groupby(["codigo", "fecha"], sort=False):
        v = g["valor"]
        texto = " y ".join(f"{x:g}" for x in v)
        if v.nunique(dropna=False) == 1:
            sobran = g.index[1:]
            bit.agregar_tabla(filas(t, sobran, variable, "eliminado", "U1", evidencia=f"copia del valor {v.iloc[0]:g}"))
            quitar.extend(sobran)
        elif v.notna().all() and v.max() > 0 and (v.max() - v.min()) / v.max() <= tol:
            queda = g["decimales"].idxmin()
            sobran = g.index.drop(queda)
            bit.agregar_tabla(filas(t, sobran, variable, "eliminado", "U2",
                                    evidencia=f"valores {texto}; se conserva {t.at[queda, 'valor']:g}"))
            quitar.extend(sobran)
        else:
            sobran = g.index[1:]
            bit.agregar_tabla(filas(t, g.index, variable, "eliminado", "U2E", evidencia=f"valores {texto}"))
            quitar.extend(sobran)
            t.loc[g.index[0], ["valor", "estado"]] = [np.nan, E["eliminado"]]
    t.drop(index=quitar, inplace=True)
    t.reset_index(drop=True, inplace=True)
    print(f"    duplicados: {rep.groupby(['codigo', 'fecha']).ngroups} fechas, {len(quitar)} filas quitadas")


# Aplica las correcciones puntuales aprobadas de este informe.
def aplicar_puntuales(t, variable, bit):
    archivo = comun.RAIZ / "parametros" / "correcciones_puntuales.csv"
    if not archivo.exists():
        return
    p = pd.read_csv(archivo, sep=";", dtype=str, keep_default_na=False, encoding="utf-8-sig")
    p = p[(p["informe"] == variable) & (p["estado"] == "aprobado")]
    for _, r in p.iterrows():
        m = (t["codigo"] == r["codigo"]) & t["fecha"].between(pd.Timestamp(r["fecha_desde"]), pd.Timestamp(r["fecha_hasta"]))
        if r["valor_si"] != "":
            m &= np.isclose(t["valor"], float(r["valor_si"].replace(",", ".")))
        idx = t.index[m & (t["estado"] != E["eliminado"])]
        if r["accion"] == "eliminar":
            eliminar(t, idx, variable, r["regla"], r["motivo"], bit)
        else:
            for i in idx:
                corregir(t, i, variable, float(r["valor_nuevo"].replace(",", ".")), r["regla"], r["motivo"], bit)
        print(f"    puntual {r['id']}: {len(idx)} filas ({r['accion']})")


# Elimina valores de relleno y precipitaciones negativas.
def centinelas_y_negativos(t, variable, bit):
    for c in R["centinelas"].get(variable, []):
        regla = "C1" if variable == "prep" else "C2"
        idx = t.index[np.isclose(t["valor"], c)]
        eliminar(t, idx, variable, regla, f"valor de relleno {c:g}", bit)
        print(f"    relleno {c:g}: {len(idx)}")
    if variable == "prep":
        idx = t.index[t["valor"] < 0]
        eliminar(t, idx, variable, "C3", "precipitación negativa", bit)
        print(f"    negativos: {len(idx)}")


# Devuelve los valores vecinos válidos de una fila dentro de una ventana de días.
def vecinos(t, indices, i, dias, lim=None):
    cod, f = t.at[i, "codigo"], t.at[i, "fecha"]
    s = t.loc[indices[cod]]
    m = ((s["fecha"] - f).dt.days.abs() <= dias) & (s.index != i) & (s["estado"] != E["eliminado"]) & s["valor"].notna()
    if lim is not None:
        m &= s["valor"].between(*lim)
    return s.loc[m, "valor"]


# Corrige la coma decimal corrida en valores fuera de rango; si no hay una única corrección, los elimina.
def coma_decimal(t, variable, bit, filtro=None):
    lim = R["limites"][variable]
    cd = R["coma_decimal"]
    tol, dias = cd["tolerancia"][variable], cd["vecinos_dias"][variable]
    fuera = (t["estado"] != E["eliminado"]) & t["valor"].notna() & ~t["valor"].between(*lim)
    if filtro is not None:
        fuera &= filtro
    indices = t.groupby("codigo").indices
    n_corr = n_elim = 0
    for i in t.index[fuera]:
        v = t.at[i, "valor"]
        vec = vecinos(t, indices, i, dias, lim)
        if len(vec) == 0:
            eliminar(t, [i], variable, "D2", "fuera de rango y sin datos vecinos", bit)
            n_elim += 1
            continue
        lo, hi = vec.min() - tol, vec.max() + tol
        cand = [round(v / d, 4) for d in cd["divisores"] if lim[0] <= v / d <= lim[1] and lo <= v / d <= hi]
        ev = f"días vecinos entre {vec.min():g} y {vec.max():g}"
        if len(cand) == 1:
            corregir(t, i, variable, cand[0], "D1", ev, bit)
            n_corr += 1
        else:
            eliminar(t, [i], variable, "D2", f"fuera de rango; {ev}", bit)
            n_elim += 1
    print(f"    coma decimal: {n_corr} corregidos, {n_elim} eliminados")


# Intercambia mínima y máxima cuando la mínima supera a la máxima y ambos valores calzan con sus vecinos.
def intercambiar_extremas(tn, tx, bit, marcas):
    par = R["intercambio_extremas"]
    a = tn[(tn["estado"] != E["eliminado"]) & tn["valor"].notna()].reset_index()[["index", "codigo", "fecha", "valor"]]
    b = tx[(tx["estado"] != E["eliminado"]) & tx["valor"].notna()].reset_index()[["index", "codigo", "fecha", "valor"]]
    m = a.merge(b, on=["codigo", "fecha"], suffixes=("_n", "_x"))
    inv = m[m["valor_n"] > m["valor_x"]]
    ind_n, ind_x = tn.groupby("codigo").indices, tx.groupby("codigo").indices
    n_inter = n_elim = n_marc = 0
    for _, r in inv.iterrows():
        i_n, i_x, vn, vx = r["index_n"], r["index_x"], r["valor_n"], r["valor_x"]
        if vn - vx <= par["diferencia_minima"]:
            marcas.append(("tmin", tn, i_n, "mínima mayor que máxima por menos de 0,5 °C"))
            marcas.append(("tmax", tx, i_x, "mínima mayor que máxima por menos de 0,5 °C"))
            n_marc += 1
            continue
        vec_n = vecinos(tn, ind_n, i_n, par["vecinos_dias"])
        vec_x = vecinos(tx, ind_x, i_x, par["vecinos_dias"])
        tol = par["tolerancia"]
        calza = (len(vec_n) > 0 and len(vec_x) > 0
                 and vec_n.min() - tol <= vx <= vec_n.max() + tol
                 and vec_x.min() - tol <= vn <= vec_x.max() + tol)
        ev = (f"mínima {vn:g} y máxima {vx:g}; mínimas vecinas {vec_n.min():g} a {vec_n.max():g}, "
              f"máximas vecinas {vec_x.min():g} a {vec_x.max():g}") if len(vec_n) and len(vec_x) else \
             f"mínima {vn:g} y máxima {vx:g}; sin días vecinos suficientes"
        if calza:
            corregir(tn, i_n, "tmin", vx, "T1", ev, bit)
            corregir(tx, i_x, "tmax", vn, "T1", ev, bit)
            n_inter += 1
        else:
            eliminar(tn, [i_n], "tmin", "T1E", ev, bit)
            eliminar(tx, [i_x], "tmax", "T1E", ev, bit)
            n_elim += 1
    print(f"    mínima > máxima: {n_inter} intercambiados, {n_elim} eliminados, {n_marc} marcados")


# Recalcula la temperatura media desde mínima y máxima depuradas y elimina las medias de días con extremas eliminadas.
def media_derivada(tm, tn, tx, bit):
    tol = R["media_derivada"]["tolerancia_redondeo"]
    a = tn[["codigo", "fecha", "valor", "estado"]].rename(columns={"valor": "vn", "estado": "en"})
    b = tx[["codigo", "fecha", "valor", "estado"]].rename(columns={"valor": "vx", "estado": "ex"})
    m = tm.reset_index()[["index", "codigo", "fecha", "valor", "estado"]].merge(a, on=["codigo", "fecha"]).merge(b, on=["codigo", "fecha"])
    m = m[m["estado"] != E["eliminado"]]
    calc = ((m["vn"] + m["vx"]) / 2).round(2)
    cambia = m[calc.notna() & ((calc - m["valor"]).abs() > tol)]
    for (_, r), c in zip(cambia.iterrows(), calc[cambia.index]):
        corregir(tm, r["index"], "tmed", c, "T2", f"mínima {r['vn']:g} y máxima {r['vx']:g}", bit)
    sin = m[(m["en"] == E["eliminado"]) | (m["ex"] == E["eliminado"])]
    eliminar(tm, sin["index"].to_numpy(), "tmed", "T2", "mínima o máxima del día eliminada", bit)
    print(f"    media: {len(cambia)} recalculadas, {len(sin)} eliminadas")
    return set(m["index"])


# Identifica tramos de días seguidos con el mismo valor de al menos cierta longitud.
def tramos_constantes(t, minimo, solo_positivos):
    v, c, f = t["valor"].to_numpy(), t["codigo"].to_numpy(), t["fecha"].to_numpy()
    nuevo = np.r_[True, (c[1:] != c[:-1]) | ((f[1:] - f[:-1]) != np.timedelta64(1, "D")) | (v[1:] != v[:-1])]
    rid = np.cumsum(nuevo)
    largo = np.bincount(rid)[rid]
    m = (largo >= minimo) & ~np.isnan(v)
    if solo_positivos:
        m &= v > 0
    return m, largo


# Valores del día anterior y siguiente de la misma estación, si son días seguidos.
def vecinos_inmediatos(t):
    v, c, f = t["valor"].to_numpy(), t["codigo"].to_numpy(), t["fecha"].to_numpy()
    uno = np.timedelta64(1, "D")
    ant = np.r_[np.nan, np.where((c[1:] == c[:-1]) & (f[1:] - f[:-1] == uno), v[:-1], np.nan)]
    sig = np.r_[np.where((c[:-1] == c[1:]) & (f[1:] - f[:-1] == uno), v[1:], np.nan), np.nan]
    return v, ant, sig


# Marca como dudosos los valores posibles pero sospechosos, sin cambiarlos.
def marcar(t, variable, marcas):
    mk = R["marcas"]
    v, ant, sig = vecinos_inmediatos(t)
    reglas = []
    if variable == "caudal":
        F = mk["caudal_pico_factor"]
        with np.errstate(invalid="ignore"):
            reglas.append(((v > 1) & (v > F * ant) & (v > F * sig), "pico aislado de caudal"))
            reglas.append(((ant > 1) & (sig > 1) & (v * F < ant) & (v * F < sig), "caída aislada de caudal"))
            reglas.append((v > mk["caudal_maximo_dudoso"], f"caudal mayor que {mk['caudal_maximo_dudoso']} m³/s"))
        m, _ = tramos_constantes(t, mk["caudal_tramo_constante_dias"], True)
        reglas.append((m, f"tramo de {mk['caudal_tramo_constante_dias']} o más días con el mismo caudal"))
    elif variable == "prep":
        with np.errstate(invalid="ignore"):
            reglas.append((v > mk["prep_dudosa_mm"], f"precipitación mayor que {mk['prep_dudosa_mm']} mm"))
        m, _ = tramos_constantes(t, mk["prep_tramo_constante_dias"], True)
        reglas.append((m, f"tramo de {mk['prep_tramo_constante_dias']} o más días con la misma precipitación"))
    else:
        with np.errstate(invalid="ignore"):
            if variable == "tmin":
                reglas.append((v < mk["tmin_dudosa_bajo"], f"mínima bajo {mk['tmin_dudosa_bajo']} °C"))
            reglas.append((np.abs(v - ant) > mk["temp_salto"], f"salto de más de {mk['temp_salto']} °C respecto del día anterior"))
        m, _ = tramos_constantes(t, mk["temp_tramo_constante_dias"], False)
        reglas.append((m, f"tramo de {mk['temp_tramo_constante_dias']} o más días con la misma temperatura"))
    for mascara, motivo in reglas:
        for i in np.flatnonzero(mascara & (t["estado"].to_numpy() == E["original"])):
            marcas.append((variable, t, i, motivo))


# Aplica las marcas acumuladas y las devuelve como tabla.
def aplicar_marcas(marcas):
    filas_m = []
    for variable, t, i, motivo in marcas:
        if t.at[i, "estado"] in (E["original"], E["dudoso"]):
            t.at[i, "estado"] = E["dudoso"]
            filas_m.append((t.at[i, "codigo"], variable, t.at[i, "fecha"].date().isoformat(), t.at[i, "valor"], motivo))
    return pd.DataFrame(filas_m, columns=["codigo", "variable", "fecha", "valor", "motivo"])


# Verifica que todo valor fuera de rango esté tratado y que cada cambio tenga su fila de bitácora.
def controlar(series, bit):
    for nombre, t in series.items():
        lim = R["limites"][nombre]
        malos = t[(t["estado"] != E["eliminado"]) & t["valor"].notna() & ~t["valor"].between(*lim)]
        assert len(malos) == 0, f"{nombre}: {len(malos)} valores fuera de rango sin tratar"
        b = bit[(bit["variable"] == nombre) & (bit["regla"] != "U1") & (bit["regla"] != "U2")]
        claves_b = set(zip(b["codigo"], b["fecha_original"]))
        cambiados = t[t["estado"].isin([E["corregido"], E["eliminado"]])]
        claves_t = set(zip(cambiados["codigo"], cambiados["fecha"].dt.strftime("%Y-%m-%d")))
        faltan = claves_t - claves_b
        assert not faltan, f"{nombre}: {len(faltan)} cambios sin bitácora, por ejemplo {list(faltan)[:3]}"
    print("  Controles superados: sin valores fuera de rango y cada cambio con su bitácora")


# Ejecuta la corrección completa de las series diarias.
def main():
    bit = comun.Bitacora("diarias")
    series, marcas = {}, []
    for nombre in DIARIAS:
        print(f"  {nombre}")
        t = cargar(nombre)
        resolver_duplicados(t, nombre, bit)
        aplicar_puntuales(t, nombre, bit)
        centinelas_y_negativos(t, nombre, bit)
        series[nombre] = t
    tn, tx, tm = series["tmin"], series["tmax"], series["tmed"]
    print("  temperaturas")
    coma_decimal(tn, "tmin", bit)
    coma_decimal(tx, "tmax", bit)
    intercambiar_extremas(tn, tx, bit, marcas)
    con_extremas = media_derivada(tm, tn, tx, bit)
    coma_decimal(tm, "tmed", bit, filtro=~tm.index.isin(list(con_extremas)))
    for nombre in DIARIAS:
        marcar(series[nombre], nombre, marcas)
    tabla_marcas = aplicar_marcas(marcas)
    b = bit.tabla()
    b["informe"] = b["variable"]
    controlar(series, b)
    destino = comun.ruta("intermedios")
    for nombre, t in series.items():
        t[["codigo", "fecha", "valor", "estado"]].to_pickle(destino / f"{nombre}_corregido.pkl")
    revision = comun.ruta("revision")
    b.to_pickle(destino / "bitacora_diarias.pkl")
    tabla_marcas.to_pickle(destino / "marcas_diarias.pkl")
    comun.a_csv(b, revision / "bitacora_diarias.csv")
    comun.a_csv(tabla_marcas, revision / "marcas_diarias.csv")
    resumen = b.groupby(["variable", "regla", "accion"]).size().rename("casos").reset_index()
    resumen["descripcion"] = resumen["regla"].map(R["reglas"])
    comun.a_csv(resumen, revision / "resumen_diarias.csv")
    print(resumen.to_string(index=False))
    print(tabla_marcas.groupby(["variable", "motivo"]).size().to_string())


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    main()
