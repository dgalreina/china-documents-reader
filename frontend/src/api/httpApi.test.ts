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
      slug: 'a', title: 'A', pageCount: 20, status: 'DONE', stats: { chunks: 58, hitos: 2, personajes: 0, costeEstimadoUsd: 0 },
    });
    expect(mapBook({ slug: 'sin-titulo' }).title).toBe('sin-titulo');
  });

  it('la portada llega como URL; vacía o null es "sin portada" (la lista enseña el recambio)', () => {
    expect(mapBook({ slug: 'a', coverUrl: 'https://s3/portada?firma' }).coverUrl).toBe('https://s3/portada?firma');
    expect(mapBook({ slug: 'a', coverUrl: null }).coverUrl).toBeUndefined();
    expect(mapBook({ slug: 'a' }).coverUrl).toBeUndefined();
    expect(mapJob({ jobId: 'j', status: 'RUNNING', source: 'a', coverUrl: 'https://s3/p' }, NOW).coverUrl).toBe('https://s3/p');
    expect(mapJob({ jobId: 'j', status: 'RUNNING', source: 'a', coverUrl: '' }, NOW).coverUrl).toBeUndefined();
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

  it('el agente aún no existe: la interfaz lo sabe y no enseña su pestaña', async () => {
    const api = makeApi(fakeServer({}));
    expect(api.features).toEqual({ hitos: true, agent: false });
    await expect(api.ask()).rejects.toThrow(/no está disponible/);
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
