// Cálculos sobre series diarias y mediciones irregulares de Mapas DGA.
import type { SerieDiaria } from "./datos";

const MS_DIA = 86_400_000;
export const ESTADO = { original: 0, corregido: 1, eliminado: 2, dudoso: 3 } as const;

export interface Fila {
  etiqueta: string;
  anio: number;
  mes?: number;
  valor: number | null;
  n_validos: number;
  n_dias: number;
}

// Convierte un número de días desde 1970-01-01 a fecha UTC.
export function fechaDeDia(d: number): Date {
  return new Date(d * MS_DIA);
}

// Convierte un número de días desde 1970-01-01 a texto ISO.
export function isoDeDia(d: number): string {
  return fechaDeDia(d).toISOString().slice(0, 10);
}

// Convierte una fecha ISO (AAAA, AAAA-MM o AAAA-MM-DD) al número de días desde 1970-01-01.
export function diaDeIso(txt: string, alFinal = false): number {
  const p = txt.split("-").map(Number);
  if (p.some(Number.isNaN) || p.length < 1 || p.length > 3) throw new Error(`Fecha no válida: ${txt}`);
  const [a, m, d] = p;
  if (p.length === 1) return Date.UTC(alFinal ? a + 1 : a, 0, 1) / MS_DIA - (alFinal ? 1 : 0);
  if (p.length === 2) return Date.UTC(a, alFinal ? m : m - 1, 1) / MS_DIA - (alFinal ? 1 : 0);
  return Date.UTC(a, m - 1, d) / MS_DIA;
}

// Convierte desde/hasta en días; con año hidrológico, un año solo (AAAA) abarca el año hidrológico completo que empieza en él.
export function periodo(desde?: string, hasta?: string, mesInicio = 1): [number | undefined, number | undefined] {
  const soloAnio = /^\d{4}$/;
  const hidro = mesInicio !== 1;
  const a = desde === undefined ? undefined : hidro && soloAnio.test(desde) ? Date.UTC(Number(desde), mesInicio - 1, 1) / MS_DIA : diaDeIso(desde);
  const b =
    hasta === undefined ? undefined : hidro && soloAnio.test(hasta) ? Date.UTC(Number(hasta) + 1, mesInicio - 1, 1) / MS_DIA - 1 : diaDeIso(hasta, true);
  return [a, b];
}

// Recorta una serie diaria al período pedido (días inclusivos).
export function recortar(s: SerieDiaria, desde?: number, hasta?: number): SerieDiaria {
  const fin = s.inicio + s.valores.length - 1;
  const a = Math.max(s.inicio, desde ?? s.inicio);
  const b = Math.min(fin, hasta ?? fin);
  if (b < a) return { inicio: a, valores: new Float32Array(0), estados: new Uint8Array(0) };
  return { inicio: a, valores: s.valores.subarray(a - s.inicio, b - s.inicio + 1), estados: s.estados.subarray(a - s.inicio, b - s.inicio + 1) };
}

// Cuenta valores válidos de una serie.
export function nValidos(v: Float32Array): number {
  let n = 0;
  for (let i = 0; i < v.length; i++) if (!Number.isNaN(v[i])) n++;
  return n;
}

// Cuenta los valores corregidos, eliminados y dudosos de un conjunto de estados.
export function contarEstados(e: Uint8Array): { corregidos: number; eliminados: number; dudosos: number } {
  const c = { corregidos: 0, eliminados: 0, dudosos: 0 };
  for (let i = 0; i < e.length; i++) {
    if (e[i] === ESTADO.corregido) c.corregidos++;
    else if (e[i] === ESTADO.eliminado) c.eliminados++;
    else if (e[i] === ESTADO.dudoso) c.dudosos++;
  }
  return c;
}

// Días de un mes calendario.
export function diasDelMes(a: number, m: number): number {
  return new Date(Date.UTC(a, m, 0)).getUTCDate();
}

// Agrega una serie diaria a meses; un mes con completitud menor que el mínimo queda nulo.
export function agregarMensual(s: SerieDiaria, agregacion: "media" | "suma", minCompletitud: number): Fila[] {
  const grupos = new Map<number, { suma: number; n: number; anio: number; mes: number }>();
  for (let i = 0; i < s.valores.length; i++) {
    const f = fechaDeDia(s.inicio + i);
    const anio = f.getUTCFullYear();
    const mes = f.getUTCMonth() + 1;
    const clave = anio * 12 + mes;
    let g = grupos.get(clave);
    if (!g) {
      g = { suma: 0, n: 0, anio, mes };
      grupos.set(clave, g);
    }
    const v = s.valores[i];
    if (!Number.isNaN(v)) {
      g.suma += v;
      g.n++;
    }
  }
  return [...grupos.entries()]
    .sort((x, y) => x[0] - y[0])
    .map(([, g]) => {
      const dias = diasDelMes(g.anio, g.mes);
      const completo = g.n > 0 && g.n / dias >= minCompletitud;
      const valor = completo ? (agregacion === "suma" ? (g.suma / g.n) * dias : g.suma / g.n) : null;
      return { etiqueta: `${g.anio}-${String(g.mes).padStart(2, "0")}`, anio: g.anio, mes: g.mes, valor, n_validos: g.n, n_dias: dias };
    });
}

// Agrega meses a años (calendario o desde mesInicio); un año entra solo con sus doce meses válidos.
export function agregarAnual(meses: Fila[], agregacion: "media" | "suma", mesInicio = 1): Fila[] {
  const g = new Map<number, Fila[]>();
  for (const m of meses) {
    const a = m.mes! >= mesInicio ? m.anio : m.anio - 1;
    if (!g.has(a)) g.set(a, []);
    g.get(a)!.push(m);
  }
  return [...g.entries()]
    .sort((x, y) => x[0] - y[0])
    .map(([a, ms]) => {
      const validos = ms.filter((m) => m.valor !== null);
      const nDias = ms.reduce((x, m) => x + m.n_dias, 0);
      let valor: number | null = null;
      if (validos.length === 12) {
        valor = agregacion === "suma" ? validos.reduce((x, m) => x + m.valor!, 0) : validos.reduce((x, m) => x + m.valor! * m.n_dias, 0) / nDias;
      }
      const etiqueta = mesInicio === 1 ? String(a) : `${a}/${String((a + 1) % 100).padStart(2, "0")}`;
      return { etiqueta, anio: a, valor, n_validos: ms.reduce((x, m) => x + m.n_validos, 0), n_dias: nDias };
    });
}

// Promedia dos agregaciones fila a fila (temperatura media desde mínima y máxima).
export function promedioFilas(a: Fila[], b: Fila[]): Fila[] {
  const mb = new Map(b.map((f) => [f.etiqueta, f]));
  return a
    .filter((f) => mb.has(f.etiqueta))
    .map((f) => {
      const g = mb.get(f.etiqueta)!;
      const valor = f.valor !== null && g.valor !== null ? (f.valor + g.valor) / 2 : null;
      return { ...f, valor, n_validos: Math.min(f.n_validos, g.n_validos) };
    });
}

// Valor igualado o superado con la probabilidad p, posición de Weibull i/(n+1).
export function excedencia(ordenDesc: Float64Array, p: number): number | null {
  const n = ordenDesc.length;
  if (n === 0) return null;
  const pos = p * (n + 1);
  if (pos <= 1) return ordenDesc[0];
  if (pos >= n) return ordenDesc[n - 1];
  const i = Math.floor(pos);
  const f = pos - i;
  return ordenDesc[i - 1] + f * (ordenDesc[i] - ordenDesc[i - 1]);
}

// Valores válidos de una serie ordenados de mayor a menor.
export function validosDesc(v: Float32Array): Float64Array {
  const out: number[] = [];
  for (let i = 0; i < v.length; i++) if (!Number.isNaN(v[i])) out.push(v[i]);
  return Float64Array.from(out).sort().reverse();
}

// Media, desviación estándar, mínimo y máximo de una lista de números.
export function estadisticos(x: number[]): { n: number; media: number; desv: number; min: number; max: number } | null {
  if (x.length === 0) return null;
  const media = x.reduce((a, b) => a + b, 0) / x.length;
  const desv = x.length > 1 ? Math.sqrt(x.reduce((a, b) => a + (b - media) ** 2, 0) / (x.length - 1)) : 0;
  let min = Infinity, max = -Infinity;
  for (const v of x) {
    if (v < min) min = v;
    if (v > max) max = v;
  }
  return { n: x.length, media, desv, min, max };
}

// Percentil por interpolación lineal (p entre 0 y 1) de una lista de números.
export function percentil(x: number[], p: number): number | null {
  if (x.length === 0) return null;
  const s = [...x].sort((a, b) => a - b);
  const pos = p * (s.length - 1);
  const i = Math.floor(pos);
  return i + 1 < s.length ? s[i] + (pos - i) * (s[i + 1] - s[i]) : s[i];
}

// Pendiente por año de una regresión lineal de valor contra fecha (días).
export function tendencia(dias: number[], valores: number[]): { pendiente_anual: number; n: number } | null {
  const n = dias.length;
  if (n < 3) return null;
  const mx = dias.reduce((a, b) => a + b, 0) / n;
  const my = valores.reduce((a, b) => a + b, 0) / n;
  let sxy = 0, sxx = 0;
  for (let i = 0; i < n; i++) {
    sxy += (dias[i] - mx) * (valores[i] - my);
    sxx += (dias[i] - mx) ** 2;
  }
  if (sxx === 0) return null;
  return { pendiente_anual: (sxy / sxx) * 365.25, n };
}

// Máximo y mínimo diario de cada año calendario con datos suficientes.
export function extremosAnuales(s: SerieDiaria, minCompletitud: number): { anio: number; max: number; fechaMax: string; min: number; fechaMin: string }[] {
  const g = new Map<number, { n: number; max: number; iMax: number; min: number; iMin: number }>();
  for (let i = 0; i < s.valores.length; i++) {
    const v = s.valores[i];
    if (Number.isNaN(v)) continue;
    const a = fechaDeDia(s.inicio + i).getUTCFullYear();
    let r = g.get(a);
    if (!r) {
      r = { n: 0, max: -Infinity, iMax: 0, min: Infinity, iMin: 0 };
      g.set(a, r);
    }
    r.n++;
    if (v > r.max) {
      r.max = v;
      r.iMax = i;
    }
    if (v < r.min) {
      r.min = v;
      r.iMin = i;
    }
  }
  return [...g.entries()]
    .filter(([a, r]) => r.n / ((Date.UTC(a + 1, 0, 1) - Date.UTC(a, 0, 1)) / MS_DIA) >= minCompletitud)
    .sort((x, y) => x[0] - y[0])
    .map(([a, r]) => ({ anio: a, max: r.max, fechaMax: isoDeDia(s.inicio + r.iMax), min: r.min, fechaMin: isoDeDia(s.inicio + r.iMin) }));
}

// Formatea un número con cifras significativas razonables.
export function num(v: number | null | undefined, dec?: number): string {
  if (v === null || v === undefined || Number.isNaN(v)) return "s/d";
  if (dec !== undefined) return v.toFixed(dec);
  const a = Math.abs(v);
  if (a === 0) return "0";
  if (a >= 100) return v.toFixed(0);
  if (a >= 10) return v.toFixed(1);
  if (a >= 1) return v.toFixed(2);
  return v.toPrecision(3);
}

// Arma una tabla Markdown.
export function tabla(cabecera: string[], filas: (string | number)[][]): string {
  const l = [`| ${cabecera.join(" | ")} |`, `|${cabecera.map(() => "---").join("|")}|`];
  for (const f of filas) l.push(`| ${f.join(" | ")} |`);
  return l.join("\n");
}

// Hora HH:MM desde minutos del día, o vacío si no hay hora.
export function hora(m: number): string {
  return m < 0 ? "" : `${String(Math.floor(m / 60)).padStart(2, "0")}:${String(m % 60).padStart(2, "0")}`;
}

export interface FilaMediciones {
  etiqueta: string;
  n: number;
  media: number | null;
  min: number | null;
  max: number | null;
}

// Agrupa mediciones irregulares por mes o año (calendario o desde mesInicio) con su media, mínimo, máximo y número.
export function agregarMediciones(dias: number[], valores: number[], paso: "mensual" | "anual", mesInicio = 1): FilaMediciones[] {
  const g = new Map<number, { etiqueta: string; xs: number[] }>();
  for (let i = 0; i < dias.length; i++) {
    const f = fechaDeDia(dias[i]);
    const y = f.getUTCFullYear();
    const m = f.getUTCMonth() + 1;
    let clave: number, etiqueta: string;
    if (paso === "mensual") {
      clave = y * 12 + m;
      etiqueta = `${y}-${String(m).padStart(2, "0")}`;
    } else {
      const a = m >= mesInicio ? y : y - 1;
      clave = a;
      etiqueta = mesInicio === 1 ? String(a) : `${a}/${String((a + 1) % 100).padStart(2, "0")}`;
    }
    if (!g.has(clave)) g.set(clave, { etiqueta, xs: [] });
    g.get(clave)!.xs.push(valores[i]);
  }
  return [...g.entries()]
    .sort((x, y) => x[0] - y[0])
    .map(([, { etiqueta, xs }]) => {
      const e = estadisticos(xs.filter((v) => !Number.isNaN(v)));
      return { etiqueta, n: xs.length, media: e?.media ?? null, min: e?.min ?? null, max: e?.max ?? null };
    });
}

// Recorta una lista de filas a una página y arma el aviso de paginación.
export function paginar<T>(filas: T[], pagina: number, porPagina: number): { filas: T[]; aviso: string } {
  const paginas = Math.max(1, Math.ceil(filas.length / porPagina));
  const p = Math.min(Math.max(1, pagina), paginas);
  const sel = filas.slice((p - 1) * porPagina, p * porPagina);
  const aviso = paginas > 1 ? `\n\nPágina ${p} de ${paginas} (${filas.length} filas en total, ${porPagina} por página); pida la siguiente con pagina=${Math.min(p + 1, paginas)} o acote el período.` : "";
  return { filas: sel, aviso };
}
