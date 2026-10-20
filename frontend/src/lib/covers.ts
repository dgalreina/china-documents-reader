import type { Book } from '../types';

/** Las direcciones firmadas de las portadas duran 6 horas en el servidor; se reutilizan hasta un poco antes de caducar. */
export const COVER_URL_REUSE_MS = 5 * 3600_000;

export type CoverCache = Map<string, { url: string; version: string; at: number }>;

/**
 * Cada vez que se pide la lista de libros, el servidor firma de nuevo la dirección de cada portada (otra firma = otra dirección = el navegador la vuelve a bajar y la imagen
 * parpadea). Como ahora la lista se refresca sola, se conserva la dirección que ya se tenía mientras sea del mismo libro (`createdAt`: cambia si se reemplaza el PDF) y siga vigente.
 */
export function stableCovers(books: Book[], cache: CoverCache, now: number): Book[] {
  const seen = new Set<string>();
  const result = books.map((book) => {
    if (!book.coverUrl) return book;
    seen.add(book.slug);
    const cached = cache.get(book.slug);
    if (cached && cached.version === book.createdAt && now - cached.at < COVER_URL_REUSE_MS) return { ...book, coverUrl: cached.url };
    cache.set(book.slug, { url: book.coverUrl, version: book.createdAt, at: now });
    return book;
  });
  for (const slug of [...cache.keys()]) if (!seen.has(slug)) cache.delete(slug); // un libro borrado o sin portada ya no ocupa sitio
  return result;
}
