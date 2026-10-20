import { describe, expect, it } from 'vitest';
import type { Book, Job } from '../types';
import { estimateOcrCost, needsOcr, ocrState } from './ocr';

const book = (extra: Partial<Book> = {}): Book => ({ slug: 'libro', title: 'Libro', filename: 'l.pdf', pageCount: 554, analysis: 'none', createdAt: '2026-10-20T10:00:00Z', ...extra });
const job = (extra: Partial<Job> = {}): Job => ({ jobId: 'j', kind: 'ocr', slug: 'libro', filename: 'l.pdf', status: 'RUNNING', phase: '', progress: null, createdAt: '2026-10-20T11:00:00Z', ...extra });

describe('coste estimado del OCR', () => {
  it('Textract cuesta unos céntimos y OpenAI unas nueve veces más', () => {
    expect(estimateOcrCost('textract', 554)).toBe(0.83);
    expect(estimateOcrCost('openai', 554)).toBe(7.2);
    expect(estimateOcrCost('textract', -5)).toBe(0);
  });
});

describe('needsOcr', () => {
  it('solo un escaneado sin OCR; un libro antiguo (sin el dato) o con texto, no', () => {
    expect(needsOcr(book({ textLayer: false }))).toBe(true);
    expect(needsOcr(book({ textLayer: false, ocr: { engine: 'textract', pages: 554 } }))).toBe(false);
    expect(needsOcr(book({ textLayer: true }))).toBe(false);
    expect(needsOcr(book())).toBe(false);
  });
});

describe('ocrState', () => {
  it('en marcha si hay un trabajo de OCR activo (aunque ya tuviera OCR: es una repetición)', () => {
    expect(ocrState(book({ textLayer: false }), [job()]).status).toBe('running');
    expect(ocrState(book({ textLayer: false, ocr: { engine: 'textract', pages: 1 } }), [job()]).status).toBe('running');
  });
  it('hecho si el libro lo dice; fallido con su motivo si el último intento falló', () => {
    expect(ocrState(book({ ocr: { engine: 'openai', pages: 1 } }), []).status).toBe('done');
    expect(ocrState(book({ textLayer: false }), [job({ status: 'FAILED', error: 'Textract no pudo leer el PDF' })])).toEqual({ status: 'failed', error: 'Textract no pudo leer el PDF' });
    expect(ocrState(book({ textLayer: false }), []).status).toBe('none');
  });
  it('los análisis y las subidas de ese libro no cuentan como OCR', () => {
    expect(ocrState(book({ textLayer: false }), [job({ kind: 'analysis' }), job({ kind: 'upload' })]).status).toBe('none');
  });
});
