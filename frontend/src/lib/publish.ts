import type { Book, Job } from '../types';

/** Lo que pasa con la publicación de un libro en producción (la base que lee china-history). */
export type PublishStatus = 'none' | 'running' | 'published' | 'outdated';

export interface PublishState {
  status: PublishStatus;
  /** Si `running`: true = es la vista previa (no escribe nada). */
  preview?: boolean;
}

/**
 * El estado de publicación de un libro: en marcha si hay un trabajo de publicación corriendo (la vista previa también lo es: bloquea el libro igual); si no, publicado
 * ("outdated" si el libro cambió DESPUÉS de publicarlo, p. ej. se volvió a analizar: lo publicado ya no es lo que hay en dev) o sin publicar.
 */
export function publishState(book: Book, jobs: Job[]): PublishState {
  const running = jobs.find((job) => job.slug === book.slug && job.kind === 'publish' && (job.status === 'RUNNING' || job.status === 'QUEUED'));
  if (running) return { status: 'running', preview: running.dryRun };
  if (!book.published) return { status: 'none' };
  return { status: book.updatedAt && book.updatedAt > book.published.at ? 'outdated' : 'published' };
}
