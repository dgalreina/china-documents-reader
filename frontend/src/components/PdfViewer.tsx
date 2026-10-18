import { useEffect, useRef, useState } from 'react';
import * as pdfjs from 'pdfjs-dist';
import type { PDFDocumentProxy } from 'pdfjs-dist';
import workerUrl from 'pdfjs-dist/build/pdf.worker.min.mjs?url';

// pdf.js hace el trabajo pesado en un "worker" (un hilo aparte) para no congelar la página.
pdfjs.GlobalWorkerOptions.workerSrc = workerUrl;

const PAGE_GAP = 16; // px entre páginas
const SIDE_PADDING = 24; // px de margen a cada lado
const BUFFER_PAGES = 2; // páginas de más que se dibujan por delante y por detrás de lo visible
const MAX_PAGE_WIDTH = 900; // en pantallas grandes la página no se estira más

interface PdfViewerProps {
  url?: string;
  /** Pide saltar a una página. `nonce` cambia en cada petición para poder volver a saltar a la misma. */
  target: { page: number; nonce: number } | null;
  onPageChange: (page: number) => void;
  onLoaded: (pageCount: number) => void;
  /** Sin PDF disponible, el visor ofrece abrir uno desde el ordenador. */
  onPickLocal: (file: File) => void;
}

export function PdfViewer({ url, target, onPageChange, onLoaded, onPickLocal }: PdfViewerProps) {
  const scrollRef = useRef<HTMLDivElement>(null);
  const [doc, setDoc] = useState<PDFDocumentProxy | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [width, setWidth] = useState(0);
  const [ratio, setRatio] = useState(1.414); // alto/ancho de una página (A4 por defecto; se corrige con la del documento)
  const [range, setRange] = useState({ first: 1, last: 1 });
  const lastReportedPage = useRef(0);

  // Las funciones del padre cambian en cada render: se guardan en una referencia para no recargar el PDF por eso.
  const callbacks = useRef({ onPageChange, onLoaded });
  callbacks.current = { onPageChange, onLoaded };

  // 1. Cargar el documento.
  useEffect(() => {
    setDoc(null);
    setError(null);
    lastReportedPage.current = 0;
    if (!url) return;
    let cancelled = false;
    const task = pdfjs.getDocument({ url });
    task.promise
      .then(async (loaded) => {
        if (cancelled) return; // la limpieza ya destruyó la tarea de carga, y con ella el documento
        const viewport = (await loaded.getPage(1)).getViewport({ scale: 1 });
        if (cancelled) return;
        setRatio(viewport.height / viewport.width);
        setDoc(loaded);
        callbacks.current.onLoaded(loaded.numPages);
      })
      .catch(() => {
        if (!cancelled) setError('No se pudo abrir el PDF. Si es el de muestra, comprueba que existe frontend/public/samples/oxford-zhou.pdf.');
      });
    return () => {
      cancelled = true;
      void task.destroy();
    };
  }, [url]);

  // 2. Medir el ancho disponible (se adapta al girar el móvil o al plegar el panel).
  useEffect(() => {
    const element = scrollRef.current;
    if (!element) return;
    const observer = new ResizeObserver(([entry]) => setWidth(entry.contentRect.width));
    observer.observe(element);
    return () => observer.disconnect();
    // El contenedor del scroll solo existe cuando ya hay documento: hay que volver a engancharse entonces.
  }, [doc]);

  const pageWidth = Math.max(0, Math.min(width - SIDE_PADDING * 2, MAX_PAGE_WIDTH));
  const pageHeight = pageWidth * ratio;
  const slot = pageHeight + PAGE_GAP;

  // 3. Con las páginas todas del mismo tamaño, qué páginas se ven es una cuenta sencilla (sin observadores): posición del scroll / alto de página.
  const updateRange = () => {
    const element = scrollRef.current;
    if (!element || !doc || slot <= PAGE_GAP) return;
    const first = Math.max(1, Math.floor(element.scrollTop / slot) + 1);
    const last = Math.min(doc.numPages, Math.floor((element.scrollTop + element.clientHeight) / slot) + 1);
    setRange((previous) => (previous.first === first && previous.last === last ? previous : { first, last }));
    const current = Math.min(doc.numPages, Math.max(1, Math.floor((element.scrollTop + element.clientHeight * 0.3) / slot) + 1));
    if (current !== lastReportedPage.current) {
      lastReportedPage.current = current;
      callbacks.current.onPageChange(current);
    }
  };

  useEffect(() => {
    updateRange();
  }, [doc, slot]);

  // 4. Saltar a una página cuando se pide desde fuera (un hito, una cita del agente...).
  useEffect(() => {
    if (!target || !scrollRef.current || !doc) return;
    const page = Math.min(doc.numPages, Math.max(1, target.page));
    scrollRef.current.scrollTo({ top: (page - 1) * slot, behavior: 'smooth' });
  }, [target, doc, slot]);

  if (!url) {
    return (
      <div className="flex h-full flex-col items-center justify-center gap-4 p-6 text-center">
        <p className="max-w-sm text-stone">Este libro no tiene PDF disponible en la maqueta.</p>
        <label className="cursor-pointer rounded bg-seal px-4 py-2 text-sm font-semibold text-white hover:bg-seal-dark">
          Abrir un PDF desde mi ordenador
          <input
            type="file"
            accept="application/pdf"
            className="hidden"
            onChange={(event) => {
              const file = event.target.files?.[0];
              if (file) onPickLocal(file);
            }}
          />
        </label>
      </div>
    );
  }
  if (error) return <div className="flex h-full items-center justify-center p-6 text-center text-seal-dark">{error}</div>;
  if (!doc) return <div className="flex h-full items-center justify-center text-stone">Cargando el PDF…</div>;

  return (
    <div ref={scrollRef} onScroll={updateRange} className="h-full overflow-y-auto bg-paper-dark" data-testid="pdf-scroll">
      <div style={{ padding: `${PAGE_GAP}px ${SIDE_PADDING}px`, width: pageWidth + SIDE_PADDING * 2, margin: '0 auto' }}>
        {Array.from({ length: doc.numPages }, (_, index) => {
          const pageNumber = index + 1;
          const active = pageNumber >= range.first - BUFFER_PAGES && pageNumber <= range.last + BUFFER_PAGES;
          return <PdfPage key={pageNumber} doc={doc} pageNumber={pageNumber} width={pageWidth} height={pageHeight} active={active} />;
        })}
      </div>
    </div>
  );
}

interface PdfPageProps {
  doc: PDFDocumentProxy;
  pageNumber: number;
  width: number;
  height: number;
  active: boolean;
}

/** Una página. Reserva siempre su hueco (para que el scroll no salte) y solo dibuja el contenido cuando está cerca de lo visible. */
function PdfPage({ doc, pageNumber, width, height, active }: PdfPageProps) {
  const canvasRef = useRef<HTMLCanvasElement>(null);

  useEffect(() => {
    const canvas = canvasRef.current;
    if (!canvas || width <= 0) return;
    if (!active) {
      canvas.width = 0; // libera la memoria de las páginas que ya quedan lejos
      canvas.height = 0;
      return;
    }
    let cancelled = false;
    let task: { promise: Promise<unknown>; cancel: () => void } | null = null;
    void doc.getPage(pageNumber).then((page) => {
      if (cancelled) return;
      const scale = width / page.getViewport({ scale: 1 }).width;
      const viewport = page.getViewport({ scale });
      const dpr = Math.min(window.devicePixelRatio || 1, 2); // más nitidez en pantallas de alta densidad, sin pasarse de memoria
      canvas.width = Math.floor(viewport.width * dpr);
      canvas.height = Math.floor(viewport.height * dpr);
      canvas.style.width = `${viewport.width}px`;
      canvas.style.height = `${viewport.height}px`;
      task = page.render({ canvas, viewport, transform: dpr !== 1 ? [dpr, 0, 0, dpr, 0, 0] : undefined });
      task.promise.catch(() => undefined); // una cancelación (al hacer scroll rápido) no es un error
    });
    return () => {
      cancelled = true;
      task?.cancel();
    };
  }, [doc, pageNumber, width, active]);

  return (
    <div data-page={pageNumber} className="mb-4 bg-white shadow" style={{ width, height }}>
      <canvas ref={canvasRef} aria-label={`Página ${pageNumber}`} />
    </div>
  );
}
