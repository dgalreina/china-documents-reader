import * as path from 'node:path';
import * as cdk from 'aws-cdk-lib';
import { Construct } from 'constructs';
import * as apigw from 'aws-cdk-lib/aws-apigatewayv2';
import * as authorizers from 'aws-cdk-lib/aws-apigatewayv2-authorizers';
import * as integrations from 'aws-cdk-lib/aws-apigatewayv2-integrations';
import * as cloudfront from 'aws-cdk-lib/aws-cloudfront';
import * as cloudfrontOrigins from 'aws-cdk-lib/aws-cloudfront-origins';
import * as cognito from 'aws-cdk-lib/aws-cognito';
import * as dynamodb from 'aws-cdk-lib/aws-dynamodb';
import * as events from 'aws-cdk-lib/aws-events';
import * as targets from 'aws-cdk-lib/aws-events-targets';
import * as iam from 'aws-cdk-lib/aws-iam';
import * as lambda from 'aws-cdk-lib/aws-lambda';
import * as logs from 'aws-cdk-lib/aws-logs';
import * as s3 from 'aws-cdk-lib/aws-s3';
import * as s3deploy from 'aws-cdk-lib/aws-s3-deployment';
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
  /** Amplitud máxima (años) del rango de un chunk: más ancho va a pendientes (suele ser el alcance de un capítulo, no del fragmento). */
  maxSpanYears?: number;
}

export class ChinaDocumentsStack extends cdk.Stack {
  constructor(scope: Construct, id: string, props: ChinaDocumentsStackProps) {
    super(scope, id, props);
    const ssmPrefix = props.ssmPrefix ?? '/china-docs';
    const batchSize = props.batchSize ?? 100;
    const maxSpanYears = props.maxSpanYears ?? 2000;

    // ------------------------------------------------------------------------------------------------------------ la web
    // La web (frontend/dist) son ficheros estáticos: un bucket PRIVADO y CloudFront delante, que es lo único que puede leerlo (OAC) y da la
    // dirección pública https://<algo>.cloudfront.net, válida desde el PC y el móvil. Se crea antes que lo demás porque su dirección hay que
    // permitirla en el CORS de la API y de los buckets.
    const siteBucket = new s3.Bucket(this, 'SiteBucket', {
      blockPublicAccess: s3.BlockPublicAccess.BLOCK_ALL,
      encryption: s3.BucketEncryption.S3_MANAGED,
      enforceSSL: true,
      removalPolicy: cdk.RemovalPolicy.DESTROY, // se reconstruye desde el repositorio: no hay nada irrecuperable aquí
      autoDeleteObjects: true,
    });
    const distribution = new cloudfront.Distribution(this, 'SiteDistribution', {
      defaultRootObject: 'index.html',
      defaultBehavior: {
        origin: cloudfrontOrigins.S3BucketOrigin.withOriginAccessControl(siteBucket),
        viewerProtocolPolicy: cloudfront.ViewerProtocolPolicy.REDIRECT_TO_HTTPS,
        compress: true,
        responseHeadersPolicy: cloudfront.ResponseHeadersPolicy.SECURITY_HEADERS, // HSTS, nosniff, X-Frame-Options, referrer...
      },
      // Es una SPA (react-router): /libros/mi-libro no existe como fichero, así que se sirve index.html y el router decide.
      errorResponses: [
        { httpStatus: 403, responseHttpStatus: 200, responsePagePath: '/index.html', ttl: cdk.Duration.seconds(0) },
        { httpStatus: 404, responseHttpStatus: 200, responsePagePath: '/index.html', ttl: cdk.Duration.seconds(0) },
      ],
      priceClass: cloudfront.PriceClass.PRICE_CLASS_100, // solo Europa y Norteamérica: más barato y de sobra para un proyecto personal
      // Sin dominio propio CloudFront usa su certificado por defecto, cuya política TLS mínima no se puede cambiar. Con un dominio propio se fijaría TLS 1.2.
      comment: 'china-documents-reader: web',
    });
    const siteUrl = `https://${distribution.distributionDomainName}`;
    // Orígenes que pueden llamar a la API y subir/leer en S3: los de fuera (localhost para desarrollar) y la propia web desplegada.
    const origins = [...props.frontendOrigins, siteUrl];

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
          allowedOrigins: origins,
          allowedHeaders: ['*'],
          maxAge: 3000,
        },
      ],
      removalPolicy: cdk.RemovalPolicy.DESTROY,
      autoDeleteObjects: true,
    });

    // Los libros: el PDF original (books/<libro>/original.pdf) y las traducciones al español (translations/<libro>/...). A diferencia del bucket
    // de datos, NO caduca: el visor y el traductor necesitan el PDF mientras el libro exista. Tampoco se borra con el stack (son los libros del usuario).
    const booksBucket = new s3.Bucket(this, 'BooksBucket', {
      blockPublicAccess: s3.BlockPublicAccess.BLOCK_ALL,
      encryption: s3.BucketEncryption.S3_MANAGED,
      enforceSSL: true,
      cors: [
        {
          // Para que el visor (pdf.js, en el navegador) pueda leer el PDF con una URL firmada de lectura.
          allowedMethods: [s3.HttpMethods.GET, s3.HttpMethods.HEAD],
          allowedOrigins: origins,
          allowedHeaders: ['*'],
          // pdf.js lee el PDF por trozos (cabecera Range) y necesita VER estas cabeceras de la respuesta; sin exponerlas descarga el libro entero de golpe.
          exposedHeaders: ['Accept-Ranges', 'Content-Range', 'Content-Length', 'ETag'],
          maxAge: 3000,
        },
      ],
      removalPolicy: cdk.RemovalPolicy.RETAIN,
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

    // El catálogo de libros: un registro por libro ya procesado, PERMANENTE (a diferencia de los trabajos, que caducan a los 30 días). Lo escribe
    // `store_book` al final del flujo y lo lee `books`. Se conserva si se destruye el stack: es la lista de los libros del usuario.
    const booksTable = new dynamodb.Table(this, 'BooksTable', {
      partitionKey: { name: 'slug', type: dynamodb.AttributeType.STRING },
      billingMode: dynamodb.BillingMode.PAY_PER_REQUEST,
      removalPolicy: cdk.RemovalPolicy.RETAIN,
    });

    // ------------------------------------------------------------------------------------------------------------ Lambdas
    // Un único paquete (infra/build/lambda, lo genera `npm run build:lambda`) para todas; cada función apunta a su handler.
    const code = lambda.Code.fromAsset(path.join(__dirname, '..', 'build', 'lambda'));
    const commonEnv: Record<string, string> = {
      SSM_PREFIX: ssmPrefix,
      GEODATA_BUCKET: geodataBucket.bucketName,
      JOBS_TABLE: jobs.tableName,
      BOOKS_TABLE: booksTable.tableName,
      UPLOAD_BUCKET: dataBucket.bucketName,
      BOOKS_BUCKET: booksBucket.bucketName,
      MONGO_TARGET: props.mongoTarget, // dev o prod lo decide SOLO el despliegue, nunca el evento
      MAX_SPAN_YEARS: String(maxSpanYears),
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
    const storeBook = fn('StoreBookFn', 'lambdas.store_book.handler.handler', 30, 256);
    // 29 s: API Gateway (HTTP API) corta cualquier integración a los 30 s, así que una Lambda más lenta no serviría de nada. Una página tarda ~5-8 s.
    // 512 MB de /tmp (el de por defecto) bastan: el PDF (hasta 100 MB) se baja ahí una vez y la Lambda "caliente" lo reutiliza.
    const booksApi = fn('BooksFn', 'lambdas.books.handler.handler', 15, 256);
    // Lee los hitos y personajes de un libro de Mongo (solo lectura). 20 s: conectar a Atlas en frío puede tardar un par de segundos.
    // Prepara un libro recién subido: valida el PDF, lo guarda de forma permanente, dibuja la portada y lo apunta como "sin analizar". No llama a OpenAI.
    // 120 s y 1 GB: baja el PDF (hasta 100 MB), lo abre con pypdf y dibuja su primera página.
    const ingest = fn('IngestFn', 'lambdas.ingest.handler.handler', 120, 1024);
    // Arranca el análisis de un libro ya subido (lo pide el usuario con el botón "Analizar"): solo apunta el trabajo y lanza Step Functions.
    const analyze = fn('AnalyzeFn', 'lambdas.analyze.handler.handler', 15, 256);
    const readHitos = fn('ReadHitosFn', 'lambdas.read_hitos.handler.handler', 20, 512);
    // Borra un libro y TODO lo que es solo suyo (Mongo, S3, trabajos y catálogo). Los permisos más amplios de la app: va detrás del login y del límite de peticiones.
    const deleteBook = fn('DeleteBookFn', 'lambdas.delete_book.handler.handler', 60, 512);
    const translate = fn('TranslatePageFn', 'lambdas.translate_page.handler.handler', 29, 512);

    // Permisos mínimos para cada una.
    dataBucket.grantPut(presign); // firma subidas: necesita poder escribir en esa clave
    jobs.grantReadWriteData(presign); // apunta la subida y mira si el libro ya se está analizando o subiendo
    booksTable.grantReadData(presign); // ...y si el libro ya existe (reemplazarlo hay que confirmarlo)
    jobs.grantReadData(jobStatus);
    for (const f of [extractChunk, embedPeriod, embedBatch, extractHitos, writeChunks, writeHitos]) dataBucket.grantReadWrite(f);
    // ingest: lee la subida (y la borra al terminar), guarda PDF y portada en el bucket permanente, borra las traducciones si reemplaza un libro, y apunta el libro.
    dataBucket.grantReadWrite(ingest);
    booksBucket.grantReadWrite(ingest);
    jobs.grantReadWriteData(ingest);
    booksTable.grantReadWriteData(ingest);
    // analyze: mira el libro y los trabajos en curso y apunta el análisis; arrancar Step Functions se concede más abajo, cuando existe la máquina.
    booksTable.grantReadData(analyze);
    jobs.grantReadWriteData(analyze);
    // extract_chunk lee el PDF del bucket permanente de libros (sus ficheros intermedios siguen yendo al de datos).
    booksBucket.grantRead(extractChunk);
    booksBucket.grantReadWrite(translate); // lee el PDF y lee/escribe la caché de traducciones
    booksTable.grantWriteData(storeBook); // al terminar el análisis, marca el libro como analizado en el catálogo (ya no toca S3)
    booksTable.grantReadWriteData(deleteBook); // lee y borra la entrada del catálogo
    jobs.grantReadWriteData(deleteBook); // busca y borra los trabajos del libro
    booksBucket.grantReadWrite(deleteBook); // borra el PDF, la portada y las traducciones
    dataBucket.grantReadWrite(deleteBook); // borra lo intermedio de sus análisis y la subida original
    booksTable.grantReadData(booksApi); // lista los libros y comprueba que existan antes de firmar...
    booksBucket.grantRead(booksApi); // ...la URL de lectura de su PDF
    for (const f of [embedBatch, extractHitos]) geodataBucket.grantRead(f); // geocodifican con CHGIS y las listas de china-history
    const readSecrets = new iam.PolicyStatement({
      actions: ['ssm:GetParameter'],
      resources: [cdk.Arn.format({ service: 'ssm', resource: 'parameter', resourceName: `${ssmPrefix.replace(/^\//, '')}/*`, arnFormat: cdk.ArnFormat.SLASH_RESOURCE_NAME }, this)],
    });
    for (const f of [embedPeriod, embedBatch, extractHitos, writeChunks, writeHitos, finalize, translate, readHitos, deleteBook, ingest]) f.addToRolePolicy(readSecrets); // translate: la clave de OpenAI; readHitos, deleteBook e ingest (al reemplazar un libro): la URI de Mongo

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

    // El análisis lo arranca `analyze` con: bucket (donde el flujo deja sus intermedios, que caducan), pdfBucket + key (el PDF ya guardado en books/<libro>/original.pdf) y jobId.
    const parse = new sfn.Pass(this, 'ParseUpload', {
      parameters: {
        'bucket.$': '$.bucket',
        'pdfBucket.$': '$.pdfBucket',
        'key.$': '$.key',
        'jobId.$': '$.jobId',
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
      sfn.TaskInput.fromObject({ 'jobId.$': '$.jobId', 'bucket.$': '$.bucket', 'pdfBucket.$': '$.pdfBucket', 'key.$': '$.key' }),
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

    // Último paso: marcar el libro como analizado en el catálogo.
    const storeBookStep = invoke(
      'StoreBook',
      storeBook,
      // Solo marca el libro como analizado en el catálogo: el PDF y la portada ya están guardados desde la subida.
      sfn.TaskInput.fromObject({ 'source.$': '$.job.Item.source.S', 'jobId.$': '$.jobId', 'summary.$': '$.summary' }),
      sfn.JsonPath.DISCARD,
    );

    const jobFailed = new sfn.Fail(this, 'JobFailed', { error: 'JobFailed', cause: 'Mira el campo error del registro del trabajo en DynamoDB' });
    markFailed.next(jobFailed);
    for (const step of [getJob, markRunning, extractStep, periodStep, embedMap, extractMap, writeMap, finalizeStep, storeBookStep, markDone]) {
      step.addCatch(markFailed, { resultPath: '$.error' }); // cualquier fallo deja el trabajo en FAILED con su motivo
    }

    const afterExtract = new sfn.Choice(this, 'IsScannedPdf')
      .when(sfn.Condition.booleanEquals('$.extract.scanned', true), markNeedsOcr.next(new sfn.Fail(this, 'NeedsOcr', { error: 'NeedsOcr', cause: 'PDF escaneado: hace falta OCR' })))
      .otherwise(periodStep.next(makeBatches).next(embedMap).next(extractMap).next(writeMap).next(finalizeStep).next(storeBookStep).next(markDone).next(new sfn.Succeed(this, 'JobDone')));

    const stateMachine = new sfn.StateMachine(this, 'Pipeline', {
      definitionBody: sfn.DefinitionBody.fromChainable(parse.next(getJob).next(markRunning).next(extractStep).next(afterExtract)),
      stateMachineType: sfn.StateMachineType.STANDARD, // Standard (no Express): dura más de 5 min y permite reanudar desde el paso fallido (redrive)
      timeout: cdk.Duration.hours(4),
      logs: {
        destination: new logs.LogGroup(this, 'PipelineLogs', { retention: logs.RetentionDays.ONE_MONTH, removalPolicy: cdk.RemovalPolicy.DESTROY }),
        level: sfn.LogLevel.ERROR,
      },
    });

    // Cuando un PDF llega a uploads/ (solo lo sube `presign`), EventBridge lo pasa a `ingest`, que lo prepara y lo deja "sin analizar". El análisis NO arranca solo:
    // lo pide el usuario (POST /books/{slug}/analyze), porque cuesta dinero y hay libros que solo se quieren leer y traducir.
    new events.Rule(this, 'OnPdfUploaded', {
      eventPattern: {
        source: ['aws.s3'],
        detailType: ['Object Created'],
        detail: { bucket: { name: [dataBucket.bucketName] }, object: { key: [{ prefix: 'uploads/' }] } },
      },
      targets: [
        new targets.LambdaFunction(ingest, {
          event: events.RuleTargetInput.fromObject({
            bucket: events.EventField.fromPath('$.detail.bucket.name'),
            key: events.EventField.fromPath('$.detail.object.key'),
          }),
        }),
      ],
    });
    // analyze necesita el ARN de la máquina (que se crea antes que esto) y permiso para arrancarla.
    analyze.addEnvironment('STATE_MACHINE_ARN', stateMachine.stateMachineArn);
    stateMachine.grantStartExecution(analyze);

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
        allowOrigins: origins,
        allowMethods: [apigw.CorsHttpMethod.GET, apigw.CorsHttpMethod.POST, apigw.CorsHttpMethod.DELETE, apigw.CorsHttpMethod.OPTIONS],
        allowHeaders: ['authorization', 'content-type'],
        maxAge: cdk.Duration.days(1),
      },
    });
    api.addRoutes({ path: '/uploads', methods: [apigw.HttpMethod.POST], integration: new integrations.HttpLambdaIntegration('PresignIntegration', presign) });
    api.addRoutes({ path: '/jobs', methods: [apigw.HttpMethod.GET], integration: new integrations.HttpLambdaIntegration('JobListIntegration', jobStatus) });
    api.addRoutes({ path: '/books', methods: [apigw.HttpMethod.GET], integration: new integrations.HttpLambdaIntegration('BookListIntegration', booksApi) });
    api.addRoutes({ path: '/books/{slug}/pdf-url', methods: [apigw.HttpMethod.GET], integration: new integrations.HttpLambdaIntegration('BookPdfIntegration', booksApi) });
    api.addRoutes({ path: '/books/{slug}/analyze', methods: [apigw.HttpMethod.POST], integration: new integrations.HttpLambdaIntegration('BookAnalyzeIntegration', analyze) });
    api.addRoutes({ path: '/books/{slug}', methods: [apigw.HttpMethod.DELETE], integration: new integrations.HttpLambdaIntegration('BookDeleteIntegration', deleteBook) });
    api.addRoutes({ path: '/books/{slug}/hitos', methods: [apigw.HttpMethod.GET], integration: new integrations.HttpLambdaIntegration('BookHitosIntegration', readHitos) });
    api.addRoutes({ path: '/books/{slug}/personajes', methods: [apigw.HttpMethod.GET], integration: new integrations.HttpLambdaIntegration('BookPersonajesIntegration', readHitos) });
    // Los hitos y personajes de TODOS los libros (cada uno dice de cuál): una sola petición en lugar de una por libro (el freno de la API es de 2 por segundo).
    api.addRoutes({ path: '/hitos', methods: [apigw.HttpMethod.GET], integration: new integrations.HttpLambdaIntegration('AllHitosIntegration', readHitos) });
    api.addRoutes({ path: '/personajes', methods: [apigw.HttpMethod.GET], integration: new integrations.HttpLambdaIntegration('AllPersonajesIntegration', readHitos) });
    api.addRoutes({ path: '/jobs/{jobId}', methods: [apigw.HttpMethod.GET], integration: new integrations.HttpLambdaIntegration('JobStatusIntegration', jobStatus) });
    // La traducción de una página: el navegador manda solo libro y página (nunca texto). Va detrás del mismo autorizador JWT que el resto (es el
    // defaultAuthorizer de la API) y bajo el mismo freno de peticiones.
    api.addRoutes({
      path: '/books/{slug}/pages/{page}/translation',
      methods: [apigw.HttpMethod.GET],
      integration: new integrations.HttpLambdaIntegration('TranslateIntegration', translate),
    });
    // Freno de peticiones: aunque alguien robara un token, no puede lanzar cientos de trabajos (cada PDF cuesta dinero en OpenAI).
    const defaultStage = api.defaultStage?.node.defaultChild as apigw.CfnStage;
    defaultStage.defaultRouteSettings = { throttlingBurstLimit: 5, throttlingRateLimit: 2 };

    // ------------------------------------------------------------------------------------------- publicar la web
    // Sube frontend/dist (lo construye `npm run build:front`, que ya quita los PDFs de muestra) junto con config.json, que la web leerá para saber
    // dónde está la API. Se excluye samples/ por duplicado: un PDF en este bucket quedaría descargable por cualquiera con el enlace.
    // Se publica en DOS tandas con distinta caché:
    //  1. assets/: los ficheros de la web con la huella del contenido en el nombre (index-DJLRIaQ5.js). Nunca cambian con ese nombre: se guardan un año y el navegador no vuelve a
    //     preguntar por ellos. `prune: false` conserva los de versiones anteriores (unos kB): una pestaña abierta con la versión vieja puede seguir pidiendo sus ficheros.
    //  2. el resto (index.html, config.json, iconos): SIEMPRE se revalida, para que una versión nueva se vea al instante. Va después de la 1: index.html nunca apunta a algo que aún no está.
    const assets = new s3deploy.BucketDeployment(this, 'DeployAssets', {
      sources: [s3deploy.Source.asset(path.join(__dirname, '..', '..', 'frontend', 'dist', 'assets'))],
      destinationBucket: siteBucket,
      destinationKeyPrefix: 'assets',
      cacheControl: [s3deploy.CacheControl.fromString('public, max-age=31536000, immutable')],
      prune: false,
    });
    // ---------------------------------------------------------------------------------------------------- agente (con streaming)
    // El agente responde ESCRIBIENDO según el modelo genera (streaming). API Gateway (HTTP API) no puede hacerlo y Python no tiene streaming nativo en Lambda, así que esta
    // es la única Lambda en Node.js, con su propia dirección (Function URL en modo RESPONSE_STREAM). Esa dirección es pública: NO tiene el autorizador de la API, por eso la
    // función comprueba ella misma el token de Cognito (firma, caducidad, user pool y aplicación) ANTES de escribir nada. Sin el freno de 2 peticiones por segundo de la API:
    // el límite de gasto son la pregunta corta (600 caracteres), el historial recortado y que solo entra quien tiene sesión.
    const askFn = new lambda.Function(this, 'AskFn', {
      runtime: lambda.Runtime.NODEJS_22_X,
      architecture: lambda.Architecture.X86_64,
      code: lambda.Code.fromAsset(path.join(__dirname, '..', 'build', 'ask')),
      handler: 'src/handler.handler',
      timeout: cdk.Duration.seconds(60),
      memorySize: 512,
      environment: {
        SSM_PREFIX: ssmPrefix,
        MONGO_TARGET: props.mongoTarget,
        USER_POOL_ID: userPool.userPoolId,
        USER_POOL_CLIENT_ID: webClient.userPoolClientId,
      },
      logGroup: new logs.LogGroup(this, 'AskFnLogs', { retention: logs.RetentionDays.ONE_MONTH, removalPolicy: cdk.RemovalPolicy.DESTROY }),
    });
    askFn.addToRolePolicy(readSecrets); // la clave de OpenAI y la URI de Mongo, de SSM
    const askUrl = askFn.addFunctionUrl({
      authType: lambda.FunctionUrlAuthType.NONE,
      invokeMode: lambda.InvokeMode.RESPONSE_STREAM,
      cors: { allowedOrigins: origins, allowedMethods: [lambda.HttpMethod.POST], allowedHeaders: ['authorization', 'content-type'], maxAge: cdk.Duration.days(1) },
    });

    const site = new s3deploy.BucketDeployment(this, 'DeploySite', {
      sources: [
        s3deploy.Source.asset(path.join(__dirname, '..', '..', 'frontend', 'dist'), { exclude: ['samples', 'samples/*', '*.pdf', 'assets', 'assets/*'] }),
        s3deploy.Source.jsonData('config.json', {
          region: this.region,
          apiUrl: api.apiEndpoint,
          userPoolId: userPool.userPoolId,
          userPoolClientId: webClient.userPoolClientId,
          askUrl: askUrl.url,
        }),
      ],
      destinationBucket: siteBucket,
      exclude: ['samples/*', 'assets/*'], // assets/ ya lo publica la tanda 1: esta no debe borrarlo (prune) ni tocarlo
      distribution,
      distributionPaths: ['/*'], // al subir una versión nueva, CloudFront olvida la anterior
      cacheControl: [s3deploy.CacheControl.noCache()],
      prune: true,
    });
    site.node.addDependency(assets);

    // ------------------------------------------------------------------------------------------------------ salidas
    new cdk.CfnOutput(this, 'SiteUrl', { value: siteUrl, description: 'La web: ábrela desde el PC o el móvil' });
    new cdk.CfnOutput(this, 'ApiUrl', { value: api.apiEndpoint });
    new cdk.CfnOutput(this, 'AskUrl', { value: askUrl.url, description: 'El agente (streaming)' });
    new cdk.CfnOutput(this, 'UserPoolId', { value: userPool.userPoolId });
    new cdk.CfnOutput(this, 'UserPoolClientId', { value: webClient.userPoolClientId });
    new cdk.CfnOutput(this, 'DataBucketName', { value: dataBucket.bucketName });
    new cdk.CfnOutput(this, 'BooksTableName', { value: booksTable.tableName });
    new cdk.CfnOutput(this, 'BooksBucketName', { value: booksBucket.bucketName });
    new cdk.CfnOutput(this, 'GeodataBucketName', { value: geodataBucket.bucketName });
    new cdk.CfnOutput(this, 'StateMachineArn', { value: stateMachine.stateMachineArn });
    new cdk.CfnOutput(this, 'MongoTarget', { value: props.mongoTarget });
  }
}
