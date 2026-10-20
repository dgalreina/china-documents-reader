import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import type { AskEvent } from '../api/client';
import { MockApi, SAMPLE_SLUG } from './mockApi';

const pdf = (name = 'libro.pdf', size = 1000) => new File([new Uint8Array(size)], name, { type: 'application/pdf' });
const options = (slug = 'libro-nuevo', replaceSource = false) => ({ slug, title: 'Libro nuevo', replaceSource });

let api: MockApi;
beforeEach(() => {
  vi.useFakeTimers();
  api = new MockApi({ stepMs: 10, uploadMs: 100, askMs: 5 });
});
afterEach(() => vi.useRealTimers());

const job = async (slug: string, kind?: 'upload' | 'analysis') => (await api.listJobs()).find((j) => j.slug === slug && (!kind || j.kind === kind))!;
const book = async (slug: string) => (await api.listBooks()).find((b) => b.slug === slug)!;
/** Sube un libro y espera a que la subida termine (queda "sin analizar"). */
const upload = async (name = 'libro.pdf', slug = 'libro-nuevo') => {
  await api.startUpload(pdf(name), options(slug));
  await vi.advanceTimersByTimeAsync(120);
};

describe('datos iniciales', () => {
  it('trae el libro de muestra ya analizado, con sus hitos y personajes', async () => {
    expect((await book(SAMPLE_SLUG)).analysis).toBe('done');
    expect(await api.getHitos(SAMPLE_SLUG)).toHaveLength(4);
    expect((await api.getPersonajes(SAMPLE_SLUG)).map((p) => p.nombre)).toContain('Rey Wu de Zhou');
  });
});

describe('subir (no analiza)', () => {
  it('la subida avanza y al terminar deja el libro SIN ANALIZAR: sin hitos, sin estadísticas y sin ningún análisis en marcha', async () => {
    await api.startUpload(pdf(), options());
    expect((await job('libro-nuevo', 'upload')).status).toBe('UPLOADING');
    expect(await book('libro-nuevo')).toBeUndefined(); // hasta que no termina la subida no es un libro

    await vi.advanceTimersByTimeAsync(60);
    expect((await job('libro-nuevo', 'upload')).progress).toBeGreaterThan(0); // la subida avanza

    await vi.advanceTimersByTimeAsync(60);
    expect((await job('libro-nuevo', 'upload')).status).toBe('DONE');
    expect(await book('libro-nuevo')).toMatchObject({ analysis: 'none', title: 'Libro nuevo' });
    expect((await book('libro-nuevo')).stats).toBeUndefined();
    expect(await api.getHitos('libro-nuevo')).toEqual([]);
    expect(await job('libro-nuevo', 'analysis')).toBeUndefined(); // NADA arrancó el análisis

    await vi.advanceTimersByTimeAsync(2000);
    expect(await job('libro-nuevo', 'analysis')).toBeUndefined(); // ni pasado un buen rato
  });

  it('un PDF dañado acaba en FAILED con su motivo y no crea el libro', async () => {
    await upload('libro-danado.pdf', 'roto');
    expect(await job('roto', 'upload')).toMatchObject({ status: 'FAILED', error: expect.stringMatching(/dañado/) });
    expect(await book('roto')).toBeUndefined();
  });

  it('reemplazar el PDF de un libro analizado lo deja sin analizar y borra sus hitos', async () => {
    await upload();
    await api.analyzeBook('libro-nuevo');
    await vi.advanceTimersByTimeAsync(500);
    expect(await book('libro-nuevo')).toMatchObject({ analysis: 'done' });
    expect(await api.getHitos('libro-nuevo')).toHaveLength(3);

    await api.startUpload(pdf('otro.pdf'), options('libro-nuevo', true));
    await vi.advanceTimersByTimeAsync(120);
    expect(await book('libro-nuevo')).toMatchObject({ analysis: 'none', filename: 'otro.pdf' });
    expect(await api.getHitos('libro-nuevo')).toEqual([]);
  });
});

describe('analizar (se pide aparte)', () => {
  it('pasa por las fases, termina y deja el libro analizado con hitos y estadísticas', async () => {
    await upload();
    await api.analyzeBook('libro-nuevo');
    expect((await job('libro-nuevo', 'analysis')).status).toBe('RUNNING');

    await vi.advanceTimersByTimeAsync(600);
    expect((await job('libro-nuevo', 'analysis')).status).toBe('DONE');
    expect(await book('libro-nuevo')).toMatchObject({ analysis: 'done', stats: { hitos: 3 } });
    expect(await api.getHitos('libro-nuevo')).toHaveLength(3);
  });

  it('el progreso nunca baja y recorre las fases en orden', async () => {
    await upload();
    await api.analyzeBook('libro-nuevo');
    const phases: string[] = [];
    let last = 0;
    for (let i = 0; i < 80; i++) {
      await vi.advanceTimersByTimeAsync(10);
      const j = await job('libro-nuevo', 'analysis');
      if (j.status === 'RUNNING') {
        expect(j.progress).not.toBeNull();
        expect(j.progress!).toBeGreaterThanOrEqual(last);
        last = j.progress!;
        if (phases[phases.length - 1] !== j.phase) phases.push(j.phase);
      }
    }
    expect(phases).toEqual(['Troceando el PDF', 'Etiquetando y calculando embeddings', 'Extrayendo hitos', 'Guardando hitos y personajes']);
  });

  it('un libro a la vez: el segundo espera en cola con su posición y empieza al terminar el primero', async () => {
    await upload('a.pdf', 'libro-a');
    await upload('b.pdf', 'libro-b');
    await api.analyzeBook('libro-a');
    await api.analyzeBook('libro-b');
    expect((await job('libro-a', 'analysis')).status).toBe('RUNNING');
    const waiting = await job('libro-b', 'analysis');
    expect(waiting.status).toBe('QUEUED');
    expect(waiting.queuePosition).toBe(1);

    await vi.advanceTimersByTimeAsync(1500);
    expect((await job('libro-a', 'analysis')).status).toBe('DONE');
    expect((await job('libro-b', 'analysis')).status).toBe('DONE');
  });

  it('un PDF escaneado termina en NEEDS_OCR con su explicación y el libro sigue sin analizar', async () => {
    await upload('libro-escaneado.pdf', 'escaneado');
    await api.analyzeBook('escaneado');
    await vi.advanceTimersByTimeAsync(500);
    const j = await job('escaneado', 'analysis');
    expect(j.status).toBe('NEEDS_OCR');
    expect(j.error).toMatch(/OCR/);
    expect((await book('escaneado')).analysis).toBe('none');
  });

  it('un fallo a mitad queda en FAILED con su motivo y libera la cola', async () => {
    await upload('libro-error.pdf', 'con-error');
    await upload('b.pdf', 'libro-b');
    await api.analyzeBook('con-error');
    await api.analyzeBook('libro-b');
    await vi.advanceTimersByTimeAsync(1500);
    expect((await job('con-error', 'analysis')).status).toBe('FAILED');
    expect((await job('con-error', 'analysis')).error).toMatch(/OpenAI/);
    expect((await job('libro-b', 'analysis')).status).toBe('DONE'); // el fallo del primero no bloquea al siguiente
  });

  it('no se analiza dos veces a la vez, ni un libro que no existe', async () => {
    await upload();
    await api.analyzeBook('libro-nuevo');
    await expect(api.analyzeBook('libro-nuevo')).rejects.toThrow(/ya se está/);
    await expect(api.analyzeBook('fantasma')).rejects.toThrow(/No existe/);
  });
});

describe('validación al subir', () => {
  it('rechaza lo que no es un PDF, con el mensaje de la validación', async () => {
    await expect(api.startUpload(new File(['x'], 'a.txt', { type: 'text/plain' }), options())).rejects.toThrow(/PDF/);
  });

  it('rechaza un identificador inválido', async () => {
    await expect(api.startUpload(pdf(), options('Con Espacios'))).rejects.toThrow(/identificador/);
  });

  it('un libro que ya existe pide confirmar el reemplazo; confirmado, se sube', async () => {
    await expect(api.startUpload(pdf(), options(SAMPLE_SLUG))).rejects.toThrow(/reemplazo/);
    await expect(api.startUpload(pdf(), options(SAMPLE_SLUG, true))).resolves.toHaveProperty('jobId');
  });

  it('no se sube sobre un libro que se está analizando', async () => {
    await upload();
    await api.analyzeBook('libro-nuevo');
    await expect(api.startUpload(pdf(), options('libro-nuevo', true))).rejects.toThrow(/analizando o subiendo/);
  });

  it('un rechazo no deja trabajos a medias', async () => {
    const before = (await api.listJobs()).length;
    await expect(api.startUpload(new File(['x'], 'a.txt'), options())).rejects.toThrow();
    expect((await api.listJobs()).length).toBe(before);
  });
});

describe('agente', () => {
  /** Hace la pregunta, deja correr el reloj y devuelve los eventos que fueron llegando. */
  const ask = async (slug: string | null, question: string) => {
    const events: AskEvent[] = [];
    const pending = api.ask({ slug, question, history: [] }, (event) => events.push(event));
    await vi.advanceTimersByTimeAsync(2000);
    await pending;
    return events;
  };
  const textOf = (events: AskEvent[]) => events.flatMap((event) => (event.type === 'delta' ? [event.text] : [])).join('');

  it('la respuesta llega en flujo: primero las citas, luego trozos de texto y al final "fin"', async () => {
    const events = await ask(SAMPLE_SLUG, '¿Qué pasó en Muye?');
    expect(events[0]).toMatchObject({ type: 'fuentes', fuentes: [{ pagina: 4, slug: SAMPLE_SLUG }] });
    expect(events.filter((event) => event.type === 'delta').length).toBeGreaterThan(3); // en trozos, no de golpe
    expect(textOf(events)).toMatch(/Muye/);
    expect(events[events.length - 1]).toEqual({ type: 'fin', origen: 'libro' });
  });

  it('si no está en el libro, lo dice, no cita nada y no se presenta como del libro', async () => {
    const events = await ask(SAMPLE_SLUG, '¿Cuándo se unificó China?');
    expect(events[0]).toEqual({ type: 'fuentes', fuentes: [] });
    expect(events[events.length - 1]).toEqual({ type: 'fin', origen: 'ninguno' });
  });

  it('distingue preguntar a un libro o a todos', async () => {
    expect(textOf(await ask(SAMPLE_SLUG, 'algo'))).toMatch(/libro abierto/);
    expect(textOf(await ask(null, 'algo'))).toMatch(/ninguno de los libros/);
  });

  it('un fallo a mitad llega como evento "error"', async () => {
    const events = await ask(SAMPLE_SLUG, 'provoca un fallo');
    expect(events[events.length - 1]).toMatchObject({ type: 'error' });
  });

  it('se puede detener: rechaza con AbortError y deja de emitir', async () => {
    const controller = new AbortController();
    const events: AskEvent[] = [];
    const pending = api.ask({ slug: SAMPLE_SLUG, question: '¿Qué pasó en Muye?', history: [] }, (event) => events.push(event), controller.signal);
    const caught = pending.catch((error: unknown) => error);
    await vi.advanceTimersByTimeAsync(10);
    controller.abort();
    await vi.advanceTimersByTimeAsync(2000);
    expect(((await caught) as Error).name).toBe('AbortError');
    expect(events[events.length - 1]?.type).not.toBe('fin');
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

describe('borrar', () => {
  it('quita el libro, sus trabajos y sus datos, y avisa a la interfaz', async () => {
    const listener = vi.fn();
    api.subscribe(listener);
    await api.deleteBook(SAMPLE_SLUG);
    expect((await api.listBooks()).map((b) => b.slug)).not.toContain(SAMPLE_SLUG);
    expect((await api.listJobs()).some((j) => j.slug === SAMPLE_SLUG)).toBe(false);
    expect(await api.getHitos(SAMPLE_SLUG)).toEqual([]);
    expect(listener).toHaveBeenCalled();
  });

  it('no deja borrar mientras se sube o se analiza, ni un libro que no existe', async () => {
    await api.startUpload(pdf(), options());
    await expect(api.deleteBook('libro-nuevo')).rejects.toThrow(/se está analizando/);
    await expect(api.deleteBook('fantasma')).rejects.toThrow(/No existe/);
  });
});
