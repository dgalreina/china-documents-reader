import { useCallback, useRef, useState } from 'react';
import type { ApiClient } from '../api/client';
import { readCachedTranslation, translationKey, writeCachedTranslation } from '../lib/translationCache';

export type PageState = { status: 'loading' } | { status: 'done'; text: string; cached: boolean } | { status: 'error'; message: string };

const NONE: Record<number, PageState> = {};

/**
 * Traducciones de las páginas de UN libro, en memoria. Las que ya se tradujeron se guardan también en el navegador (IndexedDB: `lib/translationCache.ts`) para que
 * al volver a abrir salgan al instante; la caché de verdad (la que evita pagar otra vez a OpenAI) está en el servidor.
 *
 * - `request([...páginas])` pide solo lo que aún no se ha pedido: una página ya pedida o en curso no se vuelve a pedir, y un fallo sí se puede reintentar.
 * - `peek([...páginas])` mira SOLO la caché del navegador (sin red ni coste) y se puede llamar sin esperar: así las páginas ya traducidas no pasan por el inglés.
 * - Quien lo use decide CUÁNDO pedir (el lector espera a que una página se quede a la vista); aquí no se pide nada por sí solo.
 * - Al cambiar de libro se empieza de cero. Se hace al renderizar y no en un efecto: un efecto del hijo (que pide páginas del libro nuevo)
 *   se ejecuta antes que uno de este hook y lo borraría.
 */
export function useTranslations(api: ApiClient, slug: string | null, bookStamp?: string) {
  // El "ámbito" es el libro Y su versión (`bookStamp`: cambia al reemplazar el PDF o al hacerle OCR): al cambiar, se empieza de cero, porque las páginas que salieron vacías ya no lo son.
  const scope = slug ? `${slug}|${bookStamp ?? ''}` : null;
  const [state, setState] = useState<{ scope: string | null; pages: Record<number, PageState> }>({ scope, pages: {} });
  const current = useRef({ scope, requested: new Set<number>(), peeked: new Set<number>() });
  if (current.current.scope !== scope) current.current = { scope, requested: new Set(), peeked: new Set() };
  const pages = state.scope === scope ? state.pages : NONE;

  const load = useCallback(
    (page: number) => {
      if (!slug) return;
      const book = current.current;
      if (book.requested.has(page)) return;
      book.requested.add(page);
      const set = (next: PageState) =>
        setState((previous) => ({ scope, pages: { ...(previous.scope === scope ? previous.pages : {}), [page]: next } }));
      set({ status: 'loading' });
      const key = translationKey(slug, bookStamp, page);
      // Primero la caché del navegador (milisegundos); solo si no está, el servidor.
      void readCachedTranslation(key).then((local) => {
        if (current.current !== book) return;
        if (local !== undefined) return set({ status: 'done', text: local, cached: true });
        api.getTranslation(slug, page).then(
          (result) => {
            if (current.current !== book) return; // el usuario ya cambió de libro: esta respuesta es de otro
            set({ status: 'done', text: result.text, cached: result.cached });
            void writeCachedTranslation(key, result.text); // (los vacíos no se guardan: ver translationCache)
          },
          (error: unknown) => {
            if (current.current !== book) return;
            book.requested.delete(page);
            set({ status: 'error', message: error instanceof Error ? error.message : 'No se pudo traducir la página.' });
          },
        );
      });
    },
    [api, slug, bookStamp, scope],
  );

  // Solo la caché del navegador: lo que está lo pone ya; lo que no, se queda como estaba (pendiente de que el lector lo pida de verdad).
  const peek = useCallback(
    (list: number[]) => {
      if (!slug) return;
      const book = current.current;
      for (const page of list) {
        if (book.requested.has(page) || book.peeked.has(page)) continue;
        book.peeked.add(page);
        void readCachedTranslation(translationKey(slug, bookStamp, page)).then((local) => {
          if (local === undefined || current.current !== book || book.requested.has(page)) return;
          book.requested.add(page);
          setState((previous) => ({ scope, pages: { ...(previous.scope === scope ? previous.pages : {}), [page]: { status: 'done', text: local, cached: true } } }));
        });
      }
    },
    [slug, bookStamp, scope],
  );

  const request = useCallback((list: number[]) => list.forEach(load), [load]);
  return { pages, request, peek, retry: load };
}

export type Translations = ReturnType<typeof useTranslations>;
