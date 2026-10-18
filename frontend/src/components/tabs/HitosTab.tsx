import { useMemo, useState } from 'react';
import { formatYear } from '../../lib/format';
import type { Hito } from '../../types';

interface HitosTabProps {
  hitos: Hito[];
  currentPage: number;
  onGoToPage: (page: number) => void;
}

type Order = 'pagina' | 'año';

export function HitosTab({ hitos, currentPage, onGoToPage }: HitosTabProps) {
  const [order, setOrder] = useState<Order>('pagina');

  const sorted = useMemo(
    () => [...hitos].sort((a, b) => (order === 'año' ? a.año - b.año : (a.pagina ?? Infinity) - (b.pagina ?? Infinity) || a.año - b.año)),
    [hitos, order],
  );

  if (hitos.length === 0) return <p className="p-6 text-center text-sm text-stone">Este libro aún no tiene hitos.</p>;

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
              className={`px-2 py-1 ${order === value ? 'bg-seal text-white' : 'bg-white text-stone hover:text-ink'}`}
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
                  <button onClick={() => onGoToPage(hito.pagina!)} className="shrink-0 rounded bg-paper-dark px-2 py-0.5 text-xs font-semibold text-ink-soft hover:bg-seal hover:text-white">
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
