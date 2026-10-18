import type { Book, ChatMessage, Hito, Job, Personaje } from '../types';

export interface UploadOptions {
  /** Identificador del libro (el `source` de MongoDB): minúsculas, números y guiones. */
  slug: string;
  title: string;
  /** Si el libro ya existe: reprocesarlo y reemplazar lo anterior. */
  replaceSource: boolean;
}

/**
 * Todo lo que el front necesita del backend. Hay dos implementaciones: `MockApi` (datos simulados, para construir y probar la interfaz sin AWS)
 * y, más adelante, una real que llamará a API Gateway con el token del login (que se enchufará aquí, en una sola función `getToken`).
 */
export interface ApiClient {
  listBooks(): Promise<Book[]>;
  listJobs(): Promise<Job[]>;
  getHitos(slug: string): Promise<Hito[]>;
  getPersonajes(slug: string): Promise<Personaje[]>;
  /** Valida, sube el PDF y arranca el análisis. Rechaza con un mensaje en español si algo no es válido. */
  startUpload(file: File, options: UploadOptions): Promise<{ jobId: string }>;
  /** Pregunta al agente. `slug` null = preguntar a todos los libros. */
  ask(slug: string | null, question: string): Promise<ChatMessage>;
  /** Avisa cuando cambian libros o trabajos (progreso, cola...). Devuelve la función para dejar de escuchar. */
  subscribe(listener: () => void): () => void;
}
