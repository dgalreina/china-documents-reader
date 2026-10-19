import { useState } from 'react';
import { MAX_PDF_MB, isValidSlug, suggestSlug, suggestTitle, validatePdf } from '../../lib/files';
import type { Book, JobStatus } from '../../types';
import { ConfirmDialog } from '../ConfirmDialog';
import { Cover } from '../Cover';
import { TrashIcon } from '../icons';

const STATUS_LABEL: Record<JobStatus, string> = {
  UPLOADING: 'Subiendo',
  QUEUED: 'En cola',
  RUNNING: 'Analizando',
  DONE: 'Listo',
  FAILED: 'Error',
  NEEDS_OCR: 'Necesita OCR',
};

const STATUS_STYLE: Record<JobStatus, string> = {
  UPLOADING: 'bg-paper-dark text-ink-soft',
  QUEUED: 'bg-paper-dark text-ink-soft',
  RUNNING: 'bg-ink/10 text-ink',
  DONE: 'bg-ink text-paper',
  FAILED: 'bg-red-100 text-red-800',
  NEEDS_OCR: 'bg-amber-100 text-amber-800',
};

interface FilesTabProps {
  books: Book[];
  currentSlug?: string;
  onSelect: (slug: string) => void;
  onUpload: (file: File, options: { slug: string; title: string; replaceSource: boolean }) => Promise<void>;
  /** Cierra la sesión. */
  onSignOut?: () => void;
  /** Borra un libro con todo lo suyo. Rechaza con un mensaje en español. */
  onDelete?: (slug: string) => Promise<void>;
}

export function FilesTab({ books, currentSlug, onSelect, onUpload, onSignOut, onDelete }: FilesTabProps) {
  const [formOpen, setFormOpen] = useState(false);
  const [pending, setPending] = useState<Book | null>(null); // el libro que se está a punto de borrar
  const [deleting, setDeleting] = useState(false);
  const [deleteError, setDeleteError] = useState<string | null>(null);

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

      {books.length === 0 ? (
        <p className="py-6 text-center text-sm text-stone">Todavía no hay libros. Sube un PDF para empezar.</p>
      ) : (
        <ul className="space-y-2">
          {books.map((book) => (
            <li key={book.slug} className="relative">
              <button
                onClick={() => onSelect(book.slug)}
                aria-current={book.slug === currentSlug ? 'true' : undefined}
                className={`flex w-full gap-3 rounded border p-3 text-left ${
                  book.slug === currentSlug ? 'border-seal bg-white' : 'border-line bg-white/50 hover:bg-white'
                }`}
              >
                <Cover url={book.coverUrl} />
                <div className="min-w-0 flex-1">
                  <div className="flex items-start justify-between gap-2">
                    <span className="text-sm font-semibold">{book.title}</span>
                    <span className={`shrink-0 rounded px-1.5 py-0.5 text-[11px] font-semibold ${STATUS_STYLE[book.status]}`}>{STATUS_LABEL[book.status]}</span>
                  </div>
                  <div className="mt-1 text-xs text-stone">
                    {book.pageCount > 0 ? `${book.pageCount} págs.` : 'sin datos de páginas'}
                    {book.stats && book.stats.hitos > 0 ? ` · ${book.stats.hitos} hitos · ${book.stats.personajes} personajes` : ''}
                  </div>
                </div>
              </button>
              {onDelete && (
                <button
                  onClick={() => setPending(book)}
                  aria-label={`Borrar «${book.title}»`}
                  title="Borrar el libro"
                  className="absolute bottom-1.5 right-1.5 rounded-md p-1.5 text-stone-light hover:bg-paper-dark hover:text-seal-dark"
                >
                  <TrashIcon />
                </button>
              )}
            </li>
          ))}
        </ul>
      )}

      {pending && (
        <ConfirmDialog title={`¿Borrar «${pending.title}»?`} confirmLabel="Borrar libro" busy={deleting} error={deleteError} onConfirm={confirmDelete} onCancel={closeDialog}>
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
              <span>Ya existe un libro con ese identificador. Reprocesarlo y reemplazar lo anterior.</span>
            </label>
          )}
        </>
      )}
      {error && <p role="alert" className="text-sm text-red-700">{error}</p>}
      <button disabled={!canSubmit} className="w-full rounded bg-ink px-3 py-2 text-sm font-semibold text-white disabled:opacity-40">
        {busy ? 'Subiendo…' : 'Subir y analizar'}
      </button>
    </form>
  );
}
