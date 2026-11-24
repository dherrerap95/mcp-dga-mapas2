# Unifica los metadatos de estaciones de todos los informes con el catálogo de la página y registra las correcciones.
import json
import sys

import numpy as np
import pandas as pd

import comun

R = comun.reglas()
INFORMES = ["caudal", "prep", "tmin", "tmax", "tmed", "pozos", "sed_integrado", "sed_rutinario", "calidad"]


# Distancia en kilómetros entre dos puntos geográficos.
def distancia_km(lat1, lon1, lat2, lon2):
    p = np.pi / 180
    a = np.sin((lat2 - lat1) * p / 2) ** 2 + np.cos(lat1 * p) * np.cos(lat2 * p) * np.sin((lon2 - lon1) * p / 2) ** 2
    return 12742 * np.arcsin(np.sqrt(a))


# Convierte la coordenada GGMMSS numérica del informe de calidad a texto 0GGMMSS.
def gms_calidad(texto):
    t = str(texto).strip().split(".")[0]
    return t.zfill(7) if t.isdigit() and len(t) >= 6 else ""


# Reúne los metadatos de cada informe en una tabla larga con coordenadas decimales.
def metadatos_informes():
    filas = []
    for n in INFORMES:
        e = pd.read_pickle(comun.ruta("intermedios") / f"estaciones_{n}.pkl").reset_index()
        if n == "calidad":
            e["lat"], e["lon"] = e["lat"].map(gms_calidad), e["lon"].map(gms_calidad)
        for _, r in e.iterrows():
            lat, s1 = comun.gms_a_decimal(r["lat"])
            lon, s2 = comun.gms_a_decimal(r["lon"])
            alt = pd.to_numeric(r["altitud"], errors="coerce")
            filas.append({"codigo": r["codigo"], "informe": n, "nombre": r["nombre"], "region": r.get("region", ""),
                          "comuna": r.get("comuna", ""), "codigo_dv": r.get("codigo_dv", ""),
                          "cuenca": r.get("cuenca", ""), "subcuenca": r.get("subcuenca", ""),
                          "lat_texto": r["lat"], "lon_texto": r["lon"], "lat": lat, "lon": lon,
                          "sesenta": s1 or s2, "altitud": alt,
                          "utm_e": pd.to_numeric(r.get("utm_e"), errors="coerce"),
                          "utm_n": pd.to_numeric(r.get("utm_n"), errors="coerce")})
    return pd.DataFrame(filas)


# Distancia entre las coordenadas geográficas y las UTM WGS84 del huso que corresponde a cada longitud.
def diferencia_utm(m):
    from pyproj import Transformer
    d = pd.Series(np.nan, index=m.index)
    for huso, epsg in ((18, 32718), (19, 32719)):
        sel = m.index[m["lon"].notna() & m["utm_e"].notna() & (np.floor((m["lon"] + 180) / 6) + 1 == huso)]
        if len(sel):
            tr = Transformer.from_crs(4326, epsg, always_xy=True)
            x, y = tr.transform(m.loc[sel, "lon"].to_numpy(), m.loc[sel, "lat"].to_numpy())
            d[sel] = np.hypot(x - m.loc[sel, "utm_e"].to_numpy(), y - m.loc[sel, "utm_n"].to_numpy()) / 1000
    return d


# Ejecuta la unificación de estaciones.
def main():
    par = comun.parametros()
    with open(comun.ruta("originales") / par["catalogo_pagina"], encoding="utf-8") as f:
        pag = pd.DataFrame(json.load(f))
    pag = pag.set_index("cod_estacion")
    m = metadatos_informes()
    m["dif_utm_km"] = diferencia_utm(m)
    bit = comun.Bitacora("estaciones")
    salida, marcas = [], []
    for cod, g in m.groupby("codigo"):
        r = g.iloc[0]
        informes = sorted(g["informe"].unique(), key=INFORMES.index)
        lat, lon = g["lat"].dropna().median(), g["lon"].dropna().median()
        for _, x in g[g["sesenta"]].drop_duplicates(["lat_texto", "lon_texto"]).iterrows():
            bit.agregar(codigo=cod, variable="estacion", campo="coordenadas", accion="corregido", regla="M1",
                        valor_anterior=f"{x['lat_texto']} {x['lon_texto']}", valor_corregido=f"{x['lat']:.5f} {x['lon']:.5f}",
                        evidencia=f"minutos o segundos iguales a 60 en el informe {x['informe']}")
        alt_inf = g["altitud"][g["altitud"] > 0]
        if (g["altitud"] == 0).any():
            bit.agregar(codigo=cod, variable="estacion", campo="altitud", accion="corregido", regla="M2",
                        valor_anterior="0", valor_corregido="", evidencia="altitud 0 sin medición")
        p = pag.loc[cod] if cod in pag.index else None
        if p is not None and not (-56.6 <= float(p["val_latitud"]) <= -17.4 and -110 <= float(p["val_longitud"]) <= -66):
            bit.agregar(codigo=cod, variable="estacion", campo="coordenadas", accion="eliminado", regla="M3",
                        valor_anterior=f"{p['val_latitud']} {p['val_longitud']}", valor_corregido="",
                        evidencia="coordenada truncada en la página y en el informe, fuera de Chile")
            pag.loc[cod, ["val_latitud", "val_longitud"]] = np.nan
            p = pag.loc[cod]
        if p is not None and pd.notna(p["val_latitud"]):
            plat, plon = float(p["val_latitud"]), float(p["val_longitud"])
            dist = distancia_km(lat, lon, plat, plon) if pd.notna(lat) else np.nan
            if pd.isna(lat) or dist > 1:
                bit.agregar(codigo=cod, variable="estacion", campo="coordenadas", accion="corregido", regla="M3",
                            valor_anterior="" if pd.isna(lat) else f"{lat:.5f} {lon:.5f}", valor_corregido=f"{plat:.5f} {plon:.5f}",
                            evidencia="sin coordenadas en el informe" if pd.isna(lat) else
                            f"a {dist:.1f} km de la posición del catálogo de la página")
            lat, lon = plat, plon
            alt = float(p["val_altitud"]) if float(p["val_altitud"] or 0) > 0 else (alt_inf.median() if len(alt_inf) else np.nan)
            nombre, estado, tipos = p["gls_estacion"], p["estado_estacion"], p["glosa_tipo"]
        elif p is not None:
            alt = float(p["val_altitud"]) if float(p["val_altitud"] or 0) > 0 else (alt_inf.median() if len(alt_inf) else np.nan)
            nombre, estado, tipos = p["gls_estacion"], p["estado_estacion"], p["glosa_tipo"]
        else:
            alt = alt_inf.median() if len(alt_inf) else np.nan
            nombre, estado, tipos = r["nombre"].title(), "", ""
            if g["dif_utm_km"].max() > 1:
                marcas.append((cod, f"coordenadas geográficas y UTM difieren {g['dif_utm_km'].max():.1f} km"))
        if pd.isna(lat) or pd.isna(lon):
            lat = lon = np.nan
            marcas.append((cod, "sin coordenadas"))
        dv = g.loc[g["codigo_dv"] != "", "codigo_dv"]
        cal = g[g["informe"] == "calidad"]
        salida.append({"codigo": cod, "codigo_dv": dv.iloc[0] if len(dv) else "", "nombre": nombre,
                       "region": r["region"].title() if r["region"] else "", "comuna": r["comuna"].title() if r["comuna"] else "",
                       "cuenca": cal["cuenca"].iloc[0] if len(cal) else "", "subcuenca": cal["subcuenca"].iloc[0] if len(cal) else "",
                       "lat": round(lat, 5) if pd.notna(lat) else np.nan, "lon": round(lon, 5) if pd.notna(lon) else np.nan,
                       "altitud": alt, "estado": estado, "tipos": tipos, "informes": informes})
    est = pd.DataFrame(salida)
    for c in ("nombre", "region", "comuna", "cuenca", "subcuenca", "tipos"):
        est[c] = est[c].fillna("").astype(str).str.split().str.join(" ")
    b = bit.tabla()
    mk = pd.DataFrame(marcas, columns=["codigo", "motivo"])
    destino, revision = comun.ruta("intermedios"), comun.ruta("revision")
    est.to_pickle(destino / "estaciones.pkl")
    b.to_pickle(destino / "bitacora_estaciones.pkl")
    comun.a_csv(est.assign(informes=est["informes"].str.join(", ")), revision / "estaciones.csv")
    comun.a_csv(b, revision / "bitacora_estaciones.csv")
    comun.a_csv(mk, revision / "marcas_estaciones.csv")
    print(f"  {len(est)} estaciones, {int((est['estado'] != '').sum())} en el catálogo de la página")
    print(b.groupby(["regla"]).size().to_string())
    print(mk.groupby("motivo").size().to_string() if len(mk) else "  sin marcas")


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    main()
