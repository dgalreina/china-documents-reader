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
| Web: `SiteBucket` + CloudFront | La web (`frontend/dist`) en un bucket privado y CloudFront delante: da la dirección pública `SiteUrl` (HTTPS, PC y móvil, sin dominio propio). Un despliegue con `cdk deploy` construye la web, la sube junto con un `config.json` (dirección de la API y datos de Cognito) y vacía la caché de CloudFront. Nunca lleva PDFs: `npm run build:front` borra `public/samples` y falla si queda alguno. |
| Bucket `BooksBucket` | Los libros: el PDF original (`books/<libro>/original.pdf`) y las traducciones al español (`translations/<libro>/...`). **No caduca** y no se borra con el stack. |
| Bucket `GeodataBucket` | Datos geográficos externos (CHGIS no se puede redistribuir). Privado y no se borra con el stack. |
| Tabla `BooksTable` (DynamoDB) | El catálogo de libros ya procesados (título, páginas, estadísticas): permanente y no se borra con el stack. La escribe `store_book` al terminar el flujo y la lee la Lambda `books`. |
| Tabla `JobsTable` (DynamoDB) | Estado de cada trabajo (UPLOADING, RUNNING, DONE, FAILED, NEEDS_OCR) y su resumen. Pago por uso; caduca a los 30 días. |
| 13 Lambdas (Python 3.11) | `presign`, `jobs`, `books`, `read_hitos` (lee de Mongo los hitos y personajes de un libro; solo lectura), `extract_chunk`, periodo del libro, etiquetar+embeddings, hitos, 3 de escritura en Mongo, `store_book` (guarda el PDF de forma permanente y apunta el libro en el catálogo al final del flujo) y `translate_page` (traduce una página al español, con caché). |
| Step Functions (Standard) | Reparte el libro en tandas de 100 chunks (hasta 5 a la vez), y escribe hitos y personajes **de uno en uno**. Se puede reanudar desde el paso fallido (*redrive*). |
| Cognito | Un solo usuario, sin registro abierto. |
| API Gateway (HTTP) | `POST /uploads`, `GET /jobs`, `GET /jobs/{jobId}`, `GET /books`, `GET /books/{slug}/pdf-url` (URL firmada de lectura del PDF, 6 h), `GET /books/{slug}/hitos` y `GET /books/{slug}/personajes` y `GET /books/{slug}/pages/{page}/translation`; todas exigen un token de Cognito. Limitada a 2 peticiones/s (ráfaga de 5). La traducción solo recibe libro y página, nunca texto; su Lambda dura como mucho 29 s porque API Gateway corta a los 30. |

El login en la web **no** protege la API por sí solo (cualquiera podría llamarla directamente): por eso la API valida el token.

## Antes de desplegar (lo haces tú)

El destino de escritura en Mongo es **dev por defecto** (`MONGO_TARGET`). Producción solo con `-c mongoTarget=prod`, a propósito.
Otro parámetro del despliegue: `-c maxSpanYears=2000` (por defecto 2000): un chunk con un rango de años más ancho no se guarda y queda como pendiente.

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
3. **Frontend**: la web publicada lee `ApiUrl`, `UserPoolId` y `UserPoolClientId` de su `config.json` (lo sube el despliegue) y pide el login de Cognito;
   la dirección de CloudFront (`SiteUrl`) ya se permite sola en el CORS. Para desarrollar en local contra el stack real: `node infra/scripts/write-front-config.mjs`
   (escribe `frontend/public/config.json`, ignorado por git) y `cd frontend && npm run dev`. Si algún día hay dominio propio, se pasa con `-c frontendOrigins=https://tu-dominio`
   (se suma a la de CloudFront) y se fijaría TLS 1.2 como mínimo: con el certificado por defecto de CloudFront no se puede.

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
- `cdk destroy` borra todo menos los buckets de libros y de datos geográficos (son datos del usuario). Si se quieren borrar de verdad, vaciarlos y eliminarlos a mano.
- La traducción cuesta ≈ 0,01 USD por página (medido con `gpt-4o`) y solo se paga una vez por página: queda en `translations/`.
