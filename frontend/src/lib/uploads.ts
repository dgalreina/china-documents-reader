import type { Book, Job } from '../types';

const IN_PROGRESS = new Set(['UPLOADING', 'QUEUED', 'RUNNING']);

/** Cuánto tiempo sigue enseñándose una subida terminada ("Listo, ya está en Archivos") antes de quitarla de Progreso. */
export const RECENT_UPLOAD_MS = 30_000;

/** ¿Terminó hace poco? (`now` en milisegundos). */
export function finishedRecently(job: Job, now: number, windowMs = RECENT_UPLOAD_MS): boolean {
  if (!job.finishedAt) return false;
  const finished = Date.parse(job.finishedAt);
  return Number.isFinite(finished) && now - finished < windowMs;
}

/**
 * Las subidas que aún no son un libro del catálogo: en marcha, o recién terminadas pero con la lista de libros sin refrescar todavía. Sin esto, entre el final
 * de la subida y la aparición del libro en Archivos, el libro no estaba en ninguna pestaña y parecía perdido.
 */
export function pendingUploads(jobs: Job[], books: Book[], now: number): Job[] {
  return jobs.filter(
    (job) => job.kind === 'upload' && !books.some((book) => book.slug === job.slug) && (IN_PROGRESS.has(job.status) || (job.status === 'DONE' && finishedRecently(job, now))),
  );
}
