import type { AnalysisState } from '../lib/analysis';

interface AnalysisNoticeProps {
  /** Qué echa en falta quien mira: "hitos" o "personajes". */
  what: 'hitos' | 'personajes';
  analysis: AnalysisState | null;
  /** Pide analizar el libro abierto (la web enseña antes el coste). */
  onAnalyze?: () => void;
}

const button = 'mt-3 rounded-md border border-seal/40 px-3 py-1.5 text-sm font-semibold text-seal hover:bg-seal hover:text-on-seal';

/**
 * Lo que se ve en Hitos y Personajes cuando no hay nada que enseñar, según el estado del análisis: sin analizar (con el botón para hacerlo), en marcha, fallido
 * o analizado sin resultados. Un libro sin analizar es un caso normal, no un error: solo se quiere leer y traducir.
 */
export function AnalysisNotice({ what, analysis, onAnalyze }: AnalysisNoticeProps) {
  const status = analysis?.status ?? 'done';
  return (
    <div className="p-6 text-center text-sm text-stone">
      {status === 'none' && (
        <>
          <p>Este libro todavía no se ha analizado: sus {what} aparecerán cuando lo hagas.</p>
          {onAnalyze && (
            <button onClick={onAnalyze} className={button}>
              Analizar este libro
            </button>
          )}
        </>
      )}
      {status === 'running' && <p>Analizando el libro… sus {what} aparecerán aquí cuando termine. Mientras tanto puedes seguir leyendo.</p>}
      {status === 'failed' && (
        <>
          <p className="text-red-700">{analysis?.error ?? 'El análisis falló.'}</p>
          {onAnalyze && (
            <button onClick={onAnalyze} className={button}>
              Reintentar análisis
            </button>
          )}
        </>
      )}
      {status === 'needs_ocr' && <p>{analysis?.error ?? 'Este PDF parece escaneado: hace falta OCR, que aún no está disponible.'}</p>}
      {status === 'done' && <p>Este libro aún no tiene {what}.</p>}
    </div>
  );
}
