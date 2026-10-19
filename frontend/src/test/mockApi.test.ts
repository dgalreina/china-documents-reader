import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { MockApi, SAMPLE_SLUG } from './mockApi';

const pdf = (name = 'libro.pdf', size = 1000) => new File([new Uint8Array(size)], name, { type: 'application/pdf' });
const options = (slug = 'libro-nuevo', replaceSource = false) => ({ slug, title: 'Libro nuevo', replaceSource });

let api: MockApi;
beforeEach(() => {
  vi.useFakeTimers();
  api = new MockApi({ stepMs: 10, uploadMs: 100, askMs: 5 });
});
afterEach(() => vi.useRealTimers());

const job = async (slug: string) => (await api.listJobs()).find((j) => j.slug === slug)!;
const book = async (slug: string) => (await api.listBooks()).find((b) => b.slug === slug)!;

describe('datos iniciales', () => {
  it('trae el libro de muestra ya procesado, con sus hitos y personajes', async () => {
    expect((await book(SAMPLE_SLUG)).status).toBe('DONE');
    expect(await api.getHitos(SAMPLE_SLUG)).toHaveLength(4);
    expect((await api.getPersonajes(SAMPLE_SLUG)).map((p) => p.nombre)).toContain('Rey Wu de Zhou');
  });
});

describe('subida y análisis', () => {
  it('pasa por subir, analizar y terminar, y deja hitos y estadísticas', async () => {
    await api.startUpload(pdf(), options());
    expect((await job('libro-nuevo')).status).toBe('UPLOADING');

    await vi.advanceTimersByTimeAsync(60);
    expect((await job('libro-nuevo')).progress).toBeGreaterThan(0); // la subida avanza

    await vi.advanceTimersByTimeAsync(60); // a los 120 ms: la subida (100 ms) ya acabó y el análisis (6 pasos de 10 ms) acaba de empezar
    expect((await job('libro-nuevo')).status).toBe('RUNNING');

    await vi.advanceTimersByTimeAsync(500);
    expect((await job('libro-nuevo')).status).toBe('DONE');
    expect((await book('libro-nuevo')).stats?.hitos).toBe(3);
    expect(await api.getHitos('libro-nuevo')).toHaveLength(3);
  });

  it('el progreso del análisis nunca baja y recorre las fases en orden', async () => {
    await api.startUpload(pdf(), options());
    const phases: string[] = [];
    let last = 0;
    for (let i = 0; i < 80; i++) {
      await vi.advanceTimersByTimeAsync(10);
      const j = await job('libro-nuevo');
      if (j.status === 'RUNNING') {
        expect(j.progress).toBeGreaterThanOrEqual(last);
        last = j.progress;
        if (phases[phases.length - 1] !== j.phase) phases.push(j.phase);
      }
    }
    expect(phases).toEqual(['Troceando el PDF', 'Etiquetando y calculando embeddings', 'Extrayendo hitos', 'Guardando hitos y personajes']);
  });

  it('un libro a la vez: el segundo espera en cola con su posición y empieza al terminar el primero', async () => {
    await api.startUpload(pdf('a.pdf'), options('libro-a'));
    await api.startUpload(pdf('b.pdf'), options('libro-b'));
    await vi.advanceTimersByTimeAsync(120); // ambas subidas terminan
    expect((await job('libro-a')).status).toBe('RUNNING');
    const waiting = await job('libro-b');
    expect(waiting.status).toBe('QUEUED');
    expect(waiting.queuePosition).toBe(1);
    expect((await book('libro-b')).status).toBe('QUEUED');

    await vi.advanceTimersByTimeAsync(1000);
    expect((await job('libro-a')).status).toBe('DONE');
    expect((await job('libro-b')).status).toBe('DONE');
  });

  it('un PDF escaneado termina en NEEDS_OCR con su explicación', async () => {
    await api.startUpload(pdf('libro-escaneado.pdf'), options('escaneado'));
    await vi.advanceTimersByTimeAsync(500);
    const j = await job('escaneado');
    expect(j.status).toBe('NEEDS_OCR');
    expect(j.error).toMatch(/OCR/);
  });

  it('un fallo a mitad queda en FAILED con su motivo y libera la cola', async () => {
    await api.startUpload(pdf('libro-error.pdf'), options('con-error'));
    await api.startUpload(pdf('b.pdf'), options('libro-b'));
    await vi.advanceTimersByTimeAsync(1000);
    expect((await job('con-error')).status).toBe('FAILED');
    expect((await job('con-error')).error).toMatch(/OpenAI/);
    expect((await job('libro-b')).status).toBe('DONE'); // el fallo del primero no bloquea al siguiente
  });
});

describe('validación al subir', () => {
  it('rechaza lo que no es un PDF, con el mensaje de la validación', async () => {
    await expect(api.startUpload(new File(['x'], 'a.txt', { type: 'text/plain' }), options())).rejects.toThrow(/PDF/);
  });

  it('rechaza un identificador inválido', async () => {
    await expect(api.startUpload(pdf(), options('Con Espacios'))).rejects.toThrow(/identificador/);
  });

  it('rechaza un libro que ya existe, salvo que se pida reprocesarlo', async () => {
    await expect(api.startUpload(pdf(), options(SAMPLE_SLUG))).rejects.toThrow(/Reprocesar/);
    await expect(api.startUpload(pdf(), options(SAMPLE_SLUG, true))).resolves.toHaveProperty('jobId');
  });

  it('un rechazo no deja trabajos a medias', async () => {
    const before = (await api.listJobs()).length;
    await expect(api.startUpload(new File(['x'], 'a.txt'), options())).rejects.toThrow();
    expect((await api.listJobs()).length).toBe(before);
  });
});

describe('agente', () => {
  const ask = async (slug: string | null, question: string) => {
    const pending = api.ask(slug, question);
    await vi.advanceTimersByTimeAsync(10);
    return pending;
  };

  it('si la respuesta sale del libro, lo dice y cita la página', async () => {
    const answer = await ask(SAMPLE_SLUG, '¿Qué pasó en Muye?');
    expect(answer.origen).toBe('libro');
    expect(answer.fuentes?.[0].pagina).toBe(4);
  });

  it('si no está en el libro, avisa de que es conocimiento general y no cita nada', async () => {
    const answer = await ask(SAMPLE_SLUG, '¿Cuándo se unificó China?');
    expect(answer.origen).toBe('general');
    expect(answer.fuentes).toBeUndefined();
  });

  it('distingue preguntar a un libro o a todos', async () => {
    expect((await ask(SAMPLE_SLUG, 'algo')).text).toMatch(/libro abierto/);
    expect((await ask(null, 'algo')).text).toMatch(/ninguno de los libros/);
  });
});

describe('traducción', () => {
  const translate = async (slug: string, page: number) => {
    const pending = api.getTranslation(slug, page);
    await vi.advanceTimersByTimeAsync(3000);
    return pending;
  };

  it('la primera vez tarda y no viene de la caché; la segunda sale al instante y sí', async () => {
    api = new MockApi({ translateMs: 1000 });
    const pending = api.getTranslation(SAMPLE_SLUG, 3);
    let done = false;
    void pending.then(() => (done = true));
    await vi.advanceTimersByTimeAsync(500);
    expect(done).toBe(false); // aún traduciendo
    await vi.advanceTimersByTimeAsync(600);
    expect(await pending).toMatchObject({ page: 3, cached: false });

    const again = api.getTranslation(SAMPLE_SLUG, 3);
    await vi.advanceTimersByTimeAsync(30);
    expect(await again).toMatchObject({ page: 3, cached: true });
  });

  it('rechaza un libro o una página que no existen, con mensaje en español', async () => {
    await expect(api.getTranslation('no-existe', 1)).rejects.toThrow(/libro/);
    await expect(api.getTranslation(SAMPLE_SLUG, 0)).rejects.toThrow(/página/);
    await expect(api.getTranslation(SAMPLE_SLUG, 21)).rejects.toThrow(/página/); // la muestra tiene 20
  });

  it('el texto trae párrafos separados y conserva los caracteres chinos', async () => {
    const { text } = await translate(SAMPLE_SLUG, 2);
    expect(text.split('\n\n').length).toBeGreaterThan(1);
    expect(text).toMatch(/周武王/);
  });
});

describe('subscribe', () => {
  it('avisa de los cambios y deja de avisar al darse de baja', async () => {
    const listener = vi.fn();
    const unsubscribe = api.subscribe(listener);
    await api.startUpload(pdf(), options());
    expect(listener).toHaveBeenCalled();
    unsubscribe();
    listener.mockClear();
    await vi.advanceTimersByTimeAsync(1000);
    expect(listener).not.toHaveBeenCalled();
  });
});
