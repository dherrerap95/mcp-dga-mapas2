# Pruebas de las reglas de corrección de las series diarias (C1 a C3, D1, D2, T1, T1E, T2, U1, U2, U2E y puntuales).
import numpy as np
import pandas as pd
import pytest

import comun
import corregir_diarias as cd
from conftest import serie_diaria


# Devuelve la bitácora como tabla.
def tabla(bit):
    return bit.tabla()


# Valores de relleno de precipitación y precipitaciones negativas se eliminan; 0 mm se conserva.
def test_centinelas_y_negativos_prep(bit, estados):
    t = serie_diaria([0.0, 999.9, 12.5, -3.0, 0.0])
    cd.centinelas_y_negativos(t, "prep", bit)
    assert t["estado"].tolist() == [estados["original"], estados["eliminado"], estados["original"],
                                    estados["eliminado"], estados["original"]]
    assert t["valor"].isna().tolist() == [False, True, False, True, False]
    b = tabla(bit)
    assert b["regla"].tolist() == ["C1", "C3"]
    assert b["valor_anterior"].tolist() == [999.9, -3.0]


# El relleno -99 °C de temperatura se elimina con la regla C2.
def test_centinela_temperatura(bit, estados):
    t = serie_diaria([12.0, -99.0, 13.0])
    cd.centinelas_y_negativos(t, "tmin", bit)
    assert t.at[1, "estado"] == estados["eliminado"]
    assert tabla(bit)["regla"].tolist() == ["C2"]


# 215 °C con máximas vecinas de 20 a 23 °C pasa a 21,5 °C.
def test_coma_decimal_corrige(bit, estados):
    t = serie_diaria([20.0, 21.0, 22.0, 215.0, 23.0, 22.0, 21.0])
    cd.coma_decimal(t, "tmax", bit)
    assert t.at[3, "valor"] == pytest.approx(21.5)
    assert t.at[3, "estado"] == estados["corregido"]
    b = tabla(bit)
    assert b["regla"].tolist() == ["D1"]
    assert b["valor_anterior"].iloc[0] == 215.0
    assert b["valor_corregido"].iloc[0] == pytest.approx(21.5)


# 77,5 °C con máximas vecinas de 11 a 13 °C no pasa a 7,75 °C (fuera de la tolerancia de 3 °C) y se elimina.
def test_coma_decimal_rechaza_candidato_lejano(bit, estados):
    t = serie_diaria([11.0, 12.0, 13.0, 77.5, 12.0, 11.5, 12.5])
    cd.coma_decimal(t, "tmax", bit)
    assert np.isnan(t.at[3, "valor"])
    assert t.at[3, "estado"] == estados["eliminado"]
    assert tabla(bit)["regla"].tolist() == ["D2"]


# Un valor fuera de rango sin días vecinos válidos se elimina.
def test_coma_decimal_sin_vecinos(bit, estados):
    t = serie_diaria([215.0, 20.0], fechas=["2010-01-01", "2010-03-01"])
    cd.coma_decimal(t, "tmax", bit)
    assert t.at[0, "estado"] == estados["eliminado"]
    assert t.at[1, "estado"] == estados["original"]
    assert "sin datos vecinos" in tabla(bit)["evidencia"].iloc[0]


# Mínima y máxima invertidas que calzan con sus vecinas se intercambian (T1).
def test_intercambiar_extremas(bit, estados):
    tn = serie_diaria([9.0, 10.0, 11.0, 25.0, 10.0, 9.5, 10.5])
    tx = serie_diaria([24.0, 25.0, 26.0, 10.0, 25.0, 24.5, 25.5])
    marcas = []
    cd.intercambiar_extremas(tn, tx, bit, marcas)
    assert tn.at[3, "valor"] == 10.0 and tx.at[3, "valor"] == 25.0
    assert tn.at[3, "estado"] == estados["corregido"] and tx.at[3, "estado"] == estados["corregido"]
    assert tabla(bit)["regla"].tolist() == ["T1", "T1"]
    assert marcas == []


# Mínima mayor que máxima que no calza con las vecinas se elimina en ambas series (T1E).
def test_intercambiar_extremas_sin_calce(bit, estados):
    tn = serie_diaria([5.0, 5.0, 5.0, 25.0, 5.0, 5.0, 5.0])
    tx = serie_diaria([15.0, 15.0, 15.0, 22.0, 15.0, 15.0, 15.0])
    cd.intercambiar_extremas(tn, tx, bit, [])
    assert tn.at[3, "estado"] == estados["eliminado"] and tx.at[3, "estado"] == estados["eliminado"]
    assert tabla(bit)["regla"].tolist() == ["T1E", "T1E"]


# Una mínima mayor que la máxima por menos de 0,5 °C solo se marca.
def test_intercambiar_extremas_diferencia_menor(bit, estados):
    tn = serie_diaria([10.0, 10.3, 10.0])
    tx = serie_diaria([20.0, 10.0, 20.0])
    marcas = []
    cd.intercambiar_extremas(tn, tx, bit, marcas)
    assert len(marcas) == 2
    assert tn.at[1, "estado"] == estados["original"]
    assert tabla(bit).empty


# La media distinta de (mín+máx)/2 se recalcula; la media de un día con extrema eliminada se elimina (T2).
def test_media_derivada(bit, estados):
    tn = serie_diaria([10.0, 10.0, 10.0])
    tx = serie_diaria([20.0, 20.0, 20.0])
    tm = serie_diaria([15.0, 17.0, 15.0])
    tn.loc[2, ["valor", "estado"]] = [np.nan, estados["eliminado"]]
    cd.media_derivada(tm, tn, tx, bit)
    assert tm.at[0, "estado"] == estados["original"]
    assert tm.at[1, "valor"] == 15.0 and tm.at[1, "estado"] == estados["corregido"]
    assert tm.at[2, "estado"] == estados["eliminado"]
    assert tabla(bit)["regla"].tolist() == ["T2", "T2"]


# Duplicados: copia idéntica (U1), diferencia de redondeo bajo 5 % (U2) y valores incompatibles (U2E).
def test_resolver_duplicados(bit, estados):
    t = serie_diaria([5.0, 5.0, 10.4, 10.0, 10.0, 12.5, 7.0],
                     fechas=["2010-01-01", "2010-01-01", "2010-01-02", "2010-01-02", "2010-01-03", "2010-01-03",
                             "2010-01-04"])
    t["decimales"] = [1, 1, 2, 1, 1, 1, 1]
    cd.resolver_duplicados(t, "caudal", bit)
    assert len(t) == 4
    assert t["fecha"].dt.day.tolist() == [1, 2, 3, 4]
    assert t["valor"].tolist()[:2] == [5.0, 10.0]
    assert np.isnan(t.at[2, "valor"]) and t.at[2, "estado"] == estados["eliminado"]
    assert t.at[3, "estado"] == estados["original"]
    assert sorted(tabla(bit)["regla"]) == ["U1", "U2", "U2E", "U2E"]


# Las correcciones puntuales rechazadas (01044001 y 05421002) dejan los valores en bruto.
def test_puntuales_rechazados_no_cambian(bit, estados):
    t = serie_diaria([0.23] * 40, desde="1997-01-01", codigo="05421002")
    antes = t.copy()
    cd.aplicar_puntuales(t, "caudal", bit)
    pd.testing.assert_frame_equal(t, antes)
    assert tabla(bit).empty


# Una corrección puntual aprobada se aplica y queda en la bitácora con su regla.
def test_puntual_aprobada_se_aplica(bit, estados, tmp_path, monkeypatch):
    (tmp_path / "parametros").mkdir()
    (tmp_path / "parametros" / "correcciones_puntuales.csv").write_text(
        "id;informe;codigo;variable;fecha_desde;fecha_hasta;valor_si;accion;valor_nuevo;regla;motivo;estado\n"
        "1;caudal;00000001;caudal;2010-01-02;2010-01-03;0,23;eliminar;;C4;prueba;aprobado\n", encoding="utf-8")
    monkeypatch.setattr(comun, "RAIZ", tmp_path)
    t = serie_diaria([0.23, 0.23, 0.23, 0.23])
    cd.aplicar_puntuales(t, "caudal", bit)
    assert t["estado"].tolist() == [estados["original"], estados["eliminado"], estados["eliminado"], estados["original"]]
    assert tabla(bit)["regla"].tolist() == ["C4", "C4"]


# Un tramo de días iguales se marca como dudoso sin cambiar el valor.
def test_marca_tramo_constante(estados):
    t = serie_diaria([0.0, 14.4, 14.4, 14.4, 14.4, 14.4, 0.0])
    marcas = []
    cd.marcar(t, "prep", marcas)
    m = cd.aplicar_marcas(marcas)
    assert t["estado"].tolist() == [estados["original"]] + [estados["dudoso"]] * 5 + [estados["original"]]
    assert t["valor"].tolist() == [0.0, 14.4, 14.4, 14.4, 14.4, 14.4, 0.0]
    assert len(m) == 5
