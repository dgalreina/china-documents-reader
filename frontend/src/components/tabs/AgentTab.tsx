import { useEffect, useRef, useState } from 'react';
import type { ApiClient, AskEvent, AskTurn } from '../../api/client';
import type { AnalysisState } from '../../lib/analysis';
import type { Book, ChatMessage } from '../../types';
import { BookFilter, type BookScope } from '../BookFilter';

interface AgentTabProps {
  api: ApiClient;
  /** Libro abierto (null si no hay ninguno). */
  slug: string | null;
  books: Book[];
  /** Estado del análisis del libro abierto: sin analizar no tiene fragmentos y el agente no puede responder sobre él. */
  analysis?: AnalysisState | null;
  onAnalyze?: () => void;
  /** El libro abierto es un escaneado sin OCR: antes de preguntarle hay que reconocer su texto. */
  scanned?: boolean;
  onOcr?: () => void;
  /** Ir a una página de un libro (abre el libro si no es el actual). */
  onGoTo: (slug: string, page: number) => void;
}

const HISTORY_TURNS = 6; // las últimas intervenciones que se mandan con cada pregunta (el servidor guarda la conversación en ningún sitio)
const ALL = '*';
const button = 'mt-3 rounded-md border border-seal/40 px-3 py-1.5 text-sm font-semibold text-seal hover:bg-seal hover:text-on-seal';

export function AgentTab({ api, slug, books, analysis = null, onAnalyze, scanned = false, onOcr, onGoTo }: AgentTabProps) {
  const [scope, setScope] = useState<BookScope>('este');
  // La conversación se guarda por libro (y una aparte para "todos los libros"), para no mezclarlas al cambiar de PDF.
  const [conversations, setConversations] = useState<Record<string, ChatMessage[]>>({});
  const [question, setQuestion] = useState('');
  const [busy, setBusy] = useState(false);
  const abort = useRef<AbortController | null>(null);
  const endRef = useRef<HTMLDivElement>(null);

  const askAll = scope === 'todos' || !slug;
  const key = askAll ? ALL : slug!;
  const messages = conversations[key] ?? [];
  const titleOf = (bookSlug: string) => books.find((book) => book.slug === bookSlug)?.title ?? bookSlug;

  // Al desmontar (cerrar el panel, cambiar de pestaña) se corta la respuesta en curso: no tiene sentido seguir pagándola.
  useEffect(() => () => abort.current?.abort(), []);
  useEffect(() => {
    endRef.current?.scrollIntoView?.({ behavior: 'auto', block: 'end' });
  }, [messages, busy]);

  const patch = (conversation: string, id: string, change: (message: ChatMessage) => ChatMessage) =>
    setConversations((previous) => ({ ...previous, [conversation]: (previous[conversation] ?? []).map((message) => (message.id === id ? change(message) : message)) }));

  const ask = async (text: string, base: ChatMessage[]) => {
    const stamp = Date.now();
    const asked: ChatMessage = { id: `u-${stamp}`, role: 'user', text };
    const reply: ChatMessage = { id: `a-${stamp}`, role: 'assistant', text: '', streaming: true };
    const history: AskTurn[] = base.filter((message) => !message.failed && message.text).slice(-HISTORY_TURNS).map((message) => ({ role: message.role, text: message.text }));
    const conversation = key;
    setConversations((previous) => ({ ...previous, [conversation]: [...base, asked, reply] }));
    setBusy(true);
    const controller = new AbortController();
    abort.current = controller;
    const onEvent = (event: AskEvent) => {
      if (event.type === 'fuentes') patch(conversation, reply.id, (message) => ({ ...message, fuentes: event.fuentes }));
      else if (event.type === 'delta') patch(conversation, reply.id, (message) => ({ ...message, text: message.text + event.text }));
      else if (event.type === 'fin') patch(conversation, reply.id, (message) => ({ ...message, streaming: false, origen: event.origen === 'libro' ? 'libro' : undefined }));
      else patch(conversation, reply.id, (message) => ({ ...message, streaming: false, failed: true, text: message.text ? `${message.text}\n\n${event.message}` : event.message }));
    };
    try {
      await api.ask({ slug: askAll ? null : slug, question: text, history }, onEvent, controller.signal);
      patch(conversation, reply.id, (message) => (message.streaming ? { ...message, streaming: false } : message)); // por si el flujo se cerró sin "fin"
    } catch (problem) {
      const stopped = controller.signal.aborted;
      patch(conversation, reply.id, (message) => ({
        ...message,
        streaming: false,
        failed: !stopped,
        text: stopped ? message.text || 'Respuesta detenida.' : problem instanceof Error ? problem.message : 'No se pudo obtener una respuesta. Inténtalo de nuevo.',
      }));
    } finally {
      if (abort.current === controller) abort.current = null;
      setBusy(false);
    }
  };

  const send = () => {
    const text = question.trim();
    if (!text || busy) return;
    setQuestion('');
    void ask(text, messages);
  };
  const retry = (failedId: string) => {
    const index = messages.findIndex((message) => message.id === failedId);
    const asked = messages[index - 1];
    if (busy || index < 1 || asked?.role !== 'user') return;
    void ask(asked.text, messages.slice(0, index - 1)); // se quita la pregunta y la respuesta fallida y se vuelve a preguntar
  };

  // Un libro sin analizar no tiene fragmentos: no hay nada que buscar. Se dice y se ofrece analizarlo (o preguntar a todos).
  const blocked = !askAll && (scanned || (analysis !== null && analysis.status !== 'done'));

  return (
    <div className="flex h-full flex-col">
      <div className="space-y-2 border-b border-line p-3 text-xs">
        {books.length > 1 && slug && (
          <div className="flex">
            <BookFilter value={askAll ? 'todos' : 'este'} onChange={setScope} />
          </div>
        )}
        <p className="text-stone">Responde solo con lo que dice {askAll ? 'tus libros' : 'el libro'} y cita la página. Si no lo encuentra, lo dice.</p>
      </div>

      <div className="min-h-0 flex-1 space-y-3 overflow-y-auto p-3" aria-live="polite" aria-busy={busy}>
        {blocked ? (
          <AgentNotice analysis={analysis} onAnalyze={onAnalyze} scanned={scanned} onOcr={onOcr} />
        ) : (
          <>
            {messages.length === 0 && <p className="py-6 text-center text-sm text-stone">Pregunta lo que quieras sobre {askAll ? 'tus libros' : 'este libro'}.</p>}
            {messages.map((message) => (
              <Message key={message.id} message={message} showBook={askAll} titleOf={titleOf} currentSlug={slug} onGoTo={onGoTo} onRetry={() => retry(message.id)} />
            ))}
          </>
        )}
        <div ref={endRef} />
      </div>

      <form
        // En el móvil el campo queda más arriba: pegado al borde de abajo coincide con la barra de gestos del iPhone y cuesta tocarlo (como mínimo 1,75 rem, o la zona segura si es mayor).
        className="flex gap-2 border-t border-line p-3 pb-[max(1.75rem,env(safe-area-inset-bottom))] md:pb-3"
        onSubmit={(event) => {
          event.preventDefault();
          send();
        }}
      >
        <input
          value={question}
          onChange={(event) => setQuestion(event.target.value)}
          placeholder={blocked ? 'Analiza el libro para poder preguntarle' : 'Escribe tu pregunta…'}
          aria-label="Pregunta"
          maxLength={600}
          disabled={blocked}
          className="min-w-0 flex-1 rounded border border-line bg-white px-3 py-2 text-sm disabled:opacity-50"
        />
        {busy ? (
          <button type="button" onClick={() => abort.current?.abort()} className="rounded border border-line bg-white px-4 py-2 text-sm font-semibold text-ink-soft hover:border-seal">
            Detener
          </button>
        ) : (
          <button disabled={!question.trim() || blocked} className="rounded bg-seal px-4 py-2 text-sm font-semibold text-on-seal disabled:opacity-40">
            Enviar
          </button>
        )}
      </form>
    </div>
  );
}

function AgentNotice({ analysis, onAnalyze, scanned, onOcr }: { analysis: AnalysisState | null; onAnalyze?: () => void; scanned: boolean; onOcr?: () => void }) {
  const status = analysis?.status;
  if (scanned) {
    return (
      <div className="p-3 text-center text-sm text-stone">
        <p>Este libro es un escaneado (solo imágenes): para poder preguntarle hay que reconocer antes su texto con OCR y luego analizarlo.</p>
        {onOcr && (
          <button onClick={onOcr} className={button}>
            Reconocer texto (OCR)
          </button>
        )}
      </div>
    );
  }
  return (
    <div className="p-3 text-center text-sm text-stone">
      {status === 'none' && (
        <>
          <p>Este libro todavía no se ha analizado: el agente necesita ese análisis para poder responder sobre él.</p>
          {onAnalyze && (
            <button onClick={onAnalyze} className={button}>
              Analizar este libro
            </button>
          )}
        </>
      )}
      {status === 'running' && <p>Analizando el libro… podrás preguntarle en cuanto termine. Mientras tanto puedes seguir leyendo.</p>}
      {status === 'failed' && (
        <>
          <p className="text-red-700">{analysis?.error ?? 'El análisis falló.'}</p>
          {onAnalyze && (
            <button onClick={onAnalyze} className={button}>
              Reintentar análisis
            </button>
          )}
        </>
      )}
      {status === 'needs_ocr' && <p>{analysis?.error ?? 'Este PDF parece escaneado: hace falta OCR, que aún no está disponible.'}</p>}
    </div>
  );
}

interface MessageProps {
  message: ChatMessage;
  showBook: boolean;
  titleOf: (slug: string) => string;
  currentSlug: string | null;
  onGoTo: (slug: string, page: number) => void;
  onRetry: () => void;
}

function Message({ message, showBook, titleOf, currentSlug, onGoTo, onRetry }: MessageProps) {
  if (message.role === 'user') {
    return <div className="ml-8 rounded bg-seal px-3 py-2 text-sm text-on-seal">{message.text}</div>;
  }
  return (
    <div className="mr-4 rounded border border-line bg-white p-3 text-sm">
      {message.origen === 'libro' && !message.streaming && <p className="mb-1 text-[11px] font-bold uppercase tracking-wide text-seal">Del libro</p>}
      {message.streaming && !message.text && <p className="animate-pulse text-stone">Buscando en el libro…</p>}
      {message.text && (
        <p className={`whitespace-pre-wrap ${message.failed ? 'text-red-700' : ''}`}>
          {message.text}
          {message.streaming && <span aria-hidden className="ml-0.5 inline-block animate-pulse text-seal">▍</span>}
        </p>
      )}
      {message.failed && (
        <button onClick={onRetry} className="mt-2 rounded border border-line bg-white px-2 py-1 text-xs font-semibold hover:border-seal">
          Reintentar
        </button>
      )}
      {message.fuentes && message.fuentes.length > 0 && (
        <ul className="mt-2 space-y-1 border-t border-line pt-2">
          {message.fuentes.map((fuente, index) => {
            const source = fuente.slug ?? currentSlug;
            return (
              <li key={`${source}-${fuente.pagina}-${index}`} className="text-xs">
                {source ? (
                  <button onClick={() => onGoTo(source, fuente.pagina)} className="font-semibold text-seal hover:underline">
                    Pág. {fuente.pagina}
                  </button>
                ) : (
                  <span className="font-semibold">Pág. {fuente.pagina}</span>
                )}{' '}
                {showBook && source && <span className="font-semibold text-ink-soft">{titleOf(source)} · </span>}
                <span className="text-stone">{fuente.texto}</span>
              </li>
            );
          })}
        </ul>
      )}
    </div>
  );
}
