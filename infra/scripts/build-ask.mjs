// Empaqueta la Lambda del agente (Node.js) en infra/build/ask: su código y SOLO las dependencias de producción (`npm ci --omit=dev`).
// Las dependencias de Node son JavaScript puro (mongodb, openai, aws-jwt-verify), así que no hace falta construirlas "para Linux" como las de Python.
// El SDK de AWS (SSM) ya viene en el runtime de Lambda: por eso está solo como dependencia de desarrollo.
//
// Uso: npm run build:ask   (lo ejecutan también `npm run synth`, `diff` y `deploy`)
import { spawnSync } from 'node:child_process';
import { cpSync, existsSync, mkdirSync, readdirSync, rmSync, statSync } from 'node:fs';
import { dirname, join, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';

const root = resolve(dirname(fileURLToPath(import.meta.url)), '..', '..');
const source = join(root, 'backend', 'ask');
const out = join(root, 'infra', 'build', 'ask');

rmSync(out, { recursive: true, force: true });
mkdirSync(out, { recursive: true });

cpSync(join(source, 'src'), join(out, 'src'), { recursive: true });
for (const file of ['package.json', 'package-lock.json']) {
  if (existsSync(join(source, file))) cpSync(join(source, file), join(out, file));
}

console.log('Instalando dependencias de producción del agente...');
// shell: true porque en Windows npm es un .cmd; los argumentos son fijos.
const install = spawnSync('npm', ['ci', '--omit=dev', '--ignore-scripts', '--no-audit', '--no-fund'], { cwd: out, stdio: 'inherit', shell: true });
if (install.status !== 0) {
  console.error('\nFalló npm ci en el paquete del agente.');
  process.exit(install.status ?? 1);
}

const sizeBytes = (dir) =>
  readdirSync(dir, { withFileTypes: true }).reduce((total, entry) => {
    const path = join(dir, entry.name);
    return total + (entry.isDirectory() ? sizeBytes(path) : statSync(path).size);
  }, 0);
console.log(`Listo: ${out} (${(sizeBytes(out) / 1024 / 1024).toFixed(0)} MB)`);
