import { useEffect, useRef, useState } from 'react';
import type { ApiClient } from '../../api/client';
import type { ChatMessage } from '../../types';

interface AgentTabProps {
  api: ApiClient;
  /** Libro abierto (null si no hay ninguno). */
  slug: string | null;
  onGoToPage: (page: number) => void;
}

type Scope = 'libro' | 'todos';

export function AgentTab({ api, slug, onGoToPage }: AgentTabProps) {
  const [scope, setScope] = useState<Scope>('libro');
  // La conversación se guarda por libro (y una aparte para "todos los libros"), para no mezclarlas al cambiar de PDF.
  const [history, setHistory] = useState<Record<string, ChatMessage[]>>({});
  const [question, setQuestion] = useState('');
  const [waiting, setWaiting] = useState(false);
  const endRef = useRef<HTMLDivElement>(null);

  const askAll = scope === 'todos' || !slug;
  const key = askAll ? '*' : slug!;
  const messages = history[key] ?? [];

  useEffect(() => {
    endRef.current?.scrollIntoView?.({ behavior: 'smooth' });
  }, [messages.length, waiting]);

  const send = async () => {
    const text = question.trim();
    if (!text || waiting) return;
    const asked = { id: `u-${Date.now()}`, role: 'user' as const, text };
    setHistory((previous) => ({ ...previous, [key]: [...(previous[key] ?? []), asked] }));
    setQuestion('');
    setWaiting(true);
    try {
      const answer = await api.ask(askAll ? null : slug, text);
      setHistory((previous) => ({ ...previous, [key]: [...(previous[key] ?? []), answer] }));
    } catch {
      setHistory((previous) => ({
        ...previous,
        [key]: [...(previous[key] ?? []), { id: `e-${Date.now()}`, role: 'assistant', text: 'No se pudo obtener una respuesta. Inténtalo de nuevo.' }],
      }));
    } finally {
      setWaiting(false);
    }
  };

  return (
    <div className="flex h-full flex-col">
      <div className="space-y-2 border-b border-line p-3 text-xs">
        <div className="flex overflow-hidden rounded border border-line font-semibold" role="group" aria-label="A quién preguntar">
          {(['libro', 'todos'] as const).map((value) => (
            <button
              key={value}
              onClick={() => setScope(value)}
              aria-pressed={askAll ? value === 'todos' : value === 'libro'}
              disabled={value === 'libro' && !slug}
              className={`flex-1 px-2 py-1 ${(askAll ? value === 'todos' : value === 'libro') ? 'bg-seal text-on-seal' : 'bg-white text-stone hover:text-ink'} disabled:opacity-40`}
            >
              {value === 'libro' ? 'Este libro' : 'Todos los libros'}
            </button>
          ))}
        </div>
        <p className="text-stone">Respuestas simuladas. El agente real buscará en el libro, citará la página, y si la pregunta es de historia general, avisará de que no viene del libro.</p>
      </div>

      <div className="min-h-0 flex-1 space-y-3 overflow-y-auto p-3" aria-live="polite">
        {messages.length === 0 && <p className="py-6 text-center text-sm text-stone">Pregunta lo que quieras sobre {askAll ? 'tus libros' : 'este libro'}. Prueba con «Muye» o «Luoyi».</p>}
        {messages.map((message) => (
          <Message key={message.id} message={message} onGoToPage={onGoToPage} />
        ))}
        {waiting && <p className="text-sm text-stone">Pensando…</p>}
        <div ref={endRef} />
      </div>

      <form
        className="flex gap-2 border-t border-line p-3"
        onSubmit={(event) => {
          event.preventDefault();
          void send();
        }}
      >
        <input
          value={question}
          onChange={(event) => setQuestion(event.target.value)}
          placeholder="Escribe tu pregunta…"
          aria-label="Pregunta"
          className="min-w-0 flex-1 rounded border border-line bg-white px-3 py-2 text-sm"
        />
        <button disabled={!question.trim() || waiting} className="rounded bg-seal px-4 py-2 text-sm font-semibold text-on-seal disabled:opacity-40">
          Enviar
        </button>
      </form>
    </div>
  );
}

function Message({ message, onGoToPage }: { message: ChatMessage; onGoToPage: (page: number) => void }) {
  if (message.role === 'user') {
    return <div className="ml-8 rounded bg-seal px-3 py-2 text-sm text-on-seal">{message.text}</div>;
  }
  return (
    <div className="mr-4 rounded border border-line bg-white p-3 text-sm">
      {message.origen && (
        <p className={`mb-1 text-[11px] font-bold uppercase tracking-wide ${message.origen === 'libro' ? 'text-seal' : 'text-amber-800'}`}>
          {message.origen === 'libro' ? 'Del libro' : 'Conocimiento general · no está en el libro'}
        </p>
      )}
      <p>{message.text}</p>
      {message.fuentes && message.fuentes.length > 0 && (
        <ul className="mt-2 space-y-1 border-t border-line pt-2">
          {message.fuentes.map((fuente) => (
            <li key={`${fuente.pagina}-${fuente.texto}`} className="text-xs">
              <button onClick={() => onGoToPage(fuente.pagina)} className="font-semibold text-seal hover:underline">
                Pág. {fuente.pagina}
              </button>{' '}
              <span className="text-stone">{fuente.texto}</span>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
