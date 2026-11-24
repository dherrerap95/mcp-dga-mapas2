# Arma el reporte semanal: datos nuevos respecto de la versión anterior, correcciones nuevas y noticias de la DGA.
import argparse
import json
import sys
import unicodedata
import urllib.request
import xml.etree.ElementTree as ET
from collections import Counter
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path

import comun

AGENTE = "Mozilla/5.0 (compatible; mcp-dga-mapas2/0.1; +https://github.com/dherrerap95/mcp-dga-mapas2)"
NOMBRES = {"caudal": "Caudal", "prep": "Precipitación", "tmin": "Temperatura mínima", "tmax": "Temperatura máxima",
           "tmed": "Temperatura media", "pozos": "Niveles de pozos", "sed_integrado": "Sedimentos (integrado)",
           "sed_rutinario": "Sedimentos (rutinario)", "calidad": "Calidad de aguas"}


# Lee el índice de estaciones y las fichas de una carpeta de datos procesados.
def leer(carpeta):
    with open(carpeta / "estaciones.json", encoding="utf-8") as f:
        est = json.load(f)
    resumen, reglas = {}, Counter()
    for ruta in (carpeta / "ficha").glob("*.json"):
        with open(ruta, encoding="utf-8") as f:
            ficha = json.load(f)
        resumen[ruta.stem] = ficha["resumen"]
        col = ficha["bitacora"]["columnas"].index("regla")
        reglas.update(fila[col] for fila in ficha["bitacora"]["filas"])
    return est, resumen, reglas


# Valores válidos de una variable de una estación (días o mediciones).
def validos(r):
    return r.get("n_validos", r.get("n_mediciones", 0)) or 0


# Compara dos versiones de los datos y devuelve las líneas del reporte de datos.
def comparar(anterior, nueva, reglas_texto):
    _, res_a, reg_a = leer(anterior)
    est_n, res_n, reg_n = leer(nueva)
    lineas = []
    nuevas = sorted(set(res_n) - set(res_a))
    retiradas = sorted(set(res_a) - set(res_n))
    lineas.append(f"Estaciones: {len(res_n)} ({len(nuevas)} nuevas, {len(retiradas)} que ya no aparecen).")
    if nuevas:
        nombres = dict(zip(est_n["datos"]["codigo"], est_n["datos"]["nombre"]))
        lineas.append("Estaciones nuevas: " + "; ".join(f"{c} {nombres.get(c, '')}" for c in nuevas[:20]) + (" y más." if len(nuevas) > 20 else "."))
    lineas += ["", "| variable | estaciones con datos nuevos | datos nuevos | dato más reciente |", "|---|---|---|---|"]
    for var, nombre in NOMBRES.items():
        con_nuevos, n_nuevos, ultimo = 0, 0, ""
        for cod, r in res_n.items():
            rn = r.get(var)
            if not rn:
                continue
            ultimo = max(ultimo, rn["fin"])
            ra = res_a.get(cod, {}).get(var)
            dif = validos(rn) - (validos(ra) if ra else 0)
            if dif > 0 or (ra and rn["fin"] > ra["fin"]):
                con_nuevos += 1
                n_nuevos += max(dif, 0)
        lineas.append(f"| {nombre} | {con_nuevos} | {n_nuevos} | {ultimo or 's/d'} |")
    cambios = {k: reg_n[k] - reg_a.get(k, 0) for k in reg_n if reg_n[k] != reg_a.get(k, 0)}
    lineas += ["", "Correcciones y eliminaciones respecto de la versión anterior:"]
    if cambios:
        lineas += [f"- {k} ({reglas_texto.get(k, k)}): {v:+d}" for k, v in sorted(cambios.items())]
    else:
        lineas.append("- Sin cambios en la bitácora.")
    return lineas


# Quita tildes y pasa a minúsculas.
def normalizar(t):
    return unicodedata.normalize("NFD", t).encode("ascii", "ignore").decode().lower()


# Lee el canal RSS de la DGA y agrupa por tema las noticias desde una fecha.
def noticias(cfg, desde):
    req = urllib.request.Request(cfg["rss"], headers={"User-Agent": AGENTE})
    with urllib.request.urlopen(req, timeout=60) as r:
        raiz = ET.fromstring(r.read())
    temas = {t: [normalizar(p) for p in ps] for t, ps in cfg["temas"].items()}
    grupos, otras = {t: [] for t in temas}, []
    for item in raiz.iter("item"):
        fecha = parsedate_to_datetime(item.findtext("pubDate"))
        if fecha < desde:
            continue
        titulo, enlace = item.findtext("title", "").strip(), item.findtext("link", "").strip()
        texto = normalizar(titulo + " " + (item.findtext("description") or "")[:400])
        linea = f"- {fecha.date().isoformat()}: [{titulo}]({enlace})"
        coincide = [t for t, ps in temas.items() if any(p in texto for p in ps)]
        for t in coincide:
            grupos[t].append(linea)
        if not coincide:
            otras.append(linea)
    lineas = []
    for t, ls in grupos.items():
        if ls:
            lineas += [f"**{t}**", *ls, ""]
    if otras:
        lineas += ["**Otras noticias**", *otras, ""]
    return lineas or ["Sin noticias publicadas en el período."]


# Escribe el reporte en Markdown.
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--anterior", type=Path, help="carpeta mapas2 de la versión anterior de los datos")
    ap.add_argument("--desde", help="fecha AAAA-MM-DD desde la cual se listan noticias (por omisión, 8 días atrás)")
    ap.add_argument("--salida", type=Path, required=True)
    a = ap.parse_args()
    par, reglas = comun.parametros(), comun.reglas()
    desde = datetime.fromisoformat(a.desde).replace(tzinfo=timezone.utc) if a.desde else datetime.now(timezone.utc) - timedelta(days=8)
    lineas = [f"Actualización semanal de los datos de Mapas DGA descargados el {par['fecha_descarga']}.", "", "## Datos nuevos", ""]
    if a.anterior and (a.anterior / "estaciones.json").exists():
        lineas += comparar(a.anterior, comun.ruta("procesados"), reglas["reglas"])
    else:
        lineas.append("No se encontró la versión anterior para comparar.")
    lineas += ["", f"## Noticias de la DGA desde el {desde.date().isoformat()}", ""]
    try:
        lineas += noticias(par["noticias"], desde)
    except (OSError, ET.ParseError) as e:
        lineas.append(f"No se pudo leer el canal de noticias de la DGA ({e}).")
    lineas += ["", "## Revisión", "", "Al fusionar este cambio, el despliegue prueba los datos nuevos en una vista previa y espera aprobación para pasar a producción."]
    a.salida.write_text("\n".join(lineas) + "\n", encoding="utf-8")
    print("\n".join(lineas))


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    main()
