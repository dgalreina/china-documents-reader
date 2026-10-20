import { describe, expect, it } from 'vitest';
import type { Book } from '../types';
import { COVER_URL_REUSE_MS, stableCovers, type CoverCache } from './covers';

const book = (coverUrl: string | undefined, extra: Partial<Book> = {}): Book => ({ slug: 'libro', title: 'L', filename: 'l.pdf', pageCount: 3, analysis: 'none', createdAt: 'v1', coverUrl, ...extra });

describe('stableCovers', () => {
  it('la primera vez deja la dirección tal cual; las siguientes (otra firma) conservan la primera', () => {
    const cache: CoverCache = new Map();
    expect(stableCovers([book('https://s3/cover?firma=1')], cache, 0)[0].coverUrl).toBe('https://s3/cover?firma=1');
    expect(stableCovers([book('https://s3/cover?firma=2')], cache, 60_000)[0].coverUrl).toBe('https://s3/cover?firma=1'); // no parpadea
  });

  it('si caduca (o se reemplazó el PDF) se coge la nueva', () => {
    const cache: CoverCache = new Map();
    stableCovers([book('https://s3/a')], cache, 0);
    expect(stableCovers([book('https://s3/b')], cache, COVER_URL_REUSE_MS + 1)[0].coverUrl).toBe('https://s3/b');
    expect(stableCovers([book('https://s3/c', { createdAt: 'v2' })], cache, COVER_URL_REUSE_MS + 2)[0].coverUrl).toBe('https://s3/c');
  });

  it('un libro sin portada queda igual y un libro que desaparece se olvida', () => {
    const cache: CoverCache = new Map();
    stableCovers([book('https://s3/a')], cache, 0);
    expect(stableCovers([book(undefined)], cache, 1)[0].coverUrl).toBeUndefined();
    expect(cache.size).toBe(0);
  });
});
