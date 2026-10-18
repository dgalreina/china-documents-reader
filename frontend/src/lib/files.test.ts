import { describe, expect, it } from 'vitest';
import { MAX_PDF_MB, isValidSlug, suggestSlug, suggestTitle, validatePdf } from './files';

const pdf = (over: Partial<{ name: string; size: number; type: string }> = {}) => ({ name: 'libro.pdf', size: 1000, type: 'application/pdf', ...over });

describe('validatePdf', () => {
  it('acepta un PDF normal', () => expect(validatePdf(pdf())).toBeNull());

  it('rechaza lo que no es un PDF, por el nombre o por el tipo', () => {
    expect(validatePdf(pdf({ name: 'libro.txt', type: 'text/plain' }))).toMatch(/PDF/);
    expect(validatePdf(pdf({ name: 'libro.pdf', type: 'image/png' }))).toMatch(/PDF/);
    expect(validatePdf(pdf({ name: 'libro.PDF' }))).toBeNull(); // la extensión en mayúsculas vale
  });

  it('rechaza el vacío y lo que pasa del máximo, con el tamaño en el mensaje', () => {
    expect(validatePdf(pdf({ size: 0 }))).toMatch(/vacío/);
    const tooBig = validatePdf(pdf({ size: (MAX_PDF_MB + 1) * 1024 * 1024 }));
    expect(tooBig).toMatch(new RegExp(`${MAX_PDF_MB} MB`));
    expect(validatePdf(pdf({ size: MAX_PDF_MB * 1024 * 1024 }))).toBeNull(); // justo el máximo se acepta
  });
});

describe('suggestSlug', () => {
  it('sin acentos, en minúsculas y con guiones', () => {
    expect(suggestSlug('Historia de China (2ª ed.).pdf')).toBe('historia-de-china-2a-ed');
    expect(suggestSlug('Mi_libro  de   historia.PDF')).toBe('mi-libro-de-historia');
  });

  it('el resultado es un slug válido para el backend', () => {
    expect(isValidSlug(suggestSlug('The Oxford Handbook of Early China.pdf'))).toBe(true);
  });

  it('un nombre sin letras latinas no da un slug válido (el backend también pedirá indicarlo a mano)', () => {
    expect(isValidSlug(suggestSlug('中国历史.pdf'))).toBe(false);
  });

  it('se acota a 60 caracteres sin acabar en guion', () => {
    const slug = suggestSlug(`${'palabra '.repeat(20)}.pdf`);
    expect(slug.length).toBeLessThanOrEqual(60);
    expect(slug.endsWith('-')).toBe(false);
  });
});

describe('isValidSlug', () => {
  it('replica las reglas del backend', () => {
    expect(isValidSlug('oxford-handbook')).toBe(true);
    expect(isValidSlug('a')).toBe(false); // mínimo 2
    expect(isValidSlug('Con Mayúsculas')).toBe(false);
    expect(isValidSlug('-empieza-con-guion')).toBe(false);
    expect(isValidSlug('x'.repeat(61))).toBe(false);
  });
});

describe('suggestTitle', () => {
  it('un título legible a partir del nombre', () => expect(suggestTitle('mi_libro_de_historia.pdf')).toBe('Mi libro de historia'));
});
