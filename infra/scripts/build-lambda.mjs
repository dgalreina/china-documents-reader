// Empaqueta el backend para AWS Lambda en infra/build/lambda: las dependencias de Python para LINUX (la Lambda corre en Linux, tú en Windows)
// más el código de backend/lambdas y backend/shared. No necesita Docker: pip descarga las ruedas ya compiladas para Linux.
//
// Uso: npm run build:lambda   (lo ejecutan también `npm run synth` y `npm run deploy`)
import { spawnSync } from 'node:child_process';
import { cpSync, existsSync, mkdirSync, readdirSync, rmSync, statSync } from 'node:fs';
import { dirname, join, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';

const root = resolve(dirname(fileURLToPath(import.meta.url)), '..', '..');
const backend = join(root, 'backend');
const out = join(root, 'infra', 'build', 'lambda');

// Debe coincidir con el runtime de la Lambda (lib/china-documents-stack.ts): Python 3.11 en x86_64.
const PYTHON_VERSION = '3.11';
const PLATFORM = 'manylinux2014_x86_64';

const venvPython = process.platform === 'win32' ? join(backend, '.venv', 'Scripts', 'python.exe') : join(backend, '.venv', 'bin', 'python');
const python = existsSync(venvPython) ? venvPython : 'python';

rmSync(out, { recursive: true, force: true });
mkdirSync(out, { recursive: true });

console.log(`Instalando dependencias para Linux (${PLATFORM}, Python ${PYTHON_VERSION})...`);
const pip = spawnSync(
  python,
  [
    '-m', 'pip', 'install',
    '--quiet', '--upgrade',
    '--platform', PLATFORM,
    '--implementation', 'cp',
    '--python-version', PYTHON_VERSION,
    '--only-binary=:all:',
    '--target', out,
    '-r', join(backend, 'requirements.txt'),
  ],
  { stdio: 'inherit' },
);
if (pip.status !== 0) {
  console.error('\nFalló pip. Si dice que no hay una versión compatible, algún paquete no publica rueda para Linux con este Python.');
  process.exit(pip.status ?? 1);
}

console.log('Copiando backend/lambdas y backend/shared...');
const skip = (src) => !/__pycache__|\.pyc$/.test(src);
for (const folder of ['lambdas', 'shared']) cpSync(join(backend, folder), join(out, folder), { recursive: true, filter: skip });

// Scripts de consola que pip deja en bin/: no se usan en la nube.
rmSync(join(out, 'bin'), { recursive: true, force: true });

const sizeBytes = (dir) =>
  readdirSync(dir, { withFileTypes: true }).reduce((total, entry) => {
    const path = join(dir, entry.name);
    return total + (entry.isDirectory() ? sizeBytes(path) : statSync(path).size);
  }, 0);
console.log(`Listo: ${out} (${(sizeBytes(out) / 1024 / 1024).toFixed(0)} MB descomprimido; el límite de AWS es 250 MB)`);
