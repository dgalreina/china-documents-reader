/** Cuánto tiene que quedarse una página a la vista (sin que el scroll siga) antes de pedir su traducción. Pasar de largo no gasta. */
export const DWELL_MS = 700;
/** Páginas que se traducen alrededor de la que se lee: la anterior y la siguiente. Un salto a la 40 traduce la 39, la 40 y la 41. */
export const PREFETCH_BEHIND = 1;
export const PREFETCH_AHEAD = 1;
/** Páginas alrededor de la actual de las que se saca el texto en inglés del PDF (provisional, mientras llega el español; es local y gratis). */
export const ENGLISH_WINDOW = 3;

/**
 * Páginas que hay que traducir ahora, por orden de prioridad: la actual, la siguiente y la anterior (sin salirse del libro).
 * `pageCount` 0 = aún no se sabe cuántas hay.
 */
export function pagesToLoad(current: number, pageCount: number, ahead = PREFETCH_AHEAD, behind = PREFETCH_BEHIND): number[] {
  if (current < 1) return [];
  const last = pageCount > 0 ? pageCount : Infinity;
  const pages = [current];
  for (let i = 1; i <= ahead; i++) if (current + i <= last) pages.push(current + i);
  for (let i = 1; i <= behind; i++) if (current - i >= 1) pages.push(current - i);
  return pages;
}

/** El texto traducido llega con los párrafos separados por una línea en blanco. */
export function splitParagraphs(text: string): string[] {
  return text
    .split(/\n\s*\n/)
    .map((paragraph) => paragraph.trim())
    .filter(Boolean);
}

/**
 * El texto en inglés que da pdf.js viene cortado por líneas: se une en un solo bloque, recomponiendo las palabras partidas con guion
 * al final de línea. Es solo el texto provisional de la página, no hace falta que tenga párrafos.
 */
export function joinLines(text: string): string {
  return text
    .replace(/(\p{L})-\n(?=\p{Ll})/gu, '$1')
    .replace(/\s*\n\s*/g, ' ')
    .replace(/ {2,}/g, ' ')
    .trim();
}

export interface TextRun {
  text: string;
  /** true si es un tramo de caracteres chinos (se pinta con la tipografía china). */
  cjk: boolean;
}

// Ideogramas CJK (incluye extensión A y compatibilidad).
const CJK = /([㐀-䶿一-鿿豈-﫿]+)/;

/** Parte un párrafo en tramos de español y de chino, para dar a cada uno su tipografía. */
export function splitCjk(paragraph: string): TextRun[] {
  return paragraph
    .split(CJK)
    .filter((part) => part !== '')
    .map((part) => ({ text: part, cjk: CJK.test(part) }));
}
