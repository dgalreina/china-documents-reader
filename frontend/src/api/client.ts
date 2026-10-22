import type { Book, Fuente, Hito, Job, OcrEngine, PageTranslation, Personaje } from '../types';

/** Un turno de la conversación, tal como se manda al agente (solo texto: nunca ids ni citas). */
export interface AskTurn {
  role: 'user' | 'assistant';
  text: string;
}

export interface AskRequest {
  /** El libro por el que se pregunta; null = todos. */
  slug: string | null;
  question: string;
  /** Los últimos turnos de la conversación (el servidor se queda con los últimos 6). */
  history: AskTurn[];
}

/** Lo que va llegando mientras el agente responde (en este orden: citas, trozos de texto, fin). */
export type AskEvent =
  | { type: 'fuentes'; fuentes: Fuente[] }
  | { type: 'delta'; text: string }
  | { type: 'fin'; origen: 'libro' | 'ninguno' }
  | { type: 'error'; message: string };

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
  /** Los hitos de TODOS los libros, cada uno con su `fuente` (el libro). Una sola petición. */
  getAllHitos(): Promise<Hito[]>;
  /** Los personajes de TODOS los libros, cada uno con sus `fuentes`. */
  getAllPersonajes(): Promise<Personaje[]>;
  /** URL temporal (firmada) desde la que el visor abre el PDF del libro. */
  getPdfUrl(slug: string): Promise<string>;
  /** Valida, sube el PDF y arranca el análisis. Rechaza con un mensaje en español si algo no es válido. */
  startUpload(file: File, options: UploadOptions): Promise<{ jobId: string }>;
  /**
   * Pregunta al agente. La respuesta llega EN FLUJO: `onEvent` se llama con las citas, luego con cada trozo de texto y al final con `fin` (o `error` si falla a mitad).
   * Rechaza con un mensaje en español si ni siquiera empieza (sin sesión, sin conexión, pregunta inválida). `signal` permite detenerla (rechaza con un AbortError).
   */
  ask(request: AskRequest, onEvent: (event: AskEvent) => void, signal?: AbortSignal): Promise<void>;
  /** Traduce una página al español (la primera vez tarda unos segundos; después sale de la caché). Rechaza con un mensaje en español. */
  getTranslation(slug: string, page: number): Promise<PageTranslation>;
  /** Arranca el análisis (hitos y personajes) de un libro ya subido. Cuesta dinero (OpenAI), por eso no arranca solo al subir. Rechaza con un mensaje en español, p. ej. si ya se está analizando. */
  analyzeBook(slug: string): Promise<{ jobId: string }>;
  /** Reconoce el texto de un libro escaneado (OCR) con el motor elegido. Cuesta dinero: la web enseña antes una estimación. Rechaza con un mensaje en español. */
  startOcr(slug: string, engine: OcrEngine): Promise<{ jobId: string; estimatedCostUsd: number }>;
  /** El texto OCR del libro, una cadena por página (índice 0 = página 1). `stamp` cambia cuando se repite el OCR. null si el libro no tiene OCR. */
  getOcrPages(slug: string, stamp?: string): Promise<string[] | null>;
  /** Borra el libro y TODO lo que es solo suyo (PDF, portada, traducciones, hitos, personajes, análisis). Con `unpublish`, también quita su copia de producción. Rechaza con un mensaje en español, p. ej. si aún se está analizando. */
  deleteBook(slug: string, options?: { unpublish?: boolean }): Promise<void>;
  /** Publicación en producción. `previewPublish` NO escribe nada: calcula lo que pasaría (llega como un trabajo con `publish`). `publishBook` publica de verdad. `unpublishBook` quita el libro de producción. Rechazan con un mensaje en español. */
  previewPublish(slug: string): Promise<{ jobId: string }>;
  publishBook(slug: string): Promise<{ jobId: string }>;
  unpublishBook(slug: string): Promise<void>;
  /** Avisa cuando cambian libros o trabajos (progreso, cola...). Devuelve la función para dejar de escuchar. */
  subscribe(listener: () => void): () => void;
}
