import { useEffect, useState } from 'react';
import type { PDFDocumentLoadingTask, PDFDocumentProxy } from 'pdfjs-dist';
import workerUrl from 'pdfjs-dist/build/pdf.worker.min.mjs?url'; // solo la dirección del worker (un texto): el worker en sí se descarga cuando pdf.js lo arranca

/**
 * pdf.js es lo más pesado de la web (cientos de kB) y solo hace falta al abrir un libro: se carga con un `import()` dinámico, en su propio fichero, y NO va en el
 * JavaScript principal. Así la pantalla de entrada y la lista de libros aparecen sin esperar a pdf.js. `loadPdfjs()` se puede llamar antes de necesitarlo (App lo hace
 * nada más arrancar) para que se vaya descargando mientras se carga la lista; llamarlo varias veces no lo descarga dos veces.
 */
let pdfjsPromise: Promise<typeof import('pdfjs-dist')> | null = null;
export function loadPdfjs(): Promise<typeof import('pdfjs-dist')> {
  pdfjsPromise ??= import('pdfjs-dist').then((pdfjs) => {
    // pdf.js hace el trabajo pesado en un "worker" (un hilo aparte) para no congelar la página.
    pdfjs.GlobalWorkerOptions.workerSrc = workerUrl;
    return pdfjs;
  });
  pdfjsPromise.catch(() => {
    pdfjsPromise = null; // si la descarga falló (sin red un momento), la próxima vez se vuelve a intentar
  });
  return pdfjsPromise;
}

export interface PdfState {
  doc: PDFDocumentProxy | null;
  error: string | null;
  /** alto/ancho de una página (A4 por defecto; se corrige con la primera del documento). */
  ratio: number;
}

const OPEN_ERROR = 'No se pudo abrir el PDF. Vuelve a intentarlo; si sigue pasando, recarga la página.';

/** Carga un PDF una sola vez para que lo compartan el visor del PDF y el lector en español. */
export function usePdfDocument(url: string | undefined): PdfState {
  const [state, setState] = useState<PdfState>({ doc: null, error: null, ratio: 1.414 });

  useEffect(() => {
    setState({ doc: null, error: null, ratio: 1.414 });
    if (!url) return;
    let cancelled = false;
    let task: PDFDocumentLoadingTask | null = null;
    loadPdfjs()
      .then((pdfjs) => {
        if (cancelled) return;
        task = pdfjs.getDocument({ url });
        return task.promise.then(async (loaded) => {
          if (cancelled) return; // la limpieza ya destruyó la tarea de carga, y con ella el documento
          const viewport = (await loaded.getPage(1)).getViewport({ scale: 1 });
          if (cancelled) return;
          setState({ doc: loaded, error: null, ratio: viewport.height / viewport.width });
        });
      })
      .catch(() => {
        if (!cancelled) setState({ doc: null, error: OPEN_ERROR, ratio: 1.414 });
      });
    return () => {
      cancelled = true;
      void task?.destroy();
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
