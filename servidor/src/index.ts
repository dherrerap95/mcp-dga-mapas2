// Servidor MCP público de Mapas DGA sobre Cloudflare Workers.
import { McpServer } from "@modelcontextprotocol/server";
import { createMcpHandler } from "agents/mcp/server";
import { z } from "zod";
import {
  ESTADO,
  agregarAnual,
  agregarMediciones,
  agregarMensual,
  contarEstados,
  estadisticos,
  excedencia,
  extremosAnuales,
  hora,
  isoDeDia,
  nValidos,
  num,
  paginar,
  percentil,
  periodo,
  promedioFilas,
  recortar,
  tabla,
  tendencia,
  validosDesc,
  type Fila,
} from "./calculos";
import {
  VARIABLES_DIARIAS,
  calidad,
  catalogo,
  estacion,
  estaciones,
  ficha,
  normalizarCodigo,
  serieDiaria,
  serieIrregular,
  type Catalogo,
  type Env,
  type Estacion,
  type VariableDiaria,
} from "./datos";

const AUTOR = "Diego Herrera Pino";
const VERSION = "0.1.1";
const ANOTACIONES = { readOnlyHint: true, openWorldHint: false } as const;
const TODAS = ["caudal", "prep", "tmin", "tmax", "tmed", "pozos", "sed_integrado", "sed_rutinario", "calidad"] as const;
const NOMBRE_ESTADO = ["", "corregido", "eliminado", "dudoso"];
const REGLA_PERIODO =
  "Con mes_inicio_anio distinto de 1 (por ejemplo 4, año hidrológico abril-marzo), desde y hasta escritos solo como año (AAAA) se interpretan como años hidrológicos completos nombrados por su año de inicio: desde '1980' y hasta '2019' abarcan 1980/81 a 2019/20. Para cortar por fechas exactas use AAAA-MM o AAAA-MM-DD.";

type Resultado = { content: { type: "text"; text: string }[]; isError?: boolean };

const esquemaFecha = z
  .string()
  .regex(/^\d{4}(-\d{2}(-\d{2})?)?$/, "Use AAAA, AAAA-MM o AAAA-MM-DD")
  .optional();
const esquemaCodigo = z.string().describe("Código de la estación DGA (BNA), por ejemplo '05410002' o '5410002'");

// Cita de la fuente con la fecha de descarga.
function cita(cat: Catalogo): string {
  return `Fuente: Dirección General de Aguas (DGA), Ministerio de Obras Públicas, Mapas DGA (${cat.url}), datos descargados el ${cat.fecha_descarga} y depurados por este servidor; los cambios se consultan con la herramienta correcciones.`;
}

// Respuesta de texto con la cita al final.
async function texto(env: Env, t: string): Promise<Resultado> {
  return { content: [{ type: "text", text: `${t}\n\n${cita(await catalogo(env))}` }] };
}

// Respuesta de error para el modelo.
function error(t: string): Resultado {
  return { content: [{ type: "text", text: t }], isError: true };
}

// Quita tildes y pasa a minúsculas para comparar nombres.
function normalizar(s: string | null | undefined): string {
  return (s ?? "").normalize("NFD").replace(/[̀-ͯ]/g, "").toLowerCase();
}

// Distancia en km entre dos puntos geográficos.
function distanciaKm(lat1: number, lon1: number, lat2: number, lon2: number): number {
  const r = Math.PI / 180;
  const a = Math.sin(((lat2 - lat1) * r) / 2) ** 2 + Math.cos(lat1 * r) * Math.cos(lat2 * r) * Math.sin(((lon2 - lon1) * r) / 2) ** 2;
  return 12_742 * Math.asin(Math.sqrt(a));
}

// Busca una estación por código o devuelve el mensaje de error.
async function resolver(env: Env, codigo: string): Promise<Estacion | string> {
  const c = normalizarCodigo(codigo);
  if (!c) return `Código no válido: ${codigo}. Use el código BNA de 8 dígitos, por ejemplo 05410002.`;
  const e = await estacion(env, c);
  return e ?? `No existe la estación ${c}. Use buscar_estaciones.`;
}

// Cantidad con su sustantivo en singular o plural.
function cuenta(n: number, singular: string, plural: string): string {
  return `${n} ${n === 1 ? singular : plural}`;
}

// Nota con los valores corregidos, eliminados y dudosos de un período.
function notaEstados(c: { corregidos: number; eliminados: number; dudosos: number }): string {
  if (c.corregidos + c.eliminados + c.dudosos === 0) return "Ningún valor del período fue corregido, eliminado ni marcado como dudoso.";
  return `En el período hay ${cuenta(c.corregidos, "valor corregido", "valores corregidos")}, ${cuenta(c.eliminados, "eliminado por erróneo", "eliminados por erróneos")} y ${cuenta(c.dudosos, "marcado como dudoso", "marcados como dudosos")} (conservados sin cambio); el detalle está en la herramienta correcciones.`;
}

// Resumen corto de las variables con datos de una estación.
function resumenVariables(e: Estacion): string {
  return Object.entries(e.variables)
    .map(([v, [a, b]]) => `${v} ${a}-${b}`)
    .join("; ");
}

// Códigos de parámetro de calidad que coinciden con un código o parte del nombre.
function codigosParametro(cat: Catalogo, q: string): string[] {
  const t = q.trim();
  if (/^\d+$/.test(t)) return cat.parametros.some((p) => p.codigo === t) ? [t] : [];
  const n = normalizar(t);
  return cat.parametros.filter((p) => normalizar(p.nombre).includes(n)).map((p) => p.codigo);
}

// Construye el servidor MCP con sus herramientas.
function crearServidor(env: Env): McpServer {
  const maxFilas = Number(env.MAX_FILAS);
  const maxEstaciones = Number(env.MAX_ESTACIONES);
  const minComp = Number(env.MIN_COMPLETITUD);
  const minCompPrep = Number(env.MIN_COMPLETITUD_PREP);
  const completitud = (v: VariableDiaria) => (v === "prep" ? minCompPrep : minComp);
  const server = new McpServer({ name: "dga-mapas2", title: `MCP de Mapas DGA (${AUTOR})`, version: VERSION });

  server.registerTool(
    "describir_fuente",
    {
      title: "Describir los datos de Mapas DGA",
      description:
        "Describe los datos de la DGA publicados en Mapas DGA: variables con unidades, número de estaciones y período, criterios de agregación, depuración de errores, parámetros de calidad de aguas y cómo citar. Úsela primero si no conoce los nombres de las variables.",
      inputSchema: z.object({}),
      annotations: ANOTACIONES,
    },
    async () => {
      const cat = await catalogo(env);
      const est = await estaciones(env);
      const filas = TODAS.map((v) => {
        const conDatos = est.filter((e) => e.variables[v]);
        const ini = Math.min(...conDatos.map((e) => e.variables[v]![0]));
        const fin = Math.max(...conDatos.map((e) => e.variables[v]![1]));
        const meta = v === "calidad" ? { unidad: "según parámetro", descripcion: "Calidad de aguas superficiales y subterráneas" } : v in cat.variables_diarias ? cat.variables_diarias[v as VariableDiaria] : cat.variables_irregulares[v as "pozos"];
        return [v, meta.descripcion, meta.unidad, conDatos.length, `${ini} a ${fin}`];
      });
      const ident = cat.parametros.filter((p) => p.identificado).length;
      return texto(
        env,
        [
          "# Datos hidrometeorológicos y de calidad de aguas de la DGA (Mapas DGA)",
          `Servidor MCP desarrollado por ${AUTOR}, gratuito y sin fines comerciales, para facilitar la consulta de los informes históricos que publica la Dirección General de Aguas en Mapas DGA. No es un servicio oficial de la DGA; para usos oficiales o legales consulte directamente a la DGA.`,
          "## Variables",
          tabla(["variable", "descripción", "unidad", "estaciones", "años con datos"], filas),
          "## Criterios",
          "- Las series diarias del portal empiezan en 1960, aunque algunas estaciones tienen registros anteriores. La red de temperatura es casi histórica: pocas estaciones tienen datos recientes.",
          `- Un mes se agrega con al menos ${minComp * 100}% de días con dato (precipitación: el mes completo); un año, solo con sus doce meses válidos. Caudal y temperatura se promedian; la precipitación se suma.`,
          "- La temperatura media diaria es el promedio de la mínima y la máxima; la media mensual se calcula desde las medias mensuales de mínima y máxima.",
          "- La máxima en 24 horas corresponde a la máxima precipitación diaria del año calendario (día pluviométrico).",
          "- Los niveles de pozos son profundidad bajo el terreno: un valor mayor indica nivel más bajo. Los niveles dinámicos (medidos con bombeo) se excluyen por omisión.",
          "- En calidad de aguas, los valores con calificador «<» son límites de detección y no se promedian como medidos.",
          "- Todas las series corregidas (diarias, pozos, sedimentos y calidad de aguas por parámetro) se consultan completas con la herramienta serie, medición por medición o agregadas por mes o año.",
          `- Las respuestas se limitan a ${maxFilas} filas por página y ${maxEstaciones} estaciones; las series largas se recorren con el parámetro pagina.`,
          "## Depuración",
          "Los errores evidentes se corrigen cuando el valor correcto se deduce sin ambigüedad (por ejemplo, una coma decimal corrida) y se eliminan cuando no; los valores dudosos pero posibles se conservan marcados. Cada cambio queda registrado con su valor original y su motivo (herramienta correcciones). Reglas:",
          Object.entries(cat.reglas).map(([k, v]) => `${k}: ${v}`).join("; ") + ".",
          "## Calidad de aguas",
          `${cat.parametros.length} códigos de parámetro, ${ident} identificados. Use calidad_agua sin parámetro para ver los medidos en una estación.`,
          `## Cita\n${cat.cita}. Datos descargados el ${cat.fecha_descarga}.`,
        ].join("\n\n"),
      );
    },
  );

  server.registerTool(
    "buscar_estaciones",
    {
      title: "Buscar estaciones",
      description:
        "Busca estaciones de la DGA por nombre (sin importar tildes), región, comuna, variable medida, parámetro de calidad de aguas, estado (activa o inactiva), cercanía a una coordenada y años con datos. Devuelve el código que piden las demás herramientas.",
      inputSchema: z.object({
        texto: z.string().optional().describe("Parte del nombre de la estación, por ejemplo 'Maipo' o 'Chacabuquito'"),
        region: z.string().optional().describe("Parte del nombre de la región, por ejemplo 'Coquimbo'"),
        comuna: z.string().optional(),
        variable: z.enum(TODAS).optional().describe("Variable que debe medir la estación"),
        parametro: z.string().optional().describe("Parámetro de calidad de aguas (código o parte del nombre, por ejemplo 'arsenico')"),
        estado: z.enum(["activa", "inactiva"]).optional(),
        lat: z.number().min(-56.6).max(-17).optional().describe("Latitud en grados decimales (negativa)"),
        lon: z.number().min(-110).max(-66).optional().describe("Longitud en grados decimales (negativa)"),
        radio_km: z.number().positive().max(500).optional().describe("Radio de búsqueda en km alrededor de lat/lon (50 por omisión)"),
        anios_minimos: z.number().int().min(1).max(70).optional().describe("Mínimo de años entre el primer y el último dato de la variable indicada"),
        con_datos_desde: z.number().int().min(1960).max(2030).optional().describe("Año desde el cual la variable indicada debe tener datos (estaciones vigentes)"),
        limite: z.number().int().min(1).max(50).optional().describe("Máximo de estaciones a devolver (15 por omisión)"),
      }),
      annotations: ANOTACIONES,
    },
    async ({ texto: t, region, comuna, variable, parametro, estado, lat, lon, radio_km, anios_minimos, con_datos_desde, limite }) => {
      if ((lat === undefined) !== (lon === undefined)) return error("Indique lat y lon juntas.");
      if ((anios_minimos !== undefined || con_datos_desde !== undefined) && !variable) return error("anios_minimos y con_datos_desde requieren indicar la variable.");
      const cat = await catalogo(env);
      let r = (await estaciones(env)).map((e) => ({ e, dist: undefined as number | undefined }));
      if (t) r = r.filter(({ e }) => normalizar(e.nombre).includes(normalizar(t)));
      if (region) r = r.filter(({ e }) => normalizar(e.region).includes(normalizar(region)));
      if (comuna) r = r.filter(({ e }) => normalizar(e.comuna).includes(normalizar(comuna)));
      if (estado) r = r.filter(({ e }) => normalizar(e.estado) === estado);
      if (variable) r = r.filter(({ e }) => e.variables[variable]);
      if (variable && anios_minimos) r = r.filter(({ e }) => e.variables[variable]![1] - e.variables[variable]![0] + 1 >= anios_minimos);
      if (variable && con_datos_desde) r = r.filter(({ e }) => e.variables[variable]![1] >= con_datos_desde);
      let nombresParam = "";
      if (parametro) {
        const cods = codigosParametro(cat, parametro);
        if (cods.length === 0) return error(`No hay parámetros de calidad que coincidan con «${parametro}».`);
        nombresParam = cods.map((c) => `${c} ${cat.parametros.find((p) => p.codigo === c)!.nombre}`).join(", ");
        r = r.filter(({ e }) => e.parametros.some((p) => cods.includes(p)));
      }
      if (lat !== undefined && lon !== undefined) {
        const rad = radio_km ?? 50;
        r = r.filter(({ e }) => e.lat !== null && e.lon !== null);
        for (const x of r) x.dist = distanciaKm(lat, lon, x.e.lat!, x.e.lon!);
        r = r.filter((x) => x.dist! <= rad).sort((a, b) => a.dist! - b.dist!);
      } else {
        r.sort((a, b) => (a.e.codigo < b.e.codigo ? -1 : 1));
      }
      const total = r.length;
      if (total === 0) return texto(env, "No hay estaciones que cumplan los criterios.");
      const lim = Math.min(limite ?? 15, maxEstaciones);
      const cab = ["código", "nombre", "comuna", "lat", "lon", "altitud", "estado", "datos (variable años)", ...(lat !== undefined ? ["distancia_km"] : [])];
      const filas = r.slice(0, lim).map(({ e, dist }) => [
        e.codigo,
        e.nombre,
        e.comuna ?? "s/d",
        e.lat === null ? "s/d" : e.lat.toFixed(4),
        e.lon === null ? "s/d" : e.lon.toFixed(4),
        e.altitud === null ? "s/d" : String(Math.round(e.altitud)),
        e.estado ?? "s/d",
        resumenVariables(e),
        ...(lat !== undefined ? [num(dist, 1)] : []),
      ]);
      return texto(env, `${cuenta(total, "estación cumple", "estaciones cumplen")} los criterios; se muestran ${filas.length}.${nombresParam ? ` Parámetros buscados: ${nombresParam}.` : ""}\n\n${tabla(cab, filas)}`);
    },
  );

  server.registerTool(
    "ficha_estacion",
    {
      title: "Ficha de una estación",
      description:
        "Entrega la ficha de una estación de la DGA: ubicación, estado, tipos, y para cada variable el período, los datos válidos, el porcentaje de faltantes y cuántos valores fueron corregidos, eliminados o marcados como dudosos. En calidad de aguas lista los parámetros medidos.",
      inputSchema: z.object({ codigo: esquemaCodigo }),
      annotations: ANOTACIONES,
    },
    async ({ codigo }) => {
      const e = await resolver(env, codigo);
      if (typeof e === "string") return error(e);
      const cat = await catalogo(env);
      const f = await ficha(env, e.codigo);
      const unidad = (v: string) => (v in cat.variables_diarias ? cat.variables_diarias[v as VariableDiaria].unidad : v in cat.variables_irregulares ? cat.variables_irregulares[v as "pozos"].unidad : "según parámetro");
      const partes = [
        `# ${e.nombre} (${e.codigo}${e.codigo_dv ? `, calidad ${e.codigo_dv}` : ""})`,
        tabla(
          ["región", "comuna", "cuenca", "lat", "lon", "altitud (m)", "estado", "tipos"],
          [[e.region ?? "s/d", e.comuna ?? "s/d", e.cuenca || "s/d", e.lat?.toFixed(4) ?? "s/d", e.lon?.toFixed(4) ?? "s/d", e.altitud === null ? "s/d" : Math.round(e.altitud), e.estado ?? "s/d", e.tipos ?? "s/d"]],
        ),
      ];
      const filas = Object.entries(f.resumen).map(([v, r]) => [
        v,
        unidad(v),
        r!.inicio,
        r!.fin,
        r!.n_mediciones !== undefined ? (r!.n_validos !== undefined ? `${r!.n_validos} de ${cuenta(r!.n_mediciones, "medición", "mediciones")}` : cuenta(r!.n_mediciones, "medición", "mediciones")) : cuenta(r!.n_validos ?? 0, "día", "días"),
        r!.pct_faltante !== undefined ? num(r!.pct_faltante, 1) : "",
        r!.corregidos,
        r!.eliminados,
        r!.dudosos,
      ]);
      partes.push(`## Datos disponibles\n${tabla(["variable", "unidad", "inicio", "fin", "válidos", "% faltante", "corregidos", "eliminados", "dudosos"], filas)}`);
      const cal = f.resumen.calidad?.parametros;
      if (cal) {
        const filasCal = Object.entries(cal)
          .sort((a, b) => b[1].n - a[1].n)
          .slice(0, 40)
          .map(([c, r]) => {
            const p = cat.parametros.find((x) => x.codigo === c);
            return [c, p?.nombre ?? c, p?.unidad ?? "", r.n, `${r.inicio} a ${r.fin}`, r.bajo_limite];
          });
        partes.push(`## Parámetros de calidad de aguas (${Object.keys(cal).length})\n${tabla(["código", "parámetro", "unidad", "mediciones", "período", "bajo límite de detección"], filasCal)}`);
      }
      partes.push(`La bitácora de la estación tiene ${cuenta(f.bitacora.filas.length, "cambio", "cambios")}; use la herramienta correcciones para verlos.`);
      return texto(env, partes.join("\n\n"));
    },
  );

  server.registerTool(
    "serie",
    {
      title: "Serie de tiempo",
      description:
        "Entrega cualquier serie corregida de una estación y período: caudal, precipitación y temperaturas (diarias), niveles de pozos, sedimentos y calidad de aguas (indicando el parámetro). Paso 'diario' o 'mediciones' lista cada dato con su estado (corregido, eliminado o dudoso); 'mensual' y 'anual' agregan (año calendario o hidrológico con mes_inicio_anio=4). Las series largas se recorren por páginas con el parámetro pagina. " + REGLA_PERIODO,
      inputSchema: z.object({
        codigo: esquemaCodigo,
        variable: z.enum(TODAS),
        parametro: z.string().optional().describe("Solo para calidad: código o parte del nombre del parámetro, por ejemplo '8041' o 'arsenico'"),
        paso: z.enum(["diario", "mediciones", "mensual", "anual"]).optional().describe("mensual por omisión en series diarias; mediciones en pozos, sedimentos y calidad"),
        desde: esquemaFecha.describe("Inicio del período (AAAA, AAAA-MM o AAAA-MM-DD)"),
        hasta: esquemaFecha.describe("Fin del período (AAAA, AAAA-MM o AAAA-MM-DD)"),
        mes_inicio_anio: z.number().int().min(1).max(12).optional().describe("Mes de inicio del año (1 calendario, 4 hidrológico abril-marzo)"),
        incluir_dinamicos: z.boolean().optional().describe("Solo pozos: incluir niveles medidos con bombeo (falso por omisión)"),
        profundidad: z.number().min(0).optional().describe("Solo calidad: profundidad en m (lagos y embalses; 0 es superficie)"),
        pagina: z.number().int().min(1).optional().describe("Página de resultados (1 por omisión)"),
      }),
      annotations: ANOTACIONES,
    },
    async ({ codigo, variable, parametro, paso, desde, hasta, mes_inicio_anio, incluir_dinamicos, profundidad, pagina }) => {
      const e = await resolver(env, codigo);
      if (typeof e === "string") return error(e);
      const cat = await catalogo(env);
      const mi = mes_inicio_anio ?? 1;
      const [a, b] = periodo(desde, hasta, mi);
      const pag = pagina ?? 1;
      const enPeriodo = (d: number) => (a === undefined || d >= a) && (b === undefined || d <= b);
      if (variable === "pozos" || variable === "sed_integrado" || variable === "sed_rutinario" || variable === "calidad") {
        type Med = { dia: number; min: number; valor: number; estado: number; ip: number; cal: string; prof: number; obs: string };
        let meds: Med[] = [];
        let unidad = "";
        let descripcion = "";
        let titulo: string = variable;
        if (variable === "calidad") {
          if (!parametro) return error("Para calidad indique el parámetro (código o nombre). Use calidad_agua sin parámetro para ver los medidos en la estación.");
          const q = await calidad(env, e.codigo);
          if (!q) return error(`La estación ${e.codigo} no tiene datos de calidad de aguas.`);
          const ips = codigosParametro(cat, parametro).map((c) => cat.parametros.findIndex((x) => x.codigo === c));
          for (let i = 0; i < q.fechas.length; i++) {
            if (!ips.includes(q.parametros[i]) || !enPeriodo(q.fechas[i]) || (profundidad !== undefined && q.profundidades[i] !== profundidad)) continue;
            meds.push({ dia: q.fechas[i], min: q.minutos[i], valor: q.valores[i], estado: q.estados[i], ip: q.parametros[i], cal: cat.calificadores_calidad[q.calificadores[i]], prof: q.profundidades[i], obs: "" });
          }
          if (meds.length === 0) return error(`La estación no tiene mediciones de «${parametro}» en el período pedido. Use calidad_agua sin parámetro para ver los disponibles.`);
          const usados = [...new Set(meds.map((m) => m.ip))].map((ip) => cat.parametros[ip]);
          unidad = usados.length === 1 ? usados[0].unidad : "según parámetro";
          titulo = usados.map((x) => `${x.nombre} (${x.codigo})`).join(", ");
          descripcion = `Calidad de aguas: ${usados.map((x) => `${x.nombre}, código ${x.codigo}, ${x.unidad || "unidad no informada"}`).join("; ")}. Los valores con «<» o «>» son límites de detección y no entran a las medias`;
        } else {
          const s = await serieIrregular(env, variable, e.codigo);
          if (!s) return error(`La estación ${e.codigo} no tiene datos de ${variable}.`);
          const obs = cat.observaciones_pozos;
          for (let i = 0; i < s.fechas.length; i++) {
            if (!enPeriodo(s.fechas[i])) continue;
            if (variable === "pozos" && !incluir_dinamicos && obs[s.observaciones[i]] === "Dinamico") continue;
            meds.push({ dia: s.fechas[i], min: s.minutos[i], valor: s.valores[i], estado: s.estados[i], ip: -1, cal: "", prof: 0, obs: variable === "pozos" ? obs[s.observaciones[i]] : "" });
          }
          if (meds.length === 0) return error(`Sin mediciones de ${variable} en el período pedido.`);
          ({ unidad, descripcion } = cat.variables_irregulares[variable]);
          if (variable === "pozos") descripcion += incluir_dinamicos ? "; incluye niveles dinámicos" : "; sin niveles dinámicos (medidos con bombeo)";
        }
        meds = meds.sort((x, y) => x.dia - y.dia || x.min - y.min);
        const p = paso === "mensual" || paso === "anual" ? paso : "mediciones";
        const varios = new Set(meds.map((m) => m.ip)).size > 1;
        const conProf = variable === "calidad" && meds.some((m) => m.prof !== 0);
        const esLimite = (m: Med) => m.cal === "<" || m.cal === ">";
        let cab: string[];
        let filas: (string | number)[][];
        if (p === "mediciones") {
          cab = ["fecha", "hora", ...(varios ? ["código"] : []), `valor (${unidad})`, ...(conProf ? ["profundidad (m)"] : []), ...(variable === "pozos" ? ["observación"] : []), "estado"];
          filas = meds.map((m) => [
            isoDeDia(m.dia),
            hora(m.min),
            ...(varios ? [cat.parametros[m.ip].codigo] : []),
            `${esLimite(m) ? m.cal : ""}${num(m.valor)}`,
            ...(conProf ? [num(m.prof)] : []),
            ...(variable === "pozos" ? [m.obs] : []),
            NOMBRE_ESTADO[m.estado],
          ]);
        } else {
          cab = [p === "mensual" ? "mes" : "año", ...(varios ? ["código"] : []), `media (${unidad})`, "mínimo", "máximo", "mediciones", ...(variable === "calidad" ? ["con «<» o «>»"] : [])];
          filas = [];
          for (const ip of [...new Set(meds.map((m) => m.ip))]) {
            const ms = meds.filter((m) => m.ip === ip);
            const medidas = ms.filter((m) => !esLimite(m));
            const lims = ms.filter(esLimite);
            const agg = new Map(agregarMediciones(medidas.map((m) => m.dia), medidas.map((m) => m.valor), p, mi).map((f) => [f.etiqueta, f]));
            const nLim = new Map(agregarMediciones(lims.map((m) => m.dia), lims.map((m) => m.valor), p, mi).map((f) => [f.etiqueta, f.n]));
            for (const et of [...new Set([...agg.keys(), ...nLim.keys()])].sort()) {
              const f = agg.get(et);
              filas.push([et, ...(varios ? [cat.parametros[ip].codigo] : []), num(f?.media), num(f?.min), num(f?.max), f?.n ?? 0, ...(variable === "calidad" ? [nLim.get(et) ?? 0] : [])]);
            }
          }
        }
        const { filas: hoja, aviso } = paginar(filas, pag, maxFilas);
        const est = contarEstados(Uint8Array.from(meds.map((m) => m.estado)));
        return texto(
          env,
          `# ${titulo} en ${e.nombre} (${e.codigo})\n${descripcion}. ${cuenta(meds.length, "medición", "mediciones")} entre ${isoDeDia(meds[0].dia)} y ${isoDeDia(meds.at(-1)!.dia)}${p !== "mediciones" ? "; las medias excluyen valores eliminados" : ""}. ${notaEstados(est)}\n\n${tabla(cab, hoja)}${aviso}`,
        );
      }
      if (parametro || incluir_dinamicos !== undefined || profundidad !== undefined) return error("parametro, incluir_dinamicos y profundidad solo aplican a calidad y pozos.");
      const meta = cat.variables_diarias[variable];
      const s0 = await serieDiaria(env, variable, e.codigo);
      if (!s0) return error(`La estación ${e.codigo} no tiene datos de ${variable}.`);
      const s = recortar(s0, a, b);
      if (nValidos(s.valores) === 0) return error(`Sin datos de ${variable} en el período pedido para la estación ${e.codigo}.`);
      const p = paso === "diario" || paso === "mediciones" ? "diario" : (paso ?? "mensual");
      const val = nValidos(s.valores);
      const est = contarEstados(s.estados);
      let cab: string[];
      let filas: (string | number)[][];
      let nota = "";
      if (p === "diario") {
        cab = ["fecha", `${variable} (${meta.unidad})`, "estado"];
        filas = Array.from(s.valores, (v, i) => [isoDeDia(s.inicio + i), num(v), NOMBRE_ESTADO[s.estados[i]]]);
      } else {
        let meses: Fila[] = agregarMensual(s, meta.agregacion, completitud(variable));
        if (variable === "tmed") {
          const [n, x] = await Promise.all([serieDiaria(env, "tmin", e.codigo), serieDiaria(env, "tmax", e.codigo)]);
          if (n && x) {
            meses = promedioFilas(agregarMensual(recortar(n, a, b), "media", minComp), agregarMensual(recortar(x, a, b), "media", minComp));
            nota = " La media mensual es el promedio de las medias mensuales de mínima y máxima.";
          }
        }
        const filasAgg = p === "mensual" ? meses : agregarAnual(meses, meta.agregacion, mi);
        const u = meta.agregacion === "suma" ? `${meta.unidad}/${p === "mensual" ? "mes" : "año"}` : meta.unidad;
        cab = [p === "mensual" ? "mes" : "año", `${variable} (${u})`, "días con dato"];
        filas = filasAgg.map((f) => [f.etiqueta, num(f.valor), `${f.n_validos}/${f.n_dias}`]);
        nota += p === "anual" ? " Un año se calcula solo con sus doce meses válidos." : "";
      }
      const { filas: hoja, aviso } = paginar(filas, pag, maxFilas);
      const reglaMes = variable === "prep" ? "un mes entra solo completo" : `un mes entra con al menos ${minComp * 100}% de días con dato`;
      return texto(
        env,
        `# ${variable} en ${e.nombre} (${e.codigo})\n${meta.descripcion}. Período ${isoDeDia(s.inicio)} a ${isoDeDia(s.inicio + s.valores.length - 1)}; ${num(100 * (1 - val / s.valores.length), 1)}% de días sin dato; ${reglaMes}.${nota} ${notaEstados(est)}\n\n${tabla(cab, hoja)}${aviso}`,
      );
    },
  );

  server.registerTool(
    "estadisticas",
    {
      title: "Estadísticas de una variable diaria",
      description:
        "Calcula estadísticas de caudal, precipitación o temperatura en una estación y período: media, valores anuales (media, desviación, mínimo y máximo), régimen mensual y, para caudal, curva de duración (Q5 a Q95 por excedencia) y extremos diarios anuales; para precipitación, la máxima diaria de cada año. " + REGLA_PERIODO,
      inputSchema: z.object({
        codigo: esquemaCodigo,
        variable: z.enum(VARIABLES_DIARIAS),
        desde: esquemaFecha,
        hasta: esquemaFecha,
        mes_inicio_anio: z.number().int().min(1).max(12).optional().describe("4 para año hidrológico abril-marzo (por omisión 1)"),
      }),
      annotations: ANOTACIONES,
    },
    async ({ codigo, variable, desde, hasta, mes_inicio_anio }) => {
      const e = await resolver(env, codigo);
      if (typeof e === "string") return error(e);
      const meta = (await catalogo(env)).variables_diarias[variable];
      const s0 = await serieDiaria(env, variable, e.codigo);
      if (!s0) return error(`La estación ${e.codigo} no tiene datos de ${variable}.`);
      const mi = mes_inicio_anio ?? 1;
      const [a, b] = periodo(desde, hasta, mi);
      const s = recortar(s0, a, b);
      const val = nValidos(s.valores);
      if (val === 0) return error(`Sin datos de ${variable} en el período pedido para la estación ${e.codigo}.`);
      const u = meta.unidad;
      const comp = completitud(variable);
      const partes = [
        `# Estadísticas de ${variable} en ${e.nombre} (${e.codigo})`,
        `${meta.descripcion}. Período ${isoDeDia(s.inicio)} a ${isoDeDia(s.inicio + s.valores.length - 1)}; ${cuenta(val, "día", "días")} con dato (${num(100 * (1 - val / s.valores.length), 1)}% sin dato). Año ${mi === 1 ? "calendario" : `desde el mes ${mi}`}; un mes entra con al menos ${comp * 100}% de días con dato y un año con sus doce meses válidos. ${notaEstados(contarEstados(s.estados))}`,
      ];
      const desc = validosDesc(s.valores);
      partes.push(`Media diaria de los días con dato: ${num(desc.reduce((x, y) => x + y, 0) / desc.length)} ${u}.`);
      const meses = agregarMensual(s, meta.agregacion, comp);
      const anual = agregarAnual(meses, meta.agregacion, mi).filter((f) => f.valor !== null);
      const ea = estadisticos(anual.map((f) => f.valor!));
      if (ea) {
        const ua = meta.agregacion === "suma" ? `${u}/año` : u;
        const fMin = anual.find((f) => f.valor === ea.min)!, fMax = anual.find((f) => f.valor === ea.max)!;
        partes.push(
          `## Valores anuales (${cuenta(ea.n, "año completo", "años completos")})\n${tabla(["media", "desv. estándar", "CV", "mínimo (año)", "máximo (año)"], [[`${num(ea.media)} ${ua}`, num(ea.desv), num(ea.desv / ea.media, 2), `${num(ea.min)} (${fMin.etiqueta})`, `${num(ea.max)} (${fMax.etiqueta})`]])}`,
        );
      } else {
        partes.push("No hay años con sus doce meses válidos en el período.");
      }
      const nombresMes = ["ene", "feb", "mar", "abr", "may", "jun", "jul", "ago", "sep", "oct", "nov", "dic"];
      const orden = Array.from({ length: 12 }, (_, i) => ((mi - 1 + i) % 12) + 1);
      const filasMes = orden.map((m) => {
        const r = estadisticos(meses.filter((f) => f.mes === m && f.valor !== null).map((f) => f.valor!));
        return [nombresMes[m - 1], r ? num(r.media) : "s/d", r ? num(r.min) : "s/d", r ? num(r.max) : "s/d", r ? r.n : 0];
      });
      partes.push(`## Régimen mensual (${meta.agregacion === "suma" ? `${u}/mes` : u})\n${tabla(["mes", "media", "mínimo", "máximo", "años"], filasMes)}`);
      if (variable === "caudal") {
        const ps = [0.05, 0.1, 0.25, 0.5, 0.75, 0.85, 0.9, 0.95];
        partes.push(`## Curva de duración (caudal igualado o superado el p% del tiempo, m³/s)\n${tabla(ps.map((p) => `Q${Math.round(p * 100)}`), [ps.map((p) => num(excedencia(desc, p)))])}`);
      }
      if (variable === "caudal" || variable === "prep") {
        const ext = extremosAnuales(s, comp === 1 ? 0.99 : comp);
        const emx = estadisticos(ext.map((x) => x.max));
        if (emx) {
          const xMax = ext.reduce((p, x) => (x.max > p.max ? x : p));
          const titulo = variable === "prep" ? "Máxima precipitación diaria de cada año calendario" : "Máximo caudal diario de cada año calendario";
          partes.push(`## ${titulo} (${cuenta(ext.length, "año", "años")})\n${tabla(["media", "desv. estándar", "máximo del período (fecha)"], [[num(emx.media), num(emx.desv), `${num(xMax.max)} (${xMax.fechaMax})`]])}`);
        }
      }
      return texto(env, partes.join("\n\n"));
    },
  );

  server.registerTool(
    "niveles_pozo",
    {
      title: "Niveles de un pozo",
      description:
        "Entrega los niveles del agua de un pozo de la DGA (profundidad bajo el terreno, en m; un valor mayor es un nivel más bajo): resumen del período, tendencia en m/año, nivel medio por año y las últimas mediciones. Excluye por omisión los niveles dinámicos, medidos con bombeo. Para la serie completa use serie con variable pozos.",
      inputSchema: z.object({
        codigo: esquemaCodigo,
        desde: esquemaFecha,
        hasta: esquemaFecha,
        incluir_dinamicos: z.boolean().optional().describe("Incluir niveles medidos con bombeo (falso por omisión)"),
        mediciones: z.number().int().min(0).max(400).optional().describe("Cuántas mediciones listar, desde la más reciente (20 por omisión)"),
      }),
      annotations: ANOTACIONES,
    },
    async ({ codigo, desde, hasta, incluir_dinamicos, mediciones }) => {
      const e = await resolver(env, codigo);
      if (typeof e === "string") return error(e);
      const cat = await catalogo(env);
      const s = await serieIrregular(env, "pozos", e.codigo);
      if (!s) return error(`La estación ${e.codigo} no es un pozo con niveles. Use buscar_estaciones con variable 'pozos'.`);
      const [a, b] = periodo(desde, hasta);
      const obs = cat.observaciones_pozos;
      const idx = [...s.fechas.keys()].filter((i) => (a === undefined || s.fechas[i] >= a) && (b === undefined || s.fechas[i] <= b));
      if (idx.length === 0) return error("Sin mediciones en el período pedido.");
      const usar = idx.filter((i) => !Number.isNaN(s.valores[i]) && (incluir_dinamicos || obs[s.observaciones[i]] !== "Dinamico"));
      const conteoObs = new Map<string, number>();
      for (const i of idx) if (s.observaciones[i]) conteoObs.set(obs[s.observaciones[i]], (conteoObs.get(obs[s.observaciones[i]]) ?? 0) + 1);
      const partes = [
        `# Niveles del pozo ${e.nombre} (${e.codigo})`,
        `${cuenta(idx.length, "medición", "mediciones")} entre ${isoDeDia(s.fechas[idx[0]])} y ${isoDeDia(s.fechas[idx.at(-1)!])}; ${incluir_dinamicos ? cuenta(usar.length, "nivel con valor", "niveles con valor") : cuenta(usar.length, "nivel estático con valor", "niveles estáticos con valor")}. Observaciones: ${conteoObs.size ? [...conteoObs].map(([k, v]) => `${k} ${v}`).join(", ") : "ninguna"}. ${notaEstados(contarEstados(Uint8Array.from(idx.map((i) => s.estados[i]))))}`,
      ];
      if (usar.length) {
        const v = usar.map((i) => s.valores[i]);
        const ee = estadisticos(v)!;
        const iMin = usar[v.indexOf(ee.min)], iMax = usar[v.indexOf(ee.max)];
        const tend = tendencia(usar.map((i) => s.fechas[i]), v);
        partes.push(
          tabla(
            ["nivel medio (m)", "más alto: menor profundidad (fecha)", "más bajo: mayor profundidad (fecha)", "tendencia (m/año)"],
            [[num(ee.media), `${num(ee.min)} (${isoDeDia(s.fechas[iMin])})`, `${num(ee.max)} (${isoDeDia(s.fechas[iMax])})`, tend ? `${num(tend.pendiente_anual)} (${tend.pendiente_anual > 0 ? "el nivel baja" : "el nivel sube"})` : "s/d"]],
          ),
        );
        const porAnio = new Map<number, number[]>();
        for (const i of usar) {
          const y = new Date(s.fechas[i] * 86_400_000).getUTCFullYear();
          if (!porAnio.has(y)) porAnio.set(y, []);
          porAnio.get(y)!.push(s.valores[i]);
        }
        const filasAnio = [...porAnio].sort((x, y) => x[0] - y[0]).map(([y, xs]) => [y, num(xs.reduce((p, q) => p + q, 0) / xs.length), xs.length]);
        partes.push(`## Nivel medio por año (m)\n${tabla(["año", "nivel medio", "mediciones"], filasAnio.slice(-maxFilas))}`);
      }
      const n = mediciones ?? 20;
      if (n > 0) {
        const ult = idx.slice(-n).reverse().map((i) => [isoDeDia(s.fechas[i]), num(s.valores[i]), obs[s.observaciones[i]], NOMBRE_ESTADO[s.estados[i]]]);
        partes.push(`## ${ult.length === 1 ? "Última medición" : `Últimas ${ult.length} mediciones`}\n${tabla(["fecha", "nivel (m)", "observación", "estado"], ult)}`);
      }
      return texto(env, partes.join("\n\n"));
    },
  );

  server.registerTool(
    "calidad_agua",
    {
      title: "Calidad de aguas de una estación",
      description:
        "Sin parámetro, lista los parámetros de calidad de aguas medidos en una estación. Con un parámetro (código o parte del nombre, por ejemplo 'arsenico' o 'conductividad'), entrega por código: mediciones, cuántas bajo el límite de detección, mínimo, mediana, percentil 90 y máximo de los valores medidos, último valor y, si se pide, las mediciones. Los valores con calificador «<» son límites de detección y no entran a las estadísticas. Para la serie completa de un parámetro use serie con variable calidad.",
      inputSchema: z.object({
        codigo: esquemaCodigo,
        parametro: z.string().optional(),
        desde: esquemaFecha,
        hasta: esquemaFecha,
        profundidad: z.number().min(0).optional().describe("Solo mediciones a esta profundidad en m (lagos y embalses; 0 es superficie)"),
        mediciones: z.number().int().min(0).max(400).optional().describe("Cuántas mediciones listar, desde la más reciente (10 por omisión)"),
      }),
      annotations: ANOTACIONES,
    },
    async ({ codigo, parametro, desde, hasta, profundidad, mediciones }) => {
      const e = await resolver(env, codigo);
      if (typeof e === "string") return error(e);
      const cat = await catalogo(env);
      const q = await calidad(env, e.codigo);
      if (!q) return error(`La estación ${e.codigo} no tiene datos de calidad de aguas. Use buscar_estaciones con variable 'calidad'.`);
      const [a, b] = periodo(desde, hasta);
      const cal = cat.calificadores_calidad;
      const enRango = (i: number) => (a === undefined || q.fechas[i] >= a) && (b === undefined || q.fechas[i] <= b) && (profundidad === undefined || q.profundidades[i] === profundidad);
      const todos = [...q.fechas.keys()].filter(enRango);
      if (todos.length === 0) return error("Sin mediciones de calidad en el período o profundidad pedidos.");
      const titulo = `# Calidad de aguas en ${e.nombre} (${e.codigo_dv ?? e.codigo})`;
      if (!parametro) {
        const g = new Map<number, number[]>();
        for (const i of todos) {
          if (!g.has(q.parametros[i])) g.set(q.parametros[i], []);
          g.get(q.parametros[i])!.push(i);
        }
        const filas = [...g]
          .sort((x, y) => y[1].length - x[1].length)
          .map(([ip, is]) => {
            const p = cat.parametros[ip];
            return [p.codigo, p.nombre, p.unidad, is.length, `${isoDeDia(q.fechas[is[0]])} a ${isoDeDia(q.fechas[is.at(-1)!])}`, is.filter((i) => cal[q.calificadores[i]] === "<").length];
          });
        return texto(env, `${titulo}\n${cuenta(todos.length, "medición", "mediciones")} de ${cuenta(g.size, "parámetro", "parámetros")}.\n\n${tabla(["código", "parámetro", "unidad", "mediciones", "período", "bajo límite de detección"], filas.slice(0, maxFilas))}`);
      }
      const cods = codigosParametro(cat, parametro);
      const ips = cods.map((c) => cat.parametros.findIndex((p) => p.codigo === c));
      const sel = todos.filter((i) => ips.includes(q.parametros[i]));
      if (sel.length === 0) return error(`La estación no tiene mediciones de «${parametro}» en el período pedido. Llame calidad_agua sin parámetro para ver los disponibles.`);
      const partes = [titulo];
      for (const ip of ips) {
        const is = sel.filter((i) => q.parametros[i] === ip);
        if (is.length === 0) continue;
        const p = cat.parametros[ip];
        const medidos = is.filter((i) => q.estados[i] !== ESTADO.eliminado && !["<", ">"].includes(cal[q.calificadores[i]])).map((i) => q.valores[i]);
        const bajo = is.filter((i) => cal[q.calificadores[i]] === "<");
        const validos = is.filter((i) => !Number.isNaN(q.valores[i]));
        const ult = validos.at(-1);
        const limites = bajo.map((i) => q.valores[i]);
        partes.push(
          `## ${p.nombre} (código ${p.codigo}, ${p.unidad || "unidad no informada"})${p.metodologia ? `, metodología ${p.metodologia}` : ""}\n${cuenta(is.length, "medición", "mediciones")} entre ${isoDeDia(q.fechas[is[0]])} y ${isoDeDia(q.fechas[is.at(-1)!])}; ${bajo.length} bajo el límite de detección${limites.length ? ` (límites entre ${num(Math.min(...limites))} y ${num(Math.max(...limites))})` : ""}. ${notaEstados(contarEstados(Uint8Array.from(is.map((i) => q.estados[i]))))}\n\n${tabla(
            ["valores medidos", "mínimo", "mediana", "percentil 90", "máximo", "último valor (fecha)"],
            [[medidos.length, num(medidos.length ? Math.min(...medidos) : null), num(percentil(medidos, 0.5)), num(percentil(medidos, 0.9)), num(medidos.length ? Math.max(...medidos) : null), ult === undefined ? "s/d" : `${cal[q.calificadores[ult]] === "<" ? "<" : ""}${num(q.valores[ult])} (${isoDeDia(q.fechas[ult])})`]],
          )}`,
        );
        if (!p.identificado) partes.push("Este código no tiene nombre ni unidad en los datos de la DGA; úselo solo como referencia.");
      }
      const n = mediciones ?? 10;
      if (n > 0) {
        const filas = sel.slice(-n).reverse().map((i) => [isoDeDia(q.fechas[i]), hora(q.minutos[i]), cat.parametros[q.parametros[i]].codigo, `${cal[q.calificadores[i]] === "<" || cal[q.calificadores[i]] === ">" ? cal[q.calificadores[i]] : ""}${num(q.valores[i])}`, num(q.profundidades[i]), NOMBRE_ESTADO[q.estados[i]]]);
        partes.push(`## ${filas.length === 1 ? "Última medición" : `Últimas ${filas.length} mediciones`}\n${tabla(["fecha", "hora", "código", "valor", "profundidad (m)", "estado"], filas)}`);
      }
      return texto(env, partes.join("\n\n"));
    },
  );

  server.registerTool(
    "correcciones",
    {
      title: "Correcciones y valores dudosos de una estación",
      description:
        "Muestra la bitácora de una estación: cada valor, fecha, hora, unidad o coordenada corregido o eliminado, con su valor original, el valor corregido y el motivo, y el resumen de los valores marcados como dudosos (conservados sin cambios).",
      inputSchema: z.object({
        codigo: esquemaCodigo,
        variable: z.string().optional().describe("Filtrar por variable (caudal, prep, tmin, tmax, tmed, pozos, sed_integrado, sed_rutinario, estacion) o código de parámetro de calidad"),
        desde: esquemaFecha,
        hasta: esquemaFecha,
      }),
      annotations: ANOTACIONES,
    },
    async ({ codigo, variable, desde, hasta }) => {
      const e = await resolver(env, codigo);
      if (typeof e === "string") return error(e);
      const cat = await catalogo(env);
      const f = await ficha(env, e.codigo);
      const col = Object.fromEntries(f.bitacora.columnas.map((c, i) => [c, i]));
      const ini = desde ? isoDeDia(periodo(desde)[0]!) : undefined;
      const fin = hasta ? isoDeDia(periodo(undefined, hasta)[1]!) : undefined;
      const enPeriodo = (fecha: string | null) => !fecha || ((!ini || fecha >= ini) && (!fin || fecha <= fin));
      const filas = f.bitacora.filas.filter((r) => (!variable || r[col.variable] === variable) && enPeriodo(r[col.fecha_original] as string | null));
      const partes = [`# Correcciones en ${e.nombre} (${e.codigo})`];
      if (filas.length) {
        const t = filas.slice(0, maxFilas).map((r) => [
          r[col.fecha_original] ?? "",
          r[col.variable] ?? "",
          r[col.campo] ?? "",
          r[col.valor_anterior] ?? "",
          r[col.valor_corregido] ?? "",
          r[col.accion] ?? "",
          `${r[col.regla]} ${cat.reglas[r[col.regla] as string] ?? ""}`,
          r[col.evidencia] ?? "",
        ]);
        partes.push(`## Bitácora (${cuenta(filas.length, "cambio", "cambios")}${filas.length > maxFilas ? `, se muestran ${maxFilas}` : ""})\n${tabla(["fecha", "variable", "campo", "valor original", "valor corregido", "acción", "regla", "evidencia"], t as string[][])}`);
      } else {
        partes.push("No hay valores corregidos ni eliminados para los filtros indicados.");
      }
      const marcas = Object.entries(f.marcas).filter(([v]) => !variable || v === variable);
      if (marcas.length) {
        const t: string[][] = [];
        for (const [v, motivos] of marcas) {
          for (const [motivo, tramos] of Object.entries(motivos)) {
            const tr = tramos.filter(([x, y]) => (!ini || y >= ini) && (!fin || x <= fin));
            if (!tr.length) continue;
            const dias = tr.reduce((s, [x, y]) => s + (Date.parse(y) - Date.parse(x)) / 86_400_000 + 1, 0);
            const ejemplos = tr.slice(0, 3).map(([x, y]) => (x === y ? x : `${x} a ${y}`)).join("; ");
            t.push([v, motivo, String(dias), `${ejemplos}${tr.length > 3 ? ` y ${tr.length - 3} más` : ""}`]);
          }
        }
        if (t.length) partes.push(`## Valores dudosos conservados\n${tabla(["variable", "motivo", "días o mediciones", "fechas"], t)}`);
      }
      return texto(env, partes.join("\n\n"));
    },
  );

  return server;
}

// Página de inicio con instrucciones de conexión.
function inicio(url: URL): Response {
  const cuerpo = {
    nombre: "MCP de Mapas DGA",
    descripcion: "Servidor MCP público, gratuito y sin fines comerciales para facilitar la consulta de los datos históricos que publica la Dirección General de Aguas en Mapas DGA (https://mapas2.mop.gob.cl). No es un servicio oficial de la DGA.",
    autor: AUTOR,
    version: VERSION,
    licencia_codigo: "GNU GPL v3 o posterior",
    mcp: `${url.origin}/mcp`,
    uso: "Agregue la URL del campo 'mcp' como conector personalizado en Claude (Configuración > Conectores).",
    herramientas: ["describir_fuente", "buscar_estaciones", "ficha_estacion", "serie", "estadisticas", "niveles_pozo", "calidad_agua", "correcciones"],
    datos: `${url.origin}/mapas2/catalogo.json`,
  };
  return new Response(JSON.stringify(cuerpo, null, 2), { headers: { "content-type": "application/json; charset=utf-8" } });
}

export default {
  async fetch(request: Request, env: Env, ctx: ExecutionContext): Promise<Response> {
    const url = new URL(request.url);
    if (url.pathname === "/mcp" || url.pathname.startsWith("/mcp/")) {
      return createMcpHandler(() => crearServidor(env))(request, env, ctx);
    }
    if (url.pathname === "/") return inicio(url);
    return new Response("No encontrado", { status: 404 });
  },
} satisfies ExportedHandler<Env>;

