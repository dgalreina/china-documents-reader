// Lee la salida de `cdk diff` (fichero pasado como argumento) y dice si el cambio es DELICADO: permisos nuevos o distintos (IAM, políticas de recursos, grupos
// de seguridad), recursos que se BORRAN o que se SUSTITUYEN (sustituir = borrar y crear otro, y con datos eso puede perderlos). Escribe `sensitive=true|false`
// y los motivos en GITHUB_OUTPUT. Es conservador: ante la duda, pide aprobación.
import { appendFileSync, readFileSync } from 'node:fs';

const diff = readFileSync(process.argv[2], 'utf-8');
const rules = [
  [/IAM Statement Changes/, 'cambian permisos de IAM (statements)'],
  [/IAM Policy Changes/, 'cambian políticas de IAM'],
  [/Resource Policy Changes/, 'cambian políticas de recursos (bucket, cola…)'],
  [/Security Group Changes/, 'cambian grupos de seguridad'],
  [/^\[-\]/m, 'se BORRA algún recurso'],
  [/requires replacement|\(replaced\)/, 'se SUSTITUYE algún recurso (borrar y crear otro)'],
];
const reasons = rules.filter(([re]) => re.test(diff)).map(([, why]) => why);
const sensitive = reasons.length > 0;

console.log(sensitive ? `DELICADO: ${reasons.join('; ')}` : 'Cambio normal: sin permisos nuevos ni borrados.');
if (process.env.GITHUB_OUTPUT) {
  appendFileSync(process.env.GITHUB_OUTPUT, `sensitive=${sensitive}\nreasons=${reasons.join('; ')}\n`);
}
