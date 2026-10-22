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
  /** "none": subido pero sin analizar (ya se puede leer y traducir); "done": analizado (tiene hitos y personajes). Lo que está pasando AHORA (analizando, falló...) sale de los trabajos. */
  analysis: 'none' | 'done';
  createdAt: string;
  /** Cambia cuando el libro cambia (p. ej. al terminar un análisis): sirve para volver a pedir sus hitos. */
  updatedAt?: string;
  stats?: BookStats;
  /** URL temporal de la portada (la primera página). Sin ella, la lista enseña un recambio. */
  coverUrl?: string;
  /** false = el PDF es un escaneado (solo imágenes, sin texto): no se puede traducir ni analizar hasta hacerle OCR. Sin el campo (libros antiguos) se da por bueno. */
  textLayer?: boolean;
  /** El OCR que se le hizo, si se le hizo. */
  ocr?: BookOcr;
  /** Si el libro está publicado en producción (la base que lee china-history): cuándo y cuánto. Sin el campo = no publicado. */
  published?: BookPublished;
}

export interface BookPublished {
  at: string;
  chunks: number;
  hitos: number;
}

export type OcrEngine = 'textract' | 'openai';

export interface BookOcr {
  engine: OcrEngine;
  pages: number;
  /** Cuántas páginas salieron con texto (las demás eran imágenes o estaban en blanco). */
  pagesWithText?: number;
  /** Cuándo se hizo: cambia al repetirlo (sirve para volver a pedir el texto y las traducciones). */
  at?: string;
}

/** "upload": la subida del PDF y su preparación; "analysis": el análisis (hitos, personajes), que se pide con el botón Analizar; "ocr": reconocer el texto de un escaneado. */
export type JobKind = 'upload' | 'analysis' | 'ocr' | 'publish';

export interface Job {
  jobId: string;
  kind?: JobKind;
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
  /** Solo en trabajos de publicación: true = era la vista previa (no escribió nada). */
  dryRun?: boolean;
  /** Solo en trabajos de publicación terminados: lo que se publicó (o publicaría, en la vista previa). */
  publish?: PublishSummary;
  createdAt: string;
  finishedAt?: string;
}

/** El resultado de publicar un libro en producción (o de su vista previa). */
export interface PublishSummary {
  chunks: number;
  chunksReplaced: number;
  hitos: number;
  hitosReplaced: number;
  /** Hitos que ya existían en producción (con otras palabras) y por eso no se publican. */
  duplicates: PublishDuplicate[];
  duplicateCount: number;
  /** Lo que ya había publicado de este libro antes. */
  before: { chunks: number; hitos: number };
}

export interface PublishDuplicate {
  titulo: string;
  año: number;
  existente?: string;
  fuente?: string;
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
  /** El libro (su `slug`) del que sale. Solo en la lista de todos los libros. */
  fuente?: string;
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
  /** Los libros (sus `slug`) en que sale. Solo en la lista de todos los libros. */
  fuentes?: string[];
}

export interface Fuente {
  pagina: number;
  texto: string;
  /** El libro del que sale la cita (el agente lo manda siempre; en la API simulada puede faltar: se entiende el libro abierto). */
  slug?: string;
}

export interface ChatMessage {
  id: string;
  role: 'user' | 'assistant';
  text: string;
  /** De dónde sale la respuesta: del libro (con citas) o del conocimiento general del modelo (no está en el libro). */
  origen?: 'libro' | 'general';
  fuentes?: Fuente[];
  /** La respuesta se está escribiendo todavía (llega por trozos). */
  streaming?: boolean;
  /** Falló (o se cortó): el texto es el aviso y se puede reintentar. */
  failed?: boolean;
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
