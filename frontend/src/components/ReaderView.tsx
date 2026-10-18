import { useEffect, useLayoutEffect, useRef, useState } from 'react';
import type { PDFDocumentProxy } from 'pdfjs-dist';
import { DWELL_MS, ENGLISH_WINDOW, joinLines, pagesToLoad, splitCjk, splitParagraphs } from '../lib/translation';
import { getPageText } from '../state/usePdfDocument';
import type { Translations } from '../state/Translations';
import type { JumpTarget } from '../types';

const PLACEHOLDER_HEIGHT = 1000; // px mínimos de una página sin traducir (más o menos lo que ocupa una página en español)

interface ReaderViewProps {
  doc: PDFDocumentProxy;
  translations: Translations;
  target: JumpTarget | null;
  onPageChange: (page: number) => void;
}

/**
 * El libro en español, con scroll continuo como el PDF. Cada página es un bloque:
 *  - traducida: el texto en español;
 *  - sin traducir aún: el texto en inglés del propio PDF (leído en local), atenuado y con un aviso, que se cambia por el español al llegar;
 *  - lejos de donde lees: un hueco con la altura media, sin contenido.
 *
 * Solo se pide traducción de la página que se queda a la vista un momento (DWELL_MS) y de sus vecinas: pasar de largo no gasta.
 * Al cambiar una página de altura (el español ocupa más que el inglés) el scroll se recoloca con un ANCLA (la página que lees y a qué altura
 * de la pantalla está) para que lo que lees no se mueva. Los huecos tienen altura fija: si cambiaran con cada traducción, se moverían todas a la vez.
 */
export function ReaderView({ doc, translations, target, onPageChange }: ReaderViewProps) {
  const scrollRef = useRef<HTMLDivElement>(null);
  const listRef = useRef<HTMLDivElement>(null);
  const [current, setCurrent] = useState(1);
  const [settled, setSettled] = useState(0); // 0 = todavía ninguna página lleva el tiempo suficiente a la vista
  const [english, setEnglish] = useState<Record<number, string>>({});
  const requestedEnglish = useRef(new Set<number>());
  const anchor = useRef<{ page: number; offset: number } | null>(null);

  const callbacks = useRef({ onPageChange });
  callbacks.current = { onPageChange };

  // Página que se está leyendo: la que contiene la línea situada al 30 % de la altura visible (búsqueda binaria sobre las posiciones).
  const locate = (): number | null => {
    const scroller = scrollRef.current;
    const list = listRef.current;
    if (!scroller || !list || list.children.length === 0) return null;
    const line = scroller.scrollTop + scroller.clientHeight * 0.3;
    let low = 0;
    let high = list.children.length - 1;
    while (low < high) {
      const mid = (low + high + 1) >> 1;
      if ((list.children[mid] as HTMLElement).offsetTop <= line) low = mid;
      else high = mid - 1;
    }
    return low + 1;
  };

  // El ancla: esa página y la distancia entre su borde superior y el de la pantalla. Se guarda en cada scroll y tras cada salto.
  const saveAnchor = () => {
    const scroller = scrollRef.current;
    const list = listRef.current;
    const page = locate();
    if (!scroller || !list || page === null) return;
    const element = list.children[page - 1] as HTMLElement;
    anchor.current = { page, offset: element.getBoundingClientRect().top - scroller.getBoundingClientRect().top };
    return page;
  };

  const onScroll = () => {
    const page = saveAnchor();
    if (page) setCurrent(page);
  };

  useEffect(() => {
    saveAnchor();
  }, []);

  // Tras cada cambio de contenido (llega una traducción, llega el inglés), la página-ancla vuelve a quedar donde estaba en pantalla.
  useLayoutEffect(() => {
    const saved = anchor.current;
    const scroller = scrollRef.current;
    const element = saved && (listRef.current?.children[saved.page - 1] as HTMLElement | undefined);
    if (!saved || !scroller || !element) return;
    const delta = element.getBoundingClientRect().top - scroller.getBoundingClientRect().top - saved.offset;
    if (Math.abs(delta) > 0.5) scroller.scrollTop += delta;
  }, [translations.pages, english]);

  useEffect(() => {
    callbacks.current.onPageChange(current);
  }, [current]);

  // Pedir traducción solo cuando la página lleva un rato a la vista.
  useEffect(() => {
    const timer = setTimeout(() => setSettled(current), DWELL_MS);
    return () => clearTimeout(timer);
  }, [current]);
  const { request } = translations;
  useEffect(() => {
    if (settled < 1) return;
    request(pagesToLoad(settled, doc.numPages));
  }, [settled, doc.numPages, request]);

  // El texto en inglés de las páginas cercanas, sacado del propio PDF (local y gratis).
  useEffect(() => {
    for (let page = Math.max(1, current - ENGLISH_WINDOW); page <= Math.min(doc.numPages, current + ENGLISH_WINDOW); page++) {
      if (requestedEnglish.current.has(page)) continue;
      requestedEnglish.current.add(page);
      getPageText(doc, page).then(
        (text) => setEnglish((previous) => ({ ...previous, [page]: joinLines(text) })),
        () => setEnglish((previous) => ({ ...previous, [page]: '' })),
      );
    }
  }, [current, doc]);

  // Saltar a una página (un hito, una cita del agente, el cambio de modo). Siempre al instante: con animación se recorrerían cientos de huecos.
  useEffect(() => {
    if (!target) return;
    const page = Math.min(doc.numPages, Math.max(1, target.page));
    const element = listRef.current?.children[page - 1] as HTMLElement | undefined;
    if (!element) return;
    scrollRef.current?.scrollTo({ top: element.offsetTop, behavior: 'auto' });
    const jumped = saveAnchor(); // ya, sin esperar al evento de scroll: si llegara contenido antes, recolocaría con el ancla vieja
    if (jumped) setCurrent(jumped);
  }, [target, doc]);

  return (
    <div ref={scrollRef} onScroll={onScroll} className="h-full overflow-y-auto bg-paper-dark" style={{ overflowAnchor: 'none' }} data-testid="reader-scroll">
      <div ref={listRef} className="relative mx-auto max-w-[44rem] bg-paper">
        {Array.from({ length: doc.numPages }, (_, index) => {
          const page = index + 1;
          return (
            <ReaderPage
              key={page}
              page={page}
              state={translations.pages[page]}
              english={Math.abs(page - current) <= ENGLISH_WINDOW ? english[page] : undefined}
              onRetry={() => translations.retry(page)}
            />
          );
        })}
      </div>
    </div>
  );
}

interface ReaderPageProps {
  page: number;
  state: Translations['pages'][number] | undefined;
  english: string | undefined;
  onRetry: () => void;
}

function ReaderPage({ page, state, english, onRetry }: ReaderPageProps) {
  const done = state?.status === 'done';
  return (
    <article data-page={page} className="border-b border-line px-5 py-6 md:px-8" style={done ? undefined : { minHeight: PLACEHOLDER_HEIGHT }}>
      <div className="mb-3 flex items-center justify-between text-xs font-semibold text-stone">
        <span>Pág. {page}</span>
        {state?.status === 'loading' && <span className="animate-pulse">Traduciendo… (mientras, el original)</span>}
      </div>
      {state?.status === 'done' ? (
        state.text ? (
          <div className="space-y-3 font-serif text-[16px] leading-relaxed text-ink">
            {splitParagraphs(state.text).map((paragraph, index) => (
              <p key={index}>
                {splitCjk(paragraph).map((run, i) =>
                  run.cjk ? (
                    <span key={i} lang="zh" className="font-brush">
                      {run.text}
                    </span>
                  ) : (
                    <span key={i}>{run.text}</span>
                  ),
                )}
              </p>
            ))}
          </div>
        ) : (
          <p className="text-sm text-stone">Esta página no tiene texto que traducir (puede ser una imagen o estar en blanco).</p>
        )
      ) : (
        <>
          {state?.status === 'error' && (
            <div className="mb-3 flex items-center gap-3 text-sm">
              <span className="text-seal-dark">{state.message}</span>
              <button onClick={onRetry} className="rounded border border-line bg-white px-2 py-1 font-semibold hover:border-seal">
                Reintentar
              </button>
            </div>
          )}
          {english ? (
            <p lang="en" className="font-serif text-[16px] leading-relaxed text-ink-soft opacity-60">
              {english}
            </p>
          ) : (
            <div className="space-y-3" aria-hidden="true">
              {[95, 100, 90, 100, 60].map((width, index) => (
                <div key={index} className="h-3 animate-pulse rounded bg-paper-dark" style={{ width: `${width}%` }} />
              ))}
            </div>
          )}
        </>
      )}
    </article>
  );
}
