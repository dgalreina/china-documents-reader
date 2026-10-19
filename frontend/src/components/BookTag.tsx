/**
 * De qué libro viene un hito o un personaje. Si es otro distinto del que está abierto, es un botón que lo abre.
 * El título largo se corta con puntos suspensivos (el nombre completo sale al pasar el ratón o al mantener pulsado).
 */
export function BookTag({ slug, title, current, onOpen }: { slug: string; title: string; current: boolean; onOpen?: (slug: string) => void }) {
  const className = `inline-flex max-w-full items-center gap-1 rounded bg-paper-dark px-1.5 py-0.5 text-[11px] font-semibold ${current ? 'text-ink-soft' : 'text-stone'}`;
  const label = (
    <>
      <span aria-hidden>書</span>
      <span className="truncate">{title}</span>
    </>
  );
  if (current || !onOpen) {
    return (
      <span className={className} title={title}>
        {label}
      </span>
    );
  }
  return (
    <button type="button" onClick={() => onOpen(slug)} className={`${className} hover:bg-seal hover:text-on-seal`} title={`Abrir «${title}»`}>
      {label}
    </button>
  );
}
