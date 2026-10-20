import { memo, useEffect, useLayoutEffect, useRef, useState } from 'react';
import type { PDFDocumentProxy } from 'pdfjs-dist';
import { HEADER_HEIGHT } from '../lib/layout';
import { clamp, usePinch } from '../lib/usePinch';
import type { JumpTarget } from '../types';

const PAGE_GAP = 16; // px entre páginas
const SIDE_PADDING = 24; // px de margen a cada lado
const BUFFER_PAGES = 2; // páginas de más que se dibujan por delante y por detrás de lo visible
const MAX_PAGE_WIDTH = 900; // en pantallas grandes la página no se estira más (con zoom 1)
const MIN_ZOOM = 1; // 1 = la página ajustada al ancho
const MAX_ZOOM = 4;
const MAX_CANVAS_PX = 2400; // ancho máximo del dibujo en píxeles reales: con zoom alto la memoria se dispararía; por encima, el navegador lo amplía (algo menos nítido)
const TOP = HEADER_HEIGHT + PAGE_GAP; // dónde empieza la primera página, medido desde arriba del contenido (hueco de la cabecera flotante)

interface PdfViewerProps {
  doc: PDFDocumentProxy;
  /** alto/ancho de una página. */
  ratio: number;
  /** Pide saltar a una página. `nonce` cambia en cada petición para poder volver a saltar a la misma. */
  target: JumpTarget | null;
  onPageChange: (page: number) => void;
}

/**
 * El ancho "asentado": sigue al ancho real, pero espera a que deje de cambiar. Mientras se mueve el panel lateral el ancho cambia en cada fotograma, y volver a
 * dibujar el PDF cada vez iría a tirones: las páginas se escalan con CSS durante el movimiento y solo se redibujan nítidas cuando termina. La primera medida no espera.
 */
function useSettledWidth(width: number, delay = 160): number {
  const [settled, setSettled] = useState(width);
  useEffect(() => {
    if (width === settled) return;
    if (settled === 0) {
      setSettled(width);
      return;
    }
    const timer = setTimeout(() => setSettled(width), delay);
    return () => clearTimeout(timer);
  }, [width, settled, delay]);
  return settled;
}

/** Qué punto del documento estaba bajo los dedos al empezar el zoom (página y posición dentro de ella), para dejarlo en el mismo sitio de la pantalla después. */
interface ZoomAnchor {
  page: number; // índice desde 0
  fractionY: number; // 0 a 1 dentro de la página
  fractionX: number;
  screenX: number; // dónde estaba ese punto en la pantalla (relativo al visor)
  screenY: number;
}

export function PdfViewer({ doc, ratio, target, onPageChange }: PdfViewerProps) {
  const scrollRef = useRef<HTMLDivElement>(null);
  const contentRef = useRef<HTMLDivElement>(null);
  const [width, setWidth] = useState(0);
  const [zoom, setZoom] = useState(1);
  const [range, setRange] = useState({ first: 1, last: 1 });
  const lastReportedPage = useRef(0);
  const pendingAnchor = useRef<ZoomAnchor | null>(null);

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

  const fitWidth = Math.max(0, Math.min(width - SIDE_PADDING * 2, MAX_PAGE_WIDTH)); // la página ajustada al ancho
  const pageWidth = fitWidth * zoom;
  const renderWidth = useSettledWidth(pageWidth); // el ancho con el que se dibujan los píxeles (se asienta tras mover el panel o hacer zoom)
  const pageHeight = pageWidth * ratio;
  const slot = pageHeight + PAGE_GAP;
  const buffer = zoom > 1.5 ? 0 : BUFFER_PAGES; // con zoom alto cada página pesa mucho: solo las visibles

  // Las medidas de este render, para calcular el anclaje del zoom con los valores vigentes.
  const layout = useRef({ pageWidth, pageHeight, slot });
  layout.current = { pageWidth, pageHeight, slot };

  // 3. Con las páginas todas del mismo tamaño, qué páginas se ven es una cuenta sencilla (sin observadores): posición del scroll / alto de página.
  const updateRange = () => {
    const element = scrollRef.current;
    if (!element || !doc || slot <= PAGE_GAP) return;
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
  // Cada petición se atiende UNA vez: en cuanto la página tiene medidas (al abrir el libro aún no las tiene) y sin repetirse si luego cambia el tamaño (zoom, panel).
  const jumpedTo = useRef<JumpTarget | null>(null);
  useEffect(() => {
    if (!target || jumpedTo.current === target || !scrollRef.current || !doc || slot <= PAGE_GAP) return;
    jumpedTo.current = target;
    const page = Math.min(doc.numPages, Math.max(1, target.page));
    scrollRef.current.scrollTo({ top: (page - 1) * slot, behavior: target.instant ? 'auto' : 'smooth' });
  }, [target, doc, slot]);

  // 5. Zoom. Se guarda qué punto del documento hay bajo los dedos y, cuando la página ya tiene su tamaño nuevo, se recoloca el scroll para que ese punto no se mueva.
  const captureAnchor = (screenX: number, screenY: number): ZoomAnchor | null => {
    const element = scrollRef.current;
    const { pageWidth: w, pageHeight: h, slot: s } = layout.current;
    if (!element || h <= 0 || w <= 0) return null;
    const y = element.scrollTop + screenY - TOP;
    const page = clamp(Math.floor(y / s), 0, doc.numPages - 1);
    const left = Math.max(0, (element.clientWidth - (w + SIDE_PADDING * 2)) / 2);
    return {
      page,
      fractionY: clamp(y - page * s, 0, h) / h,
      fractionX: clamp((element.scrollLeft + screenX - left - SIDE_PADDING) / w, 0, 1),
      screenX,
      screenY,
    };
  };
  const applyZoom = (next: number, screenX: number, screenY: number) => {
    const clamped = clamp(next, MIN_ZOOM, MAX_ZOOM);
    if (Math.abs(clamped - zoom) < 0.001) return;
    pendingAnchor.current = captureAnchor(screenX, screenY);
    setZoom(clamped);
  };
  useLayoutEffect(() => {
    const anchor = pendingAnchor.current;
    const element = scrollRef.current;
    if (!anchor || !element) return;
    pendingAnchor.current = null;
    const { pageWidth: w, pageHeight: h, slot: s } = layout.current;
    const left = Math.max(0, (element.clientWidth - (w + SIDE_PADDING * 2)) / 2);
    element.scrollTop = TOP + anchor.page * s + anchor.fractionY * h - anchor.screenY;
    element.scrollLeft = Math.max(0, left + SIDE_PADDING + anchor.fractionX * w - anchor.screenX);
  }, [zoom]);

  // Mientras los dedos se mueven, el contenido se amplía con CSS (fluido y sin volver a dibujar); al soltar, el zoom se aplica de verdad.
  const showPinch = (ratioNow: number, center: { x: number; y: number }) => {
    const content = contentRef.current;
    const element = scrollRef.current;
    if (!content || !element) return;
    const limited = clamp(ratioNow, MIN_ZOOM / zoom, MAX_ZOOM / zoom);
    content.style.transformOrigin = `${element.scrollLeft + center.x}px ${element.scrollTop + center.y}px`;
    content.style.transform = limited === 1 ? '' : `scale(${limited})`;
  };
  const wheelTarget = useRef<{ zoom: number; x: number; y: number; frame: number } | null>(null);
  usePinch(scrollRef, {
    onPinch: showPinch,
    onPinchEnd: (ratioEnd, center) => {
      if (contentRef.current) contentRef.current.style.transform = '';
      applyZoom(zoom * ratioEnd, center.x, center.y);
    },
    onWheelZoom: (factor, center) => {
      // La rueda llega muchas veces por segundo: se acumula y se aplica una vez por fotograma.
      const pending = wheelTarget.current ?? { zoom, x: center.x, y: center.y, frame: 0 };
      pending.zoom = clamp(pending.zoom * factor, MIN_ZOOM, MAX_ZOOM);
      pending.x = center.x;
      pending.y = center.y;
      if (!pending.frame) {
        pending.frame = requestAnimationFrame(() => {
          const done = wheelTarget.current;
          wheelTarget.current = null;
          if (done) applyZoom(done.zoom, done.x, done.y);
        });
      }
      wheelTarget.current = pending;
    },
  });

  return (
    <div
      ref={scrollRef}
      onScroll={updateRange}
      className="h-full overflow-auto overscroll-none bg-paper-dark"
      style={{ touchAction: 'pan-x pan-y' }}
      data-testid="pdf-scroll"
    >
      <div ref={contentRef} style={{ padding: `${HEADER_HEIGHT + PAGE_GAP}px ${SIDE_PADDING}px ${PAGE_GAP}px`, width: pageWidth + SIDE_PADDING * 2, margin: '0 auto' }}>
        {Array.from({ length: doc.numPages }, (_, index) => {
          const pageNumber = index + 1;
          const active = pageNumber >= range.first - buffer && pageNumber <= range.last + buffer;
          return <PdfPage key={pageNumber} doc={doc} pageNumber={pageNumber} width={pageWidth} renderWidth={renderWidth} height={pageHeight} active={active} />;
        })}
      </div>
    </div>
  );
}

interface PdfPageProps {
  doc: PDFDocumentProxy;
  pageNumber: number;
  /** Tamaño en pantalla (cambia al instante, también mientras se mueve el panel). */
  width: number;
  height: number;
  /** Ancho con el que se dibuja el canvas (cambia cuando el ancho se ha asentado). */
  renderWidth: number;
  active: boolean;
}

/**
 * Una página. Reserva siempre su hueco (para que el scroll no salte) y solo dibuja el contenido cuando está cerca de lo visible. Va en `memo`: al cambiar de página visible
 * solo cambian las que entran o salen del rango, y las otras (hasta 500) no deben repintarse.
 */
const PdfPage = memo(function PdfPage({ doc, pageNumber, width, height, renderWidth, active }: PdfPageProps) {
  const canvasRef = useRef<HTMLCanvasElement>(null);

  useEffect(() => {
    const canvas = canvasRef.current;
    if (!canvas || renderWidth <= 0) return;
    if (!active) {
      canvas.width = 0; // libera la memoria de las páginas que ya quedan lejos
      canvas.height = 0;
      return;
    }
    let cancelled = false;
    let task: { promise: Promise<unknown>; cancel: () => void } | null = null;
    void doc.getPage(pageNumber).then((page) => {
      if (cancelled) return;
      const dpr = Math.min(window.devicePixelRatio || 1, 2); // más nitidez en pantallas de alta densidad, sin pasarse de memoria
      const pixels = Math.min(renderWidth * dpr, MAX_CANVAS_PX); // con zoom alto no se dibuja más grande de lo razonable
      const viewport = page.getViewport({ scale: pixels / page.getViewport({ scale: 1 }).width });
      canvas.width = Math.floor(viewport.width);
      canvas.height = Math.floor(viewport.height);
      task = page.render({ canvas, viewport });
      task.promise.catch(() => undefined); // una cancelación (al hacer scroll rápido) no es un error
    });
    return () => {
      cancelled = true;
      task?.cancel();
    };
  }, [doc, pageNumber, renderWidth, active]);

  return (
    <div data-page={pageNumber} className="mb-4 bg-white shadow" style={{ width, height }}>
      <canvas ref={canvasRef} aria-label={`Página ${pageNumber}`} className="block h-full w-full" />
    </div>
  );
});
