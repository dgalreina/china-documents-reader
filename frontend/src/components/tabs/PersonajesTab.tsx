import { useMemo, useState } from 'react';
import { formatYear } from '../../lib/format';
import type { AnalysisState } from '../../lib/analysis';
import type { Book, Hito, Personaje } from '../../types';
import { AnalysisNotice } from '../AnalysisNotice';
import { BookFilter, type BookScope } from '../BookFilter';
import { BookTag } from '../BookTag';

interface PersonajesTabProps {
  /** Los personajes de TODOS los libros (cada uno con los `fuentes` en que sale) y los hitos de todos, para enseñar en qué hitos intervienen. */
  personajes: Personaje[];
  hitos: Hito[];
  books: Book[];
  currentSlug?: string;
  onGoTo: (slug: string, page: number) => void;
  onOpenBook: (slug: string) => void;
  analysis?: AnalysisState | null;
  onAnalyze?: () => void;
  scanned?: boolean;
  onOcr?: () => void;
}

export function PersonajesTab({ personajes, hitos, books, currentSlug, onGoTo, onOpenBook, analysis = null, onAnalyze, scanned, onOcr }: PersonajesTabProps) {
  const [scope, setScope] = useState<BookScope>('todos');
  const titleOf = (slug: string) => books.find((book) => book.slug === slug)?.title ?? slug;
  const visible = useMemo(
    () => (scope === 'este' ? personajes.filter((person) => person.fuentes?.includes(currentSlug ?? '')) : personajes),
    [personajes, scope, currentSlug],
  );

  if (personajes.length === 0) return <AnalysisNotice what="personajes" analysis={analysis} onAnalyze={onAnalyze} scanned={scanned} onOcr={onOcr} />;

  return (
    <div className="p-3">
      <div className="mb-3 flex flex-wrap items-center justify-between gap-2 text-sm">
        <span className="text-stone">{visible.length} personajes</span>
        {books.length > 1 && <BookFilter value={scope} onChange={setScope} />}
      </div>
      {visible.length === 0 && <AnalysisNotice what="personajes" analysis={analysis} onAnalyze={onAnalyze} scanned={scanned} onOcr={onOcr} />}
      <ul className="space-y-2">
        {visible.map((personaje) => {
          // Sus hitos: los de todos los libros, o solo los del abierto si se filtra.
          const related = hitos.filter((hito) => personaje.hitoIds.includes(hito.id) && (scope === 'todos' || hito.fuente === currentSlug));
          const sources = personaje.fuentes ?? [];
          return (
            <li key={personaje.id} className="rounded border border-line bg-white p-3">
              <div className="flex items-baseline gap-2">
                <h3 className="text-sm font-semibold">{personaje.nombre}</h3>
                {personaje.nombreZh && <span lang="zh" className="font-brush text-sm text-stone">{personaje.nombreZh}</span>}
              </div>
              <p className="text-xs text-stone">
                {personaje.rol}
                {personaje.estado ? ` · ${personaje.estado}` : ''}
                {personaje.nacimiento !== undefined || personaje.muerte !== undefined
                  ? ` · ${personaje.nacimiento !== undefined ? formatYear(personaje.nacimiento) : '?'} – ${personaje.muerte !== undefined ? formatYear(personaje.muerte) : '?'}`
                  : ''}
              </p>
              <p className="mt-2 text-sm text-ink-soft">{personaje.descripcion}</p>
              {sources.length > 0 && (
                <div className="mt-2 flex flex-wrap gap-1">
                  {sources.map((slug) => (
                    <BookTag key={slug} slug={slug} title={titleOf(slug)} current={slug === currentSlug} onOpen={onOpenBook} />
                  ))}
                </div>
              )}
              {related.length > 0 && (
                <ul className="mt-2 space-y-1 border-t border-line pt-2">
                  {related.map((hito) => (
                    <li key={`${hito.fuente}:${hito.id}`} className="flex items-center justify-between gap-2 text-xs">
                      <span>
                        {hito.titulo} <span className="text-stone">({formatYear(hito.año)})</span>
                        {sources.length > 1 && hito.fuente && <span className="text-stone"> · {titleOf(hito.fuente)}</span>}
                      </span>
                      {hito.pagina !== undefined && (hito.fuente ?? currentSlug) && (
                        <button onClick={() => onGoTo((hito.fuente ?? currentSlug)!, hito.pagina!)} className="shrink-0 font-semibold text-seal hover:underline">
                          Pág. {hito.pagina}
                        </button>
                      )}
                    </li>
                  ))}
                </ul>
              )}
            </li>
          );
        })}
      </ul>
    </div>
  );
}
