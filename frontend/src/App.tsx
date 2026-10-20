import { useEffect, useState } from 'react';
import { Navigate, Route, Routes, useNavigate, useParams } from 'react-router-dom';
import type { ApiClient } from './api/client';
import { AnalyzeDialog } from './components/AnalyzeDialog';
import { Drawer } from './components/Drawer';
import { CenteredSpinner, LoadingShell } from './components/Spinner';
import { ChevronIcon } from './components/icons';
import { PdfGate } from './components/PdfGate';
import { PdfViewer } from './components/PdfViewer';
import { ReaderView } from './components/ReaderView';
import { AgentTab } from './components/tabs/AgentTab';
import { FilesTab } from './components/tabs/FilesTab';
import { HitosTab } from './components/tabs/HitosTab';
import { PersonajesTab } from './components/tabs/PersonajesTab';
import { ProgressTab } from './components/tabs/ProgressTab';
import { analysisState } from './lib/analysis';
import { HEADER_HEIGHT } from './lib/layout';
import { LibraryProvider, useAllBookData, useLibrary } from './state/Library';
import { useTranslations } from './state/Translations';
import { loadPdfjs, usePdfDocument } from './state/usePdfDocument';
import { usePdfUrl } from './state/usePdfUrl';
import type { Book, JumpTarget, ReadMode, TabId } from './types';

interface AppProps {
  api: ApiClient;
  onSignOut: () => void;
}

export default function App({ api, onSignOut }: AppProps) {
  // pdf.js empieza a descargarse ya, mientras se carga la lista de libros: cuando se abra uno, ya estará (o casi).
  useEffect(() => {
    void loadPdfjs().catch(() => undefined);
  }, []);

  return (
    <LibraryProvider api={api}>
      <Routes>
        <Route path="/" element={<Home onSignOut={onSignOut} />} />
        <Route path="/libros/:slug" element={<Workspace onSignOut={onSignOut} />} />
        <Route path="*" element={<Navigate to="/" replace />} />
      </Routes>
    </LibraryProvider>
  );
}

/** La raíz lleva al primer libro (o enseña la pantalla vacía si aún no hay ninguno). */
function Home({ onSignOut }: { onSignOut: () => void }) {
  const { books, loaded } = useLibrary();
  if (!loaded) return <LoadingShell />;
  const first = books[0]; // la lista llega ordenada: el más reciente primero
  return first ? <Navigate to={`/libros/${first.slug}`} replace /> : <Workspace onSignOut={onSignOut} />;
}

const isDesktop = () => window.matchMedia('(min-width: 768px)').matches;

const MODE_KEY = 'reader-mode';
// El modo de lectura elegido se recuerda entre visitas. El almacenamiento del navegador puede fallar (ventana privada...): entonces, original.
const loadMode = (): ReadMode => {
  try {
    return localStorage.getItem(MODE_KEY) === 'es' ? 'es' : 'original';
  } catch {
    return 'original';
  }
};

const Message = ({ children }: { children: React.ReactNode }) => <div className="flex h-full items-center justify-center p-6 text-center text-stone">{children}</div>;

function Workspace({ onSignOut }: { onSignOut: () => void }) {
  const { slug } = useParams();
  const navigate = useNavigate();
  const { api: client, books, jobs, loaded, error, reload, upload, analyze, remove } = useLibrary();
  const book = books.find((candidate) => candidate.slug === slug);
  const pendingJob = !book && slug ? jobs.find((job) => job.slug === slug) : undefined;

  // El panel empieza cerrado (el PDF ocupa todo el ancho) y lo abre el usuario con la lengüeta del borde derecho.
  const [drawerOpen, setDrawerOpen] = useState(false);
  const [tab, setTab] = useState<TabId>('archivos');
  const [currentPage, setCurrentPage] = useState(1);
  const [target, setTarget] = useState<JumpTarget | null>(null);
  const [mode, setMode] = useState<ReadMode>(loadMode);
  const [analyzing, setAnalyzing] = useState<Book | null>(null); // el libro cuyo análisis se está a punto de pedir (se confirma el coste antes)

  // Hitos y personajes de TODOS los libros; se vuelven a pedir cuando cambia alguno (p. ej. al terminar un análisis o al borrar un libro).
  const { hitos, personajes } = useAllBookData(books.map((candidate) => `${candidate.slug}:${candidate.analysis}:${candidate.updatedAt ?? ''}`).join('|'));
  const { url: pdfUrl, error: pdfError } = usePdfUrl(client, book?.slug); // un libro del catálogo siempre tiene su PDF, analizado o no
  const analysis = book ? analysisState(book, jobs) : null;
  const pdf = usePdfDocument(pdfUrl);
  const pageCount = pdf.doc?.numPages ?? 0;
  const translations = useTranslations(client, pdfUrl ? book?.slug ?? null : null, book?.createdAt);
  const activeJobs = jobs.filter((job) => job.status === 'UPLOADING' || job.status === 'QUEUED' || job.status === 'RUNNING').length;

  // Solo las pestañas que tienen datos reales detrás.
  const tabs: TabId[] = ['archivos', 'progreso', ...(client.features.hitos ? (['hitos', 'personajes'] as const) : []), ...(client.features.agent ? (['agente'] as const) : [])];

  const goToPage = (page: number) => {
    setTarget({ page, nonce: Date.now() });
    if (!isDesktop()) setDrawerOpen(false); // en el móvil, el panel taparía la página a la que se salta
  };
  const changeMode = (next: ReadMode) => {
    if (next === mode) return;
    setMode(next);
    try {
      localStorage.setItem(MODE_KEY, next);
    } catch {
      /* sin almacenamiento: no se recuerda, y ya */
    }
    setTarget({ page: currentPage, nonce: Date.now(), instant: true }); // el otro visor arranca en la página que se estaba leyendo
  };
  // Ir a una página de un libro: si es otro distinto del abierto, se abre y la página queda como destino (el visor salta a ella al cargar).
  const goTo = (targetSlug: string, page: number) => {
    if (targetSlug === slug) return goToPage(page);
    navigate(`/libros/${targetSlug}`);
    setCurrentPage(page);
    setTarget({ page, nonce: Date.now(), instant: true });
    if (!isDesktop()) setDrawerOpen(false);
  };
  const select = (nextSlug: string) => {
    navigate(`/libros/${nextSlug}`);
    setCurrentPage(1);
    setTarget(null);
    if (!isDesktop()) setDrawerOpen(false);
  };

  return (
    <div className="flex h-full">
      {/* Columna de lectura. La cabecera y los controles FLOTAN encima del visor: al hacer scroll, el PDF se ve por detrás de la cabecera translúcida. */}
      <div className="relative min-w-0 flex-1">
        <header
          className="absolute inset-x-0 top-0 z-20 flex items-center border-b border-ink/10 bg-paper/80 px-4 backdrop-blur"
          style={{ height: HEADER_HEIGHT }}
        >
          <h1 className="min-w-0 flex-1 truncate text-center font-brush text-lg font-bold">{book ? book.title : 'Document Reader'}</h1>
        </header>

        {error && (
          <div role="alert" className="absolute inset-x-0 z-10 flex items-center gap-3 border-b border-line bg-red-50/95 px-4 py-2 text-sm text-red-800" style={{ top: HEADER_HEIGHT }}>
            <span className="min-w-0 flex-1">{error}</span>
            <button onClick={reload} className="shrink-0 rounded-md border border-red-300 bg-white px-2 py-1 text-xs font-semibold">
              Reintentar
            </button>
          </div>
        )}

        {/* Original / Español, pequeño y en la esquina, como los selectores de china-history; con la página en la que estás. */}
        {book && pdfUrl && (
          <div className="absolute right-3 z-10 flex items-center gap-2 md:right-5" style={{ top: HEADER_HEIGHT + 8 + (error ? 44 : 0) }}>
            {pageCount > 0 && (
              <span className="rounded-lg border border-ink/10 bg-paper/80 px-2 py-1 text-xs tabular-nums text-stone shadow-sm backdrop-blur">
                {currentPage} / {pageCount}
              </span>
            )}
            <div role="group" aria-label="Modo de lectura" className="flex rounded-lg border border-ink/10 bg-paper/80 p-0.5 shadow-sm backdrop-blur">
              {(['original', 'es'] as const).map((value) => (
                <button
                  key={value}
                  onClick={() => changeMode(value)}
                  aria-pressed={mode === value}
                  className={`rounded-md px-2.5 py-1 text-xs font-semibold ${mode === value ? 'bg-ink text-paper' : 'text-ink-soft hover:text-ink'}`}
                >
                  {value === 'original' ? 'Original' : 'Español'}
                </button>
              ))}
            </div>
          </div>
        )}

        {/* La lengüeta que abre el panel. En ordenador queda junto a la barra de scroll del PDF para no taparla. */}
        {!drawerOpen && (
          <button
            onClick={() => setDrawerOpen(true)}
            aria-label="Abrir el panel"
            aria-expanded={false}
            className="absolute right-0 top-1/2 z-10 flex h-16 w-6 -translate-y-1/2 items-center justify-center rounded-l-lg border border-r-0 border-ink/15 bg-ink/55 text-paper backdrop-blur-sm hover:bg-ink/70 md:right-4 md:rounded-lg md:border-r"
          >
            <ChevronIcon direction="left" />
            {activeJobs > 0 && <span className="absolute -left-1 -top-1 h-3 w-3 rounded-full bg-seal ring-2 ring-paper" aria-label="Hay análisis en marcha" />}
          </button>
        )}

        <main className="h-full">
          {!loaded ? (
            <CenteredSpinner />
          ) : book ? (
            <PdfGate url={pdfUrl} urlError={pdfError} pdf={pdf}>
              {(doc) =>
                mode === 'es' ? (
                  <ReaderView key={pdfUrl} doc={doc} translations={translations} target={target} onPageChange={setCurrentPage} />
                ) : (
                  <PdfViewer key={pdfUrl} doc={doc} ratio={pdf.ratio} target={target} onPageChange={setCurrentPage} />
                )
              }
            </PdfGate>
          ) : pendingJob ? (
            <Message>
              «{pendingJob.title ?? pendingJob.slug}» todavía se está subiendo o preparando. Puedes seguir su avance en la pestaña Progreso; cuando termine aparecerá aquí.
            </Message>
          ) : slug ? (
            // Una dirección vieja o mal escrita: al inicio, que lleva al libro más reciente o a la pantalla vacía.
            <Navigate to="/" replace />
          ) : (
            <Message>Todavía no hay ningún libro. Abre el panel con la lengüeta del borde derecho y sube un PDF.</Message>
          )}
        </main>
      </div>

        <Drawer
          open={drawerOpen}
          onClose={() => setDrawerOpen(false)}
          tab={tab}
          onTab={setTab}
          badges={{ progreso: activeJobs }}
          tabs={tabs}
          panels={{
            archivos: (
              <FilesTab
                books={books}
                jobs={jobs}
                onAnalyze={setAnalyzing}
                currentSlug={book?.slug}
                onSelect={select}
                onSignOut={onSignOut}
                onDelete={async (deleted) => {
                  await remove(deleted);
                  if (deleted === slug) navigate('/'); // el libro abierto ya no existe: al inicio, que abre el más reciente o la pantalla vacía
                }}
                onUpload={async (file, options) => {
                  await upload(file, options);
                  setTab('progreso'); // nada más subir, lo que interesa es ver cómo avanza
                }}
              />
            ),
            progreso: <ProgressTab jobs={jobs} books={books} onSelect={select} />,
            hitos: (
              <HitosTab hitos={hitos} books={books} currentSlug={book?.slug} currentPage={currentPage} onGoTo={goTo} onOpenBook={select} analysis={analysis} onAnalyze={book ? () => setAnalyzing(book) : undefined} />
            ),
            personajes: (
              <PersonajesTab personajes={personajes} hitos={hitos} books={books} currentSlug={book?.slug} onGoTo={goTo} onOpenBook={select} analysis={analysis} onAnalyze={book ? () => setAnalyzing(book) : undefined} />
            ),
            agente: <AgentTab api={client} slug={book?.slug ?? null} books={books} analysis={analysis} onAnalyze={book ? () => setAnalyzing(book) : undefined} onGoTo={goTo} />,
          }}
        />
      {analyzing && <AnalyzeDialog book={analyzing} jobs={jobs} onConfirm={analyze} onClose={() => setAnalyzing(null)} />}
    </div>
  );
}
