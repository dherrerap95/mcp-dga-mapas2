# Convierte las tablas corregidas de Mapas DGA a binarios por estación y JSON para el servidor MCP.
import json
import math
import shutil
import struct
import sys

import numpy as np
import pandas as pd

import comun

R = comun.reglas()
E = R["estados"]
EPOCA = pd.Timestamp("1970-01-01")
DIARIAS = {
    "caudal": {"unidad": "m³/s", "agregacion": "media", "descripcion": "Caudal medio diario"},
    "prep": {"unidad": "mm", "agregacion": "suma", "descripcion": "Precipitación diaria"},
    "tmin": {"unidad": "°C", "agregacion": "media", "descripcion": "Temperatura mínima diaria"},
    "tmax": {"unidad": "°C", "agregacion": "media", "descripcion": "Temperatura máxima diaria"},
    "tmed": {"unidad": "°C", "agregacion": "media",
             "descripcion": "Temperatura media diaria, promedio de la mínima y la máxima"},
}
IRREGULARES = {
    "pozos": {"unidad": "m", "descripcion": "Profundidad del nivel del agua bajo el terreno (negativo: sobre el terreno)"},
    "sed_integrado": {"unidad": "mg/l", "descripcion": "Concentración de sedimentos en suspensión, muestreo integrado"},
    "sed_rutinario": {"unidad": "mg/l", "descripcion": "Concentración de sedimentos en suspensión, muestreo rutinario"},
}
COLUMNAS_BITACORA = ["informe", "variable", "campo", "fecha_original", "fecha_corregida", "hora", "profundidad",
                     "valor_anterior", "valor_corregido", "accion", "regla", "evidencia"]
OBSERVACIONES = ["", "Dinamico", "Sin acceso", "Seco", "Embancado", "Surgente"]
CALIFICADORES = ["", "=", "<", ">", "D"]
FORMATOS = {
    "diario": "series/<variable>/<codigo>.bin: int32 inicio (días desde 1970-01-01), int32 n, float32[n] valor, "
              "uint8[n] estado; little-endian; NaN = sin dato",
    "irregular": "series/<variable>/<codigo>.bin: int32 n, int32[n] fecha (días desde 1970-01-01), "
                 "int16[n] minuto del día (-1 = sin hora), float32[n] valor, uint8[n] estado, uint8[n] observación",
    "calidad": "calidad/<codigo>.bin: int32 n, int32[n] fecha, int16[n] minuto, uint16[n] índice del parámetro en "
               "catalogo.parametros, float32[n] profundidad, float32[n] valor, uint8[n] calificador, uint8[n] estado",
}


# Días desde 1970-01-01 de una serie de fechas.
def dias(fechas):
    return ((fechas - EPOCA).dt.days).to_numpy(dtype="<i4")


# Minuto del día de una hora HH:MM, o -1 si no hay hora.
def minutos(horas):
    h = horas.fillna("").astype(str)
    ok = h.str.fullmatch(r"\d{2}:\d{2}")
    m = np.full(len(h), -1, dtype="<i2")
    m[ok.to_numpy()] = (h[ok].str[:2].astype(int) * 60 + h[ok].str[3:].astype(int)).to_numpy()
    return m


# Escribe un archivo binario con un encabezado de enteros y una lista de arreglos.
def escribir(ruta, encabezado, arreglos):
    ruta.parent.mkdir(parents=True, exist_ok=True)
    with open(ruta, "wb") as f:
        f.write(struct.pack("<" + "i" * len(encabezado), *encabezado))
        f.writelines(np.ascontiguousarray(a).tobytes() for a in arreglos)


# Resumen de estados de un conjunto de valores.
def conteo_estados(estado):
    return {"corregidos": int((estado == E["corregido"]).sum()), "eliminados": int((estado == E["eliminado"]).sum()),
            "dudosos": int((estado == E["dudoso"]).sum())}


# Convierte las series diarias a binarios de serie completa y devuelve su resumen por estación.
def convertir_diarias(salida, resumen):
    for var in DIARIAS:
        t = pd.read_pickle(comun.ruta("intermedios") / f"{var}_corregido.pkl")
        t = t[t["fecha"].notna()].sort_values(["codigo", "fecha"])
        n_arch = 0
        for cod, g in t.groupby("codigo", sort=False):
            validos = g[g["valor"].notna()]
            if validos.empty:
                continue
            g = g[(g["fecha"] >= validos["fecha"].min()) & (g["fecha"] <= validos["fecha"].max())]
            ini = int((g["fecha"].iloc[0] - EPOCA).days)
            fin = int((g["fecha"].iloc[-1] - EPOCA).days)
            n = fin - ini + 1
            pos = dias(g["fecha"]) - ini
            valor = np.full(n, np.nan, dtype="<f4")
            estado = np.zeros(n, dtype="u1")
            valor[pos] = g["valor"].to_numpy(dtype="<f4")
            estado[pos] = g["estado"].to_numpy(dtype="u1")
            escribir(salida / "series" / var / f"{cod}.bin", [ini, n], [valor, estado])
            n_val = int(np.isfinite(valor).sum())
            resumen.setdefault(cod, {})[var] = {
                "inicio": validos["fecha"].min().date().isoformat(), "fin": validos["fecha"].max().date().isoformat(),
                "n_validos": n_val, "pct_faltante": round(100 * (1 - n_val / n), 2), **conteo_estados(estado)}
            n_arch += 1
        print(f"  {var}: {n_arch} estaciones")


# Convierte las mediciones irregulares de pozos y sedimentos.
def convertir_irregulares(salida, resumen):
    for var in IRREGULARES:
        t = pd.read_pickle(comun.ruta("intermedios") / f"{var}_corregido.pkl")
        t = t[t["fecha"].notna()].copy()
        t["hora"] = t.get("hora", "")
        t["obs"] = t["observacion"].map(OBSERVACIONES.index) if "observacion" in t else 0
        t = t.sort_values(["codigo", "fecha", "hora"])
        n_arch = 0
        for cod, g in t.groupby("codigo", sort=False):
            validos = g[g["valor"].notna()]
            escribir(salida / "series" / var / f"{cod}.bin", [len(g)], [
                dias(g["fecha"]), minutos(g["hora"]), g["valor"].to_numpy(dtype="<f4"),
                g["estado"].to_numpy(dtype="u1"), g["obs"].to_numpy(dtype="u1")])
            resumen.setdefault(cod, {})[var] = {
                "inicio": g["fecha"].min().date().isoformat(), "fin": g["fecha"].max().date().isoformat(),
                "n_mediciones": len(g), "n_validos": len(validos), **conteo_estados(g["estado"].to_numpy())}
            n_arch += 1
        print(f"  {var}: {n_arch} estaciones")


# Convierte la calidad de aguas a un binario por estación y devuelve la lista de parámetros.
def convertir_calidad(salida, resumen, cat):
    t = pd.read_pickle(comun.ruta("intermedios") / "calidad_corregido.pkl")
    codigos = cat["cod_param"].tolist()
    t["ipar"] = t["cod_param"].map({c: i for i, c in enumerate(codigos)}).astype("<u2")
    t["ical"] = t["calificador"].map(CALIFICADORES.index).astype("u1")
    t = t.sort_values(["codigo", "fecha", "hora", "ipar", "profundidad"])
    for cod, g in t.groupby("codigo", sort=False):
        escribir(salida / "calidad" / f"{cod}.bin", [len(g)], [
            dias(g["fecha"]), minutos(g["hora"]), g["ipar"].to_numpy(dtype="<u2"),
            g["profundidad"].fillna(0).to_numpy(dtype="<f4"), g["valor"].to_numpy(dtype="<f4"),
            g["ical"].to_numpy(dtype="u1"), g["estado"].to_numpy(dtype="u1")])
        por_param = {}
        for c, h in g.groupby("cod_param"):
            por_param[c] = {"n": len(h), "inicio": h["fecha"].min().date().isoformat(),
                            "fin": h["fecha"].max().date().isoformat(), "bajo_limite": int((h["calificador"] == "<").sum())}
        resumen.setdefault(cod, {})["calidad"] = {
            "inicio": g["fecha"].min().date().isoformat(), "fin": g["fecha"].max().date().isoformat(),
            "n_mediciones": len(g), "parametros": por_param, **conteo_estados(g["estado"].to_numpy())}
    print(f"  calidad: {t['codigo'].nunique()} estaciones")


# Comprime una lista de fechas ISO en tramos [inicio, fin] de días seguidos.
def tramos(fechas):
    f = sorted(set(pd.to_datetime(fechas)))
    salida, ini, ant = [], None, None
    for x in f:
        if ini is None:
            ini = ant = x
        elif (x - ant).days == 1:
            ant = x
        else:
            salida.append([ini.date().isoformat(), ant.date().isoformat()])
            ini = ant = x
    if ini is not None:
        salida.append([ini.date().isoformat(), ant.date().isoformat()])
    return salida


# Convierte un valor a JSON (NaN a null).
def a_json(v):
    if v is None or (isinstance(v, float) and math.isnan(v)):
        return None
    if isinstance(v, (np.floating,)):
        return None if np.isnan(v) else round(float(v), 6)
    if isinstance(v, (np.integer,)):
        return int(v)
    return v


# Arma por estación la bitácora de cambios y las marcas de valores dudosos.
def registros():
    destino = comun.ruta("intermedios")
    bit = pd.concat([pd.read_pickle(destino / f"bitacora_{n}.pkl") for n in ("diarias", "irregulares", "calidad", "estaciones")],
                    ignore_index=True)
    rotulos = bit[bit["codigo"] == "todas"]
    bit = bit[bit["codigo"] != "todas"].copy()
    bit["codigo"] = bit["codigo"].str.split("-").str[0]
    mar = pd.concat([pd.read_pickle(destino / f"marcas_{n}.pkl") for n in ("diarias", "irregulares", "calidad")], ignore_index=True)
    mar["codigo"] = mar["codigo"].str.split("-").str[0]
    mar = mar[mar["fecha"].notna()]
    reg = {}
    for cod, g in bit.groupby("codigo"):
        reg.setdefault(cod, {})["bitacora"] = [[a_json(v) for v in fila] for fila in g[COLUMNAS_BITACORA].itertuples(index=False)]
    for (cod, var, motivo), h in mar.groupby(["codigo", "variable", "motivo"]):
        reg.setdefault(cod, {}).setdefault("marcas", {}).setdefault(var, {})[motivo] = tramos(h["fecha"])
    print(f"  registros: {len(reg)} estaciones con cambios o marcas")
    return reg, rotulos


# Escribe la ficha de cada estación con su resumen, bitácora y marcas.
def escribir_fichas(salida, resumen, reg):
    for cod, r in resumen.items():
        ficha = {"resumen": r, "bitacora": {"columnas": COLUMNAS_BITACORA, "filas": reg.get(cod, {}).get("bitacora", [])},
                 "marcas": reg.get(cod, {}).get("marcas", {})}
        ruta = salida / "ficha" / f"{cod}.json"
        ruta.parent.mkdir(parents=True, exist_ok=True)
        with open(ruta, "w", encoding="utf-8") as f:
            json.dump(ficha, f, ensure_ascii=False, separators=(",", ":"))
    print(f"  fichas: {len(resumen)} estaciones")


# Escribe el índice de estaciones y el catálogo.
def escribir_json(salida, resumen, cat, rotulos):
    par = comun.parametros()
    est = pd.read_pickle(comun.ruta("intermedios") / "estaciones.pkl")
    est = est[est["codigo"].isin(resumen)].sort_values("codigo").drop(columns="informes")
    est["variables"] = est["codigo"].map(lambda c: {v: [int(d["inicio"][:4]), int(d["fin"][:4])] for v, d in resumen[c].items()})
    est["parametros"] = est["codigo"].map(lambda c: sorted(resumen[c].get("calidad", {}).get("parametros", {}), key=int))
    datos = {c: [a_json(v) for v in est[c].tolist()] for c in est.columns}
    with open(salida / "estaciones.json", "w", encoding="utf-8") as f:
        json.dump({"columnas": list(est.columns), "datos": datos}, f, ensure_ascii=False, separators=(",", ":"))
    parametros = [{"codigo": r["cod_param"], "nombre": r["nombre"], "unidad": r["unidad"],
                   "metodologia": r["metodologia"], "identificado": r["identificado"] == "True"} for _, r in cat.iterrows()]
    catalogo = {
        "fuente": "Mapas DGA", "url": par["url"], "cita": par["cita"], "fecha_descarga": par["fecha_descarga"],
        "variables_diarias": DIARIAS, "variables_irregulares": IRREGULARES,
        "observaciones_pozos": OBSERVACIONES, "calificadores_calidad": CALIFICADORES,
        "estados": E, "reglas": R["reglas"], "formatos": FORMATOS, "parametros": parametros,
        "correcciones_de_rotulo": [{k: a_json(r[k]) for k in ("variable", "campo", "valor_anterior", "valor_corregido", "regla", "evidencia")}
                                   for _, r in rotulos.iterrows()],
    }
    with open(salida / "catalogo.json", "w", encoding="utf-8") as f:
        json.dump(catalogo, f, ensure_ascii=False, indent=1)
    print(f"  estaciones.json: {len(est)} estaciones; catalogo.json: {len(parametros)} parámetros")


# Ejecuta la conversión completa.
def main():
    salida = comun.ruta("procesados")
    for sub in ("series", "calidad", "registro", "ficha"):
        shutil.rmtree(salida / sub, ignore_errors=True)
    cat = pd.read_csv(comun.RAIZ / "parametros" / "parametros_calidad.csv", sep=";", dtype=str,
                      keep_default_na=False, encoding="utf-8-sig")
    resumen = {}
    convertir_diarias(salida, resumen)
    convertir_irregulares(salida, resumen)
    convertir_calidad(salida, resumen, cat)
    reg, rotulos = registros()
    escribir_fichas(salida, resumen, reg)
    escribir_json(salida, resumen, cat, rotulos)
    (salida / "resumen.json").unlink(missing_ok=True)
    archivos = [p for p in salida.rglob("*") if p.is_file()]
    print(f"Listo: {len(archivos)} archivos, {sum(p.stat().st_size for p in archivos) / 1e6:.0f} MB, "
          f"el mayor de {max(p.stat().st_size for p in archivos) / 1e6:.1f} MB")


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    main()
