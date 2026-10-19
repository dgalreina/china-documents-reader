// Silueta del jue de bronce del icono de la app (perfil calcado con potrace,
// ver scripts/generate-app-icons.mjs), usada como spinner de carga: mismo
// patrón que VasijaSpinner.tsx en china-history-web, pero con el jue y en
// rojo sello en vez de verde.
//
// El trazado vive en UN solo sitio, data/jue-path.txt, el mismo que usa el generador de iconos; aquí se importa como texto (`?raw`) y vite.config.ts lo inserta
// también en la pantalla de arranque de index.html. Si se recalca el jue, se cambia ese fichero y se actualiza todo.
import JUE_RAW from '../../data/jue-path.txt?raw';

const JUE = JUE_RAW.trim();

export default function JueSpinner({ className = 'h-12 w-12' }: { className?: string }) {
  return (
    <svg viewBox="419.939 130.352 1084.337 1238.883" className={className} aria-hidden>
      <path d={JUE} pathLength={100} fill="none" stroke="currentColor" strokeWidth="39" strokeLinejoin="round" strokeLinecap="round" opacity={0.3} />
      <path
        d={JUE}
        pathLength={100}
        fill="none"
        stroke="var(--color-seal)"
        strokeWidth="39"
        strokeLinejoin="round"
        strokeLinecap="round"
        strokeDasharray="28 100"
        className="animate-jue-girar"
      />
    </svg>
  );
}
