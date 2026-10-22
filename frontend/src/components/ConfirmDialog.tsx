import { useEffect, useId, useRef, type ReactNode } from 'react';
import { createPortal } from 'react-dom';

interface ConfirmDialogProps {
  title: string;
  children: ReactNode;
  confirmLabel: string;
  /** Lo que dice el botón mientras se hace lo pedido. */
  busyLabel: string;
  /** Mientras se hace lo pedido: los botones se bloquean y no se puede cerrar. */
  busy: boolean;
  /** Mensaje si falló, para enseñarlo dentro del diálogo y poder reintentar. */
  error?: string | null;
  /** Desactiva el botón de confirmar (p. ej. mientras aún se está calculando lo que se va a confirmar). Cancelar sigue funcionando. */
  confirmDisabled?: boolean;
  onConfirm: () => void;
  onCancel: () => void;
}

/**
 * Un diálogo de confirmación (para lo que no se puede deshacer o cuesta dinero). El foco empieza en "Cancelar" (la opción segura), Escape o tocar fuera cancelan, y mientras
 * se trabaja no se puede cerrar. Se dibuja en el `body` con un portal: el panel lateral se mueve con `translate`, y eso encerraría y recortaría un `fixed` dentro.
 */
export function ConfirmDialog({ title, children, confirmLabel, busyLabel, busy, error, confirmDisabled, onConfirm, onCancel }: ConfirmDialogProps) {
  const titleId = useId();
  const cancelRef = useRef<HTMLButtonElement>(null);

  // Lo último que dijo el padre, sin reiniciar el efecto: `onCancel` es una función nueva en cada renderizado y el foco no debe volver a "Cancelar" cada vez.
  const latest = useRef({ busy, onCancel });
  latest.current = { busy, onCancel };

  useEffect(() => {
    cancelRef.current?.focus(); // solo al abrirse
    const onKey = (event: KeyboardEvent) => {
      if (event.key === 'Escape' && !latest.current.busy) latest.current.onCancel();
    };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, []);

  return createPortal(
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/50 p-4" onClick={() => !busy && onCancel()}>
      <div
        role="alertdialog"
        aria-modal="true"
        aria-labelledby={titleId}
        className="w-full max-w-sm rounded-xl border border-line bg-paper p-5 shadow-xl"
        onClick={(event) => event.stopPropagation()}
      >
        <h2 id={titleId} className="font-brush text-lg font-bold">
          {title}
        </h2>
        <div className="mt-2 space-y-2 text-sm text-ink-soft">{children}</div>
        {error && (
          <p role="alert" className="mt-3 text-sm text-red-700">
            {error}
          </p>
        )}
        <div className="mt-5 flex justify-end gap-2">
          <button ref={cancelRef} onClick={onCancel} disabled={busy} className="rounded-lg border border-ink/15 bg-white px-3 py-1.5 text-sm font-semibold text-ink-soft hover:text-ink disabled:opacity-40">
            Cancelar
          </button>
          <button onClick={onConfirm} disabled={busy || confirmDisabled} className="rounded-lg bg-seal px-3 py-1.5 text-sm font-semibold text-on-seal hover:bg-seal-dark disabled:opacity-60">
            {busy ? busyLabel : confirmLabel}
          </button>
        </div>
      </div>
    </div>,
    document.body,
  );
}
