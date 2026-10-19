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

// Movimiento del panel: 450 ms con una curva pareja (arranca y frena suave). Con "reducir movimiento" no se anima.
const MOTION = 'duration-[450ms] ease-[cubic-bezier(0.4,0,0.2,1)] motion-reduce:transition-none';

/**
 * El panel de pestañas, que abre el usuario con la lengüeta del borde derecho. En ordenador EMPUJA al PDF (se lee y se consulta a la vez): se ensancha
 * poco a poco y el PDF se va estrechando; en móvil se desliza por encima ocupando casi todo el ancho, con una capa oscura que aparece despacio y lo
 * cierra al tocarla. Cada pestaña lleva su icono y debajo el texto, todas del mismo ancho: caben en el móvil sin desplazarse.
 */
export function Drawer({ open, onClose, tab, onTab, badges = {}, tabs, panels }: DrawerProps) {
  const visible = ALL_TABS.filter(({ id }) => tabs.includes(id));
  return (
    <>
      <div
        className={`fixed inset-0 z-30 bg-black/40 transition-opacity ${MOTION} md:hidden ${open ? 'opacity-100' : 'pointer-events-none opacity-0'}`}
        onClick={onClose}
        aria-hidden="true"
      />
      <aside
        aria-label="Panel"
        aria-hidden={!open}
        // Cerrado, el panel no recibe foco ni se lee con lector de pantalla (React 18 no conoce `inert` como propiedad: va como atributo).
        {...(open ? {} : ({ inert: '' } as Record<string, string>))}
        className={`fixed inset-y-0 right-0 z-40 w-[min(100%,26rem)] overflow-hidden border-l border-line bg-paper shadow-xl transition-[translate,width] ${MOTION} md:static md:z-auto md:shrink-0 md:translate-x-0 md:shadow-none ${
          open ? 'translate-x-0 md:w-96' : 'translate-x-full md:w-0 md:border-l-0'
        }`}
      >
        {/* El contenido mantiene siempre su ancho: lo que se anima es el hueco del panel, no se recoloca el texto mientras se abre. */}
        <div className="flex h-full w-[min(100vw,26rem)] flex-col md:w-96">
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
            <section key={id} role="tabpanel" id={`panel-${id}`} aria-labelledby={`tab-${id}`} hidden={tab !== id} className="h-full overflow-y-auto overscroll-contain">
              {panels[id]}
            </section>
          ))}
        </div>
        </div>
      </aside>
    </>
  );
}
