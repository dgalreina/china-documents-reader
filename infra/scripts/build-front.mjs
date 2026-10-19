// Construye la web (frontend/dist) para publicarla y se asegura de que NO lleve ningún PDF.
//
// Vite copia todo lo de frontend/public a dist: ahí está el PDF de muestra (public/samples), que es solo para desarrollar y no se puede
// publicar (quedaría descargable por cualquiera con el enlace). Se borra y después se comprueba que no queda ningún PDF.

import { execFileSync } from 'node:child_process';
import { existsSync, readdirSync, rmSync, statSync } from 'node:fs';
import { join, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';

const frontend = resolve(fileURLToPath(new URL('../../frontend', import.meta.url)));
const dist = join(frontend, 'dist');

console.log('Construyendo la web (tsc + vite build)...');
// shell: true porque en Windows npm es un .cmd; los argumentos son fijos, no vienen de fuera.
execFileSync('npm', ['run', 'build'], { cwd: frontend, stdio: 'inherit', shell: true, env: process.env });

rmSync(join(dist, 'samples'), { recursive: true, force: true });
// El config.json de desarrollo (apunta a donde sea) no se publica: el despliegue sube el suyo, con los datos del stack.
rmSync(join(dist, 'config.json'), { force: true });

const pdfs = [];
const walk = (dir) => {
  for (const name of readdirSync(dir)) {
    const full = join(dir, name);
    if (statSync(full).isDirectory()) walk(full);
    else if (/\.pdf$/i.test(name)) pdfs.push(full);
  }
};
if (existsSync(dist)) walk(dist);
if (pdfs.length) {
  console.error(`✗ dist contiene PDFs y no se publicaría: ${pdfs.join(', ')}`);
  process.exit(1);
}
console.log('Listo: frontend/dist sin PDFs.');
