export type BookScope = 'todos' | 'este';

/** "Todos los libros / Este libro": qué hitos o personajes se enseñan. Solo tiene sentido si hay más de un libro. */
export function BookFilter({ value, onChange }: { value: BookScope; onChange: (value: BookScope) => void }) {
  return (
    <div className="flex overflow-hidden rounded border border-line text-xs font-semibold" role="group" aria-label="Libros">
      {(['todos', 'este'] as const).map((option) => (
        <button
          key={option}
          onClick={() => onChange(option)}
          aria-pressed={value === option}
          className={`px-2 py-1 ${value === option ? 'bg-seal text-on-seal' : 'bg-white text-stone hover:text-ink'}`}
        >
          {option === 'todos' ? 'Todos los libros' : 'Este libro'}
        </button>
      ))}
    </div>
  );
}
