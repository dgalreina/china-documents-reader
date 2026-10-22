import { describe, expect, it } from 'vitest';
import { mapJob } from '../api/httpApi';
import type { Book, Job } from '../types';
import { analysisState } from './analysis';
import { publishState } from './publish';

const book = (extra: Partial<Book> = {}): Book => ({ slug: 'mi-libro', title: 'Mi libro', filename: 'a.pdf', pageCount: 20, analysis: 'done', createdAt: '2026-01-01', ...extra });
const job = (status: Job['status'], extra: Partial<Job> = {}): Job => ({
  jobId: Math.random().toString(16).slice(2), kind: 'publish', slug: 'mi-libro', filename: 'a.pdf', status, phase: '', progress: null, createdAt: '2026-06-01T00:00:00Z', ...extra,
});

describe('publishState', () => {
  it('un libro sin publicar y sin trabajos no está publicado', () => {
    expect(publishState(book(), [])).toEqual({ status: 'none' });
  });

  it('un trabajo de publicación en marcha manda, y dice si es la vista previa', () => {
    expect(publishState(book(), [job('RUNNING', { dryRun: true })])).toEqual({ status: 'running', preview: true });
    expect(publishState(book({ published: { at: '2026-06-02', chunks: 1, hitos: 1 } }), [job('RUNNING', { dryRun: false })])).toEqual({ status: 'running', preview: false });
  });

  it('publicado y sin cambios desde entonces está "published"; si el libro cambió después, "outdated"', () => {
    const published = { at: '2026-06-02T00:00:00Z', chunks: 10, hitos: 2 };
    expect(publishState(book({ published, updatedAt: '2026-06-01T00:00:00Z' }), []).status).toBe('published');
    expect(publishState(book({ published, updatedAt: '2026-06-03T00:00:00Z' }), []).status).toBe('outdated');
  });

  it('un trabajo de publicación NO cuenta como análisis', () => {
    expect(analysisState(book({ analysis: 'none' }), [job('RUNNING')]).status).toBe('none');
    expect(analysisState(book({ analysis: 'none' }), [job('FAILED', { error: 'x' })]).status).toBe('none');
  });
});

describe('mapJob de una publicación', () => {
  it('convierte el resumen del servidor en el de la web', () => {
    const mapped = mapJob(
      {
        jobId: 'j', status: 'DONE', source: 'a', kind: 'publish', dryRun: true,
        summary: { chunks: 10, chunksReemplazados: 2, hitos: 4, hitosReemplazados: 1, hitosDuplicados: 1, duplicados: [{ titulo: 'Batalla', año: -260, titulo_existente: 'Battle', fuente_existente: 'china-history' }], publicadoAntes: { chunks: 8, hitos: 3 } },
      },
      0,
    );
    expect(mapped.kind).toBe('publish');
    expect(mapped.dryRun).toBe(true);
    expect(mapped.stats).toBeUndefined();
    expect(mapped.publish).toEqual({
      chunks: 10, chunksReplaced: 2, hitos: 4, hitosReplaced: 1, duplicateCount: 1,
      duplicates: [{ titulo: 'Batalla', año: -260, existente: 'Battle', fuente: 'china-history' }], before: { chunks: 8, hitos: 3 },
    });
  });

  it('mientras corre dice si es la vista previa o la publicación real', () => {
    expect(mapJob({ jobId: 'j', status: 'RUNNING', source: 'a', kind: 'publish', dryRun: true }, 0).phase).toMatch(/Comprobando/);
    expect(mapJob({ jobId: 'j', status: 'RUNNING', source: 'a', kind: 'publish', dryRun: false }, 0).phase).toMatch(/Publicando/);
  });
});
