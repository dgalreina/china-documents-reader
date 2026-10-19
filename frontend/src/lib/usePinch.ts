import { useEffect, useRef, type RefObject } from 'react';

export interface PinchHandlers {
  /** Mientras los dedos se separan o se juntan: `ratio` respecto al inicio del gesto (1 = sin cambio) y el punto medio, en píxeles dentro del elemento. */
  onPinch: (ratio: number, center: { x: number; y: number }) => void;
  /** Se levantó algún dedo: el gesto terminó con este `ratio` final. */
  onPinchEnd: (ratio: number, center: { x: number; y: number }) => void;
  /** Ctrl + rueda (o pellizco del trackpad en el ordenador): `factor` multiplica el zoom actual (>1 acerca). */
  onWheelZoom?: (factor: number, center: { x: number; y: number }) => void;
}

const distance = (a: Touch, b: Touch) => Math.hypot(a.clientX - b.clientX, a.clientY - b.clientY);

/**
 * El zoom con dos dedos (y con Ctrl + rueda) de un visor. La web NO deja que el navegador haga zoom de toda la página (index.css pone `touch-action: pan-x pan-y`):
 * el gesto llega aquí y cada visor decide qué ampliar (el PDF, o el tamaño del texto en el lector). Con un solo dedo no hace nada: el scroll sigue siendo del navegador.
 */
export function usePinch(ref: RefObject<HTMLElement | null>, handlers: PinchHandlers) {
  const latest = useRef(handlers);
  latest.current = handlers;

  useEffect(() => {
    const element = ref.current;
    if (!element) return;
    let startDistance = 0;
    let last = { ratio: 1, center: { x: 0, y: 0 } };

    const centerOf = (a: Touch, b: Touch) => {
      const box = element.getBoundingClientRect();
      return { x: (a.clientX + b.clientX) / 2 - box.left, y: (a.clientY + b.clientY) / 2 - box.top };
    };
    const onStart = (event: TouchEvent) => {
      if (event.touches.length !== 2) return;
      startDistance = distance(event.touches[0], event.touches[1]);
      last = { ratio: 1, center: centerOf(event.touches[0], event.touches[1]) };
    };
    const onMove = (event: TouchEvent) => {
      if (startDistance === 0 || event.touches.length !== 2) return;
      event.preventDefault(); // con dos dedos el gesto es nuestro, no del navegador
      const [a, b] = [event.touches[0], event.touches[1]];
      last = { ratio: distance(a, b) / startDistance, center: centerOf(a, b) };
      latest.current.onPinch(last.ratio, last.center);
    };
    const onEnd = (event: TouchEvent) => {
      if (startDistance === 0 || event.touches.length >= 2) return;
      startDistance = 0;
      latest.current.onPinchEnd(last.ratio, last.center);
    };
    const onWheel = (event: WheelEvent) => {
      if (!event.ctrlKey || !latest.current.onWheelZoom) return; // el pellizco del trackpad llega como Ctrl + rueda (con deltas pequeños); una rueda de ratón da saltos grandes, que se limitan a ~28 % por muesca
      event.preventDefault();
      const box = element.getBoundingClientRect();
      latest.current.onWheelZoom(Math.exp(-Math.max(-25, Math.min(25, event.deltaY)) * 0.01), { x: event.clientX - box.left, y: event.clientY - box.top });
    };
    // Safari de iPhone/iPad envía su propio gesto de zoom de página; se anula (el zoom lo hacemos nosotros).
    const stopGesture = (event: Event) => event.preventDefault();

    element.addEventListener('touchstart', onStart, { passive: true });
    element.addEventListener('touchmove', onMove, { passive: false });
    element.addEventListener('touchend', onEnd, { passive: true });
    element.addEventListener('touchcancel', onEnd, { passive: true });
    element.addEventListener('wheel', onWheel, { passive: false });
    document.addEventListener('gesturestart', stopGesture);
    return () => {
      element.removeEventListener('touchstart', onStart);
      element.removeEventListener('touchmove', onMove);
      element.removeEventListener('touchend', onEnd);
      element.removeEventListener('touchcancel', onEnd);
      element.removeEventListener('wheel', onWheel);
      document.removeEventListener('gesturestart', stopGesture);
    };
  }, [ref]);
}

export const clamp = (value: number, min: number, max: number) => Math.min(max, Math.max(min, value));
