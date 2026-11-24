# Empaqueta los datos procesados de Mapas DGA en un zip reproducible y registra su huella SHA-256.
import hashlib
import json
import sys
import zipfile

import comun


# Calcula la huella SHA-256 de un archivo.
def sha256(ruta):
    h = hashlib.sha256()
    with open(ruta, "rb") as f:
        for bloque in iter(lambda: f.read(1 << 20), b""):
            h.update(bloque)
    return h.hexdigest()


# Arma el zip con rutas ordenadas y fecha fija, y escribe parametros/datos_release.json.
def main():
    par = comun.parametros()
    origen = comun.ruta("procesados")
    fecha = par["fecha_descarga"]
    anio, mes, dia = (int(x) for x in fecha.split("-"))
    nombre = f"datos_mapas2_{fecha.replace('-', '')}.zip"
    destino = comun.RAIZ / "datos" / nombre
    archivos = sorted(p for p in origen.rglob("*") if p.is_file())
    with zipfile.ZipFile(destino, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as z:
        for p in archivos:
            info = zipfile.ZipInfo(p.relative_to(origen.parent).as_posix(), (anio, mes, dia, 0, 0, 0))
            info.compress_type = zipfile.ZIP_DEFLATED
            z.writestr(info, p.read_bytes(), compresslevel=9)
    registro = {
        "etiqueta": f"datos-mapas2-{fecha}",
        "archivo": nombre,
        "sha256": sha256(destino),
        "archivos": len(archivos),
        "bytes": destino.stat().st_size,
    }
    (comun.RAIZ / "parametros" / "datos_release.json").write_text(json.dumps(registro, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(registro, indent=2))


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    main()
