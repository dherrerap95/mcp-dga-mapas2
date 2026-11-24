# Prueba las herramientas de un servidor MCP por Streamable HTTP y muestra sus respuestas.
import json
import ssl
import sys
import time
import urllib.request

try:
    import certifi

    CONTEXTO = ssl.create_default_context(cafile=certifi.where())
except ImportError:
    CONTEXTO = ssl.create_default_context()

URL = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8790/mcp"
CABECERAS = {"content-type": "application/json", "accept": "application/json, text/event-stream", "user-agent": "prueba-mcp/0.1"}


# Envía un mensaje JSON-RPC y devuelve la respuesta (JSON o evento SSE).
def llamar(metodo, params=None, id_=1, extra=None):
    cuerpo = {"jsonrpc": "2.0", "method": metodo, "params": params or {}}
    if id_ is not None:
        cuerpo["id"] = id_
    req = urllib.request.Request(URL, data=json.dumps(cuerpo).encode(), headers={**CABECERAS, **(extra or {})}, method="POST")
    t = time.time()
    with urllib.request.urlopen(req, timeout=60, context=CONTEXTO) as r:
        txt = r.read().decode("utf-8")
        dt = (time.time() - t) * 1000
    if not txt.strip():
        return None, dt
    if txt.lstrip().startswith("{"):
        return json.loads(txt), dt
    datos = [l[5:].strip() for l in txt.splitlines() if l.startswith("data:")]
    return json.loads(datos[-1]), dt


FALLAS = []
CITA = "Mapas DGA (https://mapas2.mop.gob.cl)"


# Ejecuta una herramienta, imprime su texto y registra si no responde lo esperado.
def herramienta(nombre, args, error=False, contiene=()):
    r, dt = llamar("tools/call", {"name": nombre, "arguments": args}, id_=2)
    res = r.get("result", r)
    texto = "\n".join(c.get("text", "") for c in res.get("content", [])) if "content" in res else json.dumps(res, ensure_ascii=False)
    es_error = bool(res.get("isError", False)) or "error" in r
    print(f"\n===== {nombre} {json.dumps(args, ensure_ascii=False)} ({dt:.0f} ms, error={es_error})\n{texto[:2500]}")
    if es_error != error:
        FALLAS.append(f"{nombre}: error={es_error}, se esperaba {error}")
    for c in (contiene if error else (*contiene, CITA)):
        if c not in texto:
            FALLAS.append(f"{nombre}: falta «{c}» en la respuesta")
    return res


# Recorre las herramientas con casos típicos y termina con error si alguno falla.
def main():
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    r, dt = llamar("initialize", {"protocolVersion": "2025-06-18", "capabilities": {}, "clientInfo": {"name": "prueba", "version": "0"}})
    print("initialize:", json.dumps(r.get("result", r).get("serverInfo", r), ensure_ascii=False), f"{dt:.0f} ms")
    r, _ = llamar("tools/list", id_=3)
    nombres = [t["name"] for t in r["result"]["tools"]]
    print("herramientas:", nombres)
    esperadas = {"describir_fuente", "buscar_estaciones", "ficha_estacion", "serie", "estadisticas", "niveles_pozo", "calidad_agua", "correcciones"}
    if set(nombres) != esperadas:
        FALLAS.append(f"tools/list: {sorted(nombres)}")
    herramienta("describir_fuente", {}, contiene=["Diego Herrera Pino", "caudal", "No es un servicio oficial"])
    herramienta("buscar_estaciones", {"texto": "chacabuquito"}, contiene=["05410002"])
    herramienta("buscar_estaciones", {"variable": "pozos", "lat": -30.7, "lon": -70.8, "radio_km": 100}, contiene=["04522005"])
    herramienta("buscar_estaciones", {"parametro": "temperatura del agua", "region": "coquimbo", "texto": "banos del toro"}, contiene=["04302011"])
    herramienta("ficha_estacion", {"codigo": "5410002"}, contiene=["Aconcagua", "sed_rutinario", "Conductividad"])
    herramienta("estadisticas", {"codigo": "05410002", "variable": "caudal", "desde": "1980", "hasta": "2019", "mes_inicio_anio": 4},
                contiene=["1980-04-01 a 2020-03-31", "| 32.3 m³/s | 15.8 | 0.49 | 10.1 (2019/20) | 69.0 (1987/88) |",
                          "| 98.2 | 66.8 | 37.4 | 20.1 | 12.2 | 9.70 | 8.54 | 6.80 |"])
    herramienta("serie", {"codigo": "03430007", "variable": "prep", "paso": "mensual", "desde": "2016-01", "hasta": "2016-12"}, contiene=["| 2016-06 | 1.00 | 30/30 |"])
    herramienta("serie", {"codigo": "03430007", "variable": "tmax", "paso": "diario", "desde": "2016-12-18", "hasta": "2016-12-22"}, contiene=["| 2016-12-20 | 39.0 | corregido |"])
    herramienta("serie", {"codigo": "04522005", "variable": "pozos", "paso": "anual", "desde": "2023", "hasta": "2023"}, contiene=["| 2023 | 6.13 | 6.01 | 6.24 | 6 |"])
    herramienta("serie", {"codigo": "04522005", "variable": "pozos", "desde": "2023-11", "hasta": "2024-01"}, contiene=["| 2023-11-07 |  | 6.24 |  | corregido |", "Sin acceso"])
    herramienta("serie", {"codigo": "04302011", "variable": "calidad", "parametro": "5140", "paso": "anual", "desde": "2024", "hasta": "2024"}, contiene=["| 2024 | 45.4 | 43.5 | 48.0 | 4 | 0 |"])
    herramienta("serie", {"codigo": "05410002", "variable": "sed_integrado", "paso": "mensual", "desde": "2020"}, contiene=["media (mg/l)"])
    herramienta("serie", {"codigo": "05410002", "variable": "caudal", "paso": "diario", "pagina": 2}, contiene=["Página 2 de", "| 1961-03-02 |"])
    herramienta("serie", {"codigo": "05410002", "variable": "calidad"}, error=True)
    herramienta("niveles_pozo", {"codigo": "04522005", "desde": "2023", "hasta": "2024", "mediciones": 10}, contiene=["| 2023-11-07 | 6.24 |"])
    herramienta("calidad_agua", {"codigo": "04302011", "parametro": "temperatura"}, contiene=["49.6", "Temperatura del Agua"])
    herramienta("correcciones", {"codigo": "03430007", "variable": "tmax"}, contiene=["390.1", "D1 Coma decimal corrida"])
    herramienta("ficha_estacion", {"codigo": "99999999"}, error=True)
    herramienta("serie", {"codigo": "05410002", "variable": "caudal", "desde": "ayer"}, error=True)
    if FALLAS:
        print("\nFALLAS:\n" + "\n".join(FALLAS))
        sys.exit(1)
    print("\nTodas las herramientas responden lo esperado")


if __name__ == "__main__":
    main()
