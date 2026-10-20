// Caché de traducciones en el navegador (IndexedDB), para que al volver a abrir la web las páginas ya traducidas salgan AL INSTANTE, sin esperar al servidor.
// El servidor ya las guarda (S3) y no vuelve a gastar en OpenAI; esto solo evita la espera y el parpadeo del inglés. Todo es opcional: sin IndexedDB (ventana
// privada, navegador antiguo) o si algo falla, simplemente no hay caché y se pide al servidor como siempre.

const DB_NAME = 'document-reader';
const STORE = 'translations';

/** Súbelo si cambia el texto que devuelve la traducción (p. ej. una nueva versión del prompt en el servidor): las entradas viejas dejan de usarse. */
export const TRANSLATION_CACHE_VERSION = 'v1';

/** La clave de una página: el libro y su fecha de alta (cambia al reemplazar el PDF, así no se enseña la traducción del PDF anterior), la versión y la página. */
export function translationKey(slug: string, bookStamp: string | undefined, page: number): string {
  return `${TRANSLATION_CACHE_VERSION}|${slug}|${bookStamp ?? ''}|${page}`;
}

let dbPromise: Promise<IDBDatabase | null> | null = null;

function openDb(): Promise<IDBDatabase | null> {
  dbPromise ??= new Promise((resolve) => {
    try {
      if (typeof indexedDB === 'undefined') return resolve(null);
      const request = indexedDB.open(DB_NAME, 1);
      request.onupgradeneeded = () => request.result.createObjectStore(STORE);
      request.onsuccess = () => resolve(request.result);
      request.onerror = () => resolve(null);
      request.onblocked = () => resolve(null);
    } catch {
      resolve(null);
    }
  });
  return dbPromise;
}

export async function readCachedTranslation(key: string): Promise<string | undefined> {
  const db = await openDb();
  if (!db) return undefined;
  return new Promise((resolve) => {
    try {
      const request = db.transaction(STORE, 'readonly').objectStore(STORE).get(key);
      request.onsuccess = () => resolve(typeof request.result === 'string' && request.result.trim() ? request.result : undefined); // un texto vacío (página sin texto antes del OCR) no es una traducción
      request.onerror = () => resolve(undefined);
    } catch {
      resolve(undefined);
    }
  });
}

export async function writeCachedTranslation(key: string, text: string): Promise<void> {
  if (!text.trim()) return; // lo vacío no se guarda: puede dejar de serlo (p. ej. tras hacer OCR al libro)
  const db = await openDb();
  if (!db) return;
  try {
    db.transaction(STORE, 'readwrite').objectStore(STORE).put(text, key);
  } catch {
    /* sin espacio o sin permiso: no pasa nada, se vuelve a pedir al servidor */
  }
}
