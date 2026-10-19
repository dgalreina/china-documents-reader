// Tipos compartidos por toda la app. Reflejan lo que devuelve la API real (ver backend/lambdas/books, jobs y store_book),
// de modo que cambiar de la API simulada a la real no obligue a tocar las pantallas.

/** Estado de un trabajo. QUEUED y UPLOADING se ven en la interfaz; el backend real solo tiene RUNNING, DONE, FAILED y NEEDS_OCR (+ UPLOADING). */
export type JobStatus = 'UPLOADING' | 'QUEUED' | 'RUNNING' | 'DONE' | 'FAILED' | 'NEEDS_OCR';

export interface BookStats {
  chunks: number;
  chunksListos: number;
  hitos: number;
  personajes: number;
  costeEstimadoUsd: number;
}

/** Un libro de la biblioteca. `slug` es su identificador (el `source` de MongoDB). */
export interface Book {
  slug: string;
  title: string;
  filename: string;
  pageCount: number;
  status: JobStatus;
  createdAt: string;
  stats?: BookStats;
}

export interface Job {
  jobId: string;
  slug: string;
  /** Título que se puso al subir (puede faltar en trabajos antiguos). */
  title?: string;
  filename: string;
  status: JobStatus;
  /** Texto de la fase actual, p. ej. "Etiquetando y calculando embeddings". */
  phase: string;
  /** Avance de la fase actual, p. ej. "8 de 22 tandas". */
  detail?: string;
  /** 0 a 1: avance global del trabajo (o de la subida mientras está en UPLOADING). null = no se sabe (el backend aún no informa del avance del análisis). */
  progress: number | null;
  /** Posición en la cola si está en QUEUED (1 = el siguiente). */
  queuePosition?: number;
  error?: string;
  stats?: BookStats;
  createdAt: string;
  finishedAt?: string;
}

export interface PersonajeRef {
  id: string;
  nombre: string;
  rol: string;
}

export interface Hito {
  id: string;
  titulo: string;
  descripcion: string;
  año: number;
  lugar: string;
  /** Página del PDF en la que se cuenta. Puede faltar (los hitos antiguos no la guardan). */
  pagina?: number;
  personajes: PersonajeRef[];
}

export interface Personaje {
  id: string;
  nombre: string;
  nombreZh?: string;
  rol: string;
  estado?: string;
  descripcion: string;
  nacimiento?: number;
  muerte?: number;
  hitoIds: string[];
}

export interface Fuente {
  pagina: number;
  texto: string;
}

export interface ChatMessage {
  id: string;
  role: 'user' | 'assistant';
  text: string;
  /** De dónde sale la respuesta: del libro (con citas) o del conocimiento general del modelo (no está en el libro). */
  origen?: 'libro' | 'general';
  fuentes?: Fuente[];
}

export type TabId = 'archivos' | 'progreso' | 'hitos' | 'personajes' | 'agente';

/** Petición de saltar a una página. `nonce` cambia en cada petición; `instant`: sin animación (al cambiar de modo de lectura). */
export interface JumpTarget {
  page: number;
  nonce: number;
  instant?: boolean;
}

export type ReadMode = 'original' | 'es';

/** Una página traducida al español. `cached`: ya estaba guardada (llega al instante y no cuesta nada). */
export interface PageTranslation {
  page: number;
  text: string;
  cached: boolean;
}
