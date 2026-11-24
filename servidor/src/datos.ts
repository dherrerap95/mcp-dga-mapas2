// Lectura de los datos de Mapas DGA desde los archivos estáticos del Worker.

export interface Env {
  ASSETS: Fetcher;
  MAX_FILAS: string;
  MAX_ESTACIONES: string;
  MIN_COMPLETITUD: string;
  MIN_COMPLETITUD_PREP: string;
}

export const VARIABLES_DIARIAS = ["caudal", "prep", "tmin", "tmax", "tmed"] as const;
export const VARIABLES_IRREGULARES = ["pozos", "sed_integrado", "sed_rutinario"] as const;
export type VariableDiaria = (typeof VARIABLES_DIARIAS)[number];
export type VariableIrregular = (typeof VARIABLES_IRREGULARES)[number];
export type Variable = VariableDiaria | VariableIrregular | "calidad";

export interface Estacion {
  codigo: string;
  codigo_dv: string | null;
  nombre: string;
  region: string | null;
  comuna: string | null;
  cuenca: string | null;
  subcuenca: string | null;
  lat: number | null;
  lon: number | null;
  altitud: number | null;
  estado: string | null;
  tipos: string | null;
  variables: Partial<Record<Variable, [number, number]>>;
  parametros: string[];
}

export interface Parametro {
  codigo: string;
  nombre: string;
  unidad: string;
  metodologia: string;
  identificado: boolean;
}

export interface Catalogo {
  fuente: string;
  url: string;
  cita: string;
  fecha_descarga: string;
  variables_diarias: Record<VariableDiaria, { unidad: string; agregacion: "media" | "suma"; descripcion: string }>;
  variables_irregulares: Record<VariableIrregular, { unidad: string; descripcion: string }>;
  observaciones_pozos: string[];
  calificadores_calidad: string[];
  estados: Record<string, number>;
  reglas: Record<string, string>;
  parametros: Parametro[];
}

export interface ResumenVariable {
  inicio: string;
  fin: string;
  n_validos?: number;
  n_mediciones?: number;
  pct_faltante?: number;
  corregidos: number;
  eliminados: number;
  dudosos: number;
  parametros?: Record<string, { n: number; inicio: string; fin: string; bajo_limite: number }>;
}

export interface Ficha {
  resumen: Partial<Record<Variable, ResumenVariable>>;
  bitacora: { columnas: string[]; filas: (string | number | null)[][] };
  marcas: Record<string, Record<string, [string, string][]>>;
}

export interface SerieDiaria {
  inicio: number;
  valores: Float32Array;
  estados: Uint8Array;
}

export interface SerieIrregular {
  fechas: Int32Array;
  minutos: Int16Array;
  valores: Float32Array;
  estados: Uint8Array;
  observaciones: Uint8Array;
}

export interface Calidad {
  fechas: Int32Array;
  minutos: Int16Array;
  parametros: Uint16Array;
  profundidades: Float32Array;
  valores: Float32Array;
  calificadores: Uint8Array;
  estados: Uint8Array;
}

const PREFIJO = "/mapas2";
let cacheEstaciones: Estacion[] | null = null;
let cacheIndice: Map<string, Estacion> | null = null;
let cacheCatalogo: Catalogo | null = null;

// Pide un archivo estático por su ruta.
async function pedir(env: Env, ruta: string): Promise<Response> {
  return env.ASSETS.fetch(new Request("https://assets.local" + PREFIJO + ruta));
}

// Lee un JSON estático y falla si no existe.
async function leerJson<T>(env: Env, ruta: string): Promise<T> {
  const r = await pedir(env, ruta);
  if (!r.ok) throw new Error(`No se pudo leer ${ruta} (${r.status})`);
  return (await r.json()) as T;
}

// Lee un binario estático; null si no existe.
async function leerBinario(env: Env, ruta: string): Promise<ArrayBuffer | null> {
  const r = await pedir(env, ruta);
  if (r.status === 404) return null;
  if (!r.ok) throw new Error(`No se pudo leer ${ruta} (${r.status})`);
  return r.arrayBuffer();
}

// Normaliza un código de estación a 8 dígitos (acepta números, ceros omitidos y dígito verificador).
export function normalizarCodigo(c: string | number): string | null {
  const t = String(c).trim().split("-")[0];
  if (!/^\d{1,8}$/.test(t)) return null;
  return t.padStart(8, "0");
}

// Devuelve las estaciones del índice como filas.
export async function estaciones(env: Env): Promise<Estacion[]> {
  if (cacheEstaciones) return cacheEstaciones;
  const a = await leerJson<{ columnas: string[]; datos: Record<string, unknown[]> }>(env, "/estaciones.json");
  const n = a.datos["codigo"].length;
  const filas: Estacion[] = [];
  for (let i = 0; i < n; i++) {
    const f: Record<string, unknown> = {};
    for (const c of a.columnas) f[c] = a.datos[c][i];
    filas.push(f as unknown as Estacion);
  }
  cacheEstaciones = filas;
  cacheIndice = new Map(filas.map((e) => [e.codigo, e]));
  return filas;
}

// Busca una estación por su código.
export async function estacion(env: Env, codigo: string): Promise<Estacion | undefined> {
  await estaciones(env);
  return cacheIndice!.get(codigo);
}

// Devuelve el catálogo de la fuente.
export async function catalogo(env: Env): Promise<Catalogo> {
  if (!cacheCatalogo) cacheCatalogo = await leerJson(env, "/catalogo.json");
  return cacheCatalogo!;
}

// Devuelve la ficha de una estación con su resumen, bitácora y marcas.
export async function ficha(env: Env, codigo: string): Promise<Ficha> {
  return leerJson(env, `/ficha/${codigo}.json`);
}

// Lee una serie diaria con sus estados; null si la estación no tiene la variable.
export async function serieDiaria(env: Env, variable: VariableDiaria, codigo: string): Promise<SerieDiaria | null> {
  const buf = await leerBinario(env, `/series/${variable}/${codigo}.bin`);
  if (!buf) return null;
  const dv = new DataView(buf);
  const inicio = dv.getInt32(0, true);
  const n = dv.getInt32(4, true);
  return {
    inicio,
    valores: new Float32Array(buf.slice(8, 8 + 4 * n)),
    estados: new Uint8Array(buf.slice(8 + 4 * n, 8 + 5 * n)),
  };
}

// Corta un bloque de un binario como arreglo tipado alineado.
function bloque<T>(buf: ArrayBuffer, pos: number, n: number, bytes: number, Tipo: new (b: ArrayBuffer) => T): [T, number] {
  return [new Tipo(buf.slice(pos, pos + n * bytes)), pos + n * bytes];
}

// Lee una serie de mediciones irregulares (pozos o sedimentos); null si no existe.
export async function serieIrregular(env: Env, variable: VariableIrregular, codigo: string): Promise<SerieIrregular | null> {
  const buf = await leerBinario(env, `/series/${variable}/${codigo}.bin`);
  if (!buf) return null;
  const n = new DataView(buf).getInt32(0, true);
  let p = 4;
  let fechas: Int32Array, minutos: Int16Array, valores: Float32Array, estados: Uint8Array, observaciones: Uint8Array;
  [fechas, p] = bloque(buf, p, n, 4, Int32Array);
  [minutos, p] = bloque(buf, p, n, 2, Int16Array);
  [valores, p] = bloque(buf, p, n, 4, Float32Array);
  [estados, p] = bloque(buf, p, n, 1, Uint8Array);
  [observaciones, p] = bloque(buf, p, n, 1, Uint8Array);
  return { fechas, minutos, valores, estados, observaciones };
}

// Lee las mediciones de calidad de aguas de una estación; null si no existe.
export async function calidad(env: Env, codigo: string): Promise<Calidad | null> {
  const buf = await leerBinario(env, `/calidad/${codigo}.bin`);
  if (!buf) return null;
  const n = new DataView(buf).getInt32(0, true);
  let p = 4;
  let fechas: Int32Array, minutos: Int16Array, parametros: Uint16Array, profundidades: Float32Array;
  let valores: Float32Array, calificadores: Uint8Array, estados: Uint8Array;
  [fechas, p] = bloque(buf, p, n, 4, Int32Array);
  [minutos, p] = bloque(buf, p, n, 2, Int16Array);
  [parametros, p] = bloque(buf, p, n, 2, Uint16Array);
  [profundidades, p] = bloque(buf, p, n, 4, Float32Array);
  [valores, p] = bloque(buf, p, n, 4, Float32Array);
  [calificadores, p] = bloque(buf, p, n, 1, Uint8Array);
  [estados, p] = bloque(buf, p, n, 1, Uint8Array);
  return { fechas, minutos, parametros, profundidades, valores, calificadores, estados };
}
