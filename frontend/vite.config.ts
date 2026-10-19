import { defineConfig } from 'vitest/config';
import react from '@vitejs/plugin-react';
import tailwindcss from '@tailwindcss/vite';

export default defineConfig({
  plugins: [react(), tailwindcss()],
  server: { port: 5173 },
  // amazon-cognito-identity-js usa `global`, que existe en Node pero no en el navegador: sin esto la web se queda en blanco ("global is not defined").
  define: { global: 'globalThis' },
  // Las pruebas son de lógica pura (formato, validación, API simulada): no necesitan navegador.
  test: { environment: 'node' },
});
