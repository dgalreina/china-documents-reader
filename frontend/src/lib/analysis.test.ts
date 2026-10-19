import { describe, expect, it } from 'vitest';
import type { Book, Job } from '../types';
import { analysisState, estimateAnalysisCost } from './analysis';

const book = (analysis: Book['analysis'], slug = 'mi-libro'): Book => ({ slug, title: 'Mi libro', filename: 'a.pdf', pageCount: 20, analysis, createdAt: '2026-01-01' });
const job = (status: Job['status'], extra: Partial<Job> = {}): Job => ({
  jobId: Math.random().toString(16).slice(2), slug: 'mi-libro', filename: 'a.pdf', status, phase: '', progress: null, createdAt: '2026-06-01T00:00:00Z', ...extra,
});

describe('analysisState', () => {
  it('un libro sin análisis y sin intentos está "sin analizar"', () => {
    expect(analysisState(book('none'), [])).toEqual({ status: 'none' });
  });

  it('uno analizado, sin nada en marcha, está "analizado"', () => {
    expect(analysisState(book('done'), [job('DONE', { kind: 'analysis' })])).toEqual({ status: 'done' });
  });

  it('un análisis en marcha manda, aunque el libro ya estuviera analizado (es un reanálisis)', () => {
    expect(analysisState(book('none'), [job('RUNNING', { kind: 'analysis' })]).status).toBe('running');
    expect(analysisState(book('done'), [job('RUNNING', { kind: 'analysis' })]).status).toBe('running');
    expect(analysisState(book('none'), [job('QUEUED', { kind: 'analysis' })]).status).toBe('running');
  });

  it('una subida en curso NO es un análisis en marcha', () => {
    expect(analysisState(book('none'), [job('UPLOADING', { kind: 'upload' }), job('RUNNING', { kind: 'upload' })]).status).toBe('none');
  });

  it('un trabajo antiguo sin `kind` cuenta como análisis', () => {
    expect(analysisState(book('none'), [job('RUNNING')]).status).toBe('running');
  });

  it('si el último intento falló dice por qué; si el libro ya estaba analizado, el fallo de un reanálisis no lo estropea', () => {
    expect(analysisState(book('none'), [job('FAILED', { kind: 'analysis', error: 'Se cayó OpenAI' })])).toEqual({ status: 'failed', error: 'Se cayó OpenAI' });
    expect(analysisState(book('none'), [job('NEEDS_OCR', { kind: 'analysis', error: 'escaneado' })])).toEqual({ status: 'needs_ocr', error: 'escaneado' });
    expect(analysisState(book('done'), [job('FAILED', { kind: 'analysis', error: 'x' })]).status).toBe('done');
  });

  it('cuenta el intento MÁS RECIENTE: un fallo viejo seguido de un éxito ya no se muestra, y al revés', () => {
    const old = job('FAILED', { kind: 'analysis', createdAt: '2026-01-01T00:00:00Z', error: 'viejo' });
    const recent = job('NEEDS_OCR', { kind: 'analysis', createdAt: '2026-06-01T00:00:00Z', error: 'reciente' });
    expect(analysisState(book('none'), [recent, old])).toEqual({ status: 'needs_ocr', error: 'reciente' });
  });

  it('los trabajos de otro libro no cuentan', () => {
    expect(analysisState(book('none'), [job('RUNNING', { kind: 'analysis', slug: 'otro-libro' })]).status).toBe('none');
  });
});

describe('estimateAnalysisCost', () => {
  it('unos 0,01 USD por página, redondeado a céntimos', () => {
    expect(estimateAnalysisCost(20)).toBe(0.2);
    expect(estimateAnalysisCost(825)).toBe(8.25);
    expect(estimateAnalysisCost(0)).toBe(0);
    expect(estimateAnalysisCost(-5)).toBe(0);
  });
});
