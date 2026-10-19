#!/usr/bin/env node
import * as cdk from 'aws-cdk-lib';
import { CiStack } from '../lib/ci-stack';

// App aparte de la principal a propósito: no necesita construir las Lambdas ni la web, y se despliega una sola vez.
const app = new cdk.App();
new CiStack(app, 'ChinaDocumentsCiStack', {
  env: { account: process.env.CDK_DEFAULT_ACCOUNT, region: 'eu-west-1' },
  repo: String(app.node.tryGetContext('repo') ?? 'dgalreina/china-documents-reader'),
  description: 'china-documents-reader: acceso de GitHub Actions (OIDC) para desplegar sin claves',
});
