# Configuración común de las pruebas de corrección: ruta de los módulos y constructores de series pequeñas.
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import comun

E = comun.reglas()["estados"]


# Arma una serie diaria de una estación desde una fecha y una lista de valores, con estado original.
def serie_diaria(valores, desde="2010-01-01", codigo="00000001", fechas=None):
    f = pd.to_datetime(fechas) if fechas is not None else pd.date_range(desde, periods=len(valores), freq="D")
    return pd.DataFrame({"codigo": codigo, "fecha": f, "valor": np.array(valores, dtype=float),
                         "estado": np.uint8(E["original"])})


# Devuelve una bitácora vacía.
@pytest.fixture
def bit():
    return comun.Bitacora("prueba")


# Devuelve los estados vigentes.
@pytest.fixture
def estados():
    return E
