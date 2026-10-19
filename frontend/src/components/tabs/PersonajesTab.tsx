import { formatYear } from '../../lib/format';
import type { AnalysisState } from '../../lib/analysis';
import type { Hito, Personaje } from '../../types';
import { AnalysisNotice } from '../AnalysisNotice';

interface PersonajesTabProps {
  personajes: Personaje[];
  hitos: Hito[];
  onGoToPage: (page: number) => void;
  analysis?: AnalysisState | null;
  onAnalyze?: () => void;
}

export function PersonajesTab({ personajes, hitos, onGoToPage, analysis = null, onAnalyze }: PersonajesTabProps) {
  if (personajes.length === 0) return <AnalysisNotice what="personajes" analysis={analysis} onAnalyze={onAnalyze} />;

  return (
    <ul className="space-y-2 p-3">
      {personajes.map((personaje) => {
        const related = hitos.filter((hito) => personaje.hitoIds.includes(hito.id));
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
            {related.length > 0 && (
              <ul className="mt-2 space-y-1 border-t border-line pt-2">
                {related.map((hito) => (
                  <li key={hito.id} className="flex items-center justify-between gap-2 text-xs">
                    <span>{hito.titulo} <span className="text-stone">({formatYear(hito.año)})</span></span>
                    {hito.pagina !== undefined && (
                      <button onClick={() => onGoToPage(hito.pagina!)} className="shrink-0 font-semibold text-seal hover:underline">
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
  );
}
