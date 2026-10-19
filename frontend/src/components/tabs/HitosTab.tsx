import { useMemo, useState } from 'react';
import { formatYear } from '../../lib/format';
import type { AnalysisState } from '../../lib/analysis';
import type { Book, Hito } from '../../types';
import { AnalysisNotice } from '../AnalysisNotice';
import { BookFilter, type BookScope } from '../BookFilter';
import { BookTag } from '../BookTag';

interface HitosTabProps {
  /** Los hitos de TODOS los libros (cada uno con su `fuente`). */
  hitos: Hito[];
  books: Book[];
  /** El libro abierto y su página actual (para resaltar el hito de la página que lees). */
  currentSlug?: string;
  currentPage: number;
  /** Ir a una página de un libro (abre el libro si no es el actual). */
  onGoTo: (slug: string, page: number) => void;
  onOpenBook: (slug: string) => void;
  /** Estado del análisis del libro abierto, para explicar por qué no hay hitos y ofrecer analizarlo. */
  analysis?: AnalysisState | null;
  onAnalyze?: () => void;
}

type Order = 'pagina' | 'año';

export function HitosTab({ hitos, books, currentSlug, currentPage, onGoTo, onOpenBook, analysis = null, onAnalyze }: HitosTabProps) {
  const [order, setOrder] = useState<Order>('año');
  const [scope, setScope] = useState<BookScope>('todos');
  const titleOf = (slug: string) => books.find((book) => book.slug === slug)?.title ?? slug;

  const visible = useMemo(() => (scope === 'este' ? hitos.filter((hito) => hito.fuente === currentSlug) : hitos), [hitos, scope, currentSlug]);
  // "Por página" solo tiene sentido dentro de UN libro: con varios, se agrupa por libro y luego por página. Los hitos de libros procesados antes de guardar la página
  // no la tienen: si ninguno la trae, se queda en por año.
  const hasPages = visible.some((hito) => hito.pagina !== undefined);
  const sorted = useMemo(
    () =>
      [...visible].sort((a, b) =>
        order === 'año' || !hasPages
          ? a.año - b.año
          : (a.fuente ?? '').localeCompare(b.fuente ?? '') || (a.pagina ?? Infinity) - (b.pagina ?? Infinity) || a.año - b.año,
      ),
    [visible, order, hasPages],
  );

  const bookCount = new Set(hitos.map((hito) => hito.fuente)).size;
  if (hitos.length === 0) return <AnalysisNotice what="hitos" analysis={analysis} onAnalyze={onAnalyze} />;

  return (
    <div className="p-3">
      <div className="mb-3 flex flex-wrap items-center justify-between gap-2 text-sm">
        <span className="text-stone">
          {visible.length} hitos{scope === 'todos' && books.length > 1 ? ` de ${bookCount} ${bookCount === 1 ? 'libro' : 'libros'}` : ''}
        </span>
        <div className="flex flex-wrap items-center gap-2">
          {books.length > 1 && <BookFilter value={scope} onChange={setScope} />}
          <div className="flex overflow-hidden rounded border border-line text-xs font-semibold" role="group" aria-label="Ordenar">
            {(['año', 'pagina'] as const).map((value) => (
              <button
                key={value}
                onClick={() => setOrder(value)}
                aria-pressed={order === value}
                className={`px-2 py-1 ${order === value ? 'bg-seal text-on-seal' : 'bg-white text-stone hover:text-ink'}`}
              >
                {value === 'pagina' ? 'Por página' : 'Por año'}
              </button>
            ))}
          </div>
        </div>
      </div>
      {visible.length === 0 && <AnalysisNotice what="hitos" analysis={analysis} onAnalyze={onAnalyze} />}
      <ul className="space-y-2">
        {sorted.map((hito) => {
          const slug = hito.fuente ?? currentSlug;
          const here = slug === currentSlug && hito.pagina === currentPage;
          return (
            <li key={`${slug}:${hito.id}`} className={`rounded border bg-white p-3 ${here ? 'border-seal ring-1 ring-seal' : 'border-line'}`}>
              <div className="flex items-start justify-between gap-2">
                <h3 className="text-sm font-semibold">{hito.titulo}</h3>
                {hito.pagina !== undefined && slug && (
                  <button onClick={() => onGoTo(slug, hito.pagina!)} className="shrink-0 rounded bg-paper-dark px-2 py-0.5 text-xs font-semibold text-ink-soft hover:bg-seal hover:text-on-seal">
                    Pág. {hito.pagina}
                  </button>
                )}
              </div>
              <p className="mt-0.5 text-xs text-stone">
                {formatYear(hito.año)} · {hito.lugar}
              </p>
              <p className="mt-2 text-sm text-ink-soft">{hito.descripcion}</p>
              {hito.personajes.length > 0 && <p className="mt-2 text-xs text-stone">{hito.personajes.map((p) => p.nombre).join(' · ')}</p>}
              {slug && (
                <div className="mt-2 flex">
                  <BookTag slug={slug} title={titleOf(slug)} current={slug === currentSlug} onOpen={onOpenBook} />
                </div>
              )}
            </li>
          );
        })}
      </ul>
    </div>
  );
}
