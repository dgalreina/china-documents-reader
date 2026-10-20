/** Años negativos = a. C., como en el backend (221 a. C. = -221). */
export function formatYear(year: number): string {
  return year < 0 ? `${Math.abs(year)} a. C.` : `${year} d. C.`;
}

export function formatRange(from: number, to: number): string {
  return from === to ? formatYear(from) : `${formatYear(from)} – ${formatYear(to)}`;
}

/** Coste estimado en dólares. Por debajo de un céntimo se muestra "< $0,01" en vez de "$0,00". */
export function formatUsd(value: number): string {
  if (value > 0 && value < 0.01) return '< $0,01';
  return `$${value.toFixed(2).replace('.', ',')}`;
}

/**
 * Cuánto se paga por 1 dólar, en euros (~1,15 dólares por euro). Los costes del backend están en DÓLARES (es lo que cobran OpenAI y AWS) y la web los enseña en euros; es un cambio
 * APROXIMADO y fijo (no consulta la cotización del día), válido para una estimación. Si el euro se mueve mucho, se cambia aquí.
 */
export const EUR_PER_USD = 0.87;

/** Coste estimado (dado en dólares) enseñado en euros: "3,71 €". Por debajo de un céntimo, "< 0,01 €". */
export function formatCost(usd: number): string {
  const eur = usd * EUR_PER_USD;
  if (eur > 0 && eur < 0.01) return '< 0,01 €';
  return `${eur.toFixed(2).replace('.', ',')} €`;
}

export function formatPercent(fraction: number): string {
  return `${Math.round(Math.min(1, Math.max(0, fraction)) * 100)} %`;
}
