import type { ReactNode } from 'react';
import type { TabId } from '../types';

export const ALL_TABS: { id: TabId; label: string }[] = [
  { id: 'archivos', label: 'Archivos' },
  { id: 'progreso', label: 'Progreso' },
  { id: 'hitos', label: 'Hitos' },
  { id: 'personajes', label: 'Personajes' },
  { id: 'agente', label: 'Agente' },
];

interface DrawerProps {
  open: boolean;
  onClose: () => void;
  tab: TabId;
  onTab: (tab: TabId) => void;
  /** Número que se enseña junto a una pestaña (p. ej. los trabajos activos en "Progreso"). */
  badges?: Partial<Record<TabId, number>>;
  /** Las pestañas que se enseñan (las que no tienen datos reales detrás no aparecen). */
  tabs: TabId[];
  /** Un panel por pestaña. Todos se mantienen montados (solo se ocultan) para que no se pierda lo escrito al cambiar de pestaña. */
  panels: Partial<Record<TabId, ReactNode>>;
}

/**
 * El panel de pestañas. En ordenador es una columna a la derecha del PDF que se pliega; en móvil se desliza por encima del PDF
 * ocupando casi todo el ancho, con una capa oscura detrás que lo cierra al tocarla.
 */
export function Drawer({ open, onClose, tab, onTab, badges = {}, tabs, panels }: DrawerProps) {
  const visible = ALL_TABS.filter(({ id }) => tabs.includes(id));
  return (
    <>
      {open && <div className="fixed inset-0 z-30 bg-black/40 md:hidden" onClick={onClose} aria-hidden="true" />}
      <aside
        aria-label="Panel"
        className={`fixed inset-y-0 right-0 z-40 flex w-[min(100%,26rem)] flex-col border-l border-line bg-paper shadow-xl transition-transform md:static md:z-auto md:w-96 md:shrink-0 md:shadow-none ${
          open ? 'translate-x-0' : 'translate-x-full md:hidden'
        }`}
      >
        <div className="flex items-center border-b border-line">
          <div role="tablist" aria-label="Secciones" className="flex min-w-0 flex-1 overflow-x-auto">
            {visible.map(({ id, label }) => (
              <button
                key={id}
                role="tab"
                id={`tab-${id}`}
                aria-selected={tab === id}
                aria-controls={`panel-${id}`}
                onClick={() => onTab(id)}
                className={`relative shrink-0 px-3 py-3 text-sm font-semibold ${
                  tab === id ? 'border-b-2 border-seal text-seal' : 'border-b-2 border-transparent text-stone hover:text-ink'
                }`}
              >
                {label}
                {badges[id] ? <span className="ml-1 rounded-full bg-seal px-1.5 py-0.5 text-[10px] text-white">{badges[id]}</span> : null}
              </button>
            ))}
          </div>
          <button onClick={onClose} aria-label="Cerrar el panel" className="px-3 py-3 text-xl leading-none text-stone hover:text-ink md:hidden">
            ×
          </button>
        </div>
        <div className="min-h-0 flex-1">
          {visible.map(({ id }) => (
            <section key={id} role="tabpanel" id={`panel-${id}`} aria-labelledby={`tab-${id}`} hidden={tab !== id} className="h-full overflow-y-auto">
              {panels[id]}
            </section>
          ))}
        </div>
      </aside>
    </>
  );
}
