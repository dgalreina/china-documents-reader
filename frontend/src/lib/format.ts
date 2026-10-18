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

export function formatPercent(fraction: number): string {
  return `${Math.round(Math.min(1, Math.max(0, fraction)) * 100)} %`;
}
