import { describe, expect, it } from 'vitest';
import { readCachedTranslation, translationKey, writeCachedTranslation } from './translationCache';

describe('caché de traducciones del navegador', () => {
  it('la clave cambia si se reemplaza el PDF (otra fecha de alta) o cambia la página', () => {
    const a = translationKey('libro', '2026-01-01', 3);
    expect(translationKey('libro', '2026-02-01', 3)).not.toBe(a);
    expect(translationKey('libro', '2026-01-01', 4)).not.toBe(a);
    expect(translationKey('otro', '2026-01-01', 3)).not.toBe(a);
  });

  it('sin IndexedDB (como aquí, en Node) no falla: no hay caché y ya', async () => {
    await expect(writeCachedTranslation('k', 'texto')).resolves.toBeUndefined();
    await expect(readCachedTranslation('k')).resolves.toBeUndefined();
  });
});
