import { createContext, useCallback, useContext, useEffect, useMemo, useRef, useState, type ReactNode } from 'react';
import type { ApiClient, UploadOptions } from '../api/client';
import { stableCovers, type CoverCache } from '../lib/covers';
import type { Book, Hito, Job, OcrEngine, Personaje } from '../types';

interface LibraryValue {
  api: ApiClient;
  books: Book[];
  jobs: Job[];
  /** false hasta que llega la primera respuesta: evita mostrar "no hay libros" mientras aún se está cargando. */
  loaded: boolean;
  /** Mensaje si la última carga falló (sin conexión, sesión caducada...). null si fue bien. */
  error: string | null;
  reload: () => void;
  upload: (file: File, options: UploadOptions) => Promise<{ jobId: string }>;
  /** Arranca el análisis de un libro y refresca la lista. */
  analyze: (slug: string) => Promise<void>;
  /** Arranca el OCR de un libro escaneado con el motor elegido y refresca la lista. */
  startOcr: (slug: string, engine: OcrEngine) => Promise<void>;
  /** Borra un libro con todo lo suyo y refresca la lista. */
  remove: (slug: string) => Promise<void>;
}

const LibraryContext = createContext<LibraryValue | null>(null);

const MIN_GAP_MS = 5_000; // nunca se refresca más seguido que esto, por muchos eventos que lleguen a la vez (volver a la app trae a veces varios seguidos)

/**
 * Mantiene al día la lista de libros y de trabajos: se refresca sola cuando la API avisa de un cambio (progreso, fin...) y, para lo que pase en OTROS dispositivos (subir un
 * PDF desde el ordenador y verlo en el móvil), al volver a la app y al recuperar la conexión. NO hay un refresco periódico: solo cuando el usuario vuelve a mirar. Sin esto,
 * el móvil solo se enteraba al cerrar y abrir la app.
 */
export function LibraryProvider({ api, children }: { api: ApiClient; children: ReactNode }) {
  const [books, setBooks] = useState<Book[]>([]);
  const [jobs, setJobs] = useState<Job[]>([]);
  const [loaded, setLoaded] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const lastRefresh = useRef(0);
  const covers = useRef<CoverCache>(new Map());

  const refresh = useCallback(async () => {
    lastRefresh.current = Date.now();
    try {
      const [nextBooks, nextJobs] = await Promise.all([api.listBooks(), api.listJobs()]);
      setBooks(stableCovers(nextBooks, covers.current, Date.now())); // las portadas conservan su dirección: no parpadean en cada refresco
      setJobs(nextJobs);
      setError(null);
    } catch (problem) {
      setError(problem instanceof Error ? problem.message : 'No se pudieron cargar tus libros.');
    } finally {
      setLoaded(true);
    }
  }, [api]);

  useEffect(() => {
    void refresh();
    return api.subscribe(() => void refresh());
  }, [api, refresh]);

  useEffect(() => {
    const refreshIfSeen = () => {
      if (document.visibilityState !== 'visible' || Date.now() - lastRefresh.current < MIN_GAP_MS) return;
      void refresh();
    };
    document.addEventListener('visibilitychange', refreshIfSeen); // volver a la app (en el móvil, abrirla desde el segundo plano)
    window.addEventListener('focus', refreshIfSeen);
    window.addEventListener('online', refreshIfSeen);
    return () => {
      document.removeEventListener('visibilitychange', refreshIfSeen);
      window.removeEventListener('focus', refreshIfSeen);
      window.removeEventListener('online', refreshIfSeen);
    };
  }, [refresh]);

  const value = useMemo<LibraryValue>(
    () => ({
      api,
      books,
      jobs,
      loaded,
      error,
      reload: () => void refresh(),
      upload: (file, options) => api.startUpload(file, options),
      analyze: async (slug) => {
        await api.analyzeBook(slug);
        await refresh();
      },
      startOcr: async (slug, engine) => {
        await api.startOcr(slug, engine);
        await refresh();
      },
      remove: async (slug) => {
        await api.deleteBook(slug);
        await refresh();
      },
    }),
    [api, books, jobs, loaded, error, refresh],
  );
  return <LibraryContext.Provider value={value}>{children}</LibraryContext.Provider>;
}

export function useLibrary(): LibraryValue {
  const value = useContext(LibraryContext);
  if (!value) throw new Error('useLibrary debe usarse dentro de <LibraryProvider>');
  return value;
}

/** Hitos y personajes de TODOS los libros (cada uno dice de cuál). Solo se piden si la API los tiene; se vuelven a pedir cuando cambia `version` (p. ej. al terminar un análisis o borrar un libro). */
export function useAllBookData(version: string) {
  const { api } = useLibrary();
  const [hitos, setHitos] = useState<Hito[]>([]);
  const [personajes, setPersonajes] = useState<Personaje[]>([]);

  useEffect(() => {
    if (!api.features.hitos) {
      setHitos([]);
      setPersonajes([]);
      return;
    }
    let cancelled = false;
    void Promise.all([api.getAllHitos(), api.getAllPersonajes()]).then(
      ([nextHitos, nextPersonajes]) => {
        if (cancelled) return;
        setHitos(nextHitos);
        setPersonajes(nextPersonajes);
      },
      () => undefined, // un fallo al cargar hitos no debe tumbar la pantalla: la lista queda como estaba
    );
    return () => {
      cancelled = true;
    };
  }, [api, version]);

  return { hitos, personajes };
}
