import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { ApiError, HttpApi, mapBook, mapHito, mapJob } from './httpApi';
import type { PresignedPost } from './s3Upload';

const NOW = Date.parse('2026-10-19T12:00:00Z');
const pdf = (name = 'libro.pdf', size = 1000) => new File([new Uint8Array(size)], name, { type: 'application/pdf' });
const options = (slug = 'libro-nuevo', replaceSource = false) => ({ slug, title: 'Libro nuevo', replaceSource });

interface Call {
  url: string;
  method: string;
  headers: Record<string, string>;
  body?: string;
}

/** Un fetch falso: cada ruta ("GET /books") devuelve {status, body}. Apunta las llamadas que recibe. */
function fakeServer(routes: Record<string, () => { status?: number; body?: unknown }>) {
  const calls: Call[] = [];
  const fetchFn = (async (url: string, init: RequestInit = {}) => {
    const method = init.method ?? 'GET';
    calls.push({ url, method, headers: init.headers as Record<string, string>, body: init.body as string | undefined });
    const path = url.replace('https://api.test', '');
    const route = routes[`${method} ${path}`];
    if (!route) return new Response(JSON.stringify({ error: `sin ruta ${method} ${path}` }), { status: 404 });
    const { status = 200, body = {} } = route();
    return new Response(JSON.stringify(body), { status });
  }) as unknown as typeof fetch;
  return { calls, fetchFn };
}

let unauthorized: ReturnType<typeof vi.fn>;
const makeApi = (server: ReturnType<typeof fakeServer>, extra: Partial<ConstructorParameters<typeof HttpApi>[0]> = {}) =>
  new HttpApi({ apiUrl: 'https://api.test', getToken: async () => 'tok-123', onUnauthorized: unauthorized, fetchFn: server.fetchFn, now: () => NOW, ...extra });

beforeEach(() => {
  unauthorized = vi.fn();
});
afterEach(() => vi.useRealTimers());

describe('conversiones', () => {
  it('un libro del catálogo se convierte en un Book terminado, con 0 en lo que falte', () => {
    expect(mapBook({ slug: 'a', title: 'A', pageCount: 20, stats: { chunks: 58, hitos: 2 } })).toMatchObject({
      slug: 'a', title: 'A', pageCount: 20, analysis: 'done', stats: { chunks: 58, hitos: 2, personajes: 0, costeEstimadoUsd: 0 },
    });
    expect(mapBook({ slug: 'sin-titulo' }).title).toBe('sin-titulo');
  });

  it('el estado de análisis del libro: "none" es sin analizar y cualquier otra cosa (o la ausencia, en libros antiguos) es analizado', () => {
    expect(mapBook({ slug: 'a', analysis: 'none' }).analysis).toBe('none');
    expect(mapBook({ slug: 'a', analysis: 'done' }).analysis).toBe('done');
    expect(mapBook({ slug: 'a' }).analysis).toBe('done'); // los libros de antes de existir el campo ya estaban analizados
    expect(mapBook({ slug: 'a', updatedAt: '2026-10-19T00:00:00Z' }).updatedAt).toBe('2026-10-19T00:00:00Z');
  });

  it('el tipo de trabajo: "upload" es una subida y todo lo demás (incluidos los registros antiguos, sin el campo) es un análisis', () => {
    expect(mapJob({ jobId: 'j', status: 'DONE', source: 'a', kind: 'upload' }, NOW).kind).toBe('upload');
    expect(mapJob({ jobId: 'j', status: 'RUNNING', source: 'a', kind: 'analysis' }, NOW).kind).toBe('analysis');
    expect(mapJob({ jobId: 'j', status: 'RUNNING', source: 'a' }, NOW).kind).toBe('analysis');
  });

  it('la portada llega como URL; vacía o null es "sin portada" (la lista enseña el recambio)', () => {
    expect(mapBook({ slug: 'a', coverUrl: 'https://s3/portada?firma' }).coverUrl).toBe('https://s3/portada?firma');
    expect(mapBook({ slug: 'a', coverUrl: null }).coverUrl).toBeUndefined();
    expect(mapBook({ slug: 'a' }).coverUrl).toBeUndefined();
  });

  it('un trabajo terminado trae sus estadísticas del resumen (personajes = cuántos nombres)', () => {
    const job = mapJob(
      { jobId: 'j', status: 'DONE', source: 'a', summary: { chunks: 58, chunksListos: 42, hitosEscritos: 2, personajes: { 'Rey Wu': 1, 'Rey Cheng': 2 }, costeEstimadoUsd: 0.1971 } },
      NOW,
    );
    expect(job).toMatchObject({ status: 'DONE', progress: 1, stats: { chunks: 58, chunksListos: 42, hitos: 2, personajes: 2, costeEstimadoUsd: 0.1971 } });
  });

  it('un trabajo en marcha no inventa un porcentaje: el backend aún no informa del avance', () => {
    expect(mapJob({ jobId: 'j', status: 'RUNNING', source: 'a' }, NOW)).toMatchObject({ status: 'RUNNING', progress: null, phase: 'Analizando el libro' });
  });

  it('un PDF escaneado explica qué pasa', () => {
    expect(mapJob({ jobId: 'j', status: 'NEEDS_OCR', source: 'a' }, NOW).error).toMatch(/escaneado/);
  });

  it('una subida que lleva demasiado sin llegar a S3 se da por fallida; una reciente, no', () => {
    const old = new Date(NOW - 20 * 60_000).toISOString();
    const recent = new Date(NOW - 60_000).toISOString();
    expect(mapJob({ jobId: 'j', status: 'UPLOADING', source: 'a', createdAt: old }, NOW)).toMatchObject({ status: 'FAILED' });
    expect(mapJob({ jobId: 'j', status: 'UPLOADING', source: 'a', createdAt: recent }, NOW)).toMatchObject({ status: 'UPLOADING', progress: null });
  });

  it('la subida de este navegador da su avance, y su fallo la deja en FAILED con el motivo', () => {
    const recent = new Date(NOW - 1000).toISOString();
    expect(mapJob({ jobId: 'j', status: 'UPLOADING', source: 'a', createdAt: recent }, NOW, { progress: 0.4 })).toMatchObject({ status: 'UPLOADING', progress: 0.4 });
    expect(mapJob({ jobId: 'j', status: 'UPLOADING', source: 'a', createdAt: recent }, NOW, { progress: 0.4, error: 'Se cortó' })).toMatchObject({ status: 'FAILED', error: 'Se cortó' });
  });

  it('un estado desconocido del servidor no rompe la pantalla', () => {
    expect(mapJob({ jobId: 'j', status: 'ALGO_NUEVO', source: 'a' }, NOW).status).toBe('RUNNING');
  });
});

describe('peticiones', () => {
  it('manda el token del login en cada petición y devuelve la lista de libros', async () => {
    const server = fakeServer({ 'GET /books': () => ({ body: { books: [{ slug: 'a', title: 'A' }] } }) });
    const books = await makeApi(server).listBooks();
    expect(books.map((b) => b.slug)).toEqual(['a']);
    expect(server.calls[0].headers.authorization).toBe('Bearer tok-123');
  });

  it('un 401 avisa de que hay que volver a entrar', async () => {
    const server = fakeServer({ 'GET /books': () => ({ status: 401, body: { message: 'Unauthorized' } }) });
    await expect(makeApi(server).listBooks()).rejects.toThrow(/sesión ha caducado/);
    expect(unauthorized).toHaveBeenCalledOnce();
  });

  it('si no hay token (sesión perdida) no llega a llamar y avisa igual', async () => {
    const server = fakeServer({});
    const api = makeApi(server, { getToken: async () => Promise.reject(new Error('no-session')) });
    await expect(api.listBooks()).rejects.toBeInstanceOf(ApiError);
    expect(unauthorized).toHaveBeenCalledOnce();
    expect(server.calls).toHaveLength(0);
  });

  it('un 429 dice que se espere; un error con mensaje del servidor lo enseña; sin mensaje, el código', async () => {
    const server = fakeServer({
      'GET /books': () => ({ status: 429 }),
      'GET /jobs': () => ({ status: 400, body: { error: 'Identificador de libro no válido.' } }),
      'GET /books/x/pdf-url': () => ({ status: 500, body: {} }),
    });
    const api = makeApi(server);
    await expect(api.listBooks()).rejects.toThrow(/Demasiadas peticiones/);
    await expect(api.listJobs()).rejects.toThrow('Identificador de libro no válido.');
    await expect(api.getPdfUrl('x')).rejects.toThrow(/\(500\)/);
  });

  it('sin conexión da un mensaje claro', async () => {
    const api = makeApi({ calls: [], fetchFn: (async () => Promise.reject(new TypeError('Failed to fetch'))) as unknown as typeof fetch });
    await expect(api.listBooks()).rejects.toThrow(/No hay conexión/);
  });

  it('la URL del PDF se guarda hasta poco antes de caducar y luego se vuelve a pedir', async () => {
    let now = NOW;
    const server = fakeServer({ 'GET /books/mi-libro/pdf-url': () => ({ body: { url: 'https://s3/firma', expiresInSeconds: 3600 } }) });
    const api = makeApi(server, { now: () => now });
    await api.getPdfUrl('mi-libro');
    await api.getPdfUrl('mi-libro');
    expect(server.calls).toHaveLength(1);
    now += 3400 * 1000; // quedan menos de 5 minutos
    await api.getPdfUrl('mi-libro');
    expect(server.calls).toHaveLength(2);
  });

  it('la traducción pide libro y página, y nunca manda texto', async () => {
    const server = fakeServer({ 'GET /books/mi-libro/pages/3/translation': () => ({ body: { page: 3, text: 'Hola', cached: false } }) });
    expect(await makeApi(server).getTranslation('mi-libro', 3)).toEqual({ page: 3, text: 'Hola', cached: false });
    expect(server.calls[0].body).toBeUndefined();
  });

  it('sin dirección del agente la interfaz lo sabe y no enseña su pestaña', async () => {
    const api = makeApi(fakeServer({}));
    expect(api.features).toEqual({ hitos: true, agent: false });
    await expect(api.ask({ slug: null, question: 'hola', history: [] }, () => undefined)).rejects.toThrow(/no está disponible/);
  });
});

describe('OCR', () => {
  it('el libro trae si es un escaneado y el OCR que se le hizo; un libro antiguo, ninguno de los dos', () => {
    expect(mapBook({ slug: 'a', textLayer: false, ocr: { engine: 'openai', pages: 554, pagesWithText: 540, at: '2026-10-20T12:00:00Z' } })).toMatchObject({
      textLayer: false, ocr: { engine: 'openai', pages: 554, pagesWithText: 540, at: '2026-10-20T12:00:00Z' },
    });
    const old = mapBook({ slug: 'viejo' });
    expect(old.textLayer).toBeUndefined();
    expect(old.ocr).toBeUndefined();
    expect(mapBook({ slug: 'a', ocr: { engine: 'raro', pages: 3 } }).ocr?.engine).toBe('textract'); // un motor desconocido no rompe la lista
  });

  it('un trabajo de OCR se reconoce y tiene su propia fase', () => {
    const job = mapJob({ jobId: 'j', kind: 'ocr', status: 'RUNNING', source: 'libro', createdAt: '2026-10-20T12:00:00Z' }, NOW);
    expect(job.kind).toBe('ocr');
    expect(job.phase).toMatch(/OCR/);
  });

  it('pedir el OCR manda el motor elegido y devuelve la estimación', async () => {
    const server = fakeServer({ 'POST /books/mi-libro/ocr': () => ({ status: 202, body: { jobId: 'j1', engine: 'openai', estimatedCostUsd: 7.2 } }) });
    const result = await makeApi(server).startOcr('mi-libro', 'openai');
    expect(result).toEqual({ jobId: 'j1', estimatedCostUsd: 7.2 });
    expect(JSON.parse(server.calls[0].body ?? '{}')).toEqual({ engine: 'openai' });
  });

  it('un 409 (ya hay algo en curso) llega con su motivo', async () => {
    const server = fakeServer({ 'POST /books/mi-libro/ocr': () => ({ status: 409, body: { error: 'Este libro ya se está procesando o subiendo. Espera a que termine.' } }) });
    await expect(makeApi(server).startOcr('mi-libro', 'textract')).rejects.toThrow(/ya se está procesando/);
  });

  it('el texto OCR se pide con una URL firmada, se baja una vez y se guarda', async () => {
    const calls: string[] = [];
    const fetchFn = (async (url: string) => {
      calls.push(url);
      if (url.startsWith('https://api.test/')) return new Response(JSON.stringify({ url: 'https://s3.test/books/mi-libro/ocr.json?firma' }), { status: 200 });
      return new Response(JSON.stringify({ engine: 'textract', pages: ['uno', 'dos', 7] }), { status: 200 });
    }) as unknown as typeof fetch;
    const api = new HttpApi({ apiUrl: 'https://api.test', getToken: async () => 't', onUnauthorized: unauthorized, fetchFn });
    expect(await api.getOcrPages('mi-libro', 'v1')).toEqual(['uno', 'dos', '']); // lo que no es texto queda vacío
    await api.getOcrPages('mi-libro', 'v1');
    expect(calls.length).toBe(2); // la segunda vez, de la memoria
    await api.getOcrPages('mi-libro', 'v2'); // se repitió el OCR: otra versión, se vuelve a pedir
    expect(calls.length).toBe(4);
  });

  it('si el texto no se puede leer, rechaza con un mensaje (la web sigue sin ese texto)', async () => {
    const fetchFn = (async (url: string) => (url.startsWith('https://api.test/') ? new Response(JSON.stringify({ url: 'https://s3.test/x' }), { status: 200 }) : new Response('no', { status: 403 }))) as unknown as typeof fetch;
    const api = new HttpApi({ apiUrl: 'https://api.test', getToken: async () => 't', onUnauthorized: unauthorized, fetchFn });
    await expect(api.getOcrPages('mi-libro')).rejects.toThrow(/No se pudo leer el texto/);
  });
});

describe('agente (streaming)', () => {
  const ASK_URL = 'https://ask.test';
  const request = { slug: 'libro-a', question: '¿Quién fue el rey Wu?', history: [{ role: 'user' as const, text: 'hola' }] };
  const lines = (...events: object[]) => events.map((event) => `${JSON.stringify(event)}\n`);

  /** Un fetch falso que contesta con trozos de texto (como el flujo real) y apunta la petición recibida. */
  const streamingApi = (chunks: string[], status = 200) => {
    const calls: { url: string; init: RequestInit }[] = [];
    const fetchFn = (async (url: string, init: RequestInit) => {
      calls.push({ url, init });
      if (status !== 200) return new Response(JSON.stringify({ error: 'Pregunta no válida.' }), { status });
      const encoder = new TextEncoder();
      const body = new ReadableStream<Uint8Array>({
        start(controller) {
          for (const chunk of chunks) controller.enqueue(encoder.encode(chunk));
          controller.close();
        },
      });
      return new Response(body, { status: 200 });
    }) as unknown as typeof fetch;
    return { calls, api: new HttpApi({ apiUrl: 'https://api.test', askUrl: ASK_URL, getToken: async () => 'tok-123', onUnauthorized: unauthorized, fetchFn }) };
  };

  it('con dirección, la pestaña Agente se enseña', () => {
    expect(streamingApi([]).api.features.agent).toBe(true);
  });

  it('manda el token, la pregunta y la conversación, y va entregando los eventos según llegan', async () => {
    const { api, calls } = streamingApi(lines({ type: 'fuentes', fuentes: [{ slug: 'libro-a', pagina: 4, texto: 'x' }] }, { type: 'delta', text: 'Ganó ' }, { type: 'delta', text: 'él.' }, { type: 'fin', origen: 'libro' }));
    const events: unknown[] = [];
    await api.ask(request, (event) => events.push(event));
    expect(calls[0].url).toBe(ASK_URL);
    expect((calls[0].init.headers as Record<string, string>).authorization).toBe('Bearer tok-123');
    expect(JSON.parse(calls[0].init.body as string)).toEqual(request);
    expect(events.map((event) => (event as { type: string }).type)).toEqual(['fuentes', 'delta', 'delta', 'fin']);
  });

  it('una línea partida entre dos trozos de red no se pierde', async () => {
    const [first, second] = [JSON.stringify({ type: 'delta', text: 'hola' }).slice(0, 10), `${JSON.stringify({ type: 'delta', text: 'hola' }).slice(10)}\n`];
    const { api } = streamingApi([first, second]);
    const events: unknown[] = [];
    await api.ask(request, (event) => events.push(event));
    expect(events).toEqual([{ type: 'delta', text: 'hola' }]);
  });

  it('un 401 manda a la pantalla de entrada; un 400 llega con el motivo del servidor', async () => {
    await expect(streamingApi([], 401).api.ask(request, () => undefined)).rejects.toMatchObject({ status: 401 });
    expect(unauthorized).toHaveBeenCalled();
    await expect(streamingApi([], 400).api.ask(request, () => undefined)).rejects.toThrow(/Pregunta no válida/);
  });

  it('sin conexión: un mensaje que se puede enseñar; detenida por el usuario: el AbortError tal cual', async () => {
    const offline = new HttpApi({ apiUrl: 'https://api.test', askUrl: ASK_URL, getToken: async () => 't', onUnauthorized: unauthorized, fetchFn: (async () => { throw new TypeError('Failed to fetch'); }) as unknown as typeof fetch });
    await expect(offline.ask(request, () => undefined)).rejects.toThrow(/No hay conexión/);
    const controller = new AbortController();
    controller.abort();
    const stopped = new HttpApi({ apiUrl: 'https://api.test', askUrl: ASK_URL, getToken: async () => 't', onUnauthorized: unauthorized, fetchFn: (async () => { throw new DOMException('Detenido', 'AbortError'); }) as unknown as typeof fetch });
    await expect(stopped.ask(request, () => undefined, controller.signal)).rejects.toMatchObject({ name: 'AbortError' });
  });
});

describe('hitos y personajes', () => {
  it('los hitos del libro llegan convertidos: la página, si falta, queda sin definir, y los personajes llevan su nombre', async () => {
    const server = fakeServer({
      'GET /books/mi-libro/hitos': () => ({
        body: {
          hitos: [
            { id: 'a', titulo: 'Batalla de Muye', descripcion: 'Algo.', año: -1046, lugar: 'Muye, Henan', pagina: 13, personajes: [{ id: 'p1', nombre: 'Rey Wu de Zhou', rol: 'líder' }] },
            { id: 'b', titulo: 'Sin página', descripcion: 'Otro.', año: -770, lugar: 'Luoyi, Henan', personajes: [] },
          ],
        },
      }),
    });
    const [con, sin] = await makeApi(server).getHitos('mi-libro');
    expect(con).toEqual({ id: 'a', titulo: 'Batalla de Muye', descripcion: 'Algo.', año: -1046, lugar: 'Muye, Henan', pagina: 13, personajes: [{ id: 'p1', nombre: 'Rey Wu de Zhou', rol: 'líder' }] });
    expect(sin.pagina).toBeUndefined();
  });

  it('una página inválida (0, null) no se enseña como botón', () => {
    expect(mapHito({ id: 'a', pagina: 0 }).pagina).toBeUndefined();
    expect(mapHito({ id: 'a', pagina: null }).pagina).toBeUndefined();
    expect(mapHito({ id: 'a' })).toMatchObject({ titulo: '', año: 0, personajes: [] }); // un campo que falte no rompe la pantalla
  });

  it('los personajes convierten los null del servidor en "no se sabe" y conservan los hitos en que salen', async () => {
    const server = fakeServer({
      'GET /books/mi-libro/personajes': () => ({
        body: { personajes: [{ id: 'p1', nombre: 'Rey Wu', nombre_zh: '周武王', rol: 'rey', estado: null, descripcion: 'Un rey.', nacimiento: null, muerte: -1043, hitoIds: ['a', 'b'] }] },
      }),
    });
    const [person] = await makeApi(server).getPersonajes('mi-libro');
    expect(person).toEqual({ id: 'p1', nombre: 'Rey Wu', nombreZh: '周武王', rol: 'rey', estado: undefined, descripcion: 'Un rey.', nacimiento: undefined, muerte: -1043, hitoIds: ['a', 'b'] });
  });

  it('la lista de todos los libros conserva de qué libro es cada hito y en qué libros sale cada personaje', async () => {
    const server = fakeServer({
      'GET /hitos': () => ({ body: { hitos: [{ id: 'a', titulo: 'Muye', año: -1046, fuente: 'libro-a', personajes: [] }, { id: 'b', titulo: 'Sin libro' }] } }),
      'GET /personajes': () => ({ body: { personajes: [{ id: 'p1', nombre: 'Rey Wu', hitoIds: ['a'], fuentes: ['libro-a', 'libro-b'] }] } }),
    });
    const api = makeApi(server);
    const [con, sin] = await api.getAllHitos();
    expect(con.fuente).toBe('libro-a');
    expect(sin.fuente).toBeUndefined();
    expect((await api.getAllPersonajes())[0].fuentes).toEqual(['libro-a', 'libro-b']);
  });

  it('un 502 del servidor llega como un mensaje que se puede enseñar', async () => {
    const server = fakeServer({ 'GET /books/mi-libro/hitos': () => ({ status: 502, body: { error: 'No se pudieron leer los datos del libro. Inténtalo de nuevo.' } }) });
    await expect(makeApi(server).getHitos('mi-libro')).rejects.toThrow(/No se pudieron leer/);
  });
});

describe('subida', () => {
  const presign = (): { body: unknown } => ({ body: { jobId: 'job-1', upload: { url: 'https://s3.test/', fields: { key: 'uploads/job-1/libro.pdf' } } } });
  const books = (slugs: string[] = []) => () => ({ body: { books: slugs.map((slug) => ({ slug })) } });

  it('rechaza lo que no es válido sin llamar a /uploads', async () => {
    const server = fakeServer({ 'GET /books': books(['existente']), 'POST /uploads': presign });
    const api = makeApi(server);
    await expect(api.startUpload(new File(['x'], 'a.txt'), options())).rejects.toThrow(/PDF/);
    await expect(api.startUpload(pdf(), options('Con Espacios'))).rejects.toThrow(/identificador/);
    await expect(api.startUpload(pdf(), { slug: 'libro-nuevo', title: '  ', replaceSource: false })).rejects.toThrow(/título/);
    await expect(api.startUpload(pdf(), options('existente'))).rejects.toThrow(/Reprocesar/);
    expect(server.calls.some((call) => call.method === 'POST')).toBe(false);
  });

  it('pide el permiso con el título y el identificador, sube el PDF y deja el avance a la vista', async () => {
    const server = fakeServer({
      'GET /books': books(),
      'POST /uploads': presign,
      'GET /jobs': () => ({ body: { jobs: [{ jobId: 'job-1', status: 'UPLOADING', source: 'libro-nuevo', createdAt: new Date(NOW - 1000).toISOString() }] } }),
    });
    let report: (fraction: number) => void = () => undefined;
    const uploadFn = vi.fn(async (_post: PresignedPost, _file: File, onProgress: (fraction: number) => void) => {
      report = onProgress;
      return new Promise<void>(() => undefined); // la subida sigue en curso
    });
    const api = makeApi(server, { uploadFn });
    const listener = vi.fn();
    api.subscribe(listener);

    expect(await api.startUpload(pdf('libro.pdf'), options())).toEqual({ jobId: 'job-1' });
    const post = server.calls.find((call) => call.method === 'POST')!;
    expect(JSON.parse(post.body!)).toEqual({ filename: 'libro.pdf', source: 'libro-nuevo', title: 'Libro nuevo', replaceSource: false });
    expect(uploadFn.mock.calls[0][0]).toEqual({ url: 'https://s3.test/', fields: { key: 'uploads/job-1/libro.pdf' } });
    expect(listener).toHaveBeenCalled();

    report(0.5);
    expect((await api.listJobs())[0]).toMatchObject({ status: 'UPLOADING', progress: 0.5, slug: 'libro-nuevo' });
  });

  it('si la subida a S3 falla, el trabajo aparece como fallido con el motivo', async () => {
    const server = fakeServer({
      'GET /books': books(),
      'POST /uploads': presign,
      'GET /jobs': () => ({ body: { jobs: [{ jobId: 'job-1', status: 'UPLOADING', source: 'libro-nuevo', createdAt: new Date(NOW - 1000).toISOString() }] } }),
    });
    const api = makeApi(server, { uploadFn: async () => Promise.reject(new Error('El permiso de subida caducó.')) });
    await api.startUpload(pdf(), options());
    await Promise.resolve();
    expect((await api.listJobs())[0]).toMatchObject({ status: 'FAILED', error: 'El permiso de subida caducó.' });
  });

  it('un libro que ya existe se puede reprocesar si se pide', async () => {
    const server = fakeServer({ 'GET /books': books(['existente']), 'POST /uploads': presign });
    const api = makeApi(server, { uploadFn: async () => undefined });
    await expect(api.startUpload(pdf(), options('existente', true))).resolves.toEqual({ jobId: 'job-1' });
    expect(JSON.parse(server.calls.find((c) => c.method === 'POST')!.body!).replaceSource).toBe(true);
  });
});

describe('avisos de cambios', () => {
  it('pregunta cada pocos segundos SOLO mientras hay trabajos en marcha, y deja de preguntar al darse de baja', async () => {
    vi.useFakeTimers();
    let status = 'RUNNING';
    const server = fakeServer({ 'GET /jobs': () => ({ body: { jobs: [{ jobId: 'j', status, source: 'a', createdAt: new Date(NOW).toISOString() }] } }) });
    const api = makeApi(server, { pollMs: 1000 });
    const listener = vi.fn();
    const stop = api.subscribe(listener);

    await vi.advanceTimersByTimeAsync(3000);
    expect(listener).not.toHaveBeenCalled(); // aún no se sabe que haya nada en marcha

    await api.listJobs(); // la interfaz carga la lista: hay un trabajo RUNNING
    await vi.advanceTimersByTimeAsync(3000);
    expect(listener).toHaveBeenCalledTimes(3);

    status = 'DONE';
    await api.listJobs(); // ya no hay nada en marcha
    listener.mockClear();
    await vi.advanceTimersByTimeAsync(3000);
    expect(listener).not.toHaveBeenCalled();

    status = 'RUNNING';
    await api.listJobs();
    stop();
    await vi.advanceTimersByTimeAsync(3000);
    expect(listener).not.toHaveBeenCalled(); // dado de baja
  });
});

describe('borrar un libro', () => {
  it('pide el borrado con el método DELETE y el token, y avisa a la interfaz de que la lista cambió', async () => {
    const server = fakeServer({ 'DELETE /books/mi-libro': () => ({ body: { deleted: { trabajos: 1 } } }) });
    const api = makeApi(server);
    const listener = vi.fn();
    api.subscribe(listener);
    await api.deleteBook('mi-libro');
    expect(server.calls[0]).toMatchObject({ method: 'DELETE', url: 'https://api.test/books/mi-libro' });
    expect(server.calls[0].headers.authorization).toBe('Bearer tok-123');
    expect(server.calls[0].body).toBeUndefined();
    expect(listener).toHaveBeenCalled();
  });

  it('si el libro se está analizando, el mensaje del servidor llega tal cual para enseñarlo en el diálogo', async () => {
    const server = fakeServer({ 'DELETE /books/mi-libro': () => ({ status: 409, body: { error: 'El libro se está analizando o subiendo. Espera a que termine para borrarlo.' } }) });
    await expect(makeApi(server).deleteBook('mi-libro')).rejects.toThrow(/se está analizando/);
  });

  it('tras borrar, la URL del PDF guardada se olvida (no se firmaría un libro que ya no existe)', async () => {
    const server = fakeServer({
      'GET /books/mi-libro/pdf-url': () => ({ body: { url: 'https://s3/firma', expiresInSeconds: 3600 } }),
      'DELETE /books/mi-libro': () => ({ body: {} }),
    });
    const api = makeApi(server);
    await api.getPdfUrl('mi-libro');
    await api.deleteBook('mi-libro');
    await api.getPdfUrl('mi-libro');
    expect(server.calls.filter((call) => call.url.endsWith('/pdf-url'))).toHaveLength(2); // se volvió a pedir: no salió de la caché
  });

  it('un identificador raro se codifica y no cambia la ruta', async () => {
    const server = fakeServer({});
    await makeApi(server).deleteBook('../otro').catch(() => undefined);
    expect(server.calls[0].url).toBe('https://api.test/books/..%2Fotro');
  });
});

describe('analizar un libro', () => {
  it('pide el análisis con POST, sin cuerpo, y avisa a la interfaz para que empiece a preguntar por el avance', async () => {
    const server = fakeServer({ 'POST /books/mi-libro/analyze': () => ({ status: 202, body: { jobId: 'job-9', replaceSource: false } }) });
    const api = makeApi(server);
    const listener = vi.fn();
    api.subscribe(listener);
    expect(await api.analyzeBook('mi-libro')).toEqual({ jobId: 'job-9' });
    expect(server.calls[0]).toMatchObject({ method: 'POST', url: 'https://api.test/books/mi-libro/analyze' });
    expect(server.calls[0].body).toBeUndefined();
    expect(server.calls[0].headers.authorization).toBe('Bearer tok-123');
    expect(listener).toHaveBeenCalled();
  });

  it('si ya se está analizando, el mensaje del servidor llega tal cual para enseñarlo en el diálogo', async () => {
    const server = fakeServer({ 'POST /books/mi-libro/analyze': () => ({ status: 409, body: { error: 'Este libro ya se está analizando o subiendo. Espera a que termine.' } }) });
    await expect(makeApi(server).analyzeBook('mi-libro')).rejects.toThrow(/ya se está analizando/);
  });

  it('tras pedirlo se sigue preguntando por el avance aunque aún no se hubiera cargado la lista', async () => {
    vi.useFakeTimers();
    const server = fakeServer({ 'POST /books/mi-libro/analyze': () => ({ status: 202, body: { jobId: 'j' } }) });
    const api = makeApi(server, { pollMs: 1000 });
    const listener = vi.fn();
    api.subscribe(listener);
    await api.analyzeBook('mi-libro');
    listener.mockClear();
    await vi.advanceTimersByTimeAsync(3000);
    expect(listener).toHaveBeenCalledTimes(3);
  });

  it('subir un PDF sobre un libro que ya existe: el servidor pide confirmar el reemplazo y la web lo muestra', async () => {
    const server = fakeServer({
      'GET /books': () => ({ body: { books: [] } }), // la web aún no lo sabía (lista vieja): el servidor es quien decide
      'POST /uploads': () => ({ status: 409, body: { error: 'Ya existe un libro con ese identificador. Para reemplazar su PDF hay que confirmarlo.' } }),
    });
    await expect(makeApi(server).startUpload(pdf(), options())).rejects.toThrow(/Ya existe un libro/);
  });
});
