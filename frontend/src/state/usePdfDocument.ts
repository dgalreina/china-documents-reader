import { useEffect, useState } from 'react';
import * as pdfjs from 'pdfjs-dist';
import type { PDFDocumentProxy } from 'pdfjs-dist';
import workerUrl from 'pdfjs-dist/build/pdf.worker.min.mjs?url';

// pdf.js hace el trabajo pesado en un "worker" (un hilo aparte) para no congelar la página.
pdfjs.GlobalWorkerOptions.workerSrc = workerUrl;

export interface PdfState {
  doc: PDFDocumentProxy | null;
  error: string | null;
  /** alto/ancho de una página (A4 por defecto; se corrige con la primera del documento). */
  ratio: number;
}

/** Carga un PDF una sola vez para que lo compartan el visor del PDF y el lector en español. */
export function usePdfDocument(url: string | undefined): PdfState {
  const [state, setState] = useState<PdfState>({ doc: null, error: null, ratio: 1.414 });

  useEffect(() => {
    setState({ doc: null, error: null, ratio: 1.414 });
    if (!url) return;
    let cancelled = false;
    const task = pdfjs.getDocument({ url });
    task.promise
      .then(async (loaded) => {
        if (cancelled) return; // la limpieza ya destruyó la tarea de carga, y con ella el documento
        const viewport = (await loaded.getPage(1)).getViewport({ scale: 1 });
        if (cancelled) return;
        setState({ doc: loaded, error: null, ratio: viewport.height / viewport.width });
      })
      .catch(() => {
        if (!cancelled) setState({ doc: null, error: 'No se pudo abrir el PDF. Vuelve a intentarlo; si sigue pasando, recarga la página.', ratio: 1.414 });
      });
    return () => {
      cancelled = true;
      void task.destroy();
    };
  }, [url]);

  return state;
}

/** El texto de una página tal como lo da el PDF (líneas separadas por saltos de línea). Es local: no pasa por el servidor. */
export async function getPageText(doc: PDFDocumentProxy, pageNumber: number): Promise<string> {
  const page = await doc.getPage(pageNumber);
  const content = await page.getTextContent();
  let text = '';
  for (const item of content.items) {
    if (!('str' in item)) continue; // las marcas de contenido no son texto
    text += item.str + (item.hasEOL ? '\n' : '');
  }
  return text;
}
