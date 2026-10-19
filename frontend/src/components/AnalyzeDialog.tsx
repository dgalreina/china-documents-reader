import { useState } from 'react';
import { analysisState, estimateAnalysisCost } from '../lib/analysis';
import { formatUsd } from '../lib/format';
import type { Book, Job } from '../types';
import { ConfirmDialog } from './ConfirmDialog';

interface AnalyzeDialogProps {
  book: Book;
  jobs: Job[];
  /** Arranca el análisis. Rechaza con un mensaje en español. */
  onConfirm: (slug: string) => Promise<void>;
  onClose: () => void;
}

/**
 * Pide confirmar el análisis de un libro ANTES de arrancarlo, porque cuesta dinero (OpenAI): enseña una estimación del coste. Analizar es lo que extrae los
 * hitos y los personajes; leer y traducir el libro no lo necesita y no se paga aquí.
 */
export function AnalyzeDialog({ book, jobs, onConfirm, onClose }: AnalyzeDialogProps) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const again = analysisState(book, jobs).status === 'done';
  const cost = estimateAnalysisCost(book.pageCount);

  const confirm = async () => {
    setBusy(true);
    setError(null);
    try {
      await onConfirm(book.slug);
      onClose();
    } catch (problem) {
      setError(problem instanceof Error ? problem.message : 'No se pudo arrancar el análisis.');
      setBusy(false);
    }
  };

  return (
    <ConfirmDialog title={`${again ? 'Volver a analizar' : 'Analizar'} «${book.title}»`} confirmLabel={again ? 'Volver a analizar' : 'Analizar'} busyLabel="Arrancando…" busy={busy} error={error} onConfirm={confirm} onCancel={onClose}>
      <p>Se extraerán del libro sus hitos (hechos con lugar y fecha) y sus personajes.</p>
      <p>
        Coste estimado:{' '}
        <span className="font-semibold text-ink">{book.pageCount > 0 ? `unos ${formatUsd(cost)}` : 'unos 0,01 $ por página'}</span>
        {book.pageCount > 0 && <> ({book.pageCount} págs. × 0,01 $)</>}. Es una estimación: depende de cuánto texto haya.
      </p>
      {again && <p>Se reemplazará el análisis anterior.</p>}
      <p className="text-stone">Tarda unos minutos y mientras tanto puedes seguir leyendo y traduciendo.</p>
    </ConfirmDialog>
  );
}
