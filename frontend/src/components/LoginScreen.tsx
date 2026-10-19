import { useState } from 'react';

interface LoginScreenProps {
  /** Rechaza con un mensaje en español si el correo o la contraseña no valen. */
  onSubmit: (email: string, password: string) => Promise<void>;
}

/** Pantalla de entrada. No hay registro: la cuenta se crea a mano en Cognito (un solo usuario). */
export function LoginScreen({ onSubmit }: LoginScreenProps) {
  const [email, setEmail] = useState('');
  const [password, setPassword] = useState('');
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const submit = async (event: React.FormEvent) => {
    event.preventDefault();
    if (busy) return;
    setBusy(true);
    setError(null);
    try {
      await onSubmit(email, password);
    } catch (problem) {
      setError(problem instanceof Error ? problem.message : 'No se pudo iniciar sesión.');
      setBusy(false);
    }
  };

  return (
    <main className="flex h-full items-center justify-center overflow-y-auto p-4">
      <form onSubmit={submit} className="w-full max-w-sm space-y-4 rounded border border-line bg-white p-6 shadow-sm">
        <h1 className="font-brush text-2xl font-bold">China Documents Reader</h1>
        <p className="text-sm text-stone">Entra para ver y subir tus libros.</p>

        <label className="block text-sm font-semibold">
          Correo
          <input
            type="email"
            name="email"
            autoComplete="username"
            inputMode="email"
            autoCapitalize="none"
            required
            value={email}
            onChange={(event) => setEmail(event.target.value)}
            className="mt-1 block w-full rounded border border-line px-3 py-2 text-base font-normal"
          />
        </label>
        <label className="block text-sm font-semibold">
          Contraseña
          <input
            type="password"
            name="password"
            autoComplete="current-password"
            required
            value={password}
            onChange={(event) => setPassword(event.target.value)}
            className="mt-1 block w-full rounded border border-line px-3 py-2 text-base font-normal"
          />
        </label>

        {error && (
          <p role="alert" className="text-sm text-red-700">
            {error}
          </p>
        )}
        <button disabled={busy || !email || !password} className="w-full rounded bg-seal px-4 py-2 font-semibold text-white hover:bg-seal-dark disabled:opacity-40">
          {busy ? 'Entrando…' : 'Entrar'}
        </button>
      </form>
    </main>
  );
}
