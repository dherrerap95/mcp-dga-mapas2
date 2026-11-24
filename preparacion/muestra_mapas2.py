# Copia a la carpeta de pruebas del servidor los archivos de unas pocas estaciones de Mapas DGA.
import json
import shutil
import sys

import comun


# Copia series, calidad y fichas de las estaciones de muestra y filtra el índice.
def main():
    par = comun.parametros()
    origen = comun.ruta("procesados")
    destino = comun.RAIZ / par["muestra"]["carpeta"]
    codigos = set(par["muestra"]["estaciones"])
    shutil.rmtree(destino, ignore_errors=True)
    n = 0
    for ruta in origen.rglob("*"):
        if ruta.is_file() and ruta.stem in codigos:
            nuevo = destino / ruta.relative_to(origen)
            nuevo.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(ruta, nuevo)
            n += 1
    shutil.copy2(origen / "catalogo.json", destino / "catalogo.json")
    with open(origen / "estaciones.json", encoding="utf-8") as f:
        est = json.load(f)
    filas = [i for i, c in enumerate(est["datos"]["codigo"]) if c in codigos]
    est["datos"] = {k: [v[i] for i in filas] for k, v in est["datos"].items()}
    with open(destino / "estaciones.json", "w", encoding="utf-8") as f:
        json.dump(est, f, ensure_ascii=False, separators=(",", ":"))
    tam = sum(p.stat().st_size for p in destino.rglob("*") if p.is_file())
    print(f"  muestra: {len(filas)} estaciones, {n + 2} archivos, {tam / 1e6:.1f} MB en {destino}")


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    main()
