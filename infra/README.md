# infra

AWS CDK (TypeScript). Un único stack, `ChinaDocumentsStack`, en `eu-west-1` (Irlanda).

```
PDF subido a S3 ─► EventBridge ─► Step Functions ─► Lambdas (Python) ─► MongoDB Atlas
   ▲                                    │
 URL firmada (presign)           estado de cada trabajo en DynamoDB
   ▲
 API Gateway + login (Cognito)  ◄── la web (frontend/)
```

## Qué crea

| Recurso | Para qué |
|---|---|
| Bucket `DataBucket` | PDFs subidos (`uploads/`) y ficheros intermedios de cada trabajo (`jobs/<id>/`). Se borra solo a los 30 días. |
| Bucket `GeodataBucket` | Datos geográficos externos (CHGIS no se puede redistribuir). Privado y no se borra con el stack. |
| Tabla `JobsTable` (DynamoDB) | Estado de cada trabajo (UPLOADING, RUNNING, DONE, FAILED, NEEDS_OCR) y su resumen. Pago por uso; caduca a los 30 días. |
| 9 Lambdas (Python 3.11) | `presign`, `jobs`, `extract_chunk`, periodo del libro, etiquetar+embeddings, hitos, y 3 de escritura en Mongo. |
| Step Functions (Standard) | Reparte el libro en tandas de 100 chunks (hasta 5 a la vez), y escribe hitos y personajes **de uno en uno**. Se puede reanudar desde el paso fallido (*redrive*). |
| Cognito | Un solo usuario, sin registro abierto. |
| API Gateway (HTTP) | `POST /uploads` y `GET /jobs/{jobId}`, ambas exigen un token de Cognito. Limitada a 2 peticiones/s. |

El login en la web **no** protege la API por sí solo (cualquiera podría llamarla directamente): por eso la API valida el token.

## Antes de desplegar (lo haces tú)

El destino de escritura en Mongo es **dev por defecto** (`MONGO_TARGET`). Producción solo con `-c mongoTarget=prod`, a propósito.

1. **Secretos en SSM Parameter Store**, tipo *SecureString*. Hazlo desde la consola de AWS (Systems Manager → Parameter Store → Crear
   parámetro) y no desde la terminal, para que no queden en el historial de comandos:

   | Nombre | Valor |
   |---|---|
   | `/china-docs/OPENAI_API_KEY` | tu clave de OpenAI |
   | `/china-docs/MONGODB_URI_DEV` | la cadena de conexión de la base de pruebas |
   | `/china-docs/MAPBOX_TOKEN` | tu token de Mapbox |
   | `/china-docs/MONGODB_URI_PROD` | solo cuando decidas escribir en producción |

2. **MongoDB Atlas**: permitir `0.0.0.0/0` en *Network Access*. Las Lambdas no están en una VPC y su IP cambia; el plan gratuito M0 no
   admite conexión privada. Es lo habitual en este caso, pero solo es seguro con una contraseña fuerte y un usuario de permisos mínimos.
3. **Primera vez en la cuenta**: `cdk bootstrap aws://<ID-DE-CUENTA>/eu-west-1` (crea un bucket y roles que usa el CDK; coste despreciable).

## Desplegar

```bash
cd infra
npm install
npm run build:lambda    # empaqueta backend/ para Linux (sin Docker): infra/build/lambda
npm run synth           # genera la plantilla SIN desplegar nada (gratis); revísala antes
npm run deploy          # crea los recursos en AWS
```

`aws login` da credenciales temporales (hasta 12 h): si caducan, repite `aws login`.

## Después de desplegar

1. **Datos geográficos**: súbelos al bucket `GeodataBucketName` (sale en las salidas del despliegue), con esta estructura:
   `geodata/chgis/county-points.json`, `geodata/chgis/prefecture-points.json`, `geodata/yacimientos/yacimientos.json`,
   `geodata/capitales/capitales.json`. Sin ellos el geocodificador usa solo Wikidata y Mapbox (menos cobertura).
2. **Crear tu usuario** (la contraseña la escribes tú; no se la pases a nadie):
   ```bash
   aws cognito-idp admin-create-user --user-pool-id <UserPoolId> --username tu@correo --user-attributes Name=email,Value=tu@correo Name=email_verified,Value=true --message-action SUPPRESS
   aws cognito-idp admin-set-user-password --user-pool-id <UserPoolId> --username tu@correo --password '<contraseña>' --permanent
   ```
3. **Frontend**: necesitará `ApiUrl`, `UserPoolId` y `UserPoolClientId`. Cuando tenga dirección propia (CloudFront), vuelve a
   desplegar con `-c frontendOrigins=https://tu-dominio` para permitirla en CORS.

## Probar una subida desde la terminal

```bash
# token de Cognito (acceso de administrador: solo funciona con tus credenciales de AWS, no desde un navegador)
aws cognito-idp admin-initiate-auth --user-pool-id <UserPoolId> --client-id <UserPoolClientId> \
  --auth-flow ADMIN_USER_PASSWORD_AUTH --auth-parameters USERNAME=tu@correo,PASSWORD=...
# con el IdToken: POST <ApiUrl>/uploads {"filename":"libro.pdf"} -> URL firmada; se sube el PDF; GET <ApiUrl>/jobs/<jobId>
```

## Costes y seguridad

- **AWS**: Lambda, Step Functions, DynamoDB, S3, EventBridge y Cognito tienen capa gratuita o cuestan céntimos con este uso. El gasto
  real es **OpenAI** (≈ 6-8 USD por un libro de 800 páginas, medido; ver CLAUDE.md). Hay un presupuesto de 10 USD con alerta en AWS Budgets.
- Los secretos nunca van en variables de entorno ni en el repositorio.
- `cdk destroy` borra todo menos el bucket de datos geográficos.
