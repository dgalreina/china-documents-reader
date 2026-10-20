import { useState } from 'react';
import { analysisState, type AnalysisStatus } from '../../lib/analysis';
import { MAX_PDF_MB, isValidSlug, suggestSlug, suggestTitle, validatePdf } from '../../lib/files';
import { formatPercent } from '../../lib/format';
import { OCR_ENGINE_NAME, needsOcr, ocrState } from '../../lib/ocr';
import { pendingUploads } from '../../lib/uploads';
import type { Book, Job } from '../../types';
import { ConfirmDialog } from '../ConfirmDialog';
import { Cover } from '../Cover';
import { TrashIcon } from '../icons';

/** El estado del análisis de cada libro, con el mismo vocabulario en toda la web. El rojo de marca se reserva para la marca: "Analizado" va en tinta y "Falló" en rojo de error. */
const ANALYSIS_LABEL: Record<AnalysisStatus, string> = {
  none: 'Sin analizar',
  running: 'Analizando…',
  done: 'Analizado',
  failed: 'Falló',
  needs_ocr: 'Necesita OCR',
};

const ANALYSIS_STYLE: Record<AnalysisStatus, string> = {
  none: 'border border-ink/20 text-ink-soft',
  running: 'bg-ink/10 text-ink',
  done: 'bg-ink text-paper',
  failed: 'bg-red-100 text-red-800',
  needs_ocr: 'bg-amber-100 text-amber-800',
};

interface FilesTabProps {
  books: Book[];
  /** Los trabajos recientes: de ellos sale si un libro se está analizando o falló. */
  jobs: Job[];
  currentSlug?: string;
  onSelect: (slug: string) => void;
  onUpload: (file: File, options: { slug: string; title: string; replaceSource: boolean }) => Promise<void>;
  /** Pide analizar un libro (la web enseña antes lo que costará). */
  onAnalyze?: (book: Book) => void;
  /** Pide reconocer el texto de un libro escaneado (OCR; la web enseña antes lo que costará y deja elegir el motor). */
  onOcr?: (book: Book) => void;
  /** Cierra la sesión. */
  onSignOut?: () => void;
  /** Borra un libro con todo lo suyo. Rechaza con un mensaje en español. */
  onDelete?: (slug: string) => Promise<void>;
}

export function FilesTab({ books, jobs, currentSlug, onSelect, onUpload, onAnalyze, onOcr, onSignOut, onDelete }: FilesTabProps) {
  const [formOpen, setFormOpen] = useState(false);
  const [pending, setPending] = useState<Book | null>(null); // el libro que se está a punto de borrar
  const [deleting, setDeleting] = useState(false);
  const [deleteError, setDeleteError] = useState<string | null>(null);
  const uploading = pendingUploads(jobs, books, Date.now()); // los libros que se están subiendo y todavía no están en el catálogo

  const closeDialog = () => {
    setPending(null);
    setDeleteError(null);
  };
  const confirmDelete = async () => {
    if (!pending || !onDelete) return;
    setDeleting(true);
    setDeleteError(null);
    try {
      await onDelete(pending.slug);
      setPending(null);
    } catch (problem) {
      setDeleteError(problem instanceof Error ? problem.message : 'No se pudo borrar el libro.');
    } finally {
      setDeleting(false);
    }
  };

  return (
    <div className="p-3">
      <button
        onClick={() => setFormOpen((open) => !open)}
        className="mb-3 w-full rounded bg-seal px-3 py-2 text-sm font-semibold text-on-seal hover:bg-seal-dark"
        aria-expanded={formOpen}
      >
        {formOpen ? 'Cancelar' : 'Subir un PDF'}
      </button>

      {formOpen && (
        <UploadForm
          books={books}
          onSubmit={async (file, options) => {
            await onUpload(file, options);
            setFormOpen(false);
          }}
        />
      )}

      {books.length === 0 && uploading.length === 0 ? (
        <p className="py-6 text-center text-sm text-stone">Todavía no hay libros. Sube un PDF para empezar.</p>
      ) : (
        <ul className="space-y-2">
          {uploading.map((job) => (
            <UploadingCard key={job.jobId} job={job} />
          ))}
          {books.map((book) => {
            const analysis = analysisState(book, jobs);
            const ocr = ocrState(book, jobs);
            const scanned = needsOcr(book);
            const current = book.slug === currentSlug;
            return (
              <li key={book.slug} className={`rounded border ${current ? 'border-seal bg-white' : 'border-line bg-white/50'}`}>
                <button onClick={() => onSelect(book.slug)} aria-current={current ? 'true' : undefined} className="flex w-full gap-3 rounded-t p-3 text-left hover:bg-white">
                  <Cover url={book.coverUrl} />
                  <div className="min-w-0 flex-1">
                    <div className="flex items-start justify-between gap-2">
                      <span className="text-sm font-semibold">{book.title}</span>
                      {ocr.status === 'running' ? (
                        <span className="shrink-0 whitespace-nowrap rounded bg-ink/10 px-1.5 py-0.5 text-[11px] font-semibold text-ink">Reconociendo texto…</span>
                      ) : scanned ? (
                        <span className="shrink-0 whitespace-nowrap rounded bg-amber-100 px-1.5 py-0.5 text-[11px] font-semibold text-amber-800">Escaneado</span>
                      ) : (
                        <span className={`shrink-0 whitespace-nowrap rounded px-1.5 py-0.5 text-[11px] font-semibold ${ANALYSIS_STYLE[analysis.status]}`}>{ANALYSIS_LABEL[analysis.status]}</span>
                      )}
                    </div>
                    <div className="mt-1 text-xs text-stone">
                      {book.pageCount > 0 ? `${book.pageCount} págs.` : 'sin datos de páginas'}
                      {analysis.status === 'done' && book.stats && book.stats.hitos > 0 ? ` · ${book.stats.hitos} hitos · ${book.stats.personajes} personajes` : ''}
                    </div>
                    {book.ocr && <p className="mt-1 text-xs text-stone">Texto reconocido con {OCR_ENGINE_NAME[book.ocr.engine]}.</p>}
                    {scanned && ocr.status !== 'running' && <p className="mt-1 text-xs text-stone">Es un escaneado: sin OCR no se puede traducir ni analizar.</p>}
                    {ocr.status === 'failed' && <p className="mt-1 text-xs text-red-700">{ocr.error ?? 'El OCR falló.'}</p>}
                    {!scanned && analysis.error && <p className="mt-1 text-xs text-red-700">{analysis.error}</p>}
                  </div>
                </button>
                <div className="flex items-center justify-between gap-2 border-t border-line/70 px-3 py-1">
                  {ocr.status === 'running' ? (
                    <span className="text-xs text-stone">Reconociendo texto; puedes seguir leyendo.</span>
                  ) : scanned ? (
                    onOcr ? (
                      <button onClick={() => onOcr(book)} className="rounded-md border border-seal/40 px-2.5 py-1 text-xs font-semibold text-seal hover:bg-seal hover:text-on-seal">
                        {ocr.status === 'failed' ? 'Reintentar OCR' : 'Reconocer texto (OCR)'}
                      </button>
                    ) : (
                      <span />
                    )
                  ) : (
                    <span className="flex items-center gap-3">
                      <AnalyzeAction status={analysis.status} onClick={onAnalyze ? () => onAnalyze(book) : undefined} />
                      {book.ocr && onOcr && (
                        <button onClick={() => onOcr(book)} className="text-xs font-semibold text-stone underline decoration-line underline-offset-2 hover:text-ink">
                          Repetir OCR
                        </button>
                      )}
                    </span>
                  )}
                  {onDelete && (
                    <button
                      onClick={() => setPending(book)}
                      aria-label={`Borrar «${book.title}»`}
                      title="Borrar el libro"
                      className="rounded-md p-1.5 text-stone-light hover:bg-paper-dark hover:text-seal-dark"
                    >
                      <TrashIcon />
                    </button>
                  )}
                </div>
              </li>
            );
          })}
        </ul>
      )}

      {pending && (
        <ConfirmDialog title={`¿Borrar «${pending.title}»?`} confirmLabel="Borrar libro" busyLabel="Borrando…" busy={deleting} error={deleteError} onConfirm={confirmDelete} onCancel={closeDialog}>
          <p>Se borrará para siempre todo lo que es solo de este libro:</p>
          <ul className="list-disc space-y-0.5 pl-5">
            <li>el PDF y su portada,</li>
            <li>las traducciones,</li>
            <li>sus hitos y los personajes que solo aparecen en él,</li>
            <li>los datos de su análisis.</li>
          </ul>
          <p className="font-semibold text-ink">No se puede deshacer.</p>
        </ConfirmDialog>
      )}

      {onSignOut && (
        <button onClick={onSignOut} className="mt-6 w-full rounded border border-line bg-white px-3 py-2 text-sm font-semibold text-stone hover:text-ink">
          Cerrar sesión
        </button>
      )}
    </div>
  );
}

/** Un libro que se está subiendo: ya sale en la lista (con su avance) y se convierte solo en la tarjeta normal cuando llega al catálogo. */
function UploadingCard({ job }: { job: Job }) {
  return (
    <li className="flex gap-3 rounded border border-line bg-white/50 p-3" aria-busy="true">
      <Cover />
      <div className="min-w-0 flex-1">
        <div className="flex items-start justify-between gap-2">
          <span className="text-sm font-semibold">{job.title ?? job.slug}</span>
          <span className="shrink-0 whitespace-nowrap rounded bg-ink/10 px-1.5 py-0.5 text-[11px] font-semibold text-ink">Subiendo…</span>
        </div>
        <div role="progressbar" aria-label="Subiendo" aria-valuenow={job.progress === null ? undefined : Math.round(job.progress * 100)} className="mt-2 h-2 overflow-hidden rounded bg-paper-dark">
          <div className={`h-full bg-seal ${job.progress === null ? 'w-1/3 animate-pulse' : 'transition-all'}`} style={job.progress === null ? undefined : { width: formatPercent(job.progress) }} />
        </div>
      </div>
    </li>
  );
}

/** La acción de análisis de la tarjeta: analizar (sin analizar o tras un fallo), volver a analizar (ya analizado) o nada (en marcha, o un PDF que necesita OCR). */
function AnalyzeAction({ status, onClick }: { status: AnalysisStatus; onClick?: () => void }) {
  if (status === 'running') return <span className="text-xs text-stone">Analizando; puedes seguir leyendo.</span>;
  if (status === 'needs_ocr') return <span />; // el motivo ya está en la tarjeta, y analizar otra vez daría lo mismo
  if (!onClick) return <span />;
  if (status === 'done') {
    return (
      <button onClick={onClick} className="text-xs font-semibold text-stone underline decoration-line underline-offset-2 hover:text-ink">
        Volver a analizar
      </button>
    );
  }
  return (
    <button onClick={onClick} className="rounded-md border border-seal/40 px-2.5 py-1 text-xs font-semibold text-seal hover:bg-seal hover:text-on-seal">
      {status === 'failed' ? 'Reintentar análisis' : 'Analizar'}
    </button>
  );
}

interface UploadFormProps {
  books: Book[];
  onSubmit: (file: File, options: { slug: string; title: string; replaceSource: boolean }) => Promise<void>;
}

function UploadForm({ books, onSubmit }: UploadFormProps) {
  const [file, setFile] = useState<File | null>(null);
  const [title, setTitle] = useState('');
  const [slug, setSlug] = useState('');
  const [replaceSource, setReplaceSource] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const exists = books.some((book) => book.slug === slug);

  const pick = (picked: File | undefined) => {
    if (!picked) return;
    setFile(picked);
    setError(validatePdf(picked));
    setTitle(suggestTitle(picked.name));
    setSlug(suggestSlug(picked.name));
    setReplaceSource(false);
  };

  const slugError = slug && !isValidSlug(slug) ? 'Entre 2 y 60 caracteres: minúsculas, números y guiones.' : null;
  const canSubmit = !!file && !error && !slugError && !!title.trim() && (!exists || replaceSource) && !busy;

  return (
    <form
      className="mb-4 space-y-3 rounded border border-line bg-white p-3"
      onSubmit={async (event) => {
        event.preventDefault();
        if (!file || !canSubmit) return;
        setBusy(true);
        try {
          await onSubmit(file, { slug, title: title.trim(), replaceSource });
        } catch (problem) {
          setError(problem instanceof Error ? problem.message : 'No se pudo subir el PDF.');
          setBusy(false);
        }
      }}
    >
      <label className="block text-sm font-semibold">
        PDF <span className="font-normal text-stone">(máx. {MAX_PDF_MB} MB)</span>
        <input type="file" accept="application/pdf" className="mt-1 block w-full text-sm font-normal" onChange={(event) => pick(event.target.files?.[0])} />
      </label>
      {file && (
        <>
          <label className="block text-sm font-semibold">
            Título
            <input value={title} onChange={(event) => setTitle(event.target.value)} className="mt-1 block w-full rounded border border-line px-2 py-1 text-sm font-normal" />
          </label>
          <label className="block text-sm font-semibold">
            Identificador
            <input value={slug} onChange={(event) => setSlug(event.target.value)} className="mt-1 block w-full rounded border border-line px-2 py-1 font-mono text-sm font-normal" />
            {slugError && <span className="text-xs font-normal text-red-700">{slugError}</span>}
          </label>
          {exists && (
            <label className="flex items-start gap-2 text-sm">
              <input type="checkbox" checked={replaceSource} onChange={(event) => setReplaceSource(event.target.checked)} className="mt-1" />
              <span>Ya existe un libro con ese identificador. Reemplazar su PDF: se borrarán su análisis, sus hitos y sus traducciones.</span>
            </label>
          )}
        </>
      )}
      {error && <p role="alert" className="text-sm text-red-700">{error}</p>}
      <p className="text-xs text-stone">Subir no analiza el libro: podrás leerlo y traducirlo enseguida, y analizarlo después si quieres.</p>
      <button disabled={!canSubmit} className="w-full rounded bg-ink px-3 py-2 text-sm font-semibold text-white disabled:opacity-40">
        {busy ? 'Subiendo…' : 'Subir'}
      </button>
    </form>
  );
}
