import { createContext, useCallback, useContext, useEffect, useMemo, useState, type ReactNode } from 'react';
import type { ApiClient, UploadOptions } from '../api/client';
import type { Book, Hito, Job, Personaje } from '../types';

interface LibraryValue {
  api: ApiClient;
  books: Book[];
  jobs: Job[];
  /** false hasta que llega la primera respuesta: evita mostrar "no hay libros" mientras aún se está cargando. */
  loaded: boolean;
  upload: (file: File, options: UploadOptions) => Promise<{ jobId: string }>;
}

const LibraryContext = createContext<LibraryValue | null>(null);

/** Mantiene al día la lista de libros y de trabajos: se refresca sola cuando la API avisa de un cambio (progreso, cola, fin...). */
export function LibraryProvider({ api, children }: { api: ApiClient; children: ReactNode }) {
  const [books, setBooks] = useState<Book[]>([]);
  const [jobs, setJobs] = useState<Job[]>([]);
  const [loaded, setLoaded] = useState(false);

  const refresh = useCallback(async () => {
    const [nextBooks, nextJobs] = await Promise.all([api.listBooks(), api.listJobs()]);
    setBooks(nextBooks);
    setJobs(nextJobs);
    setLoaded(true);
  }, [api]);

  useEffect(() => {
    void refresh();
    return api.subscribe(() => void refresh());
  }, [api, refresh]);

  const value = useMemo<LibraryValue>(
    () => ({ api, books, jobs, loaded, upload: (file, options) => api.startUpload(file, options) }),
    [api, books, jobs, loaded],
  );
  return <LibraryContext.Provider value={value}>{children}</LibraryContext.Provider>;
}

export function useLibrary(): LibraryValue {
  const value = useContext(LibraryContext);
  if (!value) throw new Error('useLibrary debe usarse dentro de <LibraryProvider>');
  return value;
}

/** Hitos y personajes del libro abierto. Se vuelven a pedir cuando el libro termina de procesarse (su estado pasa a DONE). */
export function useBookData(slug: string | undefined, status: Book['status'] | undefined) {
  const { api } = useLibrary();
  const [hitos, setHitos] = useState<Hito[]>([]);
  const [personajes, setPersonajes] = useState<Personaje[]>([]);

  useEffect(() => {
    if (!slug) {
      setHitos([]);
      setPersonajes([]);
      return;
    }
    let cancelled = false;
    void Promise.all([api.getHitos(slug), api.getPersonajes(slug)]).then(([nextHitos, nextPersonajes]) => {
      if (cancelled) return;
      setHitos(nextHitos);
      setPersonajes(nextPersonajes);
    });
    return () => {
      cancelled = true;
    };
  }, [api, slug, status === 'DONE']);

  return { hitos, personajes };
}
