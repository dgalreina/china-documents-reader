#!/usr/bin/env node
import * as cdk from 'aws-cdk-lib';
import { ChinaDocumentsStack } from '../lib/china-documents-stack';

const app = new cdk.App();

// Se puede cambiar por línea de comandos: cdk deploy -c mongoTarget=prod -c frontendOrigins=https://mi-sitio.example
const mongoTarget = String(app.node.tryGetContext('mongoTarget') ?? 'dev');
if (mongoTarget !== 'dev' && mongoTarget !== 'prod') {
  throw new Error(`mongoTarget debe ser "dev" o "prod" (recibido: ${mongoTarget})`);
}
const frontendOrigins = String(app.node.tryGetContext('frontendOrigins') ?? 'http://localhost:5173').split(',');

new ChinaDocumentsStack(app, 'ChinaDocumentsStack', {
  // La región es fija a propósito (Irlanda): cada región tiene sus propios recursos y no queremos desplegar en otra por descuido.
  env: { account: process.env.CDK_DEFAULT_ACCOUNT, region: 'eu-west-1' },
  mongoTarget,
  frontendOrigins,
  description: 'china-documents-reader: PDFs -> chunks, hitos y personajes en MongoDB (S3 + Lambda + Step Functions)',
});
