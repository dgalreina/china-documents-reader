import { useState } from 'react';
import { Navigate, Route, Routes, useNavigate, useParams } from 'react-router-dom';
import { api } from './api';
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
import type { JumpTarget, ReadMode, TabId } from './types';

export default function App() {
  return (
    <LibraryProvider api={api}>
      <Routes>
        <Route path="/" element={<Home />} />
        <Route path="/libros/:slug" element={<Workspace />} />
        <Route path="*" element={<Navigate to="/" replace />} />
      </Routes>
    </LibraryProvider>
  );
}

/** La raíz lleva al primer libro (o enseña la pantalla vacía si aún no hay ninguno). */
function Home() {
  const { books, loaded } = useLibrary();
  if (!loaded) return <div className="flex h-full items-center justify-center text-stone">Cargando…</div>;
  const first = books.find((book) => book.status === 'DONE') ?? books[0];
  return first ? <Navigate to={`/libros/${first.slug}`} replace /> : <Workspace />;
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

function Workspace() {
  const { slug } = useParams();
  const navigate = useNavigate();
  const { api: client, books, jobs, upload } = useLibrary();
  const book = books.find((candidate) => candidate.slug === slug);

  // En ordenador el panel empieza abierto; en móvil, cerrado para que el PDF ocupe toda la pantalla.
  const [drawerOpen, setDrawerOpen] = useState(isDesktop);
  const [tab, setTab] = useState<TabId>('hitos');
  const [currentPage, setCurrentPage] = useState(1);
  const [target, setTarget] = useState<JumpTarget | null>(null);
  const [mode, setMode] = useState<ReadMode>(loadMode);
  // PDF abierto desde el ordenador para un libro que no tiene PDF propio en la maqueta.
  const [localPdf, setLocalPdf] = useState<{ slug: string; url: string } | null>(null);

  const { hitos, personajes } = useBookData(book?.slug, book?.status);
  const pdfUrl = book?.pdfUrl ?? (localPdf && localPdf.slug === slug ? localPdf.url : undefined);
  const pdf = usePdfDocument(pdfUrl);
  const pageCount = pdf.doc?.numPages ?? 0;
  const translations = useTranslations(client, pdfUrl ? book?.slug ?? null : null);
  const activeJobs = jobs.filter((job) => job.status === 'UPLOADING' || job.status === 'QUEUED' || job.status === 'RUNNING').length;

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

      <div className="flex min-h-0 flex-1">
        <main className="min-w-0 flex-1">
          {slug && !book ? (
            <div className="flex h-full items-center justify-center p-6 text-center text-stone">No existe el libro «{slug}».</div>
          ) : book ? (
            <PdfGate url={pdfUrl} pdf={pdf} onPickLocal={(file) => setLocalPdf({ slug: book.slug, url: URL.createObjectURL(file) })}>
              {(doc) =>
                mode === 'es' ? (
                  <ReaderView key={pdfUrl} doc={doc} translations={translations} target={target} onPageChange={setCurrentPage} />
                ) : (
                  <PdfViewer key={pdfUrl} doc={doc} ratio={pdf.ratio} target={target} onPageChange={setCurrentPage} />
                )
              }
            </PdfGate>
          ) : (
            <div className="flex h-full items-center justify-center p-6 text-center text-stone">No hay ningún libro. Abre el panel y sube un PDF.</div>
          )}
        </main>

        <Drawer
          open={drawerOpen}
          onClose={() => setDrawerOpen(false)}
          tab={tab}
          onTab={setTab}
          badges={{ progreso: activeJobs }}
          panels={{
            archivos: (
              <FilesTab
                books={books}
                currentSlug={book?.slug}
                onSelect={select}
                onUpload={async (file, options) => {
                  await upload(file, options);
                  select(options.slug);
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
