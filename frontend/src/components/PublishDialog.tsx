import { useEffect, useRef, useState } from 'react';
import type { Book, Job } from '../types';
import { ConfirmDialog } from './ConfirmDialog';

interface PublishDialogProps {
  book: Book;
  jobs: Job[];
  /** Pide la vista previa (no escribe nada). Devuelve el trabajo que la calcula. Rechaza con un mensaje en español. */
  onPreview: (slug: string) => Promise<{ jobId: string }>;
  /** Publica de verdad. */
  onPublish: (slug: string) => Promise<void>;
  /** Quita el libro de producción. */
  onUnpublish: (slug: string) => Promise<void>;
  onClose: () => void;
}

const MAX_DUPLICATES_LISTED = 8;

/**
 * Publicar un libro en producción, en dos tiempos: al abrirse pide una VISTA PREVIA (no escribe nada) y enseña lo que pasaría —cuántos fragmentos y hitos, cuántos hitos se
 * descartan por estar ya en producción, si reemplaza una publicación anterior—; solo entonces se puede confirmar. Producción es la base que lee china-history: por eso se
 * enseña antes, y publicar de nuevo reemplaza la copia anterior de este libro (nunca toca nada que no sea suyo). Los personajes no se publican todavía.
 */
export function PublishDialog({ book, jobs, onPreview, onPublish, onUnpublish, onClose }: PublishDialogProps) {
  const [jobId, setJobId] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const started = useRef(false);

  useEffect(() => {
    if (started.current) return; // en desarrollo React monta dos veces: la vista previa se pide una sola
    started.current = true;
    onPreview(book.slug).then(
      (job) => setJobId(job.jobId),
      (problem) => setError(problem instanceof Error ? problem.message : 'No se pudo preparar la publicación.'),
    );
  }, [book.slug, onPreview]);

  const job = jobs.find((candidate) => candidate.jobId === jobId);
  const summary = job?.status === 'DONE' ? job.publish : undefined;
  const failed = job?.status === 'FAILED' ? (job.error ?? 'La vista previa falló.') : null;
  const waiting = !error && !failed && !summary;
  const published = Boolean(book.published);

  const run = async (action: () => Promise<void>, fallback: string) => {
    setBusy(true);
    setError(null);
    try {
      await action();
      onClose();
    } catch (problem) {
      setError(problem instanceof Error ? problem.message : fallback);
      setBusy(false);
    }
  };

  return (
    <ConfirmDialog
      title={`${published ? 'Actualizar' : 'Publicar'} «${book.title}» en producción`}
      confirmLabel={published ? 'Actualizar en producción' : 'Publicar en producción'}
      busyLabel="Publicando…"
      busy={busy}
      confirmDisabled={waiting || Boolean(failed) || !summary || summary.chunks === 0}
      error={error ?? failed}
      onConfirm={() => void run(() => onPublish(book.slug), 'No se pudo publicar el libro.')}
      onCancel={onClose}
    >
      <p>
        Producción es la base de datos que lee <span className="font-semibold text-ink">china-history</span>. Esto copia allí los fragmentos y los hitos de este libro (los personajes todavía no).
      </p>
      {waiting && <p className="text-stone">Comprobando lo que se publicaría… (no se escribe nada todavía)</p>}
      {summary && (
        <div className="rounded border border-line bg-white p-2.5 text-ink">
          <p className="text-xs font-bold uppercase tracking-wide text-stone">Vista previa</p>
          <ul className="mt-1 list-disc space-y-0.5 pl-5">
            <li>
              <span className="font-semibold">{summary.chunks}</span> fragmentos
            </li>
            <li>
              <span className="font-semibold">{summary.hitos}</span> hitos nuevos
              {summary.duplicateCount > 0 && <> · {summary.duplicateCount} descartados por estar ya en producción</>}
            </li>
          </ul>
          {summary.before.chunks > 0 && (
            <p className="mt-1.5 text-xs text-stone">
              Ya hay publicados {summary.before.chunks} fragmentos y {summary.before.hitos} hitos de este libro: se reemplazarán por los de ahora.
            </p>
          )}
          {summary.duplicates.length > 0 && (
            <details className="mt-1.5 text-xs">
              <summary className="cursor-pointer font-semibold text-stone">Ver los descartados</summary>
              <ul className="mt-1 space-y-1">
                {summary.duplicates.slice(0, MAX_DUPLICATES_LISTED).map((duplicate, index) => (
                  <li key={index}>
                    {duplicate.titulo} ({duplicate.año}) ya está como «{duplicate.existente ?? '—'}»
                  </li>
                ))}
                {summary.duplicateCount > MAX_DUPLICATES_LISTED && <li>… y {summary.duplicateCount - MAX_DUPLICATES_LISTED} más.</li>}
              </ul>
            </details>
          )}
        </div>
      )}
      {published && (
        <p className="text-xs">
          <button
            type="button"
            disabled={busy}
            onClick={() => void run(() => onUnpublish(book.slug), 'No se pudo quitar el libro de producción.')}
            className="font-semibold text-stone underline decoration-line underline-offset-2 hover:text-seal-dark disabled:opacity-50"
          >
            Quitar este libro de producción
          </button>
          <span className="text-stone"> (borra solo lo que publicó esta app).</span>
        </p>
      )}
    </ConfirmDialog>
  );
}
