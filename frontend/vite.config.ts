import { existsSync, readdirSync, readFileSync, statSync } from 'node:fs';
import { join } from 'node:path';
import { fileURLToPath } from 'node:url';
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

// pdf.js necesita unos ficheros auxiliares que NO van dentro de su JavaScript y que carga solo cuando un PDF los pide: decodificadores WebAssembly (imágenes JBIG2 y JPEG2000,
// perfiles de color ICC), mapas de caracteres (CMaps: tipografías chinas/japonesas sin incrustar) y tipografías estándar. Sin ellos, un PDF escaneado (casi todo imagen) se
// dibuja EN BLANCO y sin ningún error. Se sirven desde /pdfjs/ (en desarrollo, desde node_modules; en la web publicada, copiados a dist/pdfjs/ junto con sus licencias).
const PDFJS_ROOT = fileURLToPath(new URL('./node_modules/pdfjs-dist/', import.meta.url));
const PDFJS_ASSET_DIRS = ['wasm', 'iccs', 'cmaps', 'standard_fonts'];
const pdfjsAssets = (): Plugin => ({
  name: 'pdfjs-assets',
  configureServer(server) {
    server.middlewares.use('/pdfjs/', (request, response, next) => {
      const relative = decodeURIComponent((request.url ?? '').split('?')[0]).replace(/^\/+/, '');
      const file = join(PDFJS_ROOT, relative);
      if (relative.includes('..') || !PDFJS_ASSET_DIRS.includes(relative.split('/')[0]) || !existsSync(file) || !statSync(file).isFile()) return next();
      response.setHeader('content-type', file.endsWith('.wasm') ? 'application/wasm' : 'application/octet-stream');
      response.end(readFileSync(file));
    });
  },
  generateBundle() {
    for (const dir of PDFJS_ASSET_DIRS) {
      for (const name of readdirSync(join(PDFJS_ROOT, dir))) {
        this.emitFile({ type: 'asset', fileName: `pdfjs/${dir}/${name}`, source: readFileSync(join(PDFJS_ROOT, dir, name)) });
      }
    }
  },
});

export default defineConfig({
  plugins: [react(), tailwindcss(), inlineJuePath(), pdfjsAssets()],
  server: { port: 5173 },
  // amazon-cognito-identity-js usa `global`, que existe en Node pero no en el navegador: sin esto la web se queda en blanco ("global is not defined").
  define: { global: 'globalThis' },
  // Las pruebas son de lógica pura (formato, validación, API simulada): no necesitan navegador.
  test: { environment: 'node' },
});
