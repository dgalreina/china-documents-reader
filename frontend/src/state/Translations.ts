import { useCallback, useRef, useState } from 'react';
import type { ApiClient } from '../api/client';

export type PageState = { status: 'loading' } | { status: 'done'; text: string; cached: boolean } | { status: 'error'; message: string };

const NONE: Record<number, PageState> = {};

/**
 * Traducciones de las páginas de UN libro, en memoria (la caché de verdad está en el servidor).
 *
 * - `request([...páginas])` pide solo lo que aún no se ha pedido: una página ya pedida o en curso no se vuelve a pedir, y un fallo sí se puede reintentar.
 * - Quien lo use decide CUÁNDO pedir (el lector espera a que una página se quede a la vista); aquí no se pide nada por sí solo.
 * - Al cambiar de libro se empieza de cero. Se hace al renderizar y no en un efecto: un efecto del hijo (que pide páginas del libro nuevo)
 *   se ejecuta antes que uno de este hook y lo borraría.
 */
export function useTranslations(api: ApiClient, slug: string | null) {
  const [state, setState] = useState<{ slug: string | null; pages: Record<number, PageState> }>({ slug, pages: {} });
  const current = useRef({ slug, requested: new Set<number>() });
  if (current.current.slug !== slug) current.current = { slug, requested: new Set() };
  const pages = state.slug === slug ? state.pages : NONE;

  const load = useCallback(
    (page: number) => {
      if (!slug) return;
      const book = current.current;
      if (book.requested.has(page)) return;
      book.requested.add(page);
      const set = (next: PageState) =>
        setState((previous) => ({ slug, pages: { ...(previous.slug === slug ? previous.pages : {}), [page]: next } }));
      set({ status: 'loading' });
      api.getTranslation(slug, page).then(
        (result) => {
          if (current.current !== book) return; // el usuario ya cambió de libro: esta respuesta es de otro
          set({ status: 'done', text: result.text, cached: result.cached });
        },
        (error: unknown) => {
          if (current.current !== book) return;
          book.requested.delete(page);
          set({ status: 'error', message: error instanceof Error ? error.message : 'No se pudo traducir la página.' });
        },
      );
    },
    [api, slug],
  );

  const request = useCallback((list: number[]) => list.forEach(load), [load]);
  return { pages, request, retry: load };
}

export type Translations = ReturnType<typeof useTranslations>;
