import { useEffect, useState } from 'react';
import type { ApiClient } from '../api/client';

export interface PdfUrlState {
  url?: string;
  error?: string;
}

/**
 * La URL temporal del PDF de un libro. `slug` undefined = todavía no se puede pedir (el libro no existe o aún se está procesando).
 * La respuesta se asocia al libro que se pidió: si se cambia de libro, la URL del anterior no se enseña ni un instante.
 */
export function usePdfUrl(api: ApiClient, slug: string | undefined): PdfUrlState {
  const [state, setState] = useState<{ slug: string; result: PdfUrlState } | null>(null);

  useEffect(() => {
    if (!slug) return;
    let cancelled = false;
    api.getPdfUrl(slug).then(
      (url) => !cancelled && setState({ slug, result: { url } }),
      (error: unknown) => !cancelled && setState({ slug, result: { error: error instanceof Error ? error.message : 'No se pudo obtener el PDF.' } }),
    );
    return () => {
      cancelled = true;
    };
  }, [api, slug]);

  return slug && state?.slug === slug ? state.result : {};
}
