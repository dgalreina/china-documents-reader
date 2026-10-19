/** Lo que la web necesita saber del despliegue. Lo escribe el despliegue en `config.json` (ver infra/README.md): así la web no se recompila por cambiar de entorno. */
export interface AppConfig {
  region: string;
  apiUrl: string;
  userPoolId: string;
  userPoolClientId: string;
}

const text = (value: unknown): string => (typeof value === 'string' ? value.trim() : '');

export function parseConfig(raw: unknown): AppConfig {
  const data = (raw ?? {}) as Record<string, unknown>;
  const config = {
    region: text(data.region),
    apiUrl: text(data.apiUrl).replace(/\/+$/, ''), // sin barra final: las rutas ya empiezan por "/"
    userPoolId: text(data.userPoolId),
    userPoolClientId: text(data.userPoolClientId),
  };
  const missing = Object.entries(config)
    .filter(([, value]) => !value)
    .map(([key]) => key);
  if (missing.length) throw new Error(`Falta en config.json: ${missing.join(', ')}.`);
  if (!/^https?:\/\//.test(config.apiUrl)) throw new Error('apiUrl de config.json debe empezar por https://');
  return config;
}

/** index.html pide la configuración nada más cargar (en paralelo con el JavaScript) y deja la promesa aquí. */
declare global {
  interface Window {
    __configPromise?: Promise<unknown>;
  }
}

export async function loadConfig(fetchFn: typeof fetch = fetch, early: Promise<unknown> | undefined = typeof window === 'undefined' ? undefined : window.__configPromise): Promise<AppConfig> {
  // Si la petición temprana ya trajo una configuración válida, se usa y no se repite. Si falló o vino rara, se sigue por el camino normal, que explica el error.
  if (early) {
    try {
      return parseConfig(await early);
    } catch {
      /* se vuelve a pedir abajo */
    }
  }
  let response: Response;
  try {
    response = await fetchFn('/config.json', { cache: 'no-store' });
  } catch {
    throw new Error('No se pudo cargar la configuración de la web. Comprueba tu conexión.');
  }
  let data: unknown;
  try {
    data = response.ok ? await response.json() : null;
  } catch {
    data = null; // al desarrollar, Vite contesta con index.html a lo que no existe: no es JSON
  }
  if (!data) {
    throw new Error('No se encontró config.json. Al desarrollar en local, créalo con "node infra/scripts/write-front-config.mjs" (ver infra/README.md).');
  }
  return parseConfig(data);
}
