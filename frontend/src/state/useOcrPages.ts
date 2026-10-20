import { useEffect, useState } from 'react';
import type { ApiClient } from '../api/client';
import type { Book } from '../types';

/**
 * El texto OCR de un libro escaneado (una cadena por página), para el lector en inglés: un PDF escaneado no tiene texto propio que leer. Se baja una vez, entero, cuando el libro
 * tiene OCR (y otra vez si se repite: `book.ocr.at` cambia). Un fallo al bajarlo no tumba nada: el lector se queda sin ese texto, como antes del OCR.
 */
export function useOcrPages(api: ApiClient, book: Book | undefined): string[] | null {
  const [loaded, setLoaded] = useState<{ key: string; pages: string[] } | null>(null);
  const slug = book?.slug;
  const stamp = book?.ocr ? (book.ocr.at ?? 'ocr') : undefined;
  const key = slug && stamp ? `${slug}|${stamp}` : undefined;

  useEffect(() => {
    if (!slug || !key) return;
    let cancelled = false;
    api.getOcrPages(slug, stamp).then(
      (pages) => {
        if (!cancelled && pages) setLoaded({ key, pages });
      },
      () => undefined,
    );
    return () => {
      cancelled = true;
    };
  }, [api, slug, key, stamp]);

  return key && loaded?.key === key ? loaded.pages : null;
}
