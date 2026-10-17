import * as path from 'node:path';
import * as cdk from 'aws-cdk-lib';
import { Construct } from 'constructs';
import * as apigw from 'aws-cdk-lib/aws-apigatewayv2';
import * as authorizers from 'aws-cdk-lib/aws-apigatewayv2-authorizers';
import * as integrations from 'aws-cdk-lib/aws-apigatewayv2-integrations';
import * as cognito from 'aws-cdk-lib/aws-cognito';
import * as dynamodb from 'aws-cdk-lib/aws-dynamodb';
import * as events from 'aws-cdk-lib/aws-events';
import * as targets from 'aws-cdk-lib/aws-events-targets';
import * as iam from 'aws-cdk-lib/aws-iam';
import * as lambda from 'aws-cdk-lib/aws-lambda';
import * as logs from 'aws-cdk-lib/aws-logs';
import * as s3 from 'aws-cdk-lib/aws-s3';
import * as sfn from 'aws-cdk-lib/aws-stepfunctions';
import * as tasks from 'aws-cdk-lib/aws-stepfunctions-tasks';

export interface ChinaDocumentsStackProps extends cdk.StackProps {
  /** A qué base de Mongo escriben las Lambdas. "dev" por defecto: producción solo si se pide a propósito. */
  mongoTarget: 'dev' | 'prod';
  /** Webs autorizadas a llamar a la API y a subir PDFs a S3 (CORS). */
  frontendOrigins: string[];
  /** Prefijo de SSM donde están los secretos (se crean a mano, ver infra/README.md). */
  ssmPrefix?: string;
  /** Chunks por tanda. Con 100, una tanda tarda 1-2 min y cabe de sobra en los 15 de una Lambda. */
  batchSize?: number;
}

export class ChinaDocumentsStack extends cdk.Stack {
  constructor(scope: Construct, id: string, props: ChinaDocumentsStackProps) {
    super(scope, id, props);
    const ssmPrefix = props.ssmPrefix ?? '/china-docs';
    const batchSize = props.batchSize ?? 100;

    // ---------------------------------------------------------------------------------------------------- almacenamiento
    // Los PDFs subidos y los ficheros intermedios de cada trabajo (jobs/<id>/...). Todo se borra solo a los 30 días.
    const dataBucket = new s3.Bucket(this, 'DataBucket', {
      blockPublicAccess: s3.BlockPublicAccess.BLOCK_ALL,
      encryption: s3.BucketEncryption.S3_MANAGED,
      enforceSSL: true,
      eventBridgeEnabled: true, // avisa a EventBridge cuando llega un PDF: eso arranca el flujo
      lifecycleRules: [{ expiration: cdk.Duration.days(30) }],
      cors: [
        {
          // El navegador sube el PDF directo a S3 con una URL firmada (POST): S3 necesita permitir ese origen.
          allowedMethods: [s3.HttpMethods.POST],
          allowedOrigins: props.frontendOrigins,
          allowedHeaders: ['*'],
          maxAge: 3000,
        },
      ],
      removalPolicy: cdk.RemovalPolicy.DESTROY,
      autoDeleteObjects: true,
    });

    // Datos geográficos externos (CHGIS no se puede redistribuir: bucket privado, subido a mano una vez). No se borra con el stack.
    const geodataBucket = new s3.Bucket(this, 'GeodataBucket', {
      blockPublicAccess: s3.BlockPublicAccess.BLOCK_ALL,
      encryption: s3.BucketEncryption.S3_MANAGED,
      enforceSSL: true,
      removalPolicy: cdk.RemovalPolicy.RETAIN,
    });

    // Estado de cada trabajo. Pago por uso y los registros caducan solos (atributo expiresAt).
    const jobs = new dynamodb.Table(this, 'JobsTable', {
      partitionKey: { name: 'jobId', type: dynamodb.AttributeType.STRING },
      billingMode: dynamodb.BillingMode.PAY_PER_REQUEST,
      timeToLiveAttribute: 'expiresAt',
      removalPolicy: cdk.RemovalPolicy.DESTROY,
    });

    // ------------------------------------------------------------------------------------------------------------ Lambdas
    // Un único paquete (infra/build/lambda, lo genera `npm run build:lambda`) para todas; cada función apunta a su handler.
    const code = lambda.Code.fromAsset(path.join(__dirname, '..', 'build', 'lambda'));
    const commonEnv: Record<string, string> = {
      SSM_PREFIX: ssmPrefix,
      GEODATA_BUCKET: geodataBucket.bucketName,
      JOBS_TABLE: jobs.tableName,
      UPLOAD_BUCKET: dataBucket.bucketName,
      MONGO_TARGET: props.mongoTarget, // dev o prod lo decide SOLO el despliegue, nunca el evento
    };
    const fn = (name: string, handler: string, timeoutSeconds: number, memoryMb: number) =>
      new lambda.Function(this, name, {
        runtime: lambda.Runtime.PYTHON_3_11,
        architecture: lambda.Architecture.X86_64,
        code,
        handler,
        timeout: cdk.Duration.seconds(timeoutSeconds),
        memorySize: memoryMb,
        environment: commonEnv,
        logGroup: new logs.LogGroup(this, `${name}Logs`, { retention: logs.RetentionDays.ONE_MONTH, removalPolicy: cdk.RemovalPolicy.DESTROY }),
      });

    const presign = fn('PresignFn', 'lambdas.presign.handler.handler', 15, 256);
    const jobStatus = fn('JobStatusFn', 'lambdas.jobs.handler.handler', 10, 256);
    const extractChunk = fn('ExtractChunkFn', 'lambdas.extract_chunk.handler.handler', 300, 1024);
    const embedPeriod = fn('EmbedPeriodFn', 'lambdas.embed_label.handler.period_handler', 120, 512);
    const embedBatch = fn('EmbedBatchFn', 'lambdas.embed_label.handler.handler', 600, 1024);
    const extractHitos = fn('ExtractHitosFn', 'lambdas.extract_hitos.handler.handler', 840, 1024);
    const writeChunks = fn('WriteChunksFn', 'lambdas.write_mongo.handler.chunks_handler', 300, 512);
    const writeHitos = fn('WriteHitosFn', 'lambdas.write_mongo.handler.hitos_handler', 840, 1024);
    const finalize = fn('FinalizeFn', 'lambdas.write_mongo.handler.finalize_handler', 300, 512);

    // Permisos mínimos para cada una.
    dataBucket.grantPut(presign); // firma subidas: necesita poder escribir en esa clave
    jobs.grantWriteData(presign);
    jobs.grantReadData(jobStatus);
    for (const f of [extractChunk, embedPeriod, embedBatch, extractHitos, writeChunks, writeHitos]) dataBucket.grantReadWrite(f);
    for (const f of [embedBatch, extractHitos]) geodataBucket.grantRead(f); // geocodifican con CHGIS y las listas de china-history
    const readSecrets = new iam.PolicyStatement({
      actions: ['ssm:GetParameter'],
      resources: [cdk.Arn.format({ service: 'ssm', resource: 'parameter', resourceName: `${ssmPrefix.replace(/^\//, '')}/*`, arnFormat: cdk.ArnFormat.SLASH_RESOURCE_NAME }, this)],
    });
    for (const f of [embedPeriod, embedBatch, extractHitos, writeChunks, writeHitos, finalize]) f.addToRolePolicy(readSecrets);

    // ------------------------------------------------------------------------------------------------- Step Functions
    const jobKey = { jobId: tasks.DynamoAttributeValue.fromString(sfn.JsonPath.stringAt('$.jobId')) };
    const markStatus = (stateId: string, status: string, extra?: { expression: string; values: Record<string, tasks.DynamoAttributeValue> }) =>
      new tasks.DynamoUpdateItem(this, stateId, {
        table: jobs,
        key: jobKey,
        updateExpression: `SET #s = :s, ${status === 'RUNNING' ? 'startedAt' : 'finishedAt'} = :t${extra ? `, ${extra.expression}` : ''}`,
        expressionAttributeNames: { '#s': 'status', ...(extra?.expression.includes('#e') ? { '#e': 'error' } : {}) },
        expressionAttributeValues: {
          ':s': tasks.DynamoAttributeValue.fromString(status),
          ':t': tasks.DynamoAttributeValue.fromString(sfn.JsonPath.stringAt('$$.State.EnteredTime')),
          ...(extra?.values ?? {}),
        },
        resultPath: sfn.JsonPath.DISCARD,
      });

    // El evento de S3 trae bucket y clave (uploads/<jobId>/<nombre>.pdf); de la clave sale el jobId.
    const parse = new sfn.Pass(this, 'ParseUpload', {
      parameters: {
        'bucket.$': '$.bucket',
        'key.$': '$.key',
        'jobId.$': "States.ArrayGetItem(States.StringSplit($.key, '/'), 1)",
      },
    });
    // Lo que apuntó `presign`: de qué libro es (source) y si es un reproceso.
    const getJob = new tasks.DynamoGetItem(this, 'GetJob', { table: jobs, key: jobKey, resultPath: '$.job' });
    const markRunning = markStatus('MarkRunning', 'RUNNING');
    const markNeedsOcr = markStatus('MarkNeedsOcr', 'NEEDS_OCR');
    const markFailed = markStatus('MarkFailed', 'FAILED', {
      expression: '#e = :e',
      values: { ':e': tasks.DynamoAttributeValue.fromString(sfn.JsonPath.format('{}: {}', sfn.JsonPath.stringAt('$.error.Error'), sfn.JsonPath.stringAt('$.error.Cause'))) },
    });
    const markDone = markStatus('MarkDone', 'DONE', {
      expression: 'summary = :sum',
      values: { ':sum': tasks.DynamoAttributeValue.fromString(sfn.JsonPath.jsonToString(sfn.JsonPath.objectAt('$.summary'))) },
    });

    const invoke = (stateId: string, fnToCall: lambda.IFunction, payload: sfn.TaskInput, resultPath?: string) =>
      new tasks.LambdaInvoke(this, stateId, { lambdaFunction: fnToCall, payload, payloadResponseOnly: true, resultPath });
    // Los pasos que llaman a OpenAI/Wikidata/Mapbox pueden fallar por causas pasajeras (límite de peticiones, red): se reintentan 2 veces.
    // Es seguro porque solo leen de S3 y escriben ficheros de su propia tanda. Los de escritura en Mongo no se reintentan solos.
    const withRetry = <T extends tasks.LambdaInvoke>(task: T): T => {
      task.addRetry({ errors: ['States.TaskFailed'], interval: cdk.Duration.seconds(15), maxAttempts: 2, backoffRate: 2 });
      return task;
    };

    const extractStep = invoke(
      'ExtractChunk',
      extractChunk,
      sfn.TaskInput.fromObject({ 'jobId.$': '$.jobId', 'bucket.$': '$.bucket', 'key.$': '$.key' }),
      '$.extract',
    );
    const periodStep = invoke(
      'BookPeriod',
      embedPeriod,
      sfn.TaskInput.fromObject({ 'jobId.$': '$.jobId', 'bucket.$': '$.bucket', 'chunksKey.$': '$.extract.chunksKey' }),
      '$.period',
    );

    // Tandas: States.ArrayRange(0, chunkCount-1, batchSize) -> [0, 100, 200, ...]. El -1 evita una tanda vacía al final.
    const makeBatches = new sfn.Pass(this, 'MakeBatches', {
      parameters: { 'starts.$': `States.ArrayRange(0, States.MathAdd($.extract.chunkCount, -1), ${batchSize})` },
      resultPath: '$.batches',
    });

    // Fase 1: etiquetar, geocodificar y calcular embeddings por tandas (hasta 5 a la vez) y guardar los chunks de cada una.
    const embedMap = new sfn.Map(this, 'LabelAndEmbedBatches', {
      itemsPath: '$.batches.starts',
      maxConcurrency: 5, // respeta los límites de OpenAI y de Wikidata/Mapbox, y el cupo de 10 Lambdas simultáneas de una cuenta nueva
      itemSelector: {
        'jobId.$': '$.jobId',
        'bucket.$': '$.bucket',
        'chunksKey.$': '$.extract.chunksKey',
        'periodKey.$': '$.period.periodKey',
        'source.$': '$.job.Item.source.S',
        'start.$': '$$.Map.Item.Value',
        size: batchSize,
      },
      resultPath: '$.embedResults',
    }).itemProcessor(
      withRetry(
        invoke(
          'LabelAndEmbed',
          embedBatch,
          sfn.TaskInput.fromObject({ 'jobId.$': '$.jobId', 'bucket.$': '$.bucket', 'chunksKey.$': '$.chunksKey', 'periodKey.$': '$.periodKey', 'start.$': '$.start', 'size.$': '$.size' }),
          '$.embed',
        ),
      ).next(
        new tasks.LambdaInvoke(this, 'WriteChunks', {
          lambdaFunction: writeChunks,
          payload: sfn.TaskInput.fromObject({ 'jobId.$': '$.jobId', 'bucket.$': '$.bucket', 'labeledKey.$': '$.embed.labeledKey', 'source.$': '$.source' }),
          payloadResponseOnly: true,
          resultPath: '$.write',
          outputPath: '$.embed', // de cada tanda solo interesa su resumen (dónde quedó, cuántos chunks, cuánto costó)
        }),
      ),
    );

    // Fase 2: hitos por tandas (hasta 5 a la vez). Solo extrae y deja el resultado en S3.
    const extractMap = new sfn.Map(this, 'ExtractHitosBatches', {
      itemsPath: '$.embedResults',
      maxConcurrency: 5,
      itemSelector: {
        'jobId.$': '$.jobId',
        'bucket.$': '$.bucket',
        'labeledKey.$': '$$.Map.Item.Value.labeledKey',
        'source.$': '$.job.Item.source.S',
      },
      resultPath: '$.hitosResults',
    }).itemProcessor(
      withRetry(
        invoke('ExtractHitos', extractHitos, sfn.TaskInput.fromObject({ 'jobId.$': '$.jobId', 'bucket.$': '$.bucket', 'labeledKey.$': '$.labeledKey', 'source.$': '$.source' })),
      ),
    );

    // Fase 3: escribir hitos y personajes DE UNA EN UNA y en orden (maxConcurrency 1). Dos tandas a la vez podrían crear dos fichas
    // de la misma persona o no ver que un hito ya existe.
    const writeMap = new sfn.Map(this, 'WriteHitosInOrder', {
      itemsPath: '$.hitosResults',
      maxConcurrency: 1,
      itemSelector: {
        'jobId.$': '$.jobId',
        'bucket.$': '$.bucket',
        'hitosKey.$': '$$.Map.Item.Value.hitosKey',
        'source.$': '$.job.Item.source.S',
        'replaceSource.$': '$.job.Item.replaceSource.BOOL',
      },
      resultPath: '$.writeResults',
    }).itemProcessor(
      invoke(
        'WriteHitos',
        writeHitos,
        sfn.TaskInput.fromObject({ 'jobId.$': '$.jobId', 'bucket.$': '$.bucket', 'hitosKey.$': '$.hitosKey', 'source.$': '$.source', 'replaceSource.$': '$.replaceSource' }),
      ),
    );

    const finalizeStep = invoke(
      'Finalize',
      finalize,
      sfn.TaskInput.fromObject({
        'jobId.$': '$.jobId',
        'source.$': '$.job.Item.source.S',
        'replaceSource.$': '$.job.Item.replaceSource.BOOL',
        'period.$': '$.period',
        'embedResults.$': '$.embedResults',
        'hitosResults.$': '$.hitosResults',
        'writeResults.$': '$.writeResults',
      }),
      '$.summary',
    );

    const jobFailed = new sfn.Fail(this, 'JobFailed', { error: 'JobFailed', cause: 'Mira el campo error del registro del trabajo en DynamoDB' });
    markFailed.next(jobFailed);
    for (const step of [getJob, markRunning, extractStep, periodStep, embedMap, extractMap, writeMap, finalizeStep, markDone]) {
      step.addCatch(markFailed, { resultPath: '$.error' }); // cualquier fallo deja el trabajo en FAILED con su motivo
    }

    const afterExtract = new sfn.Choice(this, 'IsScannedPdf')
      .when(sfn.Condition.booleanEquals('$.extract.scanned', true), markNeedsOcr.next(new sfn.Fail(this, 'NeedsOcr', { error: 'NeedsOcr', cause: 'PDF escaneado: hace falta OCR' })))
      .otherwise(periodStep.next(makeBatches).next(embedMap).next(extractMap).next(writeMap).next(finalizeStep).next(markDone).next(new sfn.Succeed(this, 'JobDone')));

    const stateMachine = new sfn.StateMachine(this, 'Pipeline', {
      definitionBody: sfn.DefinitionBody.fromChainable(parse.next(getJob).next(markRunning).next(extractStep).next(afterExtract)),
      stateMachineType: sfn.StateMachineType.STANDARD, // Standard (no Express): dura más de 5 min y permite reanudar desde el paso fallido (redrive)
      timeout: cdk.Duration.hours(4),
      logs: {
        destination: new logs.LogGroup(this, 'PipelineLogs', { retention: logs.RetentionDays.ONE_MONTH, removalPolicy: cdk.RemovalPolicy.DESTROY }),
        level: sfn.LogLevel.ERROR,
      },
    });

    // Cuando un PDF llega a uploads/ (solo lo sube `presign`), EventBridge arranca el flujo con su bucket y clave.
    new events.Rule(this, 'OnPdfUploaded', {
      eventPattern: {
        source: ['aws.s3'],
        detailType: ['Object Created'],
        detail: { bucket: { name: [dataBucket.bucketName] }, object: { key: [{ prefix: 'uploads/' }] } },
      },
      targets: [
        new targets.SfnStateMachine(stateMachine, {
          input: events.RuleTargetInput.fromObject({
            bucket: events.EventField.fromPath('$.detail.bucket.name'),
            key: events.EventField.fromPath('$.detail.object.key'),
          }),
        }),
      ],
    });

    // ------------------------------------------------------------------------------------------------- login y API
    // Un solo usuario (el tuyo): el registro abierto está desactivado y la cuenta se crea a mano (ver infra/README.md).
    const userPool = new cognito.UserPool(this, 'UserPool', {
      selfSignUpEnabled: false,
      signInAliases: { email: true },
      autoVerify: { email: true },
      passwordPolicy: { minLength: 12, requireLowercase: true, requireUppercase: true, requireDigits: true, requireSymbols: false },
      mfa: cognito.Mfa.OPTIONAL,
      mfaSecondFactor: { otp: true, sms: false },
      accountRecovery: cognito.AccountRecovery.EMAIL_ONLY,
      removalPolicy: cdk.RemovalPolicy.DESTROY,
    });
    const webClient = userPool.addClient('WebClient', {
      generateSecret: false, // es una web pública: no puede guardar un secreto
      // userSrp: el login seguro de la web. adminUserPassword: solo para probar la API desde la terminal con credenciales de AWS (IAM);
      // un navegador no puede usarlo.
      authFlows: { userSrp: true, adminUserPassword: true },
      accessTokenValidity: cdk.Duration.hours(1),
      idTokenValidity: cdk.Duration.hours(1),
      refreshTokenValidity: cdk.Duration.days(30),
    });

    // El login solo en el front NO protege la API (cualquiera puede llamarla directamente): la API exige un token válido de Cognito.
    const jwt = new authorizers.HttpJwtAuthorizer('CognitoJwt', `https://cognito-idp.${this.region}.amazonaws.com/${userPool.userPoolId}`, {
      jwtAudience: [webClient.userPoolClientId],
    });
    const api = new apigw.HttpApi(this, 'Api', {
      defaultAuthorizer: jwt,
      corsPreflight: {
        allowOrigins: props.frontendOrigins,
        allowMethods: [apigw.CorsHttpMethod.GET, apigw.CorsHttpMethod.POST, apigw.CorsHttpMethod.OPTIONS],
        allowHeaders: ['authorization', 'content-type'],
        maxAge: cdk.Duration.days(1),
      },
    });
    api.addRoutes({ path: '/uploads', methods: [apigw.HttpMethod.POST], integration: new integrations.HttpLambdaIntegration('PresignIntegration', presign) });
    api.addRoutes({ path: '/jobs/{jobId}', methods: [apigw.HttpMethod.GET], integration: new integrations.HttpLambdaIntegration('JobStatusIntegration', jobStatus) });
    // Freno de peticiones: aunque alguien robara un token, no puede lanzar cientos de trabajos (cada PDF cuesta dinero en OpenAI).
    const defaultStage = api.defaultStage?.node.defaultChild as apigw.CfnStage;
    defaultStage.defaultRouteSettings = { throttlingBurstLimit: 5, throttlingRateLimit: 2 };

    // ------------------------------------------------------------------------------------------------------ salidas
    new cdk.CfnOutput(this, 'ApiUrl', { value: api.apiEndpoint });
    new cdk.CfnOutput(this, 'UserPoolId', { value: userPool.userPoolId });
    new cdk.CfnOutput(this, 'UserPoolClientId', { value: webClient.userPoolClientId });
    new cdk.CfnOutput(this, 'DataBucketName', { value: dataBucket.bucketName });
    new cdk.CfnOutput(this, 'GeodataBucketName', { value: geodataBucket.bucketName });
    new cdk.CfnOutput(this, 'StateMachineArn', { value: stateMachine.stateMachineArn });
    new cdk.CfnOutput(this, 'MongoTarget', { value: props.mongoTarget });
  }
}
