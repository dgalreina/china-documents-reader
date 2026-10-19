import type { Book, Job } from '../types';

/** Lo que está pasando con el análisis de un libro. */
export type AnalysisStatus = 'none' | 'running' | 'done' | 'failed' | 'needs_ocr';

export interface AnalysisState {
  status: AnalysisStatus;
  /** El motivo, si el último intento falló. */
  error?: string;
}

/**
 * El estado del análisis de un libro. El libro solo sabe si está analizado o no; lo que ocurre AHORA se deduce de sus trabajos:
 *  - hay un análisis en marcha -> "running" (aunque el libro ya estuviera analizado: es un reanálisis);
 *  - analizado -> "done" (un reanálisis que falló no estropea el análisis anterior: sigue ahí);
 *  - sin analizar: si el último intento falló, se dice por qué (hasta que el registro caduca a los 30 días); si no, "none".
 * Los trabajos de SUBIDA no cuentan: son otra cosa.
 */
export function analysisState(book: Book, jobs: Job[]): AnalysisState {
  const attempts = jobs.filter((job) => job.slug === book.slug && job.kind !== 'upload'); // sin `kind` = trabajos antiguos, que eran análisis
  if (attempts.some((job) => job.status === 'RUNNING' || job.status === 'QUEUED')) return { status: 'running' };
  if (book.analysis === 'done') return { status: 'done' };
  const last = [...attempts].sort((a, b) => b.createdAt.localeCompare(a.createdAt))[0];
  if (last?.status === 'NEEDS_OCR') return { status: 'needs_ocr', error: last.error };
  if (last?.status === 'FAILED') return { status: 'failed', error: last.error };
  return { status: 'none' };
}

/** Lo medido en las pruebas con el Oxford: unos 0,01 USD por página (etiquetado, embeddings, hitos y personajes). Es una ESTIMACIÓN: depende de cuánto texto haya. */
export const ESTIMATED_COST_PER_PAGE_USD = 0.01;

export function estimateAnalysisCost(pageCount: number): number {
  return Math.round(Math.max(0, pageCount) * ESTIMATED_COST_PER_PAGE_USD * 100) / 100;
}
