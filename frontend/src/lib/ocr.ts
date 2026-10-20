import type { Book, Job, OcrEngine } from '../types';

/** USD por página. Textract: precio de lista (~1,5 $ por 1.000 págs.). OpenAI: estimación con gpt-4o y visión. Son ESTIMACIONES para avisar antes de empezar. */
export const OCR_COST_PER_PAGE_USD: Record<OcrEngine, number> = { textract: 0.0015, openai: 0.013 };

export const OCR_ENGINE_NAME: Record<OcrEngine, string> = { textract: 'Amazon Textract', openai: 'OpenAI (visión)' };

export function estimateOcrCost(engine: OcrEngine, pageCount: number): number {
  return Math.round(Math.max(0, pageCount) * OCR_COST_PER_PAGE_USD[engine] * 100) / 100;
}

/** ¿Es un escaneado sin texto al que aún no se le ha hecho OCR? Los libros antiguos (sin el dato) se dan por buenos. */
export function needsOcr(book: Book): boolean {
  return book.textLayer === false && !book.ocr;
}

export type OcrStatus = 'none' | 'running' | 'done' | 'failed';

export interface OcrState {
  status: OcrStatus;
  error?: string;
}

/** Lo que pasa con el OCR de un libro: hecho (lo dice el libro), en marcha o fallido (lo dicen sus trabajos). */
export function ocrState(book: Book, jobs: Job[]): OcrState {
  const attempts = jobs.filter((job) => job.slug === book.slug && job.kind === 'ocr');
  if (attempts.some((job) => job.status === 'RUNNING' || job.status === 'QUEUED')) return { status: 'running' };
  if (book.ocr) return { status: 'done' };
  const last = [...attempts].sort((a, b) => b.createdAt.localeCompare(a.createdAt))[0];
  if (last?.status === 'FAILED') return { status: 'failed', error: last.error };
  return { status: 'none' };
}
