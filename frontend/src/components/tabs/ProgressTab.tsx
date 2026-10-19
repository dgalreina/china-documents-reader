import { formatPercent, formatUsd } from '../../lib/format';
import type { Book, Job } from '../../types';
import { Cover } from '../Cover';

interface ProgressTabProps {
  jobs: Job[];
  books: Book[];
  onSelect: (slug: string) => void;
}

const ACTIVE = new Set(['UPLOADING', 'QUEUED', 'RUNNING']);

export function ProgressTab({ jobs, books, onSelect }: ProgressTabProps) {
  // Una subida terminada ya es un libro en Archivos: aquí solo estorbaría. Se ven las que están en marcha o fallaron, y los análisis.
  const shown = jobs.filter((job) => !(job.kind === 'upload' && job.status === 'DONE'));
  const active = shown.filter((job) => ACTIVE.has(job.status));
  const finished = shown.filter((job) => !ACTIVE.has(job.status));
  const titleOf = (job: Job) => job.title ?? books.find((book) => book.slug === job.slug)?.title ?? job.slug;
  const coverOf = (job: Job) => books.find((book) => book.slug === job.slug)?.coverUrl; // la portada es la del libro (la dibuja la subida)

  if (shown.length === 0) return <p className="p-6 text-center text-sm text-stone">No hay nada en marcha. Sube un PDF desde la pestaña Archivos.</p>;

  return (
    <div className="space-y-4 p-3">
      {active.length > 0 && (
        <section aria-label="En marcha">
          <h3 className="mb-2 text-xs font-bold uppercase tracking-wide text-stone">En marcha</h3>
          <ul className="space-y-2">
            {active.map((job) => (
              <JobCard key={job.jobId} job={job} title={titleOf(job)} coverUrl={coverOf(job)} onSelect={onSelect} />
            ))}
          </ul>
        </section>
      )}
      {finished.length > 0 && (
        <section aria-label="Terminados">
          <h3 className="mb-2 text-xs font-bold uppercase tracking-wide text-stone">Terminados</h3>
          <ul className="space-y-2">
            {finished.map((job) => (
              <JobCard key={job.jobId} job={job} title={titleOf(job)} coverUrl={coverOf(job)} onSelect={onSelect} />
            ))}
          </ul>
        </section>
      )}
    </div>
  );
}

function JobCard({ job, title, coverUrl, onSelect }: { job: Job; title: string; coverUrl?: string; onSelect: (slug: string) => void }) {
  const running = job.status === 'UPLOADING' || job.status === 'RUNNING';
  return (
    <li className="flex gap-3 rounded border border-line bg-white p-3 text-sm">
      <Cover url={coverUrl} />
      <div className="min-w-0 flex-1">
      <button onClick={() => onSelect(job.slug)} className="block w-full text-left font-semibold hover:text-seal">
        {title}
      </button>

      {job.status === 'QUEUED' && <p className="mt-1 text-stone">En cola (posición {job.queuePosition ?? 1}): se analiza un libro cada vez.</p>}

      {running && (
        <>
          <p className="mt-1 text-stone">
            {job.phase}
            {job.detail ? ` · ${job.detail}` : ''}
          </p>
          {job.progress === null ? (
            <div role="progressbar" aria-label="Analizando" className="mt-2 h-2 overflow-hidden rounded bg-paper-dark">
              <div className="h-full w-1/3 animate-pulse bg-seal" />
            </div>
          ) : (
            <>
              <div
                role="progressbar"
                aria-valuenow={Math.round(job.progress * 100)}
                aria-valuemin={0}
                aria-valuemax={100}
                className="mt-2 h-2 overflow-hidden rounded bg-paper-dark"
              >
                <div className="h-full bg-seal transition-all" style={{ width: formatPercent(job.progress) }} />
              </div>
              <p className="mt-1 text-right text-xs text-stone">{formatPercent(job.progress)}</p>
            </>
          )}
        </>
      )}

      {job.status === 'DONE' && job.stats && (
        <p className="mt-1 text-stone">
          {job.stats.chunksListos} de {job.stats.chunks} fragmentos guardados · {job.stats.hitos} hitos · {job.stats.personajes} personajes · coste estimado{' '}
          {formatUsd(job.stats.costeEstimadoUsd)}
        </p>
      )}

      {(job.status === 'FAILED' || job.status === 'NEEDS_OCR') && (
        <p role="alert" className={`mt-1 ${job.status === 'FAILED' ? 'text-red-700' : 'text-amber-800'}`}>
          {job.error}
        </p>
      )}
      </div>
    </li>
  );
}
