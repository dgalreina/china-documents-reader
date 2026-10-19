import { useEffect, useRef, useState } from 'react';
import type { PDFDocumentProxy } from 'pdfjs-dist';
import { HEADER_HEIGHT } from '../lib/layout';
import type { JumpTarget } from '../types';

const PAGE_GAP = 16; // px entre páginas
const SIDE_PADDING = 24; // px de margen a cada lado
const BUFFER_PAGES = 2; // páginas de más que se dibujan por delante y por detrás de lo visible
const MAX_PAGE_WIDTH = 900; // en pantallas grandes la página no se estira más

interface PdfViewerProps {
  doc: PDFDocumentProxy;
  /** alto/ancho de una página. */
  ratio: number;
  /** Pide saltar a una página. `nonce` cambia en cada petición para poder volver a saltar a la misma. */
  target: JumpTarget | null;
  onPageChange: (page: number) => void;
}

export function PdfViewer({ doc, ratio, target, onPageChange }: PdfViewerProps) {
  const scrollRef = useRef<HTMLDivElement>(null);
  const [width, setWidth] = useState(0);
  const [range, setRange] = useState({ first: 1, last: 1 });
  const lastReportedPage = useRef(0);

  // La función del padre cambia en cada render: se guarda en una referencia para no repetir efectos por eso.
  const callbacks = useRef({ onPageChange });
  callbacks.current = { onPageChange };

  useEffect(() => {
    lastReportedPage.current = 0;
  }, [doc]);

  // Medir el ancho disponible (se adapta al girar el móvil o al plegar el panel).
  useEffect(() => {
    const element = scrollRef.current;
    if (!element) return;
    const observer = new ResizeObserver(([entry]) => setWidth(entry.contentRect.width));
    observer.observe(element);
    return () => observer.disconnect();
  }, []);

  const pageWidth = Math.max(0, Math.min(width - SIDE_PADDING * 2, MAX_PAGE_WIDTH));
  const pageHeight = pageWidth * ratio;
  const slot = pageHeight + PAGE_GAP;

  // 3. Con las páginas todas del mismo tamaño, qué páginas se ven es una cuenta sencilla (sin observadores): posición del scroll / alto de página.
  const updateRange = () => {
    const element = scrollRef.current;
    if (!element || !doc || slot <= PAGE_GAP) return;
    // La primera página empieza HEADER_HEIGHT + PAGE_GAP más abajo del borde superior del contenido (hueco de la cabecera flotante).
    const top = element.scrollTop - HEADER_HEIGHT;
    const first = Math.max(1, Math.floor(top / slot) + 1);
    const last = Math.min(doc.numPages, Math.floor((top + element.clientHeight) / slot) + 1);
    setRange((previous) => (previous.first === first && previous.last === last ? previous : { first, last }));
    const line = element.scrollTop + element.clientHeight * 0.3 - HEADER_HEIGHT - PAGE_GAP; // la línea al 30 % de lo visible, medida desde la primera página
    const current = Math.min(doc.numPages, Math.max(1, Math.floor(line / slot) + 1));
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
    scrollRef.current.scrollTo({ top: (page - 1) * slot, behavior: target.instant ? 'auto' : 'smooth' });
  }, [target, doc, slot]);

  return (
    <div ref={scrollRef} onScroll={updateRange} className="h-full overflow-y-auto bg-paper-dark" data-testid="pdf-scroll">
      <div style={{ padding: `${HEADER_HEIGHT + PAGE_GAP}px ${SIDE_PADDING}px ${PAGE_GAP}px`, width: pageWidth + SIDE_PADDING * 2, margin: '0 auto' }}>
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
