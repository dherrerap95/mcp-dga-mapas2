# Corrige los niveles de pozos y los sedimentos y registra cada cambio en la bitácora.
import re
import sys

import numpy as np
import pandas as pd

import comun

R = comun.reglas()
E = R["estados"]


# Arma filas de bitácora para un conjunto de filas de una tabla con fecha y valor.
def filas(t, idx, variable, accion, regla, evidencia, campo="valor", anterior=None, nuevo=None, fecha_nueva=None):
    s = t.loc[idx]
    f = s["fecha"].dt.strftime("%Y-%m-%d").fillna(s.get("fecha_texto", pd.Series("", index=s.index)))
    return pd.DataFrame({
        "codigo": s["codigo"].to_numpy(),
        "variable": variable,
        "campo": campo,
        "fecha_original": f.to_numpy(),
        "fecha_corregida": f.to_numpy() if fecha_nueva is None else fecha_nueva,
        "hora": s["hora"].to_numpy() if "hora" in s else "",
        "valor_anterior": s["valor"].to_numpy() if anterior is None else anterior,
        "valor_corregido": np.nan if nuevo is None else nuevo,
        "accion": accion,
        "regla": regla,
        "evidencia": evidencia,
    })


# Elimina valores dejando la fila con NaN y estado eliminado.
def eliminar(t, idx, variable, regla, evidencia, bit):
    if len(idx):
        bit.agregar_tabla(filas(t, idx, variable, "eliminado", regla, evidencia))
        t.loc[idx, "valor"] = np.nan
        t.loc[idx, "estado"] = E["eliminado"]


# Marca filas como dudosas con su motivo, sin cambiar el valor.
def marcar(t, mascara, variable, motivo, marcas):
    idx = t.index[mascara & (t["estado"].isin([E["original"], E["dudoso"]])).to_numpy()]
    t.loc[idx, "estado"] = E["dudoso"]
    s = t.loc[idx]
    marcas.append(pd.DataFrame({"codigo": s["codigo"], "variable": variable,
                                "fecha": s["fecha"].dt.strftime("%Y-%m-%d"), "valor": s["valor"], "motivo": motivo}))
    print(f"    marcados ({motivo}): {len(idx)}")


# Corrige la coma decimal de valores fuera de rango usando la medición anterior y la siguiente de la misma estación.
def coma_decimal_vecinas(t, variable, bit):
    lim = R["limites"][variable]
    tol = R["coma_decimal"]["tolerancia"][variable]
    t.sort_values(["codigo", "fecha"], inplace=True, kind="stable")
    valido = t["valor"].notna() & t["valor"].between(*lim) & (t["estado"] != E["eliminado"])
    fuera = t.index[t["valor"].notna() & ~t["valor"].between(*lim) & (t["estado"] != E["eliminado"])]
    n_c = n_e = 0
    for i in fuera:
        s = t[(t["codigo"] == t.at[i, "codigo"]) & valido]
        antes = s[s["fecha"] < t.at[i, "fecha"]]["valor"].tail(1)
        despues = s[s["fecha"] > t.at[i, "fecha"]]["valor"].head(1)
        vec = pd.concat([antes, despues])
        v = t.at[i, "valor"]
        cand = [round(v / d, 4) for d in R["coma_decimal"]["divisores"]
                if len(vec) and vec.min() - tol <= v / d <= vec.max() + tol]
        ev = f"mediciones vecinas {', '.join(f'{x:g}' for x in vec)}" if len(vec) else "sin mediciones vecinas"
        if len(cand) > 1:
            dist = sorted((abs(c - vec.median()), c) for c in cand)
            if dist[0][0] < dist[1][0] / 3:
                cand = [dist[0][1]]
        if len(cand) == 1:
            bit.agregar_tabla(filas(t, [i], variable, "corregido", "D1", ev, nuevo=cand))
            t.at[i, "valor"], t.at[i, "estado"] = cand[0], E["corregido"]
            n_c += 1
        else:
            eliminar(t, [i], variable, "D2", ev, bit)
            n_e += 1
    print(f"    coma decimal: {n_c} corregidos, {n_e} eliminados")


# Corrige los niveles de pozos.
def pozos(bit, marcas):
    t = pd.read_pickle(comun.ruta("intermedios") / "pozos.pkl")
    t["estado"] = np.uint8(E["original"])
    print("  pozos")
    relleno = t.index[t["fecha"] == pd.Timestamp("1900-01-01")]
    eliminar(t, relleno, "pozos", "F1", "fecha de relleno 01-01-1900", bit)
    t.loc[relleno, "fecha"] = pd.NaT
    print(f"    fecha 01-01-1900: {len(relleno)}")
    coma_decimal_vecinas(t, "pozos", bit)
    obs = t["observacion"].str.lower()
    marcar(t, (obs == "dinamico").to_numpy(), "pozos", "nivel dinámico (medido con bombeo)", marcas)
    marcar(t, (obs == "surgente").to_numpy(), "pozos", "pozo surgente", marcas)
    marcar(t, ((obs == "seco") & t["valor"].notna()).to_numpy(), "pozos", "pozo seco: el valor corresponde al fondo", marcas)
    marcar(t, ((obs == "embancado") & t["valor"].notna()).to_numpy(), "pozos", "pozo embancado", marcas)
    marcar(t, (t["valor"] < 0).to_numpy(), "pozos", "nivel sobre el terreno", marcas)
    marcar(t, (t["fecha"].dt.year < 1950).to_numpy(), "pozos", "medición anterior a 1950, aislada del resto de la serie", marcas)
    est = (t["estado"] != E["eliminado"]) & t["valor"].notna() & (obs != "dinamico")
    s = t[est].sort_values(["codigo", "fecha"])
    mismo = (s["codigo"] == s["codigo"].shift(1)) & (s["codigo"] == s["codigo"].shift(-1))
    pico = mismo & ((s["valor"] - s["valor"].shift(1)).abs() > 10) & ((s["valor"] - s["valor"].shift(-1)).abs() > 10) \
        & (np.sign(s["valor"] - s["valor"].shift(1)) == np.sign(s["valor"] - s["valor"].shift(-1)))
    marcar(t, t.index.isin(s.index[pico]), "pozos", "cambio aislado de más de 10 m respecto de las mediciones vecinas", marcas)
    return t


# Corrige las fechas posteriores a la descarga cuando las muestras vecinas dejan un único año posible.
def fechas_futuras(t, variable, bit):
    limite = pd.Timestamp(comun.parametros()["fecha_descarga"])
    t.sort_values("fila", inplace=True)
    for i in t.index[t["fecha"] > limite]:
        cod, f = t.at[i, "codigo"], t.at[i, "fecha"]
        s = t[(t["codigo"] == cod) & (t.index != i) & (t["fecha"] <= limite)]
        antes = s[s["fila"] < t.at[i, "fila"]]["fecha"].tail(1)
        despues = s[s["fila"] > t.at[i, "fila"]]["fecha"].head(1)
        ini = antes.iloc[0] if len(antes) else pd.Timestamp("1900-01-01")
        fin = despues.iloc[0] if len(despues) else limite
        anios = [a for a in range(ini.year, fin.year + 1)
                 if ini <= pd.Timestamp(year=a, month=f.month, day=f.day) <= fin]
        ev = f"muestras vecinas del {ini.date()} y del {fin.date()}" if len(despues) else f"muestra anterior del {ini.date()}"
        if len(anios) == 1:
            nueva = pd.Timestamp(year=anios[0], month=f.month, day=f.day)
            bit.agregar_tabla(filas(t, [i], variable, "corregido", "F2", ev, campo="fecha",
                                    anterior=[t.at[i, "valor"]], nuevo=[t.at[i, "valor"]], fecha_nueva=[nueva.date().isoformat()]))
            t.at[i, "fecha"], t.at[i, "estado"] = nueva, E["corregido"]
            print(f"    fecha {f.date()} corregida a {nueva.date()}")
        else:
            eliminar(t, [i], variable, "F2", f"{ev}; años posibles {anios}", bit)
            t.at[i, "fecha"] = pd.NaT
            print(f"    fecha {f.date()} eliminada (años posibles {anios})")


# Corrige el muestreo integrado de sedimentos.
def sed_integrado(bit, marcas):
    t = pd.read_pickle(comun.ruta("intermedios") / "sed_integrado.pkl")
    t["estado"] = np.uint8(E["original"])
    print("  sed_integrado")
    fechas_futuras(t, "sed_integrado", bit)
    marcar(t, (t["valor"] == 0).to_numpy(), "sed_integrado", "concentración 0 (posible ausencia de muestra)", marcas)
    marcar(t, (t["valor"] > 20000).to_numpy(), "sed_integrado", "concentración mayor que 20 000 mg/l", marcas)
    t["unidad"] = "mg/l"
    return t


# Completa una hora truncada o sin minutos y devuelve la hora normalizada o vacío.
def normalizar_hora(texto):
    h = texto.strip()
    if re.fullmatch(r"\d{1,2}:\d{2}", h):
        return h.zfill(5)
    if re.fullmatch(r"\d{1,2}:\d", h):
        return h.zfill(4) + "0"
    if re.fullmatch(r"\d{1,2}", h):
        return h.zfill(2) + ":00"
    return ""


# Normaliza las horas y registra las que estaban mal escritas.
def corregir_horas(t, variable, bit):
    t["hora"] = t["hora_texto"].map(normalizar_hora)
    mal = t["hora_texto"].str.strip() != t["hora"]
    cambio = t[mal]
    for (ant, nueva), g in cambio.groupby([cambio["hora_texto"], cambio["hora"]]):
        b = filas(t, g.index, variable, "corregido", "F3", f"hora escrita «{ant}»", campo="hora",
                  anterior=ant, nuevo=nueva if nueva else "")
        b["hora"] = nueva
        bit.agregar_tabla(b)
    print(f"    horas mal escritas: {int(mal.sum())}")


# Corrige el muestreo rutinario de sedimentos.
def sed_rutinario(bit, marcas):
    t = pd.read_pickle(comun.ruta("intermedios") / "sed_rutinario.pkl")
    t["estado"] = np.uint8(E["original"])
    print("  sed_rutinario")
    corregir_horas(t, "sed_rutinario", bit)
    rep = t[t.duplicated(["codigo", "fecha", "valor"], keep="first") & t.duplicated(["codigo", "fecha"], keep=False)]
    bit.agregar_tabla(filas(t, rep.index, "sed_rutinario", "eliminado", "U1", "misma fecha y mismo valor en otra hora"))
    t.drop(index=rep.index, inplace=True)
    print(f"    copias de la misma fecha: {len(rep)}")
    t = t.sort_values(["codigo", "fecha", "hora"]).reset_index(drop=True)
    mismo = (t["codigo"] == t["codigo"].shift(1)) & (t["codigo"] == t["codigo"].shift(-1))
    pico = mismo & (t["valor"] > 50 * t["valor"].shift(1)) & (t["valor"] > 50 * t["valor"].shift(-1))
    marcar(t, pico.to_numpy(), "sed_rutinario", "pico aislado de concentración", marcas)
    marcar(t, (t["valor"] > 20000).to_numpy(), "sed_rutinario", "concentración mayor que 20 000 mg/l", marcas)
    t["unidad"] = "mg/l"
    return t


# Ejecuta la corrección de pozos y sedimentos y guarda resultados y bitácora.
def main():
    bit, marcas = comun.Bitacora("irregulares"), []
    tablas = {"pozos": pozos(bit, marcas), "sed_integrado": sed_integrado(bit, marcas), "sed_rutinario": sed_rutinario(bit, marcas)}
    b = bit.tabla()
    b["informe"] = b["variable"]
    m = pd.concat(marcas, ignore_index=True)
    for nombre, t in tablas.items():
        lim = R["limites"].get(nombre)
        if lim:
            malos = t[(t["estado"] != E["eliminado"]) & t["valor"].notna() & ~t["valor"].between(*lim)]
            assert len(malos) == 0, f"{nombre}: {len(malos)} valores fuera de rango sin tratar"
        n_camb = int(t["estado"].isin([E["corregido"], E["eliminado"]]).sum())
        n_bit = int(((b["variable"] == nombre) & (b["regla"].isin(["D1", "D2", "F1", "F2"]))).sum())
        assert n_camb == n_bit, f"{nombre}: {n_camb} valores cambiados y {n_bit} filas de bitácora"
        t.drop(columns=[c for c in ("hora_texto",) if c in t]).to_pickle(comun.ruta("intermedios") / f"{nombre}_corregido.pkl")
    print("  Controles superados")
    destino, revision = comun.ruta("intermedios"), comun.ruta("revision")
    b.to_pickle(destino / "bitacora_irregulares.pkl")
    m.to_pickle(destino / "marcas_irregulares.pkl")
    comun.a_csv(b, revision / "bitacora_irregulares.csv")
    comun.a_csv(m, revision / "marcas_irregulares.csv")
    resumen = b.groupby(["variable", "regla", "accion"]).size().rename("casos").reset_index()
    resumen["descripcion"] = resumen["regla"].map(R["reglas"])
    comun.a_csv(resumen, revision / "resumen_irregulares.csv")
    print(resumen.to_string(index=False))


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    main()
