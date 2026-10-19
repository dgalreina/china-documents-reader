// Escribe frontend/public/config.json con los datos del stack DESPLEGADO, para desarrollar la web en local (npm run dev) contra la API real.
//
//   node infra/scripts/write-front-config.mjs        (necesita la sesión de AWS: "aws login")
//
// No contiene secretos (son identificadores públicos: dirección de la API, grupo y cliente de Cognito) y está en .gitignore. En la web publicada
// no hace falta: el despliegue (cdk deploy) sube su propio config.json.

import { execFileSync } from 'node:child_process';
import { writeFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';

const REGION = 'eu-west-1';
const STACK = 'ChinaDocumentsStack';

let outputs;
try {
  const raw = execFileSync('aws', ['cloudformation', 'describe-stacks', '--stack-name', STACK, '--region', REGION, '--query', 'Stacks[0].Outputs', '--output', 'json'], {
    encoding: 'utf8',
    stdio: ['ignore', 'pipe', 'pipe'],
  });
  outputs = Object.fromEntries(JSON.parse(raw).map((o) => [o.OutputKey, o.OutputValue]));
} catch (error) {
  console.error(`✗ No se pudieron leer las salidas del stack: ${String(error.stderr || error.message).trim().split('\n').slice(-2).join(' ')}\n  ¿Caducó la sesión? Repite "aws login".`);
  process.exit(1);
}

for (const key of ['ApiUrl', 'UserPoolId', 'UserPoolClientId']) {
  if (!outputs[key]) {
    console.error(`✗ El stack no tiene la salida ${key}.`);
    process.exit(1);
  }
}

const target = fileURLToPath(new URL('../../frontend/public/config.json', import.meta.url));
const config = { region: REGION, apiUrl: outputs.ApiUrl, userPoolId: outputs.UserPoolId, userPoolClientId: outputs.UserPoolClientId };
writeFileSync(target, `${JSON.stringify(config, null, 2)}\n`);
console.log(`✓ Escrito ${target}\n  Ahora: cd frontend && npm run dev  (http://localhost:5173 ya está permitido en el CORS de la API)`);
