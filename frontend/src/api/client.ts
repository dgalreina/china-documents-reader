import type { Book, ChatMessage, Hito, Job, PageTranslation, Personaje } from '../types';

export interface UploadOptions {
  /** Identificador del libro (el `source` de MongoDB): minúsculas, números y guiones. */
  slug: string;
  title: string;
  /** Si el libro ya existe: reprocesarlo y reemplazar lo anterior. */
  replaceSource: boolean;
}

/** Qué partes de la interfaz tienen detrás datos reales. Lo que no, no se enseña (en vez de enseñar pestañas vacías o inventadas). */
export interface ApiFeatures {
  /** Pestañas Hitos y Personajes. */
  hitos: boolean;
  /** Pestaña Agente. */
  agent: boolean;
}

/**
 * Todo lo que el front necesita del backend. La implementación real es `HttpApi` (API Gateway con el token del login); la API simulada
 * (`src/test/mockApi.ts`) solo existe para los tests y NO forma parte de la web publicada.
 */
export interface ApiClient {
  readonly features: ApiFeatures;
  listBooks(): Promise<Book[]>;
  listJobs(): Promise<Job[]>;
  getHitos(slug: string): Promise<Hito[]>;
  getPersonajes(slug: string): Promise<Personaje[]>;
  /** URL temporal (firmada) desde la que el visor abre el PDF del libro. */
  getPdfUrl(slug: string): Promise<string>;
  /** Valida, sube el PDF y arranca el análisis. Rechaza con un mensaje en español si algo no es válido. */
  startUpload(file: File, options: UploadOptions): Promise<{ jobId: string }>;
  /** Pregunta al agente. `slug` null = preguntar a todos los libros. */
  ask(slug: string | null, question: string): Promise<ChatMessage>;
  /** Traduce una página al español (la primera vez tarda unos segundos; después sale de la caché). Rechaza con un mensaje en español. */
  getTranslation(slug: string, page: number): Promise<PageTranslation>;
  /** Borra el libro y TODO lo que es solo suyo (PDF, portada, traducciones, hitos, personajes, análisis). Rechaza con un mensaje en español, p. ej. si aún se está analizando. */
  deleteBook(slug: string): Promise<void>;
  /** Avisa cuando cambian libros o trabajos (progreso, cola...). Devuelve la función para dejar de escuchar. */
  subscribe(listener: () => void): () => void;
}
