import { isValidSlug, validatePdf } from '../lib/files';
import type { Book, BookStats, ChatMessage, Hito, Job, JobStatus, PageTranslation, Personaje, PersonajeRef } from '../types';
import type { ApiClient, ApiFeatures, UploadOptions } from './client';
import { uploadToS3, type PresignedPost, type UploadFn } from './s3Upload';

/** Un fallo de la API con un mensaje que se puede enseñar tal cual al usuario (en español). */
export class ApiError extends Error {
  constructor(
    message: string,
    readonly status?: number,
  ) {
    super(message);
    this.name = 'ApiError';
  }
}

// ------------------------------------------------------------------------------------------------ formas que devuelve el servidor
interface ServerStats {
  chunks?: number;
  chunksListos?: number;
  hitos?: number;
  personajes?: number;
  costeEstimadoUsd?: number;
}

interface ServerBook {
  slug: string;
  title?: string;
  filename?: string;
  pageCount?: number;
  createdAt?: string;
  stats?: ServerStats;
  coverUrl?: string | null;
}

interface ServerJob {
  jobId: string;
  status: string;
  filename?: string;
  source: string;
  title?: string;
  createdAt?: string;
  finishedAt?: string;
  error?: string;
  coverUrl?: string | null;
  /** El resumen que escribe Finalize al terminar (ver backend/lambdas/write_mongo/handler.py). */
  summary?: {
    chunks?: number;
    chunksListos?: number;
    hitosEscritos?: number;
    personajes?: Record<string, number>;
    costeEstimadoUsd?: number;
  } | null;
}

interface ServerHito {
  id: string;
  titulo?: string;
  descripcion?: string;
  año?: number;
  lugar?: string;
  pagina?: number | null;
  personajes?: { id: string; nombre?: string; rol?: string }[];
}

interface ServerPersonaje {
  id: string;
  nombre?: string;
  nombre_zh?: string | null;
  rol?: string;
  estado?: string | null;
  descripcion?: string;
  nacimiento?: number | null;
  muerte?: number | null;
  hitoIds?: string[];
}

/** Estado de una subida que ESTE navegador está haciendo (el servidor no sabe cuántos bytes han llegado). */
interface LocalUpload {
  progress: number;
  error?: string;
}

const STATUSES = new Set<JobStatus>(['UPLOADING', 'QUEUED', 'RUNNING', 'DONE', 'FAILED', 'NEEDS_OCR']);
const ACTIVE = new Set<JobStatus>(['UPLOADING', 'QUEUED', 'RUNNING']);

const PHASE: Record<JobStatus, string> = {
  UPLOADING: 'Subiendo el PDF',
  QUEUED: 'En cola',
  RUNNING: 'Analizando el libro',
  DONE: 'Terminado',
  FAILED: 'Ha fallado',
  NEEDS_OCR: 'Necesita OCR',
};

/** El permiso de subida dura 5 minutos: una subida "en curso" que lleva más de esto sin llegar a S3 no va a llegar nunca. */
const UPLOAD_GRACE_MS = 15 * 60_000;
const POLL_MS = 5000;
const PROGRESS_NOTIFY_MS = 300;

// ---------------------------------------------------------------------------------------------------------------- conversiones
const num = (value: unknown): number => (typeof value === 'number' && Number.isFinite(value) ? value : 0);

export function mapBook(book: ServerBook): Book {
  const stats = book.stats;
  return {
    slug: book.slug,
    title: book.title || book.slug,
    filename: book.filename ?? '',
    pageCount: num(book.pageCount),
    status: 'DONE', // el catálogo solo contiene libros terminados
    createdAt: book.createdAt ?? '',
    coverUrl: book.coverUrl || undefined,
    stats: stats && {
      chunks: num(stats.chunks),
      chunksListos: num(stats.chunksListos),
      hitos: num(stats.hitos),
      personajes: num(stats.personajes),
      costeEstimadoUsd: num(stats.costeEstimadoUsd),
    },
  };
}

/** El servidor manda `null` en lo que no se sabe; la interfaz espera que falte (undefined). */
const optional = <T>(value: T | null | undefined): T | undefined => (value === null || value === undefined || (typeof value === 'string' && value.trim() === '') ? undefined : value);

export function mapHito(hito: ServerHito): Hito {
  const pagina = typeof hito.pagina === 'number' && hito.pagina >= 1 ? hito.pagina : undefined;
  return {
    id: hito.id,
    titulo: hito.titulo ?? '',
    descripcion: hito.descripcion ?? '',
    año: num(hito.año),
    lugar: hito.lugar ?? '',
    pagina,
    personajes: (hito.personajes ?? []).map((person): PersonajeRef => ({ id: person.id, nombre: person.nombre ?? '', rol: person.rol ?? '' })),
  };
}

export function mapPersonaje(person: ServerPersonaje): Personaje {
  return {
    id: person.id,
    nombre: person.nombre ?? '',
    nombreZh: optional(person.nombre_zh),
    rol: person.rol ?? '',
    estado: optional(person.estado),
    descripcion: person.descripcion ?? '',
    nacimiento: optional(person.nacimiento),
    muerte: optional(person.muerte),
    hitoIds: person.hitoIds ?? [],
  };
}

function summaryStats(summary: ServerJob['summary']): BookStats | undefined {
  if (!summary) return undefined;
  return {
    chunks: num(summary.chunks),
    chunksListos: num(summary.chunksListos),
    hitos: num(summary.hitosEscritos),
    personajes: Object.keys(summary.personajes ?? {}).length,
    costeEstimadoUsd: num(summary.costeEstimadoUsd),
  };
}

export function mapJob(job: ServerJob, now: number, local?: LocalUpload): Job {
  let status = (STATUSES.has(job.status as JobStatus) ? job.status : 'RUNNING') as JobStatus;
  let error = job.error;
  let phase = PHASE[status];
  let progress: number | null = status === 'DONE' ? 1 : null;

  if (status === 'UPLOADING') {
    if (local?.error) {
      status = 'FAILED';
      error = local.error;
      phase = PHASE.FAILED;
    } else if (local) {
      progress = local.progress;
      if (local.progress >= 1) phase = 'Subido: empezando el análisis';
    } else if (now - Date.parse(job.createdAt ?? '') > UPLOAD_GRACE_MS) {
      status = 'FAILED';
      error = 'La subida no llegó a completarse. Vuelve a subir el PDF.';
      phase = PHASE.FAILED;
    }
  }
  if (status === 'NEEDS_OCR') error = 'El PDF parece escaneado (sin texto): hace falta OCR, que todavía no está disponible.';

  return {
    jobId: job.jobId,
    slug: job.source,
    title: job.title,
    filename: job.filename ?? '',
    status,
    phase,
    progress,
    error,
    stats: summaryStats(job.summary),
    coverUrl: job.coverUrl || undefined,
    createdAt: job.createdAt ?? '',
    finishedAt: job.finishedAt,
  };
}

// ---------------------------------------------------------------------------------------------------------------------- cliente
export interface HttpApiOptions {
  /** Dirección base de la API (sin barra final). */
  apiUrl: string;
  /** Token del login (el de ID de Cognito). Debe rechazar si no hay sesión. */
  getToken: () => Promise<string>;
  /** La API dijo 401 o no hay sesión: hay que volver a la pantalla de entrada. */
  onUnauthorized: () => void;
  fetchFn?: typeof fetch;
  uploadFn?: UploadFn;
  now?: () => number;
  pollMs?: number;
}

/**
 * La API real: API Gateway con el token de Cognito en cada petición. Mientras haya trabajos en marcha avisa a la interfaz cada pocos segundos
 * (el backend aún no avisa por sí solo: se pregunta). El agente todavía no tiene endpoint: `features` lo dice y la interfaz no enseña su pestaña.
 */
export class HttpApi implements ApiClient {
  readonly features: ApiFeatures = { hitos: true, agent: false };

  private listeners = new Set<() => void>();
  private uploads = new Map<string, LocalUpload>();
  private pdfUrls = new Map<string, { url: string; expiresAt: number }>();
  private active = false;
  private timer: ReturnType<typeof setInterval> | null = null;
  private lastProgressNotify = 0;

  constructor(private options: HttpApiOptions) {}

  private get now() {
    return (this.options.now ?? Date.now)();
  }

  private async request<T>(path: string, init?: { method?: string; body?: string }): Promise<T> {
    let token: string;
    try {
      token = await this.options.getToken();
    } catch {
      this.options.onUnauthorized();
      throw new ApiError('Tu sesión ha caducado. Vuelve a entrar.', 401);
    }
    const send = this.options.fetchFn ?? fetch; // en una variable: llamar a fetch como método de otro objeto da "Illegal invocation" en el navegador
    let response: Response;
    try {
      response = await send(`${this.options.apiUrl}${path}`, {
        method: init?.method ?? 'GET',
        headers: { authorization: `Bearer ${token}`, ...(init?.body ? { 'content-type': 'application/json' } : {}) },
        body: init?.body,
      });
    } catch {
      throw new ApiError('No hay conexión con el servidor. Comprueba tu conexión y vuelve a intentarlo.');
    }
    const body: unknown = await response.json().catch(() => null);
    if (response.ok) return body as T;
    if (response.status === 401) {
      this.options.onUnauthorized();
      throw new ApiError('Tu sesión ha caducado. Vuelve a entrar.', 401);
    }
    if (response.status === 429) throw new ApiError('Demasiadas peticiones seguidas. Espera unos segundos y vuelve a intentarlo.', 429);
    const message = (body as { error?: unknown } | null)?.error;
    throw new ApiError(typeof message === 'string' ? message : `El servidor respondió con un error (${response.status}).`, response.status);
  }

  async listBooks(): Promise<Book[]> {
    const { books } = await this.request<{ books: ServerBook[] }>('/books');
    return books.map(mapBook);
  }

  async listJobs(): Promise<Job[]> {
    const { jobs } = await this.request<{ jobs: ServerJob[] }>('/jobs');
    const now = this.now;
    const mapped = jobs.map((job) => mapJob(job, now, this.uploads.get(job.jobId)));
    // Una subida local deja de hacer falta cuando el servidor ya ha pasado de UPLOADING.
    for (const job of jobs) if (job.status !== 'UPLOADING') this.uploads.delete(job.jobId);
    this.active = mapped.some((job) => ACTIVE.has(job.status));
    return mapped;
  }

  async getHitos(slug: string): Promise<Hito[]> {
    const { hitos } = await this.request<{ hitos: ServerHito[] }>(`/books/${encodeURIComponent(slug)}/hitos`);
    return hitos.map(mapHito);
  }

  async getPersonajes(slug: string): Promise<Personaje[]> {
    const { personajes } = await this.request<{ personajes: ServerPersonaje[] }>(`/books/${encodeURIComponent(slug)}/personajes`);
    return personajes.map(mapPersonaje);
  }

  async getPdfUrl(slug: string): Promise<string> {
    const cached = this.pdfUrls.get(slug);
    if (cached && cached.expiresAt > this.now) return cached.url;
    const { url, expiresInSeconds } = await this.request<{ url: string; expiresInSeconds: number }>(`/books/${encodeURIComponent(slug)}/pdf-url`);
    this.pdfUrls.set(slug, { url, expiresAt: this.now + Math.max(0, expiresInSeconds - 300) * 1000 }); // se renueva 5 min antes de caducar
    return url;
  }

  getTranslation(slug: string, page: number): Promise<PageTranslation> {
    return this.request<PageTranslation>(`/books/${encodeURIComponent(slug)}/pages/${page}/translation`);
  }

  async ask(): Promise<ChatMessage> {
    throw new ApiError('El agente todavía no está disponible.');
  }

  async startUpload(file: File, options: UploadOptions): Promise<{ jobId: string }> {
    const problem = validatePdf(file);
    if (problem) throw new Error(problem);
    if (!isValidSlug(options.slug)) throw new Error('El identificador debe tener entre 2 y 60 caracteres: minúsculas, números y guiones.');
    if (!options.title.trim()) throw new Error('Escribe un título para el libro.');
    const books = await this.listBooks();
    if (books.some((book) => book.slug === options.slug) && !options.replaceSource) {
      throw new Error('Ya existe un libro con ese identificador. Marca «Reprocesar» para reemplazarlo.');
    }

    const { jobId, upload } = await this.request<{ jobId: string; upload: PresignedPost }>('/uploads', {
      method: 'POST',
      body: JSON.stringify({ filename: file.name, source: options.slug, title: options.title.trim(), replaceSource: options.replaceSource }),
    });

    // La subida sigue en segundo plano: el formulario ya puede cerrarse y el avance se ve en la pestaña Progreso.
    const local: LocalUpload = { progress: 0 };
    this.uploads.set(jobId, local);
    this.active = true;
    const send = this.options.uploadFn ?? uploadToS3;
    void send(upload, file, (fraction) => {
      local.progress = fraction;
      const now = this.now;
      if (fraction >= 1 || now - this.lastProgressNotify > PROGRESS_NOTIFY_MS) {
        this.lastProgressNotify = now;
        this.notify();
      }
    }).then(
      () => this.notify(),
      (error: unknown) => {
        local.error = error instanceof Error ? error.message : 'No se pudo subir el PDF.';
        this.notify();
      },
    );
    this.notify();
    return { jobId };
  }

  subscribe(listener: () => void): () => void {
    this.listeners.add(listener);
    if (!this.timer) {
      this.timer = setInterval(() => {
        if (this.active) this.notify();
      }, this.options.pollMs ?? POLL_MS);
    }
    return () => {
      this.listeners.delete(listener);
      if (this.listeners.size === 0 && this.timer) {
        clearInterval(this.timer);
        this.timer = null;
      }
    };
  }

  private notify() {
    for (const listener of this.listeners) listener();
  }
}
