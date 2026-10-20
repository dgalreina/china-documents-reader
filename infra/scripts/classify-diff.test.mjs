import assert from 'node:assert/strict';
import { test } from 'node:test';
import { classify } from './classify-diff.mjs';

const stack = (body) => `Stack ChinaDocumentsStack (aws://1/eu-west-1)\n${body}\n✨  Number of stacks with differences: 1\n`;

test('un cambio de código de Lambdas es normal', () => {
  const diff = stack('Resources\n[~] AWS::Lambda::Function PresignFn\n └─ [~] Code\n     └─ [~] .S3Key:\n         ├─ [-] a.zip\n         └─ [+] b.zip');
  assert.equal(classify(diff).sensitive, false); // "[-] a.zip" va indentado: no es un recurso borrado
});

test('permisos nuevos, borrados y sustituciones son delicados', () => {
  assert.equal(classify(stack('IAM Statement Changes\n┌───┐')).sensitive, true);
  assert.equal(classify(stack('Resources\n[-] AWS::S3::Bucket Viejo')).sensitive, true);
  assert.equal(classify(stack('[~] AWS::Cognito::UserPool Pool replace\n └─ requires replacement')).sensitive, true);
  assert.equal(classify(stack('Resource Policy Changes\n')).sensitive, true);
});

test('sin diferencias es normal', () => {
  assert.equal(classify('Stack ChinaDocumentsStack\nThere were no differences\n').sensitive, false);
});

test('una salida que no se reconoce (un error, la herramienta sin instalar) NUNCA es "normal"', () => {
  for (const output of ['', 'npm error code ETARGET\nnpm error notarget No matching version found for aws-cdk@2.1146.0.', 'Unable to resolve AWS account']) {
    const result = classify(output);
    assert.equal(result.sensitive, true);
    assert.match(result.reasons.join(' '), /no se pudo interpretar/);
  }
});
