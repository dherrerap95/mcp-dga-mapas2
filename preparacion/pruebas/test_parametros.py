# Pruebas de coherencia de los archivos de parámetros de corrección.
import re

import pandas as pd
import pytest

import comun

R = comun.reglas()
PAR = comun.RAIZ / "parametros"


# Lee un CSV de parámetros con punto y coma como texto.
def leer(nombre):
    return pd.read_csv(PAR / nombre, sep=";", dtype=str, keep_default_na=False, encoding="utf-8-sig")


# Los estados son 0 a 3 y no se repiten.
def test_estados():
    assert sorted(R["estados"].values()) == [0, 1, 2, 3]


# Cada límite físico tiene mínimo menor que máximo, también los de estación.
def test_limites():
    for variable, (a, b) in R["limites"].items():
        assert a < b, variable
    for cod, estaciones in R["calidad"]["limites_por_estacion"].items():
        for est, (a, b) in estaciones.items():
            assert a < b, f"{cod} {est}"


# Tolerancias, divisores y umbrales son positivos y cada variable con coma decimal tiene tolerancia.
def test_tolerancias():
    cd = R["coma_decimal"]
    assert all(d > 1 for d in cd["divisores"])
    assert all(v > 0 for v in cd["tolerancia"].values())
    assert set(cd["vecinos_dias"]) <= set(cd["tolerancia"])
    assert 0 < R["duplicados"]["tolerancia_redondeo_rel"] < 0.5
    assert R["intercambio_extremas"]["tolerancia"] > 0


# Toda regla escrita en el código o en los parámetros está descrita en correcciones_mapas2.json.
def test_reglas_descritas():
    usadas = set()
    for archivo in (comun.RAIZ / "preparacion").glob("*.py"):
        usadas |= set(re.findall(r'"([CDTUFPM]\d[E]?)"', archivo.read_text(encoding="utf-8")))
    usadas |= set(leer("correcciones_puntuales.csv")["regla"])
    usadas |= set(leer("parametros_calidad.csv")["regla"]) - {""}
    faltan = usadas - set(R["reglas"])
    assert not faltan, f"reglas sin descripción: {sorted(faltan)}"


# Las correcciones puntuales tienen un estado y una acción conocidos y fechas en orden.
def test_correcciones_puntuales():
    p = leer("correcciones_puntuales.csv")
    assert set(p["estado"]) <= {"aprobado", "rechazado", "pendiente"}
    assert set(p["accion"]) <= {"eliminar", "corregir"}
    assert (pd.to_datetime(p["fecha_desde"]) <= pd.to_datetime(p["fecha_hasta"])).all()


# Los límites del catálogo de calidad son numéricos, van de a pares y tienen mínimo menor que máximo.
def test_catalogo_calidad():
    c = leer("parametros_calidad.csv")
    assert c["cod_param"].is_unique
    con = c[(c["limite_min"] != "") | (c["limite_max"] != "")]
    for _, r in con.iterrows():
        assert float(r["limite_min"]) < float(r["limite_max"]), r["cod_param"]
    for cod in R["calidad"]["limites_por_estacion"]:
        assert cod in set(c["cod_param"]), cod


# Los códigos de las reglas de calidad existen en el catálogo de parámetros.
@pytest.mark.parametrize("cod", sorted(
    {r["cod_param"] for r in R["calidad"]["unidad_incierta"]}
    | set(R["calidad"]["eliminar_sin_correccion"])
    | {x for r in R["calidad"]["reasignar_parametro"] for x in (r["de"], r["a"])}))
def test_codigos_calidad_en_catalogo(cod):
    assert cod in set(leer("parametros_calidad.csv")["cod_param"])
