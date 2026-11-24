# Pruebas de las reglas de corrección de pozos y sedimentos (D1, D2, F1, F2 y F3).
import numpy as np
import pandas as pd
import pytest

import comun
import corregir_irregulares as ci


# Arma mediciones irregulares de una estación con fechas y valores dados.
def mediciones(fechas, valores, codigo="05410002", **extra):
    t = pd.DataFrame({"codigo": codigo, "fecha": pd.to_datetime(fechas), "valor": np.array(valores, dtype=float),
                      "estado": np.uint8(0)})
    for k, v in extra.items():
        t[k] = v
    return t


# Un nivel de 1 250 m con mediciones vecinas de 12,3 y 12,6 m pasa a 12,5 m.
def test_pozo_coma_decimal_corrige(bit, estados):
    t = mediciones(["2010-01-01", "2010-06-01", "2011-01-01"], [12.3, 1250.0, 12.6])
    ci.coma_decimal_vecinas(t, "pozos", bit)
    assert t.at[1, "valor"] == pytest.approx(12.5)
    assert t.at[1, "estado"] == estados["corregido"]
    assert bit.tabla()["regla"].tolist() == ["D1"]


# Con varios candidatos se toma el más cercano a la mediana si está tres veces más cerca que el siguiente.
def test_pozo_coma_decimal_mas_cercano(bit, estados):
    t = mediciones(["2010-01-01", "2010-06-01", "2011-01-01"], [-0.35, -40.0, -0.45])
    ci.coma_decimal_vecinas(t, "pozos", bit)
    assert t.at[1, "valor"] == pytest.approx(-0.4)
    assert t.at[1, "estado"] == estados["corregido"]


# Con candidatos a distancias parecidas de la mediana no hay corrección única y el valor se elimina.
def test_pozo_coma_decimal_ambiguo(bit, estados):
    t = mediciones(["2010-01-01", "2010-06-01", "2011-01-01"], [-0.1, -40.0, -0.3])
    ci.coma_decimal_vecinas(t, "pozos", bit)
    assert np.isnan(t.at[1, "valor"])
    assert t.at[1, "estado"] == estados["eliminado"]
    assert bit.tabla()["regla"].tolist() == ["D2"]


# Pozos: la fecha 01-01-1900 se elimina (F1) y se marcan nivel dinámico y nivel sobre el terreno.
def test_pozos_fecha_relleno_y_marcas(bit, estados, tmp_path, monkeypatch):
    t = mediciones(["1900-01-01", "2010-01-01", "2010-06-01", "2011-01-01"], [15.0, 15.2, 30.0, -1.5],
                   observacion=["", "", "Dinamico", ""])
    t.to_pickle(tmp_path / "pozos.pkl")
    monkeypatch.setattr(comun, "ruta", lambda clave: tmp_path)
    marcas = []
    r = ci.pozos(bit, marcas)
    assert r.at[0, "estado"] == estados["eliminado"] and pd.isna(r.at[0, "fecha"])
    assert bit.tabla()["regla"].tolist() == ["F1"]
    assert r.at[1, "estado"] == estados["original"]
    assert r.at[2, "estado"] == estados["dudoso"] and r.at[2, "valor"] == 30.0
    assert r.at[3, "estado"] == estados["dudoso"] and r.at[3, "valor"] == -1.5
    motivos = pd.concat(marcas)["motivo"].tolist()
    assert "nivel dinámico (medido con bombeo)" in motivos and "nivel sobre el terreno" in motivos


# Una fecha posterior a la descarga con un único año posible entre sus vecinas se corrige (F2).
def test_fecha_futura_corregida(bit, estados, monkeypatch):
    monkeypatch.setattr(comun, "parametros", lambda: {"fecha_descarga": "2026-11-24"})
    t = mediciones(["2019-03-10", "2091-06-15", "2019-09-20"], [100.0, 200.0, 300.0], fila=[1, 2, 3])
    ci.fechas_futuras(t, "sed_integrado", bit)
    assert t.at[1, "fecha"] == pd.Timestamp("2019-06-15")
    assert t.at[1, "estado"] == estados["corregido"] and t.at[1, "valor"] == 200.0
    b = bit.tabla()
    assert b["regla"].tolist() == ["F2"] and b["fecha_corregida"].iloc[0] == "2019-06-15"


# Una fecha posterior a la descarga con varios años posibles se elimina (F2).
def test_fecha_futura_ambigua(bit, estados, monkeypatch):
    monkeypatch.setattr(comun, "parametros", lambda: {"fecha_descarga": "2026-11-24"})
    t = mediciones(["2018-01-01", "2091-06-15", "2020-12-31"], [100.0, 200.0, 300.0], fila=[1, 2, 3])
    ci.fechas_futuras(t, "sed_integrado", bit)
    assert t.at[1, "estado"] == estados["eliminado"] and pd.isna(t.at[1, "fecha"])
    assert "2018, 2019, 2020" in bit.tabla()["evidencia"].iloc[0]


# Horas truncadas o sin minutos se completan; las ilegibles quedan vacías.
@pytest.mark.parametrize("texto, esperado", [
    ("9:30", "09:30"), ("14:3", "14:30"), ("9:3", "09:30"), ("8", "08:00"), (" 10:15 ", "10:15"), ("abc", ""), ("", ""),
])
def test_normalizar_hora(texto, esperado):
    assert ci.normalizar_hora(texto) == esperado


# Las horas mal escritas quedan en la bitácora con la regla F3 y las correctas no.
def test_corregir_horas(bit):
    t = mediciones(["2010-01-01"] * 3, [1.0, 2.0, 3.0], hora_texto=["10:15", "9:3", "8"])
    ci.corregir_horas(t, "sed_rutinario", bit)
    assert t["hora"].tolist() == ["10:15", "09:30", "08:00"]
    b = bit.tabla()
    assert sorted(b["valor_corregido"]) == ["08:00", "09:30"]
    assert set(b["regla"]) == {"F3"}
