import { useEffect, useState } from 'react';

/**
 * La portada de un libro (su primera página) en miniatura. Sin portada, o si la imagen no carga (su URL temporal caducó, un fallo de red...), se enseña un
 * recambio con el carácter 書 («libro»): la lista nunca se queda con un hueco roto.
 */
export function Cover({ url }: { url?: string }) {
  const [failed, setFailed] = useState(false);
  useEffect(() => setFailed(false), [url]);

  const box = 'h-20 w-14 shrink-0 rounded border border-line';
  if (!url || failed) {
    return (
      <div aria-hidden="true" className={`${box} flex items-center justify-center bg-paper-dark font-brush text-xl text-stone-light`}>
        書
      </div>
    );
  }
  return <img src={url} alt="" loading="lazy" onError={() => setFailed(true)} className={`${box} bg-white object-cover object-top shadow-sm`} />;
}
