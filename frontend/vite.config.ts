import { defineConfig } from 'vitest/config';
import react from '@vitejs/plugin-react';
import tailwindcss from '@tailwindcss/vite';

export default defineConfig({
  plugins: [react(), tailwindcss()],
  server: { port: 5173 },
  // Las pruebas son de lógica pura (formato, validación, API simulada): no necesitan navegador.
  test: { environment: 'node' },
});
