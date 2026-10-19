import JueSpinner from './JueSpinner';

/**
 * El indicador de carga de toda la web: la silueta del jue (el icono de la app) con un trazo rojo que la recorre, como el de china-history con su vasija.
 * `size` es el lado en píxeles. Lleva `role="status"` y una etiqueta para los lectores de pantalla (el dibujo en sí es decorativo).
 */
export function Spinner({ label = 'Cargando', size = 56 }: { label?: string; size?: number }) {
  return (
    <span role="status" aria-label={label} className="inline-flex text-stone" style={{ width: size, height: size }}>
      <JueSpinner className="h-full w-full" />
    </span>
  );
}

/** Un spinner centrado en el hueco que ocupe, con un texto corto debajo (opcional). */
export function CenteredSpinner({ label, caption }: { label?: string; caption?: string }) {
  return (
    <div className="flex h-full flex-col items-center justify-center gap-3 p-6 text-center">
      <Spinner label={label ?? caption ?? 'Cargando'} />
      {caption && <p className="text-sm text-stone">{caption}</p>}
    </div>
  );
}

/**
 * La pantalla mientras arranca la web (config, sesión, lista de libros): la cabecera translúcida de siempre y el spinner, sin texto. Se ve el esqueleto de la
 * pantalla en lugar de un "Cargando…" suelto, y no salta cuando llega el contenido. Su gemelo en HTML puro está en index.html (se ve antes de que cargue el JavaScript).
 */
export function LoadingShell() {
  return (
    <div className="relative h-full">
      <div className="absolute inset-x-0 top-0 z-20 border-b border-ink/10 bg-paper/80 backdrop-blur" style={{ height: 48 }} aria-hidden="true" />
      <CenteredSpinner />
    </div>
  );
}
