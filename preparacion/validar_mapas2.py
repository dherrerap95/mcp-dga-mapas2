# Valida los binarios y JSON del servidor contra las tablas corregidas, valor a valor.
import json
import sys

import numpy as np
import pandas as pd
from convertir_mapas2 import CALIFICADORES, DIARIAS, EPOCA, IRREGULARES, OBSERVACIONES

import comun

CAMPOS_IRREGULAR = [("fecha", "<i4"), ("minuto", "<i2"), ("valor", "<f4"), ("estado", "u1"), ("obs", "u1")]
CAMPOS_CALIDAD = [("fecha", "<i4"), ("minuto", "<i2"), ("ipar", "<u2"), ("profundidad", "<f4"), ("valor", "<f4"),
                  ("calificador", "u1"), ("estado", "u1")]


# Lee un binario de serie diaria.
def leer_diario(ruta):
    b = ruta.read_bytes()
    ini, n = np.frombuffer(b[:8], "<i4")
    valor = np.frombuffer(b[8:8 + 4 * n], "<f4")
    estado = np.frombuffer(b[8 + 4 * n:8 + 5 * n], "u1")
    assert len(b) == 8 + 5 * n, f"{ruta}: largo inesperado"
    return int(ini), valor, estado


# Lee un binario de columnas con un entero n al inicio.
def leer_columnas(ruta, campos):
    b = ruta.read_bytes()
    n = int(np.frombuffer(b[:4], "<i4")[0])
    pos, out = 4, {}
    for nombre, tipo in campos:
        tam = np.dtype(tipo).itemsize * n
        out[nombre] = np.frombuffer(b[pos:pos + tam], tipo)
        pos += tam
    assert pos == len(b), f"{ruta}: largo inesperado"
    return out


# Compara valores con NaN en las mismas posiciones y error relativo acotado.
def iguales(a, b):
    a, b = np.asarray(a, "f8"), np.asarray(b, "f8")
    if not np.array_equal(np.isnan(a), np.isnan(b)):
        return False, np.inf
    m = ~np.isnan(a)
    err = np.abs(a[m] - b[m]) / np.maximum(np.abs(b[m]), 1e-6)
    return bool((err < 1e-6).all()) if m.any() else True, float(err.max()) if m.any() else 0.0


# Valida las series diarias.
def validar_diarias(salida):
    for var in DIARIAS:
        t = pd.read_pickle(comun.ruta("intermedios") / f"{var}_corregido.pkl")
        t = t[t["fecha"].notna()]
        err_max, n = 0.0, 0
        for cod, g in t.groupby("codigo"):
            ruta = salida / "series" / var / f"{cod}.bin"
            if not ruta.exists():
                assert g["valor"].isna().all(), f"{var} {cod}: falta el archivo"
                continue
            ini, valor, estado = leer_diario(ruta)
            pos = ((g["fecha"] - EPOCA).dt.days.to_numpy() - ini)
            dentro = (pos >= 0) & (pos < len(valor))
            assert g.loc[~dentro, "valor"].isna().all(), f"{var} {cod}: valores fuera del rango escrito"
            ok, e = iguales(valor[pos[dentro]], g["valor"].to_numpy()[dentro])
            assert ok, f"{var} {cod}: valores distintos (error {e})"
            assert (estado[pos[dentro]] == g["estado"].to_numpy()[dentro]).all(), f"{var} {cod}: estados distintos"
            libres = np.setdiff1d(np.arange(len(valor)), pos[dentro])
            assert np.isnan(valor[libres]).all() and (estado[libres] == 0).all(), f"{var} {cod}: días sin fila con dato"
            err_max, n = max(err_max, e), n + 1
        print(f"  {var}: {n} estaciones idénticas (error relativo máximo {err_max:.1e})")


# Valida las mediciones irregulares.
def validar_irregulares(salida):
    for var in IRREGULARES:
        t = pd.read_pickle(comun.ruta("intermedios") / f"{var}_corregido.pkl")
        t = t[t["fecha"].notna()].copy()
        t["hora"] = t.get("hora", "")
        t = t.sort_values(["codigo", "fecha", "hora"])
        for cod, g in t.groupby("codigo"):
            b = leer_columnas(salida / "series" / var / f"{cod}.bin", CAMPOS_IRREGULAR)
            assert (b["fecha"] == (g["fecha"] - EPOCA).dt.days.to_numpy()).all(), f"{var} {cod}: fechas"
            assert iguales(b["valor"], g["valor"])[0], f"{var} {cod}: valores"
            assert (b["estado"] == g["estado"].to_numpy()).all(), f"{var} {cod}: estados"
            if "observacion" in g:
                assert [OBSERVACIONES[i] for i in b["obs"]] == g["observacion"].tolist(), f"{var} {cod}: observación"
        print(f"  {var}: {t['codigo'].nunique()} estaciones idénticas")


# Valida la calidad de aguas.
def validar_calidad(salida, catalogo):
    t = pd.read_pickle(comun.ruta("intermedios") / "calidad_corregido.pkl")
    codigos = [p["codigo"] for p in catalogo["parametros"]]
    n = 0
    for cod, g in t.groupby("codigo"):
        b = leer_columnas(salida / "calidad" / f"{cod}.bin", CAMPOS_CALIDAD)
        leido = pd.DataFrame({"fecha": b["fecha"], "par": [codigos[i] for i in b["ipar"]],
                              "prof": b["profundidad"].astype("f8"), "valor": b["valor"].astype("f8"),
                              "cal": [CALIFICADORES[i] for i in b["calificador"]], "estado": b["estado"]})
        orig = pd.DataFrame({"fecha": (g["fecha"] - EPOCA).dt.days.to_numpy(), "par": g["cod_param"].to_numpy(),
                             "prof": g["profundidad"].fillna(0).to_numpy(dtype="f4").astype("f8"),
                             "valor": g["valor"].to_numpy(dtype="f4").astype("f8"),
                             "cal": g["calificador"].to_numpy(), "estado": g["estado"].to_numpy()})
        clave = ["fecha", "par", "prof", "cal", "estado", "valor"]
        a = leido.sort_values(clave, na_position="last").reset_index(drop=True)
        o = orig.sort_values(clave, na_position="last").reset_index(drop=True)
        pd.testing.assert_frame_equal(a.drop(columns="valor"), o.drop(columns="valor"), check_dtype=False,
                                      obj=f"calidad {cod}")
        assert iguales(a["valor"], o["valor"])[0], f"calidad {cod}: valores distintos"
        n += len(g)
    print(f"  calidad: {t['codigo'].nunique()} estaciones y {n} mediciones idénticas")


# Ejecuta todas las validaciones.
def main():
    salida = comun.ruta("procesados")
    with open(salida / "catalogo.json", encoding="utf-8") as f:
        catalogo = json.load(f)
    validar_diarias(salida)
    validar_irregulares(salida)
    validar_calidad(salida, catalogo)
    with open(salida / "estaciones.json", encoding="utf-8") as f:
        est = json.load(f)
    fichas = {r.stem for r in (salida / "ficha").glob("*.json")}
    assert set(est["datos"]["codigo"]) == fichas, "estaciones.json y las fichas no coinciden"
    for var in list(DIARIAS) + list(IRREGULARES):
        con_serie = {r.stem for r in (salida / "series" / var).glob("*.bin")}
        en_indice = {c for c, vs in zip(est["datos"]["codigo"], est["datos"]["variables"]) if var in vs}
        assert con_serie == en_indice, f"{var}: el índice no coincide con los archivos"
    print(f"  JSON coherentes: {len(fichas)} estaciones con ficha y variables del índice iguales a los archivos")
    print("Validación completa: todo coincide")


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    main()
