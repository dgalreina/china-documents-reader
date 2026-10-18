// Validación del PDF en el navegador ANTES de pedir la subida. Las reglas reflejan las del backend (lambdas/presign/signer.py), que son
// las que valen de verdad: el navegador solo las comprueba antes para dar un error inmediato y claro.

export const MAX_PDF_MB = 100;
const MAX_PDF_BYTES = MAX_PDF_MB * 1024 * 1024;

/** Mismo patrón que el backend para el slug del libro: minúsculas, números y guiones, de 2 a 60 caracteres. */
const SLUG_PATTERN = /^[a-z0-9][a-z0-9-]{1,59}$/;

export interface FileLike {
  name: string;
  size: number;
  type: string;
}

/** Devuelve un mensaje de error en español, o null si el archivo es válido. */
export function validatePdf(file: FileLike): string | null {
  if (!file.name.toLowerCase().endsWith('.pdf') || (file.type && file.type !== 'application/pdf')) {
    return 'Solo se aceptan archivos PDF.';
  }
  if (file.size <= 0) return 'El archivo está vacío.';
  if (file.size > MAX_PDF_BYTES) {
    return `El PDF pesa ${(file.size / 1024 / 1024).toFixed(1)} MB y el máximo es ${MAX_PDF_MB} MB.`;
  }
  return null;
}

export function isValidSlug(slug: string): boolean {
  return SLUG_PATTERN.test(slug);
}

/** 'Historia de China (2ª ed.).pdf' -> 'historia-de-china-2a-ed': minúsculas, sin acentos, solo letras, números y guiones. */
export function suggestSlug(filename: string): string {
  const base = filename.replace(/\.pdf$/i, '');
  return base
    .normalize('NFKD')
    .replace(/[̀-ͯ]/g, '') // quita los acentos
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, '-')
    .replace(/^-+|-+$/g, '')
    .slice(0, 60)
    .replace(/-+$/, '');
}

/** Un título legible a partir del nombre del archivo. */
export function suggestTitle(filename: string): string {
  const base = filename.replace(/\.pdf$/i, '').replace(/[_]+/g, ' ').replace(/\s+/g, ' ').trim();
  return base.charAt(0).toUpperCase() + base.slice(1);
}
