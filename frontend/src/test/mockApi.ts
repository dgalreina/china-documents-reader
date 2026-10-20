import { isValidSlug, validatePdf } from '../lib/files';
import type { Book, BookStats, Fuente, Hito, Job, PageTranslation, Personaje } from '../types';
import type { ApiClient, AskEvent, AskRequest, UploadOptions } from '../api/client';

// ---- Datos de ejemplo ------------------------------------------------------------------------------------------------------------------
// Los hitos y personajes del libro de muestra salen de la prueba real con el Oxford Handbook (capítulo de los Zhou), pero las PÁGINAS son
// inventadas y todo lo que devuelve esta API es simulado. Sirve para construir la interfaz; no hay nada real detrás.

export const SAMPLE_SLUG = 'oxford-zhou';

const SAMPLE_HITOS: Hito[] = [
  {
    id: 'ej-conquista-shang', titulo: 'Conquista de la capital Shang', año: -1050, lugar: 'Yinxu, Henan', pagina: 3, personajes: [],
    descripcion: 'Los Zhou conquistan la capital de los Shang y ponen fin a su dominio.',
  },
  {
    id: 'ej-muye', titulo: 'Batalla de Muye', año: -1046, lugar: 'Muye, Henan', pagina: 4,
    descripcion: 'El rey Wu de Zhou lideró la batalla de Muye contra los Shang en los suburbios de Yin.',
    personajes: [{ id: 'rey-wu', nombre: 'Rey Wu de Zhou', rol: 'líder de la conquista' }],
  },
  {
    id: 'ej-luoyi', titulo: 'Fundación de Luoyi por el rey Cheng', año: -1039, lugar: 'Luoyi, Henan', pagina: 8,
    descripcion: 'El rey Cheng fundó la residencia oriental de Luoyi, también llamada Chengzhou, que con el tiempo se llamó Luoyang.',
    personajes: [{ id: 'rey-cheng', nombre: 'Rey Cheng de Zhou', rol: 'fundador' }],
  },
  {
    id: 'ej-ping', titulo: 'Restauración de la dinastía Zhou en Luoyi', año: -770, lugar: 'Luoyi, Henan', pagina: 12,
    descripcion: 'Yijiu, hijo del rey You, fue entronizado como rey Ping en Luoyi tras la invasión de los Quan Rong.',
    personajes: [{ id: 'rey-ping', nombre: 'Rey Ping de Zhou', rol: 'rey restaurado' }],
  },
];

const SAMPLE_PERSONAJES: Personaje[] = [
  {
    id: 'rey-wu', nombre: 'Rey Wu de Zhou', nombreZh: '周武王', rol: 'líder de la conquista', estado: 'Zhou Occidental',
    descripcion: 'Hijo del rey Wen de Zhou. Lideró la conquista final contra los Shang.', hitoIds: ['ej-muye'],
  },
  {
    id: 'rey-cheng', nombre: 'Rey Cheng de Zhou', nombreZh: '周成王', rol: 'fundador', estado: 'Zhou Occidental',
    descripcion: 'Hijo del rey Wu. Estableció la residencia oriental de Luoyi.', hitoIds: ['ej-luoyi'],
  },
  {
    id: 'rey-ping', nombre: 'Rey Ping de Zhou', nombreZh: '周平王', rol: 'rey restaurado', estado: 'Zhou Oriental',
    descripcion: 'Hijo del rey You. Restauró la dinastía Zhou en Luoyi en el 770 a. C.', hitoIds: ['ej-ping'],
  },
];

const SAMPLE_STATS: BookStats = { chunks: 58, chunksListos: 47, hitos: 4, personajes: 3, costeEstimadoUsd: 0.16 };

interface MockOptions {
  /** Milisegundos que dura cada paso simulado del análisis. */
  stepMs?: number;
  /** Duración total de la subida simulada. */
  uploadMs?: number;
  /** Retraso de la respuesta del agente. */
  askMs?: number;
  /** Lo que tarda en traducirse una página que no estaba en caché (la real tarda ~5 s). */
  translateMs?: number;
}

/** Id de 32 caracteres hexadecimales, como los del backend. `crypto.randomUUID` solo existe en contextos seguros (no en http por red local). */
function randomId(): string {
  return Array.from({ length: 32 }, () => Math.floor(Math.random() * 16).toString(16)).join('');
}

interface Step {
  phase: string;
  detail?: string;
}

/** Las fases reales del flujo (ver docs/CLAUDE.md): trocear, etiquetar por tandas, hitos por tandas, escribir. */
function buildSteps(batches: number): Step[] {
  const steps: Step[] = [{ phase: 'Troceando el PDF' }];
  for (let i = 1; i <= batches; i++) steps.push({ phase: 'Etiquetando y calculando embeddings', detail: `${i} de ${batches} tandas` });
  for (let i = 1; i <= batches; i++) steps.push({ phase: 'Extrayendo hitos', detail: `${i} de ${batches} tandas` });
  steps.push({ phase: 'Guardando hitos y personajes' });
  return steps;
}

/** SOLO PARA TESTS: una API en memoria con datos inventados. La web publicada no la usa ni la incluye (ver src/Root.tsx). */
export class MockApi implements ApiClient {
  readonly features = { hitos: true, agent: true };
  private books: Book[];
  private jobs: Job[];
  private hitos = new Map<string, Hito[]>([[SAMPLE_SLUG, SAMPLE_HITOS]]);
  private personajes = new Map<string, Personaje[]>([[SAMPLE_SLUG, SAMPLE_PERSONAJES]]);
  private listeners = new Set<() => void>();
  private running: string | null = null;
  private queue: string[] = [];
  private steps = new Map<string, Step[]>();
  private translated = new Set<string>(); // "slug:página" ya traducidas: la segunda vez salen al instante, como con la caché real

  constructor(private options: MockOptions = {}) {
    const now = new Date().toISOString();
    this.books = [
      {
        slug: SAMPLE_SLUG, title: 'The Oxford Handbook of Early China (capítulo de los Zhou, muestra)', filename: 'oxford-zhou.pdf',
        pageCount: 20, analysis: 'done', createdAt: now, updatedAt: now, stats: SAMPLE_STATS,
      },
      {
        slug: 'gernet-mundo-chino', title: 'Jacques Gernet — El mundo chino', filename: 'gernet-mundo-chino.pdf',
        pageCount: 0, analysis: 'done', createdAt: now, updatedAt: now, stats: { chunks: 0, chunksListos: 0, hitos: 0, personajes: 0, costeEstimadoUsd: 0 },
      },
    ];
    this.jobs = [
      {
        jobId: randomId(), kind: 'analysis', slug: SAMPLE_SLUG, filename: 'oxford-zhou.pdf', status: 'DONE', phase: 'Terminado', progress: 1,
        stats: SAMPLE_STATS, createdAt: now, finishedAt: now,
      },
    ];
  }

  private get stepMs() { return this.options.stepMs ?? 700; }
  private get uploadMs() { return this.options.uploadMs ?? 1500; }

  subscribe(listener: () => void): () => void {
    this.listeners.add(listener);
    return () => this.listeners.delete(listener);
  }

  private emit() { this.listeners.forEach((listener) => listener()); }

  async listBooks(): Promise<Book[]> { return this.books.map((book) => ({ ...book })); }
  async listJobs(): Promise<Job[]> { return this.jobs.map((job) => ({ ...job })); }
  async getHitos(slug: string): Promise<Hito[]> { return [...(this.hitos.get(slug) ?? [])]; }
  async getPersonajes(slug: string): Promise<Personaje[]> { return [...(this.personajes.get(slug) ?? [])]; }
  async getAllHitos(): Promise<Hito[]> {
    return [...this.hitos].flatMap(([slug, list]) => list.map((hito) => ({ ...hito, fuente: slug }))).sort((a, b) => a.año - b.año);
  }
  async getAllPersonajes(): Promise<Personaje[]> {
    return [...this.personajes].flatMap(([slug, list]) => list.map((person) => ({ ...person, fuentes: [slug] })));
  }

  private busy(slug: string): boolean {
    return this.jobs.some((job) => job.slug === slug && (job.status === 'RUNNING' || job.status === 'QUEUED' || job.status === 'UPLOADING'));
  }

  /** Subir NO analiza: al terminar la subida el libro queda "sin analizar" (ya se puede leer y traducir). El análisis se pide con `analyzeBook`. */
  async startUpload(file: File, options: UploadOptions): Promise<{ jobId: string }> {
    const fileError = validatePdf(file);
    if (fileError) throw new Error(fileError);
    if (!isValidSlug(options.slug)) throw new Error('El identificador del libro debe tener entre 2 y 60 caracteres: minúsculas, números y guiones.');
    const existing = this.books.find((book) => book.slug === options.slug);
    if (existing && !options.replaceSource) {
      throw new Error('Ya existe un libro con ese identificador: confirma el reemplazo de su PDF (se borrarán su análisis, sus hitos y sus traducciones).');
    }
    if (this.busy(options.slug)) throw new Error('Ese libro se está analizando o subiendo. Espera a que termine.');

    const job: Job = {
      jobId: randomId(), kind: 'upload', slug: options.slug, title: options.title, filename: file.name, status: 'UPLOADING', phase: 'Subiendo el PDF', progress: 0,
      createdAt: new Date().toISOString(),
    };
    this.jobs = [job, ...this.jobs.filter((j) => !(j.slug === options.slug && j.kind === 'upload'))];
    this.emit();
    this.simulateUpload(job, file, options);
    return { jobId: job.jobId };
  }

  private patchJob(jobId: string, patch: Partial<Job>) {
    this.jobs = this.jobs.map((job) => (job.jobId === jobId ? { ...job, ...patch } : job));
    this.emit();
  }

  private patchBook(slug: string, patch: Partial<Book>) {
    this.books = this.books.map((book) => (book.slug === slug ? { ...book, ...patch, updatedAt: new Date().toISOString() } : book));
    this.emit();
  }

  private simulateUpload(job: Job, file: File, options: UploadOptions) {
    const ticks = 10;
    let done = 0;
    const timer = setInterval(() => {
      done += 1;
      if (done < ticks) {
        this.patchJob(job.jobId, { progress: done / ticks });
        return;
      }
      clearInterval(timer);
      if (/danado/i.test(file.name)) {
        this.patchJob(job.jobId, { status: 'FAILED', phase: 'Ha fallado', progress: null, error: 'No se pudo leer el PDF: puede estar dañado.', finishedAt: new Date().toISOString() });
        return;
      }
      const now = new Date().toISOString();
      const book: Book = { slug: options.slug, title: options.title, filename: file.name, pageCount: Math.max(5, Math.round(file.size / 50_000)), analysis: 'none', createdAt: now, updatedAt: now };
      this.books = [book, ...this.books.filter((b) => b.slug !== options.slug)];
      this.hitos.delete(options.slug); // reemplazar el PDF invalida lo que se derivó del anterior
      this.personajes.delete(options.slug);
      this.patchJob(job.jobId, { status: 'DONE', phase: 'Terminado', progress: 1, finishedAt: now });
    }, this.uploadMs / ticks);
  }

  /** Arranca el análisis de un libro ya subido. Un libro a la vez: el resto espera en cola (en el backend real, por los límites de Lambda y de OpenAI). */
  async analyzeBook(slug: string): Promise<{ jobId: string }> {
    const book = this.books.find((candidate) => candidate.slug === slug);
    if (!book) throw new Error('No existe ese libro.');
    if (this.busy(slug)) throw new Error('Este libro ya se está analizando o subiendo. Espera a que termine.');
    const job: Job = {
      jobId: randomId(), kind: 'analysis', slug, title: book.title, filename: book.filename, status: 'RUNNING', phase: 'Analizando el libro', progress: 0,
      createdAt: new Date().toISOString(),
    };
    this.jobs = [job, ...this.jobs];
    this.steps.set(job.jobId, buildSteps(Math.max(2, Math.round(book.pageCount / 8))));
    this.emit();
    this.enqueue(job.jobId);
    return { jobId: job.jobId };
  }

  private enqueue(jobId: string) {
    if (this.running === null) {
      this.run(jobId);
      return;
    }
    this.queue.push(jobId);
    this.refreshQueue();
  }

  private refreshQueue() {
    this.queue.forEach((id, index) => this.patchJob(id, { status: 'QUEUED', phase: 'En cola', detail: undefined, progress: 0, queuePosition: index + 1 }));
  }

  private run(jobId: string) {
    this.running = jobId;
    const steps = this.steps.get(jobId) ?? [];
    const job = this.jobs.find((j) => j.jobId === jobId);
    if (!job) return;
    let index = 0;
    const tick = () => {
      const step = steps[index];
      this.patchJob(jobId, { status: 'RUNNING', phase: step.phase, detail: step.detail, progress: index / steps.length, queuePosition: undefined });
      // Casos de error simulados, para probar la interfaz: el nombre del archivo decide qué pasa.
      if (index === 0 && /escaneado/i.test(job.filename)) {
        return this.finish(jobId, { status: 'NEEDS_OCR', phase: 'PDF escaneado', error: 'El PDF no tiene texto (es un escaneado): hace falta OCR, que aún no está disponible.' });
      }
      if (index === Math.floor(steps.length / 2) && /error/i.test(job.filename)) {
        return this.finish(jobId, { status: 'FAILED', phase: 'Error', error: 'Simulación: falló una tanda de etiquetado (límite de peticiones de OpenAI).' });
      }
      index += 1;
      if (index >= steps.length) {
        // La última fase también se queda visible un paso: si se completara en el mismo instante, en pantalla no se vería nunca.
        setTimeout(() => this.complete(jobId), this.stepMs);
        return;
      }
      setTimeout(tick, this.stepMs);
    };
    setTimeout(tick, this.stepMs);
  }

  private complete(jobId: string) {
    const job = this.jobs.find((j) => j.jobId === jobId);
    if (!job) return;
    const stats: BookStats = { chunks: 120, chunksListos: 95, hitos: 3, personajes: 1, costeEstimadoUsd: 0.31 };
    this.hitos.set(job.slug, [1, 2, 3].map((n) => ({
      id: `${job.slug}-${n}`, titulo: `Hito de ejemplo ${n}`, año: -500 + n * 100, lugar: 'Luoyang, Henan', pagina: n * 2,
      descripcion: 'Hito simulado: aquí aparecería un hecho extraído del libro.', personajes: n === 1 ? [{ id: `${job.slug}-p1`, nombre: 'Personaje de ejemplo', rol: 'protagonista' }] : [],
    })));
    this.personajes.set(job.slug, [{ id: `${job.slug}-p1`, nombre: 'Personaje de ejemplo', rol: 'protagonista', descripcion: 'Ficha simulada.', hitoIds: [`${job.slug}-1`] }]);
    this.patchBook(job.slug, { analysis: 'done', stats });
    this.finish(jobId, { status: 'DONE', phase: 'Terminado', progress: 1, stats });
  }

  private finish(jobId: string, patch: Partial<Job>) {
    this.patchJob(jobId, { progress: 1, ...patch, finishedAt: new Date().toISOString(), detail: undefined });
    this.running = null;
    const next = this.queue.shift();
    this.refreshQueue();
    if (next) this.run(next);
  }

  deleteBook(slug: string): Promise<void> {
    const running = this.jobs.find((job) => job.slug === slug && (job.status === 'RUNNING' || job.status === 'UPLOADING'));
    if (running) return Promise.reject(new Error('El libro se está analizando o subiendo. Espera a que termine para borrarlo.'));
    if (!this.books.some((book) => book.slug === slug)) return Promise.reject(new Error('No existe ese libro.'));
    this.books = this.books.filter((book) => book.slug !== slug);
    this.jobs = this.jobs.filter((job) => job.slug !== slug);
    this.hitos.delete(slug);
    this.personajes.delete(slug);
    this.emit();
    return Promise.resolve();
  }

  getPdfUrl(slug: string): Promise<string> {
    if (!this.books.some((book) => book.slug === slug)) return Promise.reject(new Error('No existe ese libro.'));
    return Promise.resolve(`/samples/${slug}.pdf`);
  }

  getTranslation(slug: string, page: number): Promise<PageTranslation> {
    const book = this.books.find((candidate) => candidate.slug === slug);
    if (!book) return Promise.reject(new Error('No existe ese libro.'));
    if (page < 1 || (book.pageCount > 0 && page > book.pageCount)) return Promise.reject(new Error('Esa página no existe en el libro.'));
    const key = `${slug}:${page}`;
    const cached = this.translated.has(key);
    const text = [
      `[Traducción simulada de la página ${page}] Aquí aparecería el texto de la página en español, con los párrafos ya reconstruidos.`,
      'Los caracteres chinos se dejan tal cual y llevan su aclaración: 周武王 (Zhou Wuwang, el rey Wu de Zhou) derrotó a los Shang en Muye 牧野 (Muye, llanura al sur de Yin).¹²',
      `¹² Nota al pie simulada de la página ${page}.`,
    ].join('\n\n');
    return new Promise((resolve) =>
      setTimeout(() => {
        this.translated.add(key);
        resolve({ page, text, cached });
      }, cached ? 20 : this.options.translateMs ?? 2500),
    );
  }

  /** Responde "en flujo": primero las citas, luego la respuesta palabra a palabra y al final `fin`, como el agente real. */
  async ask(request: AskRequest, onEvent: (event: AskEvent) => void, signal?: AbortSignal): Promise<void> {
    const pause = (ms: number) => new Promise<void>((resolve) => setTimeout(resolve, ms));
    const aborted = () => new DOMException('Detenido', 'AbortError');
    const wait = this.options.askMs ?? 700;
    await pause(wait);
    if (signal?.aborted) throw aborted();
    if (request.question.toLowerCase().includes('fallo')) {
      onEvent({ type: 'fuentes', fuentes: [] });
      onEvent({ type: 'delta', text: '[Respuesta simulada] Empiezo a res' });
      onEvent({ type: 'error', message: 'No se pudo obtener la respuesta. Inténtalo de nuevo.' });
      return;
    }
    if (request.question.toLowerCase().includes('sin conexion')) throw new Error('No hay conexión con el agente.');
    const answer = this.answer(request.slug, request.question);
    onEvent({ type: 'fuentes', fuentes: (answer.fuentes ?? []).map((fuente) => ({ ...fuente, slug: request.slug ?? SAMPLE_SLUG })) });
    for (const piece of answer.text.match(/\S+\s*/g) ?? []) {
      await pause(Math.max(1, wait / 20));
      if (signal?.aborted) throw aborted();
      onEvent({ type: 'delta', text: piece });
    }
    onEvent({ type: 'fin', origen: answer.origen === 'libro' ? 'libro' : 'ninguno' });
  }

  private answer(slug: string | null, question: string): { text: string; fuentes?: Fuente[]; origen: 'libro' | 'general' } {
    const q = question.toLowerCase();
    if (q.includes('muye')) {
      return {
        origen: 'libro',
        text: '[Respuesta simulada] Según el libro, en Muye el rey Wu lideró el ataque final contra los Shang, en los suburbios de su capital (pág. 4).',
        fuentes: [{ pagina: 4, texto: 'Fa gathered his allies at the Muye field in the suburbs of the Shang capital Yin…' }],
      };
    }
    if (q.includes('luoyi') || q.includes('luoyang')) {
      return {
        origen: 'libro',
        text: '[Respuesta simulada] Según el libro, el rey Cheng fundó Luoyi como residencia oriental, también llamada Chengzhou (pág. 8).',
        fuentes: [{ pagina: 8, texto: 'King Cheng founded the eastern royal residence Luoyi…' }],
      };
    }
    return {
      origen: 'general',
      text: `[Respuesta simulada] No encuentro nada sobre eso en ${slug ? 'el libro abierto' : 'ninguno de los libros'}.`,
    };
  }
}
