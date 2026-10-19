import { useCallback, useEffect, useMemo, useState } from 'react';
import App from './App';
import { HttpApi } from './api/httpApi';
import { AuthService } from './auth/auth';
import { LoginScreen } from './components/LoginScreen';
import { loadConfig, type AppConfig } from './config';

type Boot = { status: 'loading' } | { status: 'error'; message: string } | { status: 'ready'; config: AppConfig; auth: AuthService; signedIn: boolean };

const Centered = ({ children }: { children: React.ReactNode }) => <div className="flex h-full items-center justify-center p-6 text-center text-stone">{children}</div>;

/** Arranque: lee la configuración del despliegue, comprueba si ya había sesión y enseña el login o la app. */
export default function Root() {
  const [boot, setBoot] = useState<Boot>({ status: 'loading' });

  useEffect(() => {
    let cancelled = false;
    loadConfig()
      .then(async (config) => {
        const auth = new AuthService(config);
        const signedIn = await auth.restore();
        if (!cancelled) setBoot({ status: 'ready', config, auth, signedIn });
      })
      .catch((error: unknown) => {
        if (!cancelled) setBoot({ status: 'error', message: error instanceof Error ? error.message : 'No se pudo arrancar la web.' });
      });
    return () => {
      cancelled = true;
    };
  }, []);

  const ready = boot.status === 'ready' ? boot : null;
  const signedOut = useCallback(() => {
    ready?.auth.signOut();
    setBoot((current) => (current.status === 'ready' ? { ...current, signedIn: false } : current));
  }, [ready?.auth]);

  // Una sola instancia de la API mientras haya sesión (guarda las subidas en curso y la caché de URLs).
  const api = useMemo(
    () => (ready?.signedIn ? new HttpApi({ apiUrl: ready.config.apiUrl, getToken: () => ready.auth.getIdToken(), onUnauthorized: signedOut }) : null),
    [ready?.signedIn, ready?.config, ready?.auth, signedOut],
  );

  if (boot.status === 'loading') return <Centered>Cargando…</Centered>;
  if (boot.status === 'error') {
    return (
      <Centered>
        <p role="alert" className="max-w-md text-seal-dark">
          {boot.message}
        </p>
      </Centered>
    );
  }
  if (!api) {
    return (
      <LoginScreen
        onSubmit={async (email, password) => {
          await boot.auth.signIn(email, password);
          setBoot({ ...boot, signedIn: true });
        }}
      />
    );
  }
  return <App api={api} onSignOut={signedOut} />;
}
