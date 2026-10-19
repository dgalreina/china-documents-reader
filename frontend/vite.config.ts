import { readFileSync } from 'node:fs';
import type { Plugin } from 'vite';
import { defineConfig } from 'vitest/config';
import react from '@vitejs/plugin-react';
import tailwindcss from '@tailwindcss/vite';

// La pantalla de arranque de index.html (se ve antes de que cargue el JavaScript) dibuja el mismo jue que el spinner de React. Su trazado vive en data/jue-path.txt
// (el mismo que usa el generador de iconos): aquí se inserta donde index.html pone __JUE_PATH__, sin copiarlo a mano.
const juePath = readFileSync(new URL('./data/jue-path.txt', import.meta.url), 'utf-8').trim();
const inlineJuePath = (): Plugin => ({
  name: 'inline-jue-path',
  transformIndexHtml: (html) => html.replaceAll('__JUE_PATH__', juePath),
});

export default defineConfig({
  plugins: [react(), tailwindcss(), inlineJuePath()],
  server: { port: 5173 },
  // amazon-cognito-identity-js usa `global`, que existe en Node pero no en el navegador: sin esto la web se queda en blanco ("global is not defined").
  define: { global: 'globalThis' },
  // Las pruebas son de lógica pura (formato, validación, API simulada): no necesitan navegador.
  test: { environment: 'node' },
});
