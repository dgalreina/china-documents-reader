import { describe, expect, it } from 'vitest';
import type { Book, Job } from '../types';
import { finishedRecently, pendingUploads } from './uploads';

const NOW = Date.parse('2026-10-20T12:00:00Z');
const job = (extra: Partial<Job>): Job => ({ jobId: 'j', kind: 'upload', slug: 'libro', filename: 'x.pdf', status: 'UPLOADING', phase: '', progress: null, createdAt: '2026-10-20T11:59:00Z', ...extra });
const book = (slug: string): Book => ({ slug, title: slug, filename: 'x.pdf', pageCount: 3, analysis: 'none', createdAt: '2026-10-20T11:00:00Z' });

describe('subidas pendientes', () => {
  it('una subida en marcha que aún no es libro se enseña', () => {
    expect(pendingUploads([job({})], [], NOW)).toHaveLength(1);
  });
  it('en cuanto el libro está en el catálogo, ya no es una subida pendiente', () => {
    expect(pendingUploads([job({})], [book('libro')], NOW)).toHaveLength(0);
  });
  it('una subida recién terminada sin libro todavía (la lista no se ha refrescado) se sigue enseñando, pero no una vieja', () => {
    const recent = job({ status: 'DONE', finishedAt: '2026-10-20T11:59:50Z' });
    const old = job({ status: 'DONE', finishedAt: '2026-10-20T11:00:00Z' });
    expect(pendingUploads([recent], [], NOW)).toHaveLength(1);
    expect(pendingUploads([old], [], NOW)).toHaveLength(0); // p. ej. el libro se borró después: no debe quedar una tarjeta fantasma
  });
  it('los análisis y las subidas fallidas no cuentan', () => {
    expect(pendingUploads([job({ kind: 'analysis', status: 'RUNNING' }), job({ status: 'FAILED' })], [], NOW)).toHaveLength(0);
  });
  it('finishedRecently: sin fecha o con fecha rara es que no', () => {
    expect(finishedRecently(job({}), NOW)).toBe(false);
    expect(finishedRecently(job({ finishedAt: 'no es una fecha' }), NOW)).toBe(false);
  });
});
