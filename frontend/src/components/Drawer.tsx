import type { ReactNode } from 'react';
import type { TabId } from '../types';
import { CloseIcon, TabIcon } from './icons';

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
  /** Número que se enseña junto al icono de una pestaña (p. ej. los trabajos activos en "Progreso"). */
  badges?: Partial<Record<TabId, number>>;
  /** Las pestañas que se enseñan (las que no tienen datos reales detrás no aparecen). */
  tabs: TabId[];
  /** Un panel por pestaña. Todos se mantienen montados (solo se ocultan) para que no se pierda lo escrito al cambiar de pestaña. */
  panels: Partial<Record<TabId, ReactNode>>;
}

/**
 * El panel de pestañas, que abre el usuario con la lengüeta del borde derecho. En ordenador EMPUJA al PDF (se lee y se consulta a la vez); en móvil se
 * desliza por encima ocupando casi todo el ancho, con una capa oscura detrás que lo cierra al tocarla. Cada pestaña lleva su icono y debajo el texto,
 * todas del mismo ancho: caben en el móvil sin desplazarse.
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
        <div className="flex items-stretch border-b border-line">
          <div role="tablist" aria-label="Secciones" className="flex min-w-0 flex-1">
            {visible.map(({ id, label }) => {
              const active = tab === id;
              return (
                <button
                  key={id}
                  role="tab"
                  id={`tab-${id}`}
                  aria-selected={active}
                  aria-controls={`panel-${id}`}
                  onClick={() => onTab(id)}
                  className={`relative flex min-w-0 flex-1 flex-col items-center gap-0.5 border-b-2 px-1 pb-1.5 pt-2.5 ${
                    active ? 'border-seal text-seal' : 'border-transparent text-stone hover:text-ink'
                  }`}
                >
                  <span className="relative">
                    <TabIcon tab={id} />
                    {badges[id] ? (
                      <span className="absolute -right-2.5 -top-1.5 min-w-4 rounded-full bg-seal px-1 text-center text-[10px] font-bold leading-4 text-on-seal">{badges[id]}</span>
                    ) : null}
                  </span>
                  <span className="max-w-full truncate text-[11px] font-semibold">{label}</span>
                </button>
              );
            })}
          </div>
          <button onClick={onClose} aria-label="Cerrar el panel" className="flex w-10 shrink-0 items-start justify-center pt-3 text-stone hover:text-ink">
            <CloseIcon />
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
