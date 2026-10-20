import { describe, expect, it } from 'vitest';
import { formatCost, formatPercent, formatRange, formatUsd, formatYear } from './format';

describe('formatYear', () => {
  it('los años negativos son a. C. y los positivos d. C.', () => {
    expect(formatYear(-1046)).toBe('1046 a. C.');
    expect(formatYear(618)).toBe('618 d. C.');
  });
});

describe('formatRange', () => {
  it('un solo año si inicio y fin coinciden', () => {
    expect(formatRange(-1046, -1046)).toBe('1046 a. C.');
    expect(formatRange(-1046, -771)).toBe('1046 a. C. – 771 a. C.');
  });
});

describe('formatCost (euros)', () => {
  it('convierte los dólares del backend a euros, con coma decimal', () => {
    expect(formatCost(4.26)).toBe('3,71 €'); // el análisis del libro de 554 páginas
    expect(formatCost(0.83)).toBe('0,72 €');
    expect(formatCost(0)).toBe('0,00 €');
  });
  it('por debajo de un céntimo, "< 0,01 €"', () => {
    expect(formatCost(0.005)).toBe('< 0,01 €');
  });
});

describe('formatUsd', () => {
  it('usa la coma decimal y no enseña $0,00 para costes minúsculos', () => {
    expect(formatUsd(2.9)).toBe('$2,90');
    expect(formatUsd(0.005)).toBe('< $0,01');
    expect(formatUsd(0)).toBe('$0,00');
  });
});

describe('formatPercent', () => {
  it('redondea y acota entre 0 y 100', () => {
    expect(formatPercent(0.456)).toBe('46 %');
    expect(formatPercent(1.7)).toBe('100 %');
    expect(formatPercent(-1)).toBe('0 %');
  });
});
