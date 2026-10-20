import { useState } from 'react';
import { OCR_ENGINE_NAME, estimateOcrCost } from '../lib/ocr';
import { formatUsd } from '../lib/format';
import type { Book, OcrEngine } from '../types';
import { ConfirmDialog } from './ConfirmDialog';

interface OcrDialogProps {
  book: Book;
  /** Arranca el OCR con el motor elegido. Rechaza con un mensaje en español. */
  onConfirm: (slug: string, engine: OcrEngine) => Promise<void>;
  onClose: () => void;
}

const OPTIONS: { engine: OcrEngine; summary: string }[] = [
  { engine: 'textract', summary: 'Para libros en inglés (o español, francés, alemán, italiano, portugués). Rápido y barato. No lee chino.' },
  { engine: 'openai', summary: 'Para libros con mucho chino o caracteres antiguos. Lee chino, pero cuesta unas 9 veces más y tarda más.' },
];

/**
 * Pide confirmar el OCR de un libro escaneado ANTES de arrancarlo, porque cuesta dinero, y deja elegir el motor: Amazon Textract por defecto (barato) u OpenAI cuando el libro es
 * sobre todo chino. Un escaneado es una foto por página: sin OCR no hay texto que traducir, leer en inglés ni analizar.
 */
export function OcrDialog({ book, onConfirm, onClose }: OcrDialogProps) {
  const [engine, setEngine] = useState<OcrEngine>(book.ocr?.engine === 'textract' ? 'openai' : 'textract'); // si ya se hizo con Amazon, lo normal al repetir es probar con OpenAI
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const again = Boolean(book.ocr);

  const confirm = async () => {
    setBusy(true);
    setError(null);
    try {
      await onConfirm(book.slug, engine);
      onClose();
    } catch (problem) {
      setError(problem instanceof Error ? problem.message : 'No se pudo arrancar el OCR.');
      setBusy(false);
    }
  };

  return (
    <ConfirmDialog
      title={`${again ? 'Repetir el OCR de' : 'Reconocer el texto de'} «${book.title}»`}
      confirmLabel={again ? 'Repetir el OCR' : 'Reconocer texto'}
      busyLabel="Arrancando…"
      busy={busy}
      error={error}
      onConfirm={confirm}
      onCancel={onClose}
    >
      <p>
        Este PDF es un escaneado: cada página es una imagen, sin texto. El OCR lee esas imágenes y saca el texto, que sirve para <span className="font-semibold text-ink">traducir, leer en inglés y analizar</span> el libro.
      </p>
      <fieldset className="space-y-2" disabled={busy}>
        <legend className="sr-only">Motor de OCR</legend>
        {OPTIONS.map((option) => (
          <label key={option.engine} className={`block cursor-pointer rounded border p-2.5 ${engine === option.engine ? 'border-seal bg-white ring-1 ring-seal' : 'border-line bg-white/60'}`}>
            <span className="flex items-center gap-2">
              <input type="radio" name="ocr-engine" value={option.engine} checked={engine === option.engine} onChange={() => setEngine(option.engine)} className="accent-[var(--color-seal)]" />
              <span className="font-semibold text-ink">{OCR_ENGINE_NAME[option.engine]}</span>
              <span className="ml-auto whitespace-nowrap text-xs font-semibold text-ink">
                {book.pageCount > 0 ? `≈ ${formatUsd(estimateOcrCost(option.engine, book.pageCount))}` : ''}
              </span>
            </span>
            <span className="mt-1 block text-xs text-stone">{option.summary}</span>
          </label>
        ))}
      </fieldset>
      {book.pageCount > 0 && <p className="text-xs text-stone">Estimación para {book.pageCount} páginas. Tarda unos minutos y mientras tanto puedes seguir leyendo.</p>}
      {again && <p>Se reemplazará el texto reconocido antes y se borrarán las traducciones ya hechas de este libro (se harán de nuevo con el texto nuevo).</p>}
    </ConfirmDialog>
  );
}
