# Descarga los informes históricos y el catálogo de estaciones de Mapas DGA y registra la fecha de descarga.
import json
import sys
import time
import urllib.request
import zipfile
from datetime import datetime, timezone

import comun

AGENTE = "Mozilla/5.0 (compatible; mcp-dga-mapas2/0.1; +https://github.com/dherrerap95/mcp-dga-mapas2)"


# Descarga una URL a un archivo temporal con reintentos y devuelve sus bytes escritos.
def descargar(url, destino, intentos=4):
    tmp = destino.with_suffix(destino.suffix + ".tmp")
    for i in range(1, intentos + 1):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": AGENTE})
            with urllib.request.urlopen(req, timeout=600) as r, open(tmp, "wb") as f:
                while bloque := r.read(1 << 20):
                    f.write(bloque)
            tmp.replace(destino)
            return destino.stat().st_size
        except OSError as e:
            print(f"    intento {i} de {intentos} falló: {e}", flush=True)
            time.sleep(15 * i)
    raise RuntimeError(f"No se pudo descargar {url}")


# Extrae el catálogo de estaciones embebido en la página principal de Mapas DGA.
def catalogo_pagina(html):
    i = html.index("JSON.parse(") + len("JSON.parse(")
    cadena, _ = json.JSONDecoder().raw_decode(html[i:])
    return json.loads(cadena)


# Descarga los informes, valida que sean zip legibles y actualiza la fecha de descarga.
def main():
    par = comun.parametros()
    base = comun.ruta("originales")
    archivos = sorted({i["archivo"] for i in par["informes"].values()} | set(par["agregados_publicados"].values()))
    for nombre in archivos:
        destino = base / nombre
        destino.parent.mkdir(parents=True, exist_ok=True)
        tam = descargar(f"{par['url']}/{nombre}", destino)
        with zipfile.ZipFile(destino) as z:
            interno = z.infolist()[0]
            print(f"  {nombre}: {tam / 1e6:.1f} MB, contiene {interno.filename} ({interno.file_size / 1e6:.0f} MB)", flush=True)
    pagina = base / "pagina_mapas2.html"
    descargar(par["url"] + "/", pagina)
    est = catalogo_pagina(pagina.read_text(encoding="utf-8"))
    with open(base / par["catalogo_pagina"], "w", encoding="utf-8") as f:
        json.dump(est, f, ensure_ascii=False)
    print(f"  catálogo de la página: {len(est)} estaciones")
    hoy = datetime.now(timezone.utc).date().isoformat()
    ruta_par = comun.RAIZ / "parametros" / "mapas2_dga.json"
    datos = json.loads(ruta_par.read_text(encoding="utf-8"))
    datos["fecha_descarga"] = hoy
    ruta_par.write_text(json.dumps(datos, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"  fecha de descarga: {hoy}")


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    main()
