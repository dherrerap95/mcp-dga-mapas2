// Pruebas de los cálculos de agregación, tendencia, percentiles y códigos de estación.
import { describe, expect, it } from "vitest";
import { agregarAnual, agregarMediciones, agregarMensual, contarEstados, excedencia, paginar, percentil, periodo, promedioFilas, recortar, tendencia, validosDesc } from "../src/calculos";
import { normalizarCodigo, type SerieDiaria } from "../src/datos";

const DIA = 86_400_000;

// Serie diaria desde una fecha ISO con valores constantes o generados.
function serie(desde: string, n: number, f: (i: number) => number, estados?: (i: number) => number): SerieDiaria {
  return {
    inicio: Date.parse(desde) / DIA,
    valores: Float32Array.from({ length: n }, (_, i) => f(i)),
    estados: Uint8Array.from({ length: n }, (_, i) => (estados ? estados(i) : 0)),
  };
}

describe("normalizarCodigo", () => {
  it("completa ceros y quita el dígito verificador", () => {
    expect(normalizarCodigo("5410002")).toBe("05410002");
    expect(normalizarCodigo(5410002)).toBe("05410002");
    expect(normalizarCodigo("02110005-6")).toBe("02110005");
    expect(normalizarCodigo("abc")).toBeNull();
    expect(normalizarCodigo("123456789")).toBeNull();
  });
});

describe("agregarMensual", () => {
  it("suma la precipitación solo en meses completos cuando la completitud es 1", () => {
    const s = serie("2020-01-01", 60, (i) => (i === 40 ? NaN : 1));
    const m = agregarMensual(s, "suma", 1);
    expect(m[0].valor).toBe(31);
    expect(m[1].valor).toBeNull();
    expect(m[1].n_validos).toBe(28);
  });
  it("promedia con al menos 80 % de días", () => {
    const s = serie("2021-02-01", 28, (i) => (i < 23 ? 2 : NaN));
    expect(agregarMensual(s, "media", 0.8)[0].valor).toBe(2);
    expect(agregarMensual(s, "media", 0.85)[0].valor).toBeNull();
  });
});

describe("agregarAnual", () => {
  it("exige los doce meses válidos y respeta el año hidrológico", () => {
    const s = serie("2000-01-01", 731, () => 1);
    const meses = agregarMensual(s, "suma", 1);
    expect(agregarAnual(meses, "suma").map((f) => f.valor)).toEqual([366, 365]);
    const hidro = agregarAnual(meses, "suma", 4);
    expect(hidro.map((f) => f.etiqueta)).toEqual(["1999/00", "2000/01", "2001/02"]);
    expect(hidro.map((f) => f.valor)).toEqual([null, 365, null]);
  });
  it("pondera la media anual por días", () => {
    const s = serie("2001-01-01", 365, (i) => (i < 31 ? 10 : 0));
    const a = agregarAnual(agregarMensual(s, "media", 0.8), "media");
    expect(a[0].valor).toBeCloseTo((31 * 10) / 365, 6);
  });
});

describe("promedioFilas", () => {
  it("promedia mínima y máxima mensuales", () => {
    const n = agregarMensual(serie("2010-01-01", 31, () => 2), "media", 0.8);
    const x = agregarMensual(serie("2010-01-01", 31, () => 10), "media", 0.8);
    expect(promedioFilas(n, x)[0].valor).toBe(6);
  });
});

describe("recortar y estados", () => {
  it("recorta valores y estados juntos y cuenta los estados", () => {
    const s = serie("2020-01-01", 10, (i) => i, (i) => i % 4);
    const r = recortar(s, s.inicio + 2, s.inicio + 5);
    expect(Array.from(r.valores)).toEqual([2, 3, 4, 5]);
    expect(contarEstados(r.estados)).toEqual({ corregidos: 1, eliminados: 1, dudosos: 1 });
  });
  it("interpreta años solos como años hidrológicos completos", () => {
    const [a, b] = periodo("1980", "2019", 4);
    expect(new Date(a! * DIA).toISOString().slice(0, 10)).toBe("1980-04-01");
    expect(new Date(b! * DIA).toISOString().slice(0, 10)).toBe("2020-03-31");
  });
});

describe("estadísticos", () => {
  it("calcula excedencia de Weibull, percentiles y tendencia", () => {
    const d = validosDesc(Float32Array.from([1, 2, 3, 4, 5, 6, 7, 8, 9]));
    expect(excedencia(d, 0.5)).toBe(5);
    expect(percentil([1, 2, 3, 4], 0.5)).toBe(2.5);
    const t = tendencia([0, 365.25, 730.5], [10, 11, 12]);
    expect(t!.pendiente_anual).toBeCloseTo(1, 9);
  });
});

describe("mediciones irregulares", () => {
  it("agrupa por mes y año hidrológico ignorando valores eliminados", () => {
    const d = ["2020-03-15", "2020-04-02", "2020-04-20", "2021-02-01"].map((x) => Date.parse(x) / DIA);
    const m = agregarMediciones(d, [1, 2, NaN, 4], "mensual");
    expect(m.map((f) => [f.etiqueta, f.n, f.media])).toEqual([["2020-03", 1, 1], ["2020-04", 2, 2], ["2021-02", 1, 4]]);
    const a = agregarMediciones(d, [1, 2, NaN, 4], "anual", 4);
    expect(a.map((f) => [f.etiqueta, f.media])).toEqual([["2019/20", 1], ["2020/21", 3]]);
  });
  it("pagina y avisa la página siguiente", () => {
    const r = paginar(Array.from({ length: 25 }, (_, i) => i), 2, 10);
    expect(r.filas).toEqual([10, 11, 12, 13, 14, 15, 16, 17, 18, 19]);
    expect(r.aviso).toContain("Página 2 de 3");
    expect(paginar([1, 2], 1, 10).aviso).toBe("");
  });
});
