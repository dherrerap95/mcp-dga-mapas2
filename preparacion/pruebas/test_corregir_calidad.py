# Pruebas de las reglas de corrección de calidad de aguas (D1, D2, F3, U1, P1, P2 y P6, y sus excepciones).
import numpy as np
import pandas as pd
import pytest

import corregir_calidad as cc


# Arma mediciones de calidad de un parámetro en una estación, una por mes desde una fecha.
def muestras(valores, cod_param, codigo="05410002-0", desde="2000-01-15", **extra):
    n = len(valores)
    t = pd.DataFrame({
        "codigo_dv": codigo, "cod_param": cod_param,
        "fecha": pd.date_range(desde, periods=n, freq="MS") + pd.Timedelta(days=14),
        "hora": "10:00", "hora_texto": "10:00", "profundidad": 0.0, "valor": np.array(valores, dtype=float),
        "calificador": "", "unidad": "mg/l", "estado": np.uint8(0),
    })
    for k, v in extra.items():
        t[k] = v
    return t


# Arma una fila del catálogo de parámetros con sus límites como texto, como en parametros_calidad.csv.
def catalogo(cod_param, lim=None, regla="", nombre="X", unidad="mg/l"):
    return pd.DataFrame([{"cod_param": cod_param, "nombre_original": nombre, "unidad_original": unidad, "nombre": nombre,
                          "unidad": unidad, "regla": regla,
                          "limite_min": "" if lim is None else str(lim[0]),
                          "limite_max": "" if lim is None else str(lim[1])}])


# Oxígeno disuelto (6000) con 95 mg/l es porcentaje de saturación: se elimina y no pasa a 9,5 mg/l.
def test_oxigeno_saturacion_se_elimina(bit, estados):
    t = muestras([8.5, 9.0, 9.2, 8.8, 9.1, 95.0, 8.9], "6000")
    cc.valores(t, catalogo("6000", (0, 25)), bit, [])
    assert np.isnan(t.at[5, "valor"]) and t.at[5, "estado"] == estados["eliminado"]
    b = bit.tabla()
    assert b["regla"].tolist() == ["D2"] and "saturación" in b["evidencia"].iloc[0]


# 55 °C en Baños del Toro (fuente termal) se conserva; en otra estación queda fuera de rango y se elimina.
@pytest.mark.parametrize("codigo, estado", [("04302011-0", "original"), ("05410002-0", "eliminado")])
def test_temperatura_termal(bit, estados, codigo, estado):
    t = muestras([15.0, 14.0, 16.0, 13.0, 17.0, 55.0, 15.5], "5140", codigo=codigo)
    cc.valores(t, catalogo("5140", (-2, 45)), bit, [])
    assert t.at[5, "estado"] == estados[estado]


# pH 7 351 con pH vecinos de 7,0 a 7,8 tiene coma corrida y pasa a 7,351.
def test_ph_coma_decimal(bit, estados):
    t = muestras([7.0, 7.4, 7.8, 7.2, 7.5, 7351.0, 7.3], "6020")
    cc.valores(t, catalogo("6020", (0, 14)), bit, [])
    assert t.at[5, "valor"] == pytest.approx(7.351)
    assert t.at[5, "estado"] == estados["corregido"]
    assert bit.tabla()["regla"].tolist() == ["D1"]


# Un valor dentro de rango pero más de cien veces el percentil 99 (con más de 100 mediciones) sin corrección única se marca.
def test_extremo_sin_correccion_se_marca(bit, estados):
    base = [1.0, 1.2, 0.9, 1.1, 1.0, 1.3, 0.8, 1.0, 1.1, 1.2]
    t = muestras(base * 20 + [5000.0], "9999")
    marcas = []
    cc.valores(t, catalogo("9999"), bit, marcas)
    assert t.at[200, "valor"] == 5000.0 and t.at[200, "estado"] == estados["dudoso"]
    assert bit.tabla().empty


# Con candidatos cercanos se elige el más próximo a la mediana solo si está tres veces más cerca.
def test_candidato():
    ref = pd.Series([0.35, 0.4, 0.45])
    assert cc.candidato(40.0, ref, None) == pytest.approx(0.4)
    assert cc.candidato(40.0, pd.Series([5.0, 6.0]), None) is None


# El oxígeno con código 6001 antes de 1990 pasa al código 6000 (P2); después de 1990 no cambia.
def test_reasignar_6001(bit):
    t = muestras([9.0, 9.5], "6001", desde="1989-11-01")
    t.loc[1, "fecha"] = pd.Timestamp("1995-05-01")
    cc.reasignar(t, bit)
    assert t["cod_param"].tolist() == ["6000", "6001"]
    b = bit.tabla()
    assert b["regla"].tolist() == ["P2"] and b["valor_anterior"].iloc[0] == "6001" and b["valor_corregido"].iloc[0] == "6000"


# El código 6311 se marca con unidad incierta antes de 2014 y no después.
def test_unidad_incierta_6311(estados):
    t = muestras([5.0, 0.01], "6311", desde="2013-12-01")
    t.loc[1, "fecha"] = pd.Timestamp("2015-01-15")
    marcas = []
    cc.unidades_inciertas(t, marcas)
    assert t["estado"].tolist() == [estados["dudoso"], estados["original"]]


# Mediciones repetidas iguales se eliminan (U1); repetidas con valores distintos se marcan.
def test_duplicados_calidad(bit, estados):
    t = muestras([1.0, 1.0, 2.0, 3.0], "7000")
    t["fecha"] = pd.Timestamp("2010-01-15")
    t.loc[3, "fecha"] = pd.Timestamp("2010-02-15")
    marcas = []
    cc.duplicados(t, bit, marcas)
    assert len(t) == 3
    assert bit.tabla()["regla"].tolist() == ["U1"]
    assert t["estado"].tolist() == [estados["dudoso"], estados["dudoso"], estados["original"]]


# Un calificador sin significado se reemplaza por vacío (P6) y el valor se marca.
def test_calificadores(bit, estados):
    t = muestras([1.0, 2.0, 3.0], "7000", calificador=["<", "X", ""])
    cc.calificadores(t, bit, [])
    assert t["calificador"].tolist() == ["<", "", ""]
    assert t["estado"].tolist() == [estados["original"], estados["dudoso"], estados["original"]]
    b = bit.tabla()
    assert b["regla"].tolist() == ["P6"] and b["valor_anterior"].iloc[0] == "X"


# Una hora mal escrita en calidad se normaliza y queda en la bitácora (F3).
def test_horas_calidad(bit):
    t = muestras([1.0, 2.0], "7000", hora_texto=["10:00", "9:3"])
    cc.horas(t, bit)
    assert t["hora"].tolist() == ["10:00", "09:30"]
    assert bit.tabla()["regla"].tolist() == ["F3"]


# Un rótulo de unidad corregido en el catálogo queda en la bitácora una vez por parámetro (P1).
def test_rotulos(bit):
    t = muestras([20.0, 21.0], "5140")
    cat = catalogo("5140", regla="P1", nombre="Temperatura", unidad="°C")
    cat["unidad_original"] = "Grad. C"
    cc.rotulos(cat, t, bit)
    b = bit.tabla()
    assert b["campo"].tolist() == ["unidad"]
    assert b["valor_anterior"].iloc[0] == "Grad. C" and b["valor_corregido"].iloc[0] == "°C"
    assert "2 mediciones" in b["evidencia"].iloc[0]
