import type { ReactNode, SVGProps } from 'react';
import type { TabId } from '../types';

/** Iconos de trazo (24x24, heredan el color del texto). Propios, sin librería de iconos: son cinco formas simples. */
function Icon({ children, ...props }: SVGProps<SVGSVGElement> & { children: ReactNode }) {
  return (
    <svg width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true" {...props}>
      {children}
    </svg>
  );
}

const ICONS: Record<TabId, ReactNode> = {
  // una carpeta: los libros
  archivos: <path d="M3 7a2 2 0 0 1 2-2h4l2 2h8a2 2 0 0 1 2 2v8a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V7z" />,
  // un reloj: lo que está en marcha
  progreso: (
    <>
      <circle cx="12" cy="12" r="9" />
      <path d="M12 7v5l3 2" />
    </>
  ),
  // una chincheta de mapa: un hito es un lugar y una fecha
  hitos: (
    <>
      <path d="M12 21s7-6.2 7-11a7 7 0 1 0-14 0c0 4.8 7 11 7 11z" />
      <circle cx="12" cy="10" r="2.5" />
    </>
  ),
  // una persona
  personajes: (
    <>
      <circle cx="12" cy="8" r="4" />
      <path d="M4 21c0-4 3.6-7 8-7s8 3 8 7" />
    </>
  ),
  // un globo de conversación
  agente: <path d="M21 12a8 8 0 0 1-11.6 7.1L4 20l1-4.6A8 8 0 1 1 21 12z" />,
};

export function TabIcon({ tab }: { tab: TabId }) {
  return <Icon>{ICONS[tab]}</Icon>;
}

/** Flecha de la lengüeta del panel. */
export function ChevronIcon({ direction }: { direction: 'left' | 'right' }) {
  return (
    <Icon width="16" height="16" strokeWidth="2.2">
      <path d={direction === 'left' ? 'M15 6l-6 6 6 6' : 'M9 6l6 6-6 6'} />
    </Icon>
  );
}

export function CloseIcon() {
  return (
    <Icon width="18" height="18" strokeWidth="2">
      <path d="M6 6l12 12M18 6L6 18" />
    </Icon>
  );
}
