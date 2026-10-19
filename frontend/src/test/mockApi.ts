import { isValidSlug, validatePdf } from '../lib/files';
import type { Book, BookStats, ChatMessage, Hito, Job, PageTranslation, Personaje } from '../types';
import type { ApiClient, UploadOptions } from './client';

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

export class MockApi implements ApiClient {
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
        pageCount: 20, pdfUrl: '/samples/oxford-zhou.pdf', status: 'DONE', createdAt: now, stats: SAMPLE_STATS,
      },
      {
        slug: 'gernet-mundo-chino', title: 'Jacques Gernet — El mundo chino', filename: 'gernet-mundo-chino.pdf',
        pageCount: 0, status: 'DONE', createdAt: now, stats: { chunks: 0, chunksListos: 0, hitos: 0, personajes: 0, costeEstimadoUsd: 0 },
      },
    ];
    this.jobs = [
      {
        jobId: randomId(), slug: SAMPLE_SLUG, filename: 'oxford-zhou.pdf', status: 'DONE', phase: 'Terminado', progress: 1,
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

  async startUpload(file: File, options: UploadOptions): Promise<{ jobId: string }> {
    const fileError = validatePdf(file);
    if (fileError) throw new Error(fileError);
    if (!isValidSlug(options.slug)) throw new Error('El identificador del libro debe tener entre 2 y 60 caracteres: minúsculas, números y guiones.');
    const existing = this.books.find((book) => book.slug === options.slug);
    if (existing && !options.replaceSource) {
      throw new Error('Ya existe un libro con ese identificador: marca "Reprocesar" si quieres reemplazarlo.');
    }

    const now = new Date().toISOString();
    const job: Job = { jobId: randomId(), slug: options.slug, filename: file.name, status: 'UPLOADING', phase: 'Subiendo el PDF', progress: 0, createdAt: now };
    const book: Book = {
      slug: options.slug, title: options.title, filename: file.name, pageCount: Math.max(5, Math.round(file.size / 50_000)),
      // El PDF recién subido se puede ver al momento (en el backend real se servirá desde S3).
      pdfUrl: URL.createObjectURL(file), status: 'UPLOADING', createdAt: now,
    };
    this.books = [book, ...this.books.filter((b) => b.slug !== options.slug)];
    this.jobs = [job, ...this.jobs.filter((j) => j.slug !== options.slug)];
    this.emit();
    this.simulateUpload(job, file);
    return { jobId: job.jobId };
  }

  private patchJob(jobId: string, patch: Partial<Job>) {
    this.jobs = this.jobs.map((job) => (job.jobId === jobId ? { ...job, ...patch } : job));
    const job = this.jobs.find((j) => j.jobId === jobId);
    if (job) this.books = this.books.map((book) => (book.slug === job.slug ? { ...book, status: job.status, stats: job.stats ?? book.stats } : book));
    this.emit();
  }

  private simulateUpload(job: Job, file: File) {
    const ticks = 10;
    let done = 0;
    const timer = setInterval(() => {
      done += 1;
      if (done < ticks) {
        this.patchJob(job.jobId, { progress: done / ticks });
        return;
      }
      clearInterval(timer);
      const batches = Math.max(2, Math.round(file.size / 400_000));
      this.steps.set(job.jobId, buildSteps(batches));
      this.enqueue(job.jobId);
    }, this.uploadMs / ticks);
  }

  /** Un libro a la vez: el resto espera en cola (en el backend real, por los límites de Lambda y de OpenAI). */
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
    this.finish(jobId, { status: 'DONE', phase: 'Terminado', progress: 1, stats });
  }

  private finish(jobId: string, patch: Partial<Job>) {
    this.patchJob(jobId, { progress: 1, ...patch, finishedAt: new Date().toISOString(), detail: undefined });
    this.running = null;
    const next = this.queue.shift();
    this.refreshQueue();
    if (next) this.run(next);
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

  ask(slug: string | null, question: string): Promise<ChatMessage> {
    return new Promise((resolve) => setTimeout(() => resolve(this.answer(slug, question)), this.options.askMs ?? 700));
  }

  private answer(slug: string | null, question: string): ChatMessage {
    const q = question.toLowerCase();
    const id = randomId();
    if (q.includes('muye')) {
      return {
        id, role: 'assistant', origen: 'libro',
        text: '[Respuesta simulada] Según el libro, en Muye el rey Wu lideró el ataque final contra los Shang, en los suburbios de su capital.',
        fuentes: [{ pagina: 4, texto: 'Fa gathered his allies at the Muye field in the suburbs of the Shang capital Yin…' }],
      };
    }
    if (q.includes('luoyi') || q.includes('luoyang')) {
      return {
        id, role: 'assistant', origen: 'libro',
        text: '[Respuesta simulada] Según el libro, el rey Cheng fundó Luoyi como residencia oriental, también llamada Chengzhou.',
        fuentes: [{ pagina: 8, texto: 'King Cheng founded the eastern royal residence Luoyi…' }],
      };
    }
    return {
      id, role: 'assistant', origen: 'general',
      text: `[Respuesta simulada] Esto no aparece en ${slug ? 'el libro abierto' : 'ninguno de los libros'}. Aquí el agente respondería con su conocimiento general de historia de China, avisando de que no viene de ningún libro.`,
    };
  }
}
