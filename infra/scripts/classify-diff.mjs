// Lee la salida de `cdk diff` (fichero pasado como argumento) y dice si el cambio es DELICADO: permisos nuevos o distintos (IAM, políticas de recursos, grupos
// de seguridad), recursos que se BORRAN o que se SUSTITUYEN (sustituir = borrar y crear otro, y con datos eso puede perderlos). Escribe `sensitive=true|false`
// y los motivos en GITHUB_OUTPUT. Es conservador: ante la duda (incluida una salida que no se reconoce, p. ej. un error), pide aprobación.
import { appendFileSync, readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';

const RULES = [
  [/IAM Statement Changes/, 'cambian permisos de IAM (statements)'],
  [/IAM Policy Changes/, 'cambian políticas de IAM'],
  [/Resource Policy Changes/, 'cambian políticas de recursos (bucket, cola…)'],
  [/Security Group Changes/, 'cambian grupos de seguridad'],
  [/^\[-\]/m, 'se BORRA algún recurso'],
  [/requires replacement|\(replaced\)/, 'se SUSTITUYE algún recurso (borrar y crear otro)'],
];

// Una salida válida de `cdk diff` habla del stack o dice que no hay diferencias. Si no, algo falló (la herramienta no arrancó, no hay credenciales...):
// nunca se debe tomar por "no hay cambios delicados".
const UNDERSTOOD = /Stack ChinaDocumentsStack|There were no differences/;

export function classify(diff) {
  const reasons = RULES.filter(([re]) => re.test(diff)).map(([, why]) => why);
  if (!UNDERSTOOD.test(diff)) reasons.push('no se pudo interpretar el resultado de cdk diff (¿falló?): se pide aprobación por precaución');
  return { sensitive: reasons.length > 0, reasons };
}

if (process.argv[1] === fileURLToPath(import.meta.url)) {
  const { sensitive, reasons } = classify(readFileSync(process.argv[2], 'utf-8'));
  console.log(sensitive ? `DELICADO: ${reasons.join('; ')}` : 'Cambio normal: sin permisos nuevos ni borrados.');
  if (process.env.GITHUB_OUTPUT) {
    appendFileSync(process.env.GITHUB_OUTPUT, `sensitive=${sensitive}\nreasons=${reasons.join('; ')}\n`);
  }
}
