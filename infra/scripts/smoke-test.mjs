// Prueba de punta a punta contra el stack DESPLEGADO: login -> subir un PDF -> esperar al flujo -> pedir una página traducida.
//
//   cd infra
//   node scripts/smoke-test.mjs --email tu@correo [--pdf ruta.pdf] [--source oxford-prueba] [--page 3] [--replace]
//
// Necesita la sesión de AWS (`aws login`): lee las salidas del stack y pide el token de Cognito con tus credenciales de administrador.
// La contraseña se pide por teclado SIN mostrarse y nunca se guarda ni se imprime. Escribe en la base de DEV (MONGO_TARGET=dev del stack).
// Por defecto sube las 20 páginas de muestra del Oxford (≈ 0,16 USD de OpenAI; la traducción de una página, ≈ 0,01 USD).

import { execFileSync } from 'node:child_process';
import { readFileSync } from 'node:fs';
import { basename, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';
import readline from 'node:readline';

const REGION = 'eu-west-1';
const STACK = 'ChinaDocumentsStack';
const POLL_SECONDS = 15;
const TIMEOUT_MINUTES = 45;

const argv = process.argv.slice(2);
const option = (name, fallback) => {
  const i = argv.indexOf(`--${name}`);
  return i >= 0 && argv[i + 1] && !argv[i + 1].startsWith('--') ? argv[i + 1] : fallback;
};
const email = option('email');
const source = option('source', 'oxford-prueba');
const page = Number(option('page', '3'));
const replaceSource = argv.includes('--replace');
const pdfPath = resolve(option('pdf', fileURLToPath(new URL('../../frontend/public/samples/oxford-zhou.pdf', import.meta.url))));

const fail = (message) => {
  console.error(`\n✗ ${message}`);
  process.exit(1);
};
const step = (message) => console.log(`\n▶ ${message}`);
const seconds = (ms) => (ms / 1000).toFixed(1);

if (!email) fail('Falta --email (el usuario de Cognito que creaste).');
if (!Number.isInteger(page) || page < 1) fail('--page debe ser un número de página (1, 2, 3...).');

const aws = (...args) => {
  try {
    return JSON.parse(execFileSync('aws', [...args, '--region', REGION, '--output', 'json'], { encoding: 'utf8', stdio: ['ignore', 'pipe', 'pipe'] }));
  } catch (error) {
    const detail = String(error.stderr || error.message).trim().split('\n').slice(-3).join(' ');
    return fail(`Falló la CLI de AWS (${args.slice(0, 2).join(' ')}): ${detail}\n  ¿Caducó la sesión? Repite "aws login".`);
  }
};

/** Pide un texto por teclado sin mostrarlo en pantalla. */
const askHidden = (prompt) =>
  new Promise((done) => {
    const rl = readline.createInterface({ input: process.stdin, output: process.stdout });
    rl._writeToOutput = () => {}; // silencia lo que se escribe
    process.stdout.write(prompt);
    rl.question('', (answer) => {
      rl.close();
      process.stdout.write('\n');
      done(answer);
    });
  });

const api = async (base, token, method, path, body) => {
  const response = await fetch(`${base}${path}`, {
    method,
    headers: { authorization: `Bearer ${token}`, ...(body ? { 'content-type': 'application/json' } : {}) },
    body: body ? JSON.stringify(body) : undefined,
  });
  const text = await response.text();
  let data;
  try {
    data = JSON.parse(text);
  } catch {
    data = { raw: text.slice(0, 200) };
  }
  return { status: response.status, data };
};

// ------------------------------------------------------------------------------------------------------ 1. salidas y login
step('Leyendo las salidas del stack');
const outputs = Object.fromEntries(aws('cloudformation', 'describe-stacks', '--stack-name', STACK, '--query', 'Stacks[0].Outputs').map((o) => [o.OutputKey, o.OutputValue]));
for (const key of ['ApiUrl', 'UserPoolId', 'UserPoolClientId']) if (!outputs[key]) fail(`El stack no tiene la salida ${key}.`);
console.log(`  API: ${outputs.ApiUrl}`);

step(`Login de ${email}`);
const password = await askHidden('  Contraseña (no se muestra): ');
const auth = aws(
  'cognito-idp', 'admin-initiate-auth',
  '--user-pool-id', outputs.UserPoolId,
  '--client-id', outputs.UserPoolClientId,
  '--auth-flow', 'ADMIN_USER_PASSWORD_AUTH',
  '--auth-parameters', JSON.stringify({ USERNAME: email, PASSWORD: password }),
);
if (auth.ChallengeName) fail(`Cognito pide un paso extra (${auth.ChallengeName}). Este script no lo soporta.`);
const token = auth.AuthenticationResult?.IdToken;
if (!token) fail('No se obtuvo el token.');
console.log('  ✓ token obtenido');

step('Comprobando que la API exige login (sin token debe dar 401)');
const anonymous = await fetch(`${outputs.ApiUrl}/jobs/${'0'.repeat(32)}`);
console.log(`  sin token: HTTP ${anonymous.status} ${anonymous.status === 401 ? '✓' : '✗ ¡debería ser 401!'}`);
if (anonymous.status !== 401) fail('La API responde sin token: no continúo.');

// -------------------------------------------------------------------------------------------------------------- 2. subida
step(`Pidiendo permiso de subida para "${basename(pdfPath)}" como libro "${source}"`);
const presign = await api(outputs.ApiUrl, token, 'POST', '/uploads', { filename: basename(pdfPath), source, replaceSource });
if (presign.status !== 200) fail(`POST /uploads dio ${presign.status}: ${JSON.stringify(presign.data)}`);
const { jobId, upload } = presign.data;
console.log(`  jobId: ${jobId}`);

step('Subiendo el PDF a S3');
let bytes;
try {
  bytes = readFileSync(pdfPath);
} catch {
  fail(`No encuentro el PDF: ${pdfPath}`);
}
const form = new FormData();
for (const [name, value] of Object.entries(upload.fields)) form.append(name, value);
form.append('file', new Blob([bytes], { type: 'application/pdf' }), basename(pdfPath)); // el fichero va el último: lo exige S3
const uploaded = await fetch(upload.url, { method: 'POST', body: form });
if (uploaded.status !== 204) fail(`S3 rechazó la subida (HTTP ${uploaded.status}): ${(await uploaded.text()).slice(0, 300)}`);
console.log(`  ✓ subido (${(bytes.length / 1024 / 1024).toFixed(1)} MB). EventBridge debería arrancar el flujo.`);

// ------------------------------------------------------------------------------------------------------ 3. esperar al flujo
step(`Esperando al flujo (consulta cada ${POLL_SECONDS} s, máximo ${TIMEOUT_MINUTES} min)`);
const started = Date.now();
let last = '';
let job;
for (;;) {
  if (Date.now() - started > TIMEOUT_MINUTES * 60_000) fail('Se acabó el tiempo de espera. Mira la ejecución en Step Functions.');
  const response = await api(outputs.ApiUrl, token, 'GET', `/jobs/${jobId}`);
  if (response.status !== 200) fail(`GET /jobs dio ${response.status}: ${JSON.stringify(response.data)}`);
  job = response.data;
  if (job.status !== last) {
    console.log(`  [${seconds(Date.now() - started)} s] ${job.status}`);
    last = job.status;
  }
  if (['DONE', 'FAILED', 'NEEDS_OCR'].includes(job.status)) break;
  await new Promise((r) => setTimeout(r, POLL_SECONDS * 1000));
}
if (job.status === 'FAILED') fail(`El flujo falló: ${job.error ?? '(sin detalle)'}\n  Detalle: consola de Step Functions > ejecución > paso en rojo, o los logs de esa Lambda.`);
if (job.status === 'NEEDS_OCR') fail('El PDF parece escaneado (sin texto): hace falta OCR.');
const s = job.summary ?? {};
console.log(`  ✓ terminado en ${seconds(Date.now() - started)} s`);
console.log(`    chunks: ${s.chunks} (listos ${s.chunksListos}, pendientes ${s.chunksPendientes}, con ubicación ${s.chunksConUbicacion})`);
console.log(`    hitos escritos: ${s.hitosEscritos} (extraídos ${s.hitosExtraidos}, duplicados descartados ${s.duplicadosDescartados})`);
console.log(`    personajes: ${Object.keys(s.personajes ?? {}).length}`);
console.log(`    coste estimado de OpenAI: $${s.costeEstimadoUsd}`);
if (s.limpieza) console.log(`    limpieza: ${JSON.stringify(s.limpieza)}`);

// -------------------------------------------------------------------------------------------------------- 4. traducción
step(`Pidiendo la traducción de la página ${page}`);
for (const attempt of ['primera vez (traduce)', 'segunda vez (caché)']) {
  const t0 = Date.now();
  const response = await api(outputs.ApiUrl, token, 'GET', `/books/${source}/pages/${page}/translation`);
  if (response.status !== 200) fail(`La traducción dio ${response.status}: ${JSON.stringify(response.data)}`);
  const { text, cached } = response.data;
  console.log(`  ${attempt}: ${seconds(Date.now() - t0)} s, ${text.length} caracteres, cached=${cached}`);
  if (attempt.startsWith('primera')) console.log(`    vista previa: ${text.slice(0, 160).replace(/\s+/g, ' ')}…`);
}
console.log('\n✓ Prueba completa. Los datos están en la base de DEV (china_documents_dev).');
