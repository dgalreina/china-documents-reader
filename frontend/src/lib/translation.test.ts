import { describe, expect, it } from 'vitest';
import { PREFETCH_AHEAD, PREFETCH_BEHIND, joinLines, pagesToLoad, splitCjk, splitParagraphs } from './translation';

describe('pagesToLoad', () => {
  it('un salto a la 40 traduce solo la 40, la 41 y la 39 (por ese orden de prioridad)', () => {
    expect(pagesToLoad(40, 100)).toEqual([40, 41, 39]);
    expect([PREFETCH_BEHIND, PREFETCH_AHEAD]).toEqual([1, 1]);
  });

  it('no se sale del libro por ningún lado', () => {
    expect(pagesToLoad(1, 20)).toEqual([1, 2]);
    expect(pagesToLoad(20, 20)).toEqual([20, 19]);
    expect(pagesToLoad(1, 1)).toEqual([1]);
  });

  it('si aún no se sabe cuántas páginas hay, pide la siguiente igualmente', () => {
    expect(pagesToLoad(1, 0)).toEqual([1, 2]);
  });

  it('sin página válida no pide nada', () => {
    expect(pagesToLoad(0, 20)).toEqual([]);
  });
});

describe('splitParagraphs', () => {
  it('separa por líneas en blanco y descarta lo vacío', () => {
    expect(splitParagraphs('Uno.\n\nDos.\n \n\n  Tres.  \n')).toEqual(['Uno.', 'Dos.', 'Tres.']);
    expect(splitParagraphs('   ')).toEqual([]);
  });
});

describe('joinLines', () => {
  it('une las líneas y recompone las palabras partidas con guion', () => {
    expect(joinLines('the capital of the Yin\ndynasty, Shang. The pres-\nent chapter\n  summarizes')).toBe(
      'the capital of the Yin dynasty, Shang. The present chapter summarizes',
    );
  });

  it('un guion de verdad (seguido de mayúscula o número) no se toca', () => {
    expect(joinLines('Warring States-\nPeriod texts 780-\n771')).toBe('Warring States- Period texts 780- 771');
  });
});

describe('splitCjk', () => {
  it('separa los tramos en chino de los de español, sin perder texto', () => {
    const runs = splitCjk('el rey 周武王 (Zhou Wuwang) y 殷 (Yin)');
    expect(runs.filter((r) => r.cjk).map((r) => r.text)).toEqual(['周武王', '殷']);
    expect(runs.map((r) => r.text).join('')).toBe('el rey 周武王 (Zhou Wuwang) y 殷 (Yin)');
  });

  it('un texto sin chino es un solo tramo', () => {
    expect(splitCjk('Solo español.')).toEqual([{ text: 'Solo español.', cjk: false }]);
  });
});
