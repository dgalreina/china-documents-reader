import * as cdk from 'aws-cdk-lib';
import * as iam from 'aws-cdk-lib/aws-iam';
import { Construct } from 'constructs';

export interface CiStackProps extends cdk.StackProps {
  /** "dueño/repositorio" de GitHub, el único que puede asumir el rol. */
  repo: string;
}

/**
 * Lo que necesita GitHub Actions para desplegar SIN claves guardadas (OIDC): un proveedor de identidad (GitHub) y un rol que solo ese repo puede asumir.
 * Se despliega UNA vez a mano (`npm run deploy:ci`, con tu `aws login`); a partir de ahí los despliegues los hace el CI.
 *
 * El rol solo puede una cosa: usar los roles que creó `cdk bootstrap` (deploy, publicación de ficheros, consulta). Esos son los que tocan CloudFormation.
 */
export class CiStack extends cdk.Stack {
  constructor(scope: Construct, id: string, props: CiStackProps) {
    super(scope, id, props);

    const provider = new iam.OidcProviderNative(this, 'GithubOidc', {
      url: 'https://token.actions.githubusercontent.com',
      clientIds: ['sts.amazonaws.com'],
    });

    // `sub` es quién pide entrar. Se acepta SOLO este repo y: la rama main (el paso que calcula los cambios), o uno de los dos "environments"
    // de GitHub (despliegue normal, o el que espera aprobación humana). Un fork o una pull request tienen otro `sub` y se quedan fuera.
    const sub = (suffix: string) => `repo:${props.repo}:${suffix}`;
    const role = new iam.Role(this, 'GithubDeployRole', {
      roleName: 'china-documents-github-deploy',
      description: 'GitHub Actions (OIDC): despliega ChinaDocumentsStack a través de los roles del bootstrap de CDK',
      maxSessionDuration: cdk.Duration.hours(1),
      assumedBy: new iam.WebIdentityPrincipal(provider.oidcProviderRef.oidcProviderArn, {
        StringEquals: {
          'token.actions.githubusercontent.com:aud': 'sts.amazonaws.com',
        },
        StringLike: {
          'token.actions.githubusercontent.com:sub': [sub('ref:refs/heads/main'), sub('environment:despliegue'), sub('environment:aprobacion')],
        },
      }),
    });

    // Los roles del bootstrap (qualifier por defecto "hnb659fds"). Sin esto el rol no puede nada en la cuenta.
    role.addToPolicy(
      new iam.PolicyStatement({
        actions: ['sts:AssumeRole'],
        resources: [`arn:${this.partition}:iam::${this.account}:role/cdk-hnb659fds-*-role-${this.account}-${this.region}`],
      }),
    );

    new cdk.CfnOutput(this, 'RoleArn', { value: role.roleArn, description: 'Va en GitHub: Settings > Secrets and variables > Actions > Variables > AWS_ROLE_ARN' });
  }
}
