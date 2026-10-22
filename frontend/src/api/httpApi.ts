import { isValidSlug, validatePdf } from '../lib/files';
import { readNdjson } from '../lib/ndjson';
import type { Book, BookOcr, BookPublished, BookStats, Hito, Job, JobKind, JobStatus, OcrEngine, PageTranslation, Personaje, PersonajeRef, PublishSummary } from '../types';
import type { ApiClient, ApiFeatures, AskEvent, AskRequest, UploadOptions } from './client';
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
  updatedAt?: string;
  /** "none" (sin analizar) o "done". Los libros de antes de existir este campo estaban todos analizados. */
  analysis?: string;
  /** false = escaneado (sin texto). Los libros anteriores no lo traen. */
  textLayer?: boolean;
  ocr?: { engine?: string; pages?: number; pagesWithText?: number; at?: string } | null;
  stats?: ServerStats;
  coverUrl?: string | null;
  published?: { at?: string; chunks?: number; hitos?: number; personajes?: number } | null;
}

interface ServerJob {
  jobId: string;
  /** "upload" o "analysis"; los registros antiguos no lo traen y eran análisis. */
  kind?: string;
  status: string;
  /** Solo en un trabajo EN MARCHA: la fase en la que va, su avance (0 a 1) y un detalle ("3 de 13 tandas"). Los apunta cada fase del flujo (ver backend/lambdas/jobs). */
  phase?: string;
  progress?: number;
  detail?: string;
  filename?: string;
  source: string;
  title?: string;
  createdAt?: string;
  finishedAt?: string;
  error?: string;
  /** true = vista previa de publicación. */
  dryRun?: boolean;
  /** El resumen que escribe Finalize al terminar (ver backend/lambdas/write_mongo/handler.py) o, en una publicación, el de backend/lambdas/publish. */
  summary?: {
    hitos?: number;
    hitosReemplazados?: number;
    personajesPublicados?: number;
    chunksReemplazados?: number;
    hitosDuplicados?: number;
    duplicados?: { titulo?: string; año?: number; titulo_existente?: string; fuente_existente?: string }[];
    publicadoAntes?: { chunks?: number; hitos?: number; personajes?: number };
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
  fuente?: string;
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
  fuentes?: string[];
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

function mapOcr(ocr: ServerBook['ocr']): BookOcr | undefined {
  if (!ocr) return undefined;
  return { engine: ocr.engine === 'openai' ? 'openai' : 'textract', pages: num(ocr.pages), pagesWithText: ocr.pagesWithText === undefined ? undefined : num(ocr.pagesWithText), at: ocr.at };
}

function mapPublished(published: ServerBook['published']): BookPublished | undefined {
  if (!published || typeof published.at !== 'string') return undefined;
  return { at: published.at, chunks: num(published.chunks), hitos: num(published.hitos), personajes: num(published.personajes) };
}

function mapPublishSummary(summary: NonNullable<ServerJob['summary']>): PublishSummary {
  return {
    chunks: num(summary.chunks),
    chunksReplaced: num(summary.chunksReemplazados),
    hitos: num(summary.hitos),
    hitosReplaced: num(summary.hitosReemplazados),
    personajes: num(summary.personajesPublicados),
    duplicateCount: num(summary.hitosDuplicados),
    duplicates: (summary.duplicados ?? []).map((d) => ({ titulo: d.titulo ?? '', año: num(d.año), existente: d.titulo_existente, fuente: d.fuente_existente })),
    before: { chunks: num(summary.publicadoAntes?.chunks), hitos: num(summary.publicadoAntes?.hitos), personajes: num(summary.publicadoAntes?.personajes) },
  };
}

export function mapBook(book: ServerBook): Book {
  const stats = book.stats;
  return {
    slug: book.slug,
    title: book.title || book.slug,
    filename: book.filename ?? '',
    pageCount: num(book.pageCount),
    analysis: book.analysis === 'none' ? 'none' : 'done', // sin el campo = libro antiguo, que ya estaba analizado
    createdAt: book.createdAt ?? '',
    updatedAt: book.updatedAt,
    coverUrl: book.coverUrl || undefined,
    textLayer: typeof book.textLayer === 'boolean' ? book.textLayer : undefined,
    ocr: mapOcr(book.ocr),
    published: mapPublished(book.published),
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
    fuente: optional(hito.fuente),
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
    fuentes: person.fuentes,
  };
}

/** El resumen del análisis trae los personajes como CONTADORES (`personajes_nuevos`: fichas creadas; `personajes_unidos`, `juez_llamadas`...): las fichas son `personajes_nuevos`. Un resumen antiguo (nombre -> menciones) cuenta sus nombres. */
export function countPersonajes(people: Record<string, number> | undefined): number {
  if (!people) return 0;
  if ('personajes_nuevos' in people || 'personajes_unidos' in people) return num(people.personajes_nuevos);
  return Object.keys(people).length;
}

function summaryStats(summary: ServerJob['summary']): BookStats | undefined {
  if (!summary) return undefined;
  return {
    chunks: num(summary.chunks),
    chunksListos: num(summary.chunksListos),
    hitos: num(summary.hitosEscritos),
    personajes: countPersonajes(summary.personajes),
    costeEstimadoUsd: num(summary.costeEstimadoUsd),
  };
}

/**
 * Step Functions guarda el fallo de una Lambda como `NombreDelError: {"errorMessage": "...", "errorType": "...", "stackTrace": [...]}`. Al usuario le interesa solo el mensaje:
 * se saca de ahí (sin el rastro técnico). Un texto que no tiene esa forma se deja como está, recortado si es larguísimo.
 */
export function cleanJobError(error: string | undefined): string | undefined {
  if (!error) return error;
  const match = /^[\w.]+: (\{[\s\S]*\})\s*$/.exec(error);
  if (match) {
    try {
      const parsed = JSON.parse(match[1]) as { errorMessage?: unknown };
      if (typeof parsed.errorMessage === 'string' && parsed.errorMessage.trim()) return parsed.errorMessage.trim().slice(0, 400);
    } catch {
      /* no era JSON: se enseña tal cual, recortado */
    }
  }
  return error.length > 400 ? `${error.slice(0, 400)}…` : error;
}

export function mapJob(job: ServerJob, now: number, local?: LocalUpload): Job {
  let status = (STATUSES.has(job.status as JobStatus) ? job.status : 'RUNNING') as JobStatus;
  let error = job.error;
  let phase = PHASE[status];
  let progress: number | null = status === 'DONE' ? 1 : null;
  let detail: string | undefined;

  if (status === 'UPLOADING') {
    if (local?.error) {
      status = 'FAILED';
      error = local.error;
      phase = PHASE.FAILED;
    } else if (local) {
      progress = local.progress;
      if (local.progress >= 1) phase = 'Subido: preparando el libro';
    } else if (now - Date.parse(job.createdAt ?? '') > UPLOAD_GRACE_MS) {
      status = 'FAILED';
      error = 'La subida no llegó a completarse. Vuelve a subir el PDF.';
      phase = PHASE.FAILED;
    }
  }
  if (status === 'NEEDS_OCR') error = 'Este PDF es un escaneado (sin texto): primero reconoce su texto con OCR (botón «Reconocer texto» en Archivos) y luego analízalo.';

  const kind: JobKind = job.kind === 'upload' ? 'upload' : job.kind === 'ocr' ? 'ocr' : job.kind === 'publish' ? 'publish' : 'analysis';
  if (kind === 'ocr' && status === 'RUNNING') phase = 'Reconociendo el texto (OCR)';
  if (kind === 'publish' && status === 'RUNNING') phase = job.dryRun ? 'Comprobando lo que se publicaría' : 'Publicando en producción';
  if (status === 'RUNNING') {
    // Lo que dice el propio flujo manda sobre el texto genérico: la fase real y, si la hay, su avance (si no, la barra de "trabajando").
    if (typeof job.phase === 'string' && job.phase.trim()) phase = job.phase;
    if (typeof job.progress === 'number' && Number.isFinite(job.progress)) progress = Math.min(0.99, Math.max(0, job.progress));
    if (typeof job.detail === 'string' && job.detail.trim()) detail = job.detail;
  }
  return {
    jobId: job.jobId,
    kind,
    slug: job.source,
    title: job.title,
    filename: job.filename ?? '',
    status,
    phase,
    detail,
    progress,
    error: cleanJobError(error),
    stats: kind === 'publish' ? undefined : summaryStats(job.summary),
    dryRun: kind === 'publish' ? Boolean(job.dryRun) : undefined,
    publish: kind === 'publish' && job.summary ? mapPublishSummary(job.summary) : undefined,
    createdAt: job.createdAt ?? '',
    finishedAt: job.finishedAt,
  };
}

// ---------------------------------------------------------------------------------------------------------------------- cliente
export interface HttpApiOptions {
  /** Dirección base de la API (sin barra final). */
  apiUrl: string;
  /** Dirección del agente (Function URL con streaming). Sin ella, la pestaña Agente no se enseña. */
  askUrl?: string;
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
 * (el backend aún no avisa por sí solo: se pregunta). El agente tiene su propia dirección (con streaming): `features` dice si está configurada y, si no, la interfaz no enseña su pestaña.
 */
export class HttpApi implements ApiClient {
  readonly features: ApiFeatures;

  private listeners = new Set<() => void>();
  private uploads = new Map<string, LocalUpload>();
  private pdfUrls = new Map<string, { url: string; expiresAt: number }>();
  private active = false;
  private timer: ReturnType<typeof setInterval> | null = null;
  private lastProgressNotify = 0;

  constructor(private options: HttpApiOptions) {
    this.features = { hitos: true, agent: Boolean(options.askUrl) };
  }

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

  async getAllHitos(): Promise<Hito[]> {
    const { hitos } = await this.request<{ hitos: ServerHito[] }>('/hitos');
    return hitos.map(mapHito);
  }

  async getAllPersonajes(): Promise<Personaje[]> {
    const { personajes } = await this.request<{ personajes: ServerPersonaje[] }>('/personajes');
    return personajes.map(mapPersonaje);
  }

  async startOcr(slug: string, engine: OcrEngine): Promise<{ jobId: string; estimatedCostUsd: number }> {
    const result = await this.request<{ jobId: string; estimatedCostUsd?: number }>(`/books/${encodeURIComponent(slug)}/ocr`, { method: 'POST', body: JSON.stringify({ engine }) });
    this.active = true; // hay un OCR en marcha: a partir de ahora se pregunta por su avance
    this.notify();
    return { jobId: result.jobId, estimatedCostUsd: num(result.estimatedCostUsd) };
  }

  private ocrPages = new Map<string, string[]>();

  async getOcrPages(slug: string, stamp = ''): Promise<string[] | null> {
    const key = `${slug}|${stamp}`;
    const cached = this.ocrPages.get(key);
    if (cached) return cached;
    const { url } = await this.request<{ url: string }>(`/books/${encodeURIComponent(slug)}/ocr-url`);
    const send = this.options.fetchFn ?? fetch;
    let response: Response;
    try {
      response = await send(url);
    } catch {
      throw new ApiError('No hay conexión para leer el texto del libro.');
    }
    if (!response.ok) throw new ApiError('No se pudo leer el texto reconocido del libro.', response.status);
    const body = (await response.json().catch(() => null)) as { pages?: unknown } | null;
    if (!body || !Array.isArray(body.pages)) throw new ApiError('El texto reconocido del libro no es válido.');
    const pages = body.pages.map((page) => (typeof page === 'string' ? page : ''));
    this.ocrPages.set(key, pages);
    return pages;
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

  async analyzeBook(slug: string): Promise<{ jobId: string }> {
    const result = await this.request<{ jobId: string }>(`/books/${encodeURIComponent(slug)}/analyze`, { method: 'POST' });
    this.active = true; // hay un análisis en marcha: a partir de ahora se pregunta por su avance
    this.notify();
    return { jobId: result.jobId };
  }

  async deleteBook(slug: string, options?: { unpublish?: boolean }): Promise<void> {
    await this.request(`/books/${encodeURIComponent(slug)}${options?.unpublish ? '?unpublish=1' : ''}`, { method: 'DELETE' });
    this.pdfUrls.delete(slug);
    this.notify(); // la lista se refresca sin el libro
  }

  private async startPublish(slug: string, dryRun: boolean): Promise<{ jobId: string }> {
    const result = await this.request<{ jobId: string }>(`/books/${encodeURIComponent(slug)}/publish`, { method: 'POST', body: JSON.stringify({ dryRun }) });
    this.active = true; // hay un trabajo en marcha: a partir de ahora se pregunta por su avance
    this.notify();
    return { jobId: result.jobId };
  }

  previewPublish(slug: string): Promise<{ jobId: string }> {
    return this.startPublish(slug, true);
  }

  publishBook(slug: string): Promise<{ jobId: string }> {
    return this.startPublish(slug, false);
  }

  async unpublishBook(slug: string): Promise<void> {
    await this.request(`/books/${encodeURIComponent(slug)}/publish`, { method: 'DELETE' });
    this.notify(); // el libro vuelve a la lista sin la marca de publicado
  }

  async ask(request: AskRequest, onEvent: (event: AskEvent) => void, signal?: AbortSignal): Promise<void> {
    const askUrl = this.options.askUrl;
    if (!askUrl) throw new ApiError('El agente todavía no está disponible.');
    let token: string;
    try {
      token = await this.options.getToken();
    } catch {
      this.options.onUnauthorized();
      throw new ApiError('Tu sesión ha caducado. Vuelve a entrar.', 401);
    }
    const send = this.options.fetchFn ?? fetch;
    let response: Response;
    try {
      response = await send(askUrl, {
        method: 'POST',
        headers: { authorization: `Bearer ${token}`, 'content-type': 'application/json' },
        body: JSON.stringify(request),
        signal,
      });
    } catch (problem) {
      if (signal?.aborted) throw problem; // el usuario detuvo la respuesta: no es un fallo de red
      throw new ApiError('No hay conexión con el agente. Comprueba tu conexión y vuelve a intentarlo.');
    }
    if (!response.ok) {
      if (response.status === 401) {
        this.options.onUnauthorized();
        throw new ApiError('Tu sesión ha caducado. Vuelve a entrar.', 401);
      }
      const body = (await response.json().catch(() => null)) as { error?: unknown } | null;
      throw new ApiError(typeof body?.error === 'string' ? body.error : `El agente respondió con un error (${response.status}).`, response.status);
    }
    if (!response.body) throw new ApiError('El agente no respondió.');
    for await (const event of readNdjson(response.body)) {
      const typed = event as { type?: unknown };
      if (typed && typeof typed === 'object' && typeof typed.type === 'string') onEvent(event as AskEvent);
    }
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
