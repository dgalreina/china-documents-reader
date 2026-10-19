import { useMemo, useState } from 'react';
import { formatYear } from '../../lib/format';
import type { AnalysisState } from '../../lib/analysis';
import type { Hito } from '../../types';
import { AnalysisNotice } from '../AnalysisNotice';

interface HitosTabProps {
  hitos: Hito[];
  currentPage: number;
  onGoToPage: (page: number) => void;
  /** Estado del análisis del libro abierto, para explicar por qué no hay hitos y ofrecer analizarlo. */
  analysis?: AnalysisState | null;
  onAnalyze?: () => void;
}

type Order = 'pagina' | 'año';

export function HitosTab({ hitos, currentPage, onGoToPage, analysis = null, onAnalyze }: HitosTabProps) {
  const [order, setOrder] = useState<Order>('pagina');

  // Los hitos de libros procesados antes de guardar la página no la tienen: si ninguno la trae, "por página" se queda en por año.
  const hasPages = hitos.some((hito) => hito.pagina !== undefined);
  const sorted = useMemo(
    () => [...hitos].sort((a, b) => (order === 'año' || !hasPages ? a.año - b.año : (a.pagina ?? Infinity) - (b.pagina ?? Infinity) || a.año - b.año)),
    [hitos, order, hasPages],
  );

  if (hitos.length === 0) return <AnalysisNotice what="hitos" analysis={analysis} onAnalyze={onAnalyze} />;

  return (
    <div className="p-3">
      <div className="mb-3 flex items-center justify-between text-sm">
        <span className="text-stone">{hitos.length} hitos</span>
        <div className="flex overflow-hidden rounded border border-line text-xs font-semibold" role="group" aria-label="Ordenar">
          {(['pagina', 'año'] as const).map((value) => (
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
      <ul className="space-y-2">
        {sorted.map((hito) => {
          const here = hito.pagina === currentPage;
          return (
            <li key={hito.id} className={`rounded border bg-white p-3 ${here ? 'border-seal ring-1 ring-seal' : 'border-line'}`}>
              <div className="flex items-start justify-between gap-2">
                <h3 className="text-sm font-semibold">{hito.titulo}</h3>
                {hito.pagina !== undefined && (
                  <button onClick={() => onGoToPage(hito.pagina!)} className="shrink-0 rounded bg-paper-dark px-2 py-0.5 text-xs font-semibold text-ink-soft hover:bg-seal hover:text-on-seal">
                    Pág. {hito.pagina}
                  </button>
                )}
              </div>
              <p className="mt-0.5 text-xs text-stone">
                {formatYear(hito.año)} · {hito.lugar}
              </p>
              <p className="mt-2 text-sm text-ink-soft">{hito.descripcion}</p>
              {hito.personajes.length > 0 && (
                <p className="mt-2 text-xs text-stone">
                  {hito.personajes.map((p) => p.nombre).join(' · ')}
                </p>
              )}
            </li>
          );
        })}
      </ul>
    </div>
  );
}
