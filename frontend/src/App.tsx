import { useState } from 'react';
import { Navigate, Route, Routes, useNavigate, useParams } from 'react-router-dom';
import type { ApiClient } from './api/client';
import { Drawer } from './components/Drawer';
import { PdfGate } from './components/PdfGate';
import { PdfViewer } from './components/PdfViewer';
import { ReaderView } from './components/ReaderView';
import { AgentTab } from './components/tabs/AgentTab';
import { FilesTab } from './components/tabs/FilesTab';
import { HitosTab } from './components/tabs/HitosTab';
import { PersonajesTab } from './components/tabs/PersonajesTab';
import { ProgressTab } from './components/tabs/ProgressTab';
import { LibraryProvider, useBookData, useLibrary } from './state/Library';
import { useTranslations } from './state/Translations';
import { usePdfDocument } from './state/usePdfDocument';
import { usePdfUrl } from './state/usePdfUrl';
import type { JumpTarget, ReadMode, TabId } from './types';

interface AppProps {
  api: ApiClient;
  onSignOut: () => void;
}

export default function App({ api, onSignOut }: AppProps) {
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
  if (!loaded) return <div className="flex h-full items-center justify-center text-stone">Cargando…</div>;
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
  const { api: client, books, jobs, loaded, error, reload, upload } = useLibrary();
  const book = books.find((candidate) => candidate.slug === slug);
  const pendingJob = !book && slug ? jobs.find((job) => job.slug === slug) : undefined;

  // En ordenador el panel empieza abierto; en móvil, cerrado para que el PDF ocupe toda la pantalla.
  const [drawerOpen, setDrawerOpen] = useState(isDesktop);
  const [tab, setTab] = useState<TabId>('archivos');
  const [currentPage, setCurrentPage] = useState(1);
  const [target, setTarget] = useState<JumpTarget | null>(null);
  const [mode, setMode] = useState<ReadMode>(loadMode);

  const { hitos, personajes } = useBookData(book?.slug, book?.status);
  const { url: pdfUrl, error: pdfError } = usePdfUrl(client, book?.status === 'DONE' ? book.slug : undefined);
  const pdf = usePdfDocument(pdfUrl);
  const pageCount = pdf.doc?.numPages ?? 0;
  const translations = useTranslations(client, pdfUrl ? book?.slug ?? null : null);
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
  const select = (nextSlug: string) => {
    navigate(`/libros/${nextSlug}`);
    setCurrentPage(1);
    setTarget(null);
    if (!isDesktop()) setDrawerOpen(false);
  };

  return (
    <div className="flex h-full flex-col">
      <header className="flex items-center gap-3 border-b border-line bg-paper px-4 py-2">
        <h1 className="min-w-0 flex-1 truncate font-brush text-lg font-bold">{book ? book.title : 'China Documents Reader'}</h1>
        {book && pdfUrl && (
          <div className="flex shrink-0 overflow-hidden rounded border border-line text-sm font-semibold" role="group" aria-label="Modo de lectura">
            {(['original', 'es'] as const).map((value) => (
              <button
                key={value}
                onClick={() => changeMode(value)}
                aria-pressed={mode === value}
                className={`px-3 py-1.5 ${mode === value ? 'bg-seal text-white' : 'bg-white text-stone hover:text-ink'}`}
              >
                {value === 'original' ? 'Original' : 'Español'}
              </button>
            ))}
          </div>
        )}
        {book && pdfUrl && pageCount > 0 && <span className="hidden shrink-0 text-sm text-stone sm:inline">Pág. {currentPage} / {pageCount}</span>}
        <button
          onClick={() => setDrawerOpen((open) => !open)}
          aria-expanded={drawerOpen}
          className="relative shrink-0 rounded border border-line bg-white px-3 py-1.5 text-sm font-semibold hover:border-seal"
        >
          {drawerOpen ? 'Ocultar panel' : 'Panel'}
          {!drawerOpen && activeJobs > 0 && <span className="absolute -right-1 -top-1 h-3 w-3 rounded-full bg-seal" aria-label="Hay análisis en marcha" />}
        </button>
      </header>

      {error && (
        <div role="alert" className="flex items-center gap-3 border-b border-line bg-red-50 px-4 py-2 text-sm text-red-800">
          <span className="min-w-0 flex-1">{error}</span>
          <button onClick={reload} className="shrink-0 rounded border border-red-300 bg-white px-2 py-1 font-semibold">
            Reintentar
          </button>
        </div>
      )}

      <div className="flex min-h-0 flex-1">
        <main className="min-w-0 flex-1">
          {!loaded ? (
            <Message>Cargando…</Message>
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
              «{pendingJob.title ?? pendingJob.slug}» todavía se está procesando. Puedes seguir su avance en la pestaña Progreso; cuando termine aparecerá aquí.
            </Message>
          ) : slug ? (
            // Una dirección vieja o mal escrita: al inicio, que lleva al libro más reciente o a la pantalla vacía.
            <Navigate to="/" replace />
          ) : (
            <Message>Todavía no hay ningún libro. Abre el panel y sube un PDF.</Message>
          )}
        </main>

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
                currentSlug={book?.slug}
                onSelect={select}
                onSignOut={onSignOut}
                onUpload={async (file, options) => {
                  await upload(file, options);
                  setTab('progreso'); // nada más subir, lo que interesa es ver cómo avanza
                }}
              />
            ),
            progreso: <ProgressTab jobs={jobs} books={books} onSelect={select} />,
            hitos: <HitosTab hitos={hitos} currentPage={currentPage} onGoToPage={goToPage} />,
            personajes: <PersonajesTab personajes={personajes} hitos={hitos} onGoToPage={goToPage} />,
            agente: <AgentTab api={client} slug={book?.slug ?? null} onGoToPage={goToPage} />,
          }}
        />
      </div>
    </div>
  );
}
