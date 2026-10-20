# china-documents-reader

Proyecto de portfolio. El usuario sube PDFs sobre historia China y un backend con IA los analiza: extrae el texto, genera embeddings,
etiqueta el rango de años (`añoInicio`/`añoFin`) de cada fragmento y extrae hitos históricos con lugar, año y
coordenadas. Los resultados se guardan en MongoDB Atlas, de donde lee la otra app, `china-history`
(`C:\Users\Mountain\Documents\Repositorios\china-history`, NestJS + Next.js).

**`china-history` NO se toca desde este repo: solo lee.**

Objetivo: demostrar AWS serverless a recruiters. `china-history` se queda en DigitalOcean.

## Stack

- `frontend/`: React + Vite + TypeScript. Vite a propósito: se despliega estático en S3 + CloudFront.
- `backend/`: Python 3.11, una Lambda por paso en `backend/lambdas/<paso>/`.
- `infra/`: AWS CDK en TypeScript. Todavía vacío.
- Orquestación futura: S3 → Lambdas → Step Functions. Estado de los trabajos en DynamoDB.
- **No desplegar nada en AWS hasta tener un presupuesto con alerta en AWS Budgets.**

## Contrato con china-history (no negociable)

- Esta app es la **única que escribe** en las colecciones `book_chunks` y `hitos_conocidos`.
- `book_chunks`: `text`, `embedding` (number[]), `source` (slug del documento), `chunkIndex`, `añoInicio`, `añoFin`,
  más la página de origen y el nombre del modelo de embeddings. Índice único `(source, chunkIndex)`.
  Búsqueda por Atlas Vector Search, índice `book_chunks_vector_index`.
- Embeddings **siempre** con OpenAI `text-embedding-3-small` (1536 dimensiones). Cambiar de modelo obliga a
  reindexar todo.
- **NUNCA escribir un chunk sin `añoInicio` y `añoFin`.** Los que no se puedan etiquetar se descartan o quedan como
  "pendientes", fuera de `book_chunks`. Los años deben estar entre **-10000 y 3000** (a.C. en negativo) y
  `añoInicio <= añoFin`: el último intento de búsqueda de china-history (`MARGENES_AÑO_RAG = [50,150,400,null]`)
  filtra por ese rango, así que fuera de él el chunk queda excluido de todas las búsquedas.
- **Ubicación del chunk (OPCIONAL, a petición de china-history):** si el sitio principal se resuelve con claridad, el chunk
  lleva `ubicacion` (GeoJSON Point `[lng, lat]`), `lat`, `lng` (números sueltos: el filtro de `$vectorSearch` admite
  rangos numéricos pero no operadores geoespaciales, así que prefiltran por caja), `ubicacionNombre`, `ubicacionFuente`
  (`curated`/`chgis`/`mapbox`) y `ubicacionCoincidencia`. **Nunca una ubicación aproximada o genérica** (ni el centro de una
  provincia): si no hay resultado claro, el chunk se guarda igual, sin ubicación. Los años siguen siendo obligatorios.
  Para usarla, china-history debe añadir `lat`/`lng` como campos *filter* de su índice vectorial y cambiar `buscarEnLibro`
  (eso lo hace su dueño; verificado en dev que el prefiltro por caja funciona).
- Escritura en `book_chunks`: **upsert por `(source, chunkIndex)`**, para que reprocesar un PDF no duplique.
- Los campos extra de `book_chunks` (página de origen, modelo de embeddings) no están en el schema Mongoose
  `BookChunk` de `china-history-api`; no rompe nada, pero hay que añadirlos allí si se quieren consultar.
- `hitos_conocidos`: `id`, `lugar`, `año`, `titulo`, `descripcion`, `fuente`, `location` (GeoJSON Point,
  `coordinates: [lng, lat]`; **obligatorio**: un hito sin coordenadas se descarta). Los hitos necesitan geocodificar el lugar.
- **Idioma (corregido por china-history):** `book_chunks` es SIEMPRE en inglés (el RAG no debe favorecer un libro por idioma),
  pero en `hitos_conocidos` los campos base `titulo`/`lugar`/`descripcion` son en **ESPAÑOL** (idioma por defecto de la app; los
  1259 hitos existentes lo estan) y `titulo_en`/`lugar_en`/`descripcion_en` son la traduccion anadida encima. Escribir ingles
  en los campos base mezclaria idiomas para el usuario por defecto. Plan: la misma llamada de `extract_hitos` devuelve
  español (base) + ingles (`_en`); `_zh` queda para un paso posterior.
- `id` de un hito (sobre los campos base, es decir en español): **mismo algoritmo que `scripts/extract-hitos.mjs` de china-history-api**:
  `sha1("titulo|año|lugar|lat|lng")` en hex, primeros 10 caracteres (ojo: lat antes que lng); si el id ya existe en
  el lote, se recalcula con `base + "|" + salt` (salt 1, 2…). Nunca un UUID. Verificar contra su `hitos.json`.
- Escritura en `hitos_conocidos`: **upsert por `id`** (`updateOne` con `upsert: true`), NUNCA `deleteMany` sin filtro.

## Pruebas y base de datos de producción

**Las pruebas NUNCA escriben en la BBDD vectorial de producción.** Un chunk de prueba con año válido aparecería en
las búsquedas de china-history. Reglas para el paso de escritura en Mongo (aún sin implementar):
- Por defecto `--dry-run`; solo escribe con `--write` explícito.
- Base de datos y colecciones configurables por entorno; las pruebas van a una base separada, nunca a la de prod.
- URIs separadas dev/prod; no reutilizar el `MONGODB_URI` de china-history.
- Hasta que ese paso exista, ningún código de este repo se conecta a Mongo.

## Licencias y datos externos

- Proyecto **personal y no comercial** (portfolio). Si eso cambiara, revisar antes la licencia de CHGIS y los términos de Mapbox.
- **Los datos de CHGIS no se redistribuyen**: nunca dentro de este repo (puede ser público). Se leen desde fuera: ruta local
  (`CHGIS_DIR`) y, en AWS, un bucket de S3 privado. Igual para `yacimientos.json` / `capitales.json` de china-history
  (solo lectura, por ruta en el `.env`, sin copiarlos al repo).
- `china-history` en DigitalOcean no necesita esos datos: solo lee de Mongo las coordenadas ya calculadas.

## Secretos

`OPENAI_API_KEY` y `MONGODB_URI` van en un `.env` (ya en `.gitignore`). Nunca imprimirlos ni commitearlos.

## Estado

- Hecho: `backend/lambdas/extract_chunk` (pypdf por páginas + chunks con página de origen), esqueleto del frontend,
  `embed_label` (embeddings + años en 2 niveles: periodo del documento y luego lotes de 10 chunks; modelo de etiquetado
  `gpt-4o` vía `LABEL_MODEL` (mini fallaba con culturas poco conocidas; también descarta rangos ≈ periodo del documento); tests con cliente falso en `backend/tests`). **Falta la prueba real con la API.**
- `write_mongo` escrito (dry-run por defecto, `--target dev|prod` obligatorio, doble validación del contrato, upsert por
  `(source, chunkIndex)`). Variables: `MONGODB_URI_DEV`, `MONGODB_URI_PROD`, opcionalmente `MONGODB_DB_DEV/PROD`
  (por defecto `china_documents_dev` y `china-history`). **Falta probarlo contra la base de dev.** Cluster0 es M0
  (3 índices de búsqueda máx., 1 usado por `china-history.book_chunks`, sin backups).
- `shared/geocoder.py` (compartido con `extract_hitos`): fuentes en orden curated (china-history) -> CHGIS -> Wikidata -> Mapbox.
  Wikidata/Mapbox exigen provincia esperada, un unico candidato (o todos a <25 km), nombre parecido y, en Mapbox, solo lugares
  (nunca provincia/pais) con relevancia >= 0.8. Se guarda `ubicacionCoincidencia` para auditar. Prueba con el Oxford Handbook
  (pags. 14-45): **15 de 68** chunks localizados (antes 2 con CHGIS+Mapbox); sitios revisados a mano contra coordenadas conocidas.
  Endurecido tras un aviso de china-history (hitos con 'Reino de Qin' / 'Zhou (principado real)' acabaron en Zhejiang): CHGIS ahora
  exige provincia y la verifica con geocodificacion inversa (antes 'Qin' casaba con Qin Xian, Zhejiang); Wikidata compara solo la
  etiqueta (no alias: 'Jin'=Shanxi, 'Qi'=Jinan), nombres cortos exactos, y descarta provincias/estados; los nombres con
  state/kingdom/dynasty/reino/principado no se geocodifican. 56 tests.
  Sin resolver: Cishan, Miaodigou, Xipo (no estan en Wikidata con coordenadas o no pasan la comprobacion).
- `write_mongo` escribe con **ReplaceOne** (documento entero), no `$set`: con `$set` una ubicacion vieja sobrevivia al reprocesar.
- `write_mongo` ya tiene `--replace-source` (borra solo los chunkIndex huérfanos de ese source, tras el upsert) y
  `--ensure-index` actualiza el índice de dev con `lat`/`lng`. Dev (`china_documents_dev`): 68 chunks de `oxford-test` (15 con ubicacion).
- `extract_hitos` hecho (local, probado): 5 chunks por llamada a gpt-4o, hitos en es (base) + en + zh en la misma respuesta,
  `sitio_moderno` como segundo intento (como `lugarModerno` de china-history), y el modelo debe CITAR la frase que da el hecho y el
  lugar: se descarta si la cita no esta en el texto o no nombra el lugar (el lugar de un evento lo elegia mal: 'muerte del Rey You'
  en Luoyi, 'conquista de Shang' en Xi'an). Id verificado: 1249/1259 de china-history coinciden (los otros 10 se editaron a mano).
  `write_mongo --kind hitos`: ReplaceOne por (id, origen='china-documents-reader'), nunca pisa ni borra un hito ajeno.
  Prueba (Oxford Handbook, PDF 376-395, Zhou Occidental): 3 hitos fiables de 38 chunks; dev tiene 3. Rendimiento bajo a proposito.
- `write_mongo --kind hitos` guarda solo lo nuevo (ver Ideas -> dedupe). Probado en dev con dry-run: 2 duplicados descartados
  (0.87 y 0.94) y 1 nuevo; con `--replace-source`, reproceso sin marcar nada como duplicado de si mismo. 90 tests.
- `presign` y el `handler` de `extract_chunk` hechos y probados en local (sin AWS): `extract_chunk` lee el PDF de S3 y deja los
  chunks en `jobs/<jobId>/chunks.json`, devolviendo solo la referencia (149 bytes; con el Oxford completo, 825 pags -> 2171 chunks,
  13 s, 2.3 MB: Step Functions solo admite 256 KB entre pasos, por eso se pasan referencias y no datos). `presign` firma un POST a S3
  (solo PDF, max 100 MB, 5 min) con `uploads/<jobId>/<nombre-seguro>.pdf`. `shared/storage.py`: `S3Storage` en AWS, `LocalStorage` en
  pruebas. 152 tests.
- **Contador de coste** (`shared/usage.py`): `make_client()` devuelve un `TrackedClient` que cuenta tokens y coste estimado; cada CLI
  imprime su resumen y la extrapolacion al libro entero. Precios en `PRICES` (mayo 2026, de comparadores de terceros, NO de openai.com:
  verificar) y sobrescribibles con `OPENAI_PRICES`. Medido con el Oxford (pags. 376-395): embed_label ≈ $0.070 y 0.6-0.8 min por 20 pags
  (→ ≈ $2.9 y ≈ 25-32 min para las 825 pags; medido con el chunk ENTERO y temperature 0; con solo 600 caracteres era ≈ $2.3); extract_hitos ≈ $2.84 y ≈ 19 min por cada 1000 chunks etiquetados (→ ≈ $4-5 para ~1500).
  Libro completo ≈ $7-9 de OpenAI y ≈ 1 h en serie (+ pequenas llamadas del juez en write_mongo). Una sola muestra de 20 pags: orientativo.
- **Etiquetado con el chunk ENTERO y temperature 0 (decidido por el usuario):** antes la IA solo veia los 600 primeros caracteres de cada chunk y se
  perdian sitios de la segunda mitad (p. ej. Laoniupo). Ahora recibe el chunk entero (tope de seguridad 4000, `MAX_CHUNK_CHARS_SENT`). Todas las
  llamadas de chat (etiquetado, hitos, juez de duplicados y juez de personajes) llevan `temperature=0` desde `shared/llm.py` (`OPENAI_TEMPERATURE`;
  vacia para modelos que no admiten el parametro). Medido en 20 pags (dos ejecuciones): +27 % de coste, mas chunks con ubicacion (6-10 de ~48 frente a
  4 de 38) y 38 de 48 chunks identicos entre ejecuciones: **reduce pero NO elimina la variacion**. Efecto secundario visto y corregido: la IA nombra
  mas sitios, incluidos cortos y ambiguos, y Mapbox resolvia 'Feng' como 'Feng County' (Baoji), a ~200 km de la capital Feng de los Zhou; ahora, en
  Mapbox, un nombre de hasta 5 letras solo se acepta si el resultado se llama EXACTAMENTE igual (`MAPBOX_SHORT_NAME_CHARS`).
- **Handlers por tandas (listos para Step Functions, probados en local con servicios reales):** `embed_label.period_handler` (periodo del
  libro, una vez) y `embed_label.handler` (tanda de N chunks -> `jobs/<id>/labeled/<inicio>.json`), `extract_hitos.handler` (tanda ->
  `jobs/<id>/hitos/<inicio>.json`), `write_mongo.chunks_handler` / `hitos_handler` / `cleanup_handler`. Todo pasa por S3 y cada paso
  devuelve un resumen pequeno con su `usage` para sumar el coste. `hitos_handler` debe ir DE UNA EN UNA y en orden (resolucion de
  personajes y 'solo lo nuevo'); chunks y embed_label pueden ir en paralelo. Simulado el flujo entero con S3 local + OpenAI real + Mongo
  dev: 20 pags ≈ $0.16 y 75 s. `shared/secrets.py` (env/.env en local, SSM `SSM_PREFIX/<NOMBRE>` en AWS) y `shared/geodata.py`
  (`GEODATA_BUCKET` -> /tmp en AWS) ya estan conectados. Destino de escritura en AWS: SOLO la variable `MONGO_TARGET` (por defecto dev).
- **jobId, marca de origen y limpieza:** cada chunk/hito escrito por AWS lleva `jobId`; los chunks llevan `origen` (antes no); al final
  `cleanup_old_runs` borra lo de ejecuciones ANTERIORES del mismo libro (solo con marca de esta app). Una tanda nunca borra huerfanos
  (borraria lo de la tanda anterior). `write_chunks` se niega si el `source` ya tiene chunks sin nuestra marca (p. ej. un libro de
  china-history con el mismo slug). **Fallo corregido:** un hito con el mismo `id` de OTRO libro (mismo titulo/lugar/anio) se sobrescribia
  y luego la limpieza lo borraba; ahora el mismo id solo cuenta como 'el mismo hito' si es del mismo libro y la escritura reemplaza por
  (id, origen, fuente). Revisar siempre a mano el estado de dev tras una simulacion: asi se vio.
- **AWS (preparado, nada desplegado):** cuenta con plan GRATUITO (6 meses o hasta gastar los creditos; luego pasar a plan de pago o la
  cuenta se cierra), presupuesto de 10 USD con alerta, region `eu-west-1`, usuario IAM `cdk-deployer`, CLI v2 y CDK instalados,
  sesion con `aws login` (credenciales temporales de hasta 12 h; si caducan, repetir `aws login`; boto3 local necesita `botocore[crt]`).
  **NO habilitar IAM Identity Center / AWS Organizations: convierte la cuenta en pago por uso y los creditos caducan al instante.**
  **NO aceptar el MCP de AWS que ofrece `aws login`** sin decidirlo (da a un agente acceso a la cuenta con AdministratorAccess).
- Pendiente, en este orden: diseno de la infra CDK (esperar OK del usuario) → UI de subida/hitos → agente `ask` → CI/CD.
  Ver `README.md` y `backend/README.md`.
  **CDK escrito y validado, SIN desplegar** (`infra/`, ver `infra/README.md`): stack unico en eu-west-1 con 2 buckets, DynamoDB, 9 Lambdas
  (un paquete: `npm run build:lambda` instala dependencias de Linux con pip, sin Docker), Step Functions Standard con Map (5 en paralelo
  para etiquetar y extraer; 1 para escribir hitos), EventBridge (PDF en uploads/ arranca el flujo), Cognito (un usuario, sin registro
  abierto) y API Gateway con autorizador JWT + limite de 2 peticiones/s. Validado: `tsc`, `cdk synth`, un comprobador estructural del ASL y la
  API `aws stepfunctions test-state` del servicio real para `ParseUpload`, `MakeBatches` (2171 chunks -> 22 tandas, sin tanda vacia) y el
  Choice de PDF escaneado. Fallo corregido antes de desplegar: tres tareas tenian `Parameters: "$"` (invalido); ahora cargas explicitas.
  Lo que NO se ha probado: una ejecucion real en AWS. Antes de desplegar el usuario debe: crear los parametros SSM (OPENAI_API_KEY,
  MONGODB_URI_DEV, MAPBOX_TOKEN), permitir 0.0.0.0/0 en Atlas, y hacer `cdk bootstrap`. Despues: subir los datos geograficos al bucket
  y crear su usuario de Cognito.
  Decisiones tomadas para la infra: Standard Workflow con Map (max. 5 en paralelo) y 'redrive' para reanudar; secretos en SSM Parameter
  Store; Atlas con allowlist 0.0.0.0/0 (M0 no tiene conexion privada) y credenciales fuertes; destino dev por defecto (`MONGO_TARGET`);
  proteger la API con **login en el front (Cognito, un solo usuario, sin registro abierto) y autorizador JWT en API Gateway**, mas
  limite de peticiones; el login solo en el front NO protege la API (se puede llamar directamente). Pendiente: escribir el CDK y
  validarlo con `cdk synth` (sin desplegar), luego un primer despliegue pequeno con un PDF de 20 paginas.
- Colección `personajes`: `id` (estable, se asigna al crear y no se recalcula), `nombre`/`nombre_en`/`nombre_zh`, `aliases` {es,en,zh},
  `nacimiento?`/`muerte?`, `rol`, `estado` (dinastía/estado), `descripcion` (+`_en`/`_zh`), `fuente`, `origen`.
- `hitos_conocidos.personajes`: array opcional `[{ id, rol, menciones: {es, en, zh} }]`, con índice en `personajes.id`. `menciones` es el
  texto exacto con el que el personaje aparece en titulo/descripcion de ESE hito en cada idioma (la web lo convierte en enlace sin
  ambigüedad entre los muchos "rey Wu"). china-history no se rompe: reconstruye el hito con campos explícitos e ignora los extra.
- Los 1259 hitos existentes no tienen personajes; si el usuario los reconstruye algún día con este método (es caro), nacerán con ellos.

Decisiones: solo personajes que intervienen en algún hito (no cualquier persona del texto); `_zh` opcional (los nombres propios en
chino fallan a veces); unir identidades de forma CONSERVADORA (alias en común + época compatible + juez; ante la duda, ficha separada,
porque mezclar dos personas distintas es peor que partir una en dos).

Estado: **pasos 2 y 3 hechos, probados en dev.** La extraccion de hitos devuelve `_personajes` validados (el nombre debe estar en el
texto original; la mencion enlazable se BUSCA en codigo, la forma mas larga de nombre/alias que este literal en el texto del hito en cada
idioma, con limites de palabra; no se fia de la que da el modelo). `write_mongo --kind hitos` (`--no-personajes` lo desactiva) resuelve la
identidad contra la coleccion `personajes` de la base de destino (`lambdas/write_mongo/personajes.py`): candidatos por alias normalizado
(`aliasNorm`, indexado) y epoca compatible (actividad +-60 anios); mismo nombre canonico y estado compatible -> se une; si no, un juez
(cacheado) y ante la duda ficha separada. Las fichas de otro origen se enlazan pero no se modifican. Orden de escritura: fichas primero,
luego hitos (ningun enlace colgando). Las fichas llevan `aliases {es,en,zh}`, `aliasNorm`, `actividadInicio/Fin`, `fuentes`, `origen`.
El hito lleva `personajes: [{id, rol, rol_en, rol_zh?, menciones}]`. Probado: tres fichas (reyes Wu, Cheng, Ping), la segunda aparicion del
rey Wu se une a su ficha, "dado el personaje sus hitos" y la mencion convertida en enlace funcionan. Produccion intacta (sin coleccion
`personajes`). 123 tests. **Pendiente en china-history (su agente):** schema, endpoints y enlaces en la UI.

- **Traducción al español para leer (independiente del pipeline, no toca Mongo ni `book_chunks`):** `backend/lambdas/translate_page` traduce UNA
  página con `gpt-4o` y la guarda en `translations/<slug>/<VERSION_PROMPT>/<página>.json` (la versión va en la clave). Handler
  `GET /books/{slug}/pages/{page}/translation`: el navegador manda solo libro+página (nunca texto: sería traductor gratis con nuestra clave); la
  Lambda saca la página del PDF en `books/<slug>/original.pdf`. Medido (Oxford): ~5 s y ~0,01 USD por página; 3 páginas en paralelo tardan lo
  mismo que una. Los caracteres chinos se dejan y se aclaran entre paréntesis. Fallo visto y corregido: el modelo cambiaba números de nota.
  `lambdas/store_book` (último paso del flujo, tras Finalize) copia el PDF subido a `books/<slug>/original.pdf` en el bucket permanente
  `BooksBucket` (sin caducidad, RETAIN) y borra `translations/<slug>/` (las del PDF anterior si se reprocesa). En el CDK: bucket, `StoreBookFn`,
  `TranslatePageFn` (29 s: tope de API Gateway) y la ruta con JWT; validado con `cdk synth` y comprobación estructural del ASL, SIN desplegar.
  El front (`ReaderView`, scroll continuo, inglés del PDF mientras llega el español, solo se pide la página a la vista ±1 tras 0,7 s) usa de
  momento la API simulada. **Pendiente:** URL firmada de lectura del PDF para el visor, catálogo de libros y API real en el front.

- **Desplegado en AWS (eu-west-1, stack `ChinaDocumentsStack`) y probado de punta a punta:** subida de las 20 pags. del Oxford -> flujo OK en ~2 min,
  ~0,20 USD, 42 chunks en dev (todos con años), hitos nuevos 2 (4 duplicados de pruebas anteriores descartados), PDF guardado en `BooksBucket`,
  traduccion de una pagina OK. Script: `infra/scripts/smoke-test.mjs` (pide la contraseña sin mostrarla). Cognito: un usuario creado a mano. Parametros SSM
  creados; Atlas con 0.0.0.0/0; datos geograficos subidos al bucket privado. **Hosting de la web en el CDK (S3 privado + CloudFront con OAC, SPA):**
  desplegado (URL en la salida `SiteUrl`); NO lleva PDFs (`infra/scripts/build-front.mjs` los borra y falla si queda alguno).
  **Rebanada 1 de datos reales (en curso, validada con tests y `cdk diff`, pendiente de desplegar):** el front ya no tiene datos de ejemplo (la API simulada vive solo
  en `frontend/src/test/` para los tests). Login Cognito propio (`amazon-cognito-identity-js`, SRP; `global` definido en `vite.config.ts` o la web queda en blanco),
  `config.json` en runtime (lo sube el despliegue; en local `node infra/scripts/write-front-config.mjs`). Backend nuevo: `BooksTable` (catalogo permanente, lo escribe
  `store_book`), Lambda `books` (`GET /books`, `GET /books/{slug}/pdf-url`), `GET /jobs`, `title` en `presign`. El front solo enseña las pestañas con datos
  reales (`api.features`): hoy Archivos y Progreso; Hitos/Personajes (rebanada 2: endpoint que lea Mongo y guardar la PAGINA real del hito, que hoy no se guarda)
  y Agente (rebanada 4) siguen ocultas. Progreso sin fases ni cola todavia (rebanada 3).
  **Rebanada 2 (en curso, sin desplegar):** cada hito guarda `pagina` (la del parrafo que contiene su cita: el chunk recuerda `paragraphPages`; verificado en 6 de 6 hitos
  del Oxford buscando la cita en el PDF) y `evidencia` (la frase del libro, max. 500). Campos extra opcionales: china-history los ignora (avisar a su agente; `book_chunks`
  tambien lleva ahora `paragraphPages`). Lambda `read_hitos` (`GET /books/{slug}/hitos` y `/personajes`, solo lectura, filtra por `origen` y por libro, tope 500)
  y pestañas Hitos/Personajes con datos reales. Los hitos ya guardados antes (`oxford-test`, `oxford-prueba`) NO tienen pagina hasta reprocesar el libro.
  **Estilo (v2):** cabecera translucida que FLOTA sobre el visor (solo el titulo; HEADER_HEIGHT en lib/layout.ts), pildora Original/Español + pagina arriba a la derecha, panel cerrado
  por defecto con una lengueta en el borde derecho (en PC EMPUJA al PDF, en movil se superpone), pestañas del panel arriba con icono y texto debajo. **Portadas (hecho, sin desplegar):** `ingest` (al SUBIR, ya no en el analisis) dibuja la 1a pagina con `pypdfium2`+Pillow (JPEG 360 px, ~30 KB; opcional: si falla, el libro se guarda igual) en `books/<slug>/cover.jpg`;
  `store_book` la copia a `books/<slug>/cover.jpg` (y borra la vieja si el reproceso no trae) y marca `hasCover` en el catalogo; `books` y `jobs` devuelven `coverUrl` (URL firmada;
  `jobs` solo para los RUNNING). Web: `Cover` en Archivos y Progreso, con recambio 書. Libros ya subidos: `python -m lambdas.store_book.backfill_covers --table <BooksTableName>
  --bucket <BooksBucketName> [--write]` (por defecto solo muestra). Ojo: `pypdfium2` para Linux NO se ha podido probar hasta el primer despliegue (paquete de Lambdas 74 MB).
  **Borrar libros (hecho, sin desplegar):** `DELETE /books/{slug}` (Lambda `delete_book`, la de permisos mas amplios) borra Mongo (chunks y hitos con `origen` propio; personajes
  solo si ningun otro libro los usa, si no se les quita la fuente), S3 (`books/<slug>/`, `translations/<slug>/`, `jobs/<id>/` y `uploads/<id>/` de sus trabajos), sus trabajos y,
  el ULTIMO, el catalogo (si falla a medias el libro sigue en la lista y se reintenta). 409 mientras se analiza. Web: papelera en cada libro de Archivos + `ConfirmDialog` (portal en
  `body`: el panel usa `translate` y encerraria un `fixed`). Restos de pruebas sin catalogo: `python -m lambdas.delete_book.cli <slug> [--write]` (solo Mongo dev; por defecto solo cuenta).
  **Sin rebote** (`overflow-hidden overscroll-none` en html/body, como china-history) y panel con movimiento de 450 ms (Tailwind 4 anima `translate`, no `transform`).
  **Analizar a peticion (hecho, sin desplegar):** SUBIR NO ANALIZA. `presign` (kind `upload`) comprueba que no se pisa un libro (409 si existe y no se confirma el reemplazo, o si algo se
  analiza/sube) y EventBridge manda el PDF a `ingest`: valida (no danado, sin contraseña), cuenta paginas, dibuja la portada, lo guarda en `books/<slug>/` (PERMANENTE; antes caducaba a los 30 dias en
  `uploads/`) y lo apunta en el catalogo con `analysis: "none"`; si REEMPLAZA un libro, borra antes lo derivado del PDF anterior (misma rutina que borrar). Ya se puede leer y traducir. El boton **Analizar**
  (con coste estimado ~0,01 USD/pagina, `AnalyzeDialog`) llama a `POST /books/{slug}/analyze` (Lambda `analyze`: 409 si ya hay algo en curso; apunta el trabajo kind `analysis` y arranca Step Functions
  con `{bucket: intermedios, pdfBucket: books, key: books/<slug>/original.pdf, jobId}`). El flujo ya no copia el PDF: `store_book` solo hace `update_item` (analysis=done + estadisticas, condicion
  `attribute_exists`: si el libro se borro mientras tanto, no se recrea). Catalogo: `analysis` = none|done (sin el campo = libro antiguo = done); lo que pasa AHORA (analizando, fallo, OCR) se deduce de
  los trabajos (`lib/analysis.ts`). Intermedios (`jobs/<id>/`) siguen en el bucket de DATOS (caducan), el PDF se lee de `pdfBucket`. Web: chip de estado + Analizar/Volver a analizar en cada libro,
  aviso con boton en Hitos/Personajes, Progreso oculta las subidas terminadas. Libros ya subidos antes: sin cambios (se tratan como analizados).
  **Carga rapida (hecho, sin desplegar):** pantalla base en `index.html` (HTML+CSS en linea: spinner y cabecera, se ve antes de que cargue el JS; su gemelo React es `LoadingShell`); la config se pide YA
  desde index.html (`window.__configPromise`) en paralelo con el JS; pdf.js sale del JS principal (807 -> 326 kB; `loadPdfjs()` con `import()` dinamico, prefetch al montar `App`); CDK en DOS tandas: `assets/`
  (nombres con huella) `immutable` 1 año y el resto `no-cache`. **LECCION:** un `<link rel="stylesheet">` de Google Fonts BLOQUEA el primer pintado Y el arranque del JS (en red lenta, la web entera se quedaba en
  blanco esperando tipografias): va como `rel="preload"` + `onload`. Cualquier hoja de estilos externa debe cargarse asi. Medido con 4G lento simulado: pantalla base a ~400 ms, entrada lista a ~1,6 s.
  **Identidad visual:** acento ROJO ZHOU `#8e1b1b` (seal-dark `#6c1212`, texto `#f6efe4`), el mismo del portfolio; el verde Shang es el de china-history. 'Listo'/'Analizando'
  van en neutro (tinta) para no confundirse con 'Error'. El libro `oxford-prueba` de la prueba de AWS no esta en el catalogo (se subio antes).

  **CI/CD (hecho):** `.github/workflows/ci.yml` (tests en cada push; push a `main` -> `cdk diff` clasificado -> despliegue automatico, o con aprobacion humana si el cambio es delicado: IAM,
  borrados, sustituciones). OIDC sin claves: rol `china-documents-github-deploy` (`ChinaDocumentsCiStack`, `infra/lib/ci-stack.ts`), variable `AWS_ROLE_ARN`, environments `despliegue` y `aprobacion`.
  `npm run deploy` local sigue valiendo. Ver `infra/README.md`.

  **Agente con streaming (hecho, probado en local con servicios reales, sin desplegar):** `backend/ask/` es la UNICA Lambda en Node.js (Python no tiene streaming nativo en Lambda y el HTTP API de
  API Gateway no lo permite): Function URL en modo `RESPONSE_STREAM`, publica, y la propia funcion valida el token de Cognito (`aws-jwt-verify`, id token del user pool y cliente web) ANTES de escribir nada.
  Flujo: pregunta (max. 600 caracteres, historial = ultimos 6 turnos que manda el navegador; nada se guarda en el servidor) -> embedding (`text-embedding-3-small`) -> `$vectorSearch` en `book_chunks` de la base
  del destino (filtro `source` = libro, o sin filtro para todos; siempre `origen` propio) -> `gpt-4o` temperature 0 solo con esos fragmentos -> NDJSON: `fuentes`, `delta`..., `fin` (o `error`). Si nada pasa
  el umbral de relevancia (`ASK_MIN_SCORE`, 0,58: Atlas normaliza el coseno a 0,5-1; preguntas sin relacion sacan 0,51-0,54 y las pertinentes 0,61-0,78) NO se llama al modelo: "no lo encuentro". El indice
  vectorial de dev ahora tiene `source` como filtro (`ensure_dev_indexes`; ya aplicado en dev). Sin freno de peticiones propio (no hay API Gateway delante; reservar concurrencia no es posible con la cuota
  de una cuenta nueva): lo limitan el login, la pregunta corta y el historial recortado. Web: pestana Agente (`AgentTab`, `lib/ndjson.ts`, `HttpApi.ask`), citas con boton de pagina (abre el libro), "Detener",
  "Reintentar", aviso si el libro no esta analizado. `askUrl` va en `config.json` (opcional: sin el, la pestana se oculta). Pruebas: `cd backend/ask && npm test` (18) y `node cli.mjs "pregunta" [slug]`
  (real, cuesta centimos). El CI instala y prueba `backend/ask` y empaqueta con `npm run build:ask`.
  **Paso previo (`plan`, gpt-4o-mini, ~0,001 USD):** decide si la pregunta es GENERAL sobre el libro ("de que trata", resumen) o concreta y reescribe la concreta como consulta autonoma en INGLES (resuelve
  "¿y quien le sucedio?" con la conversacion y mejora la busqueda en libros en ingles). Fallo visto en el movil: una pregunta general no se parece a ningun fragmento y la busqueda por parecido traia un pie de nota
  al azar ('novela'). Ahora una pregunta general NO busca por parecido: si el libro cabe en `ASK_FULL_BOOK_CHARS` (100.000 caracteres ≈ 25.000 tokens ≈ 0,06 USD/pregunta) se lee ENTERO; si no, el comienzo
  (3 fragmentos) y 7 repartidos por el resto. Un resumen de verdad de un libro de 800 paginas pediria un resumen precalculado en el analisis (idea, no hecho). Si el paso previo falla, se busca como antes.

  **OCR de libros escaneados (hecho, probado con servicios falsos y la maquina de estados validada, sin desplegar):** un PDF sin texto (todo imagenes, p. ej. 'Records of the Grand Historian', 554 pags., CCITT 1 bit) no
  se puede traducir, leer en ingles ni analizar. `ingest` marca `textLayer: false` en el catalogo si ninguna de ~8 paginas repartidas tiene texto. Boton **Reconocer texto (OCR)** en Archivos (con estimacion y dos motores):
  **Amazon Textract** por defecto (~0,0015 USD/pag.; ~0,83 USD el libro; ingles y lenguas europeas, NO chino) y **OpenAI vision** (gpt-4o, ~0,013 USD/pag.; lee chino) para libros casi todo chino.
  Flujo: `POST /books/{slug}/ocr` (`lambdas/ocr`, `start_handler`) -> maquina `OcrPipeline` (Textract: `prepare` arranca el trabajo asincrono -> espera 20 s -> `check` -> `collect`; OpenAI: tandas de 8 pags., 5 en
  paralelo, `pages` renderiza con pypdfium2 y transcribe) -> `finish` junta los trozos en `books/<slug>/ocr.json` ({engine, createdAt, pageCount, pages[]}), BORRA las traducciones del libro (las paginas sin texto se
  habian traducido 'vacias') y apunta `ocr` en el catalogo. Quien usa el texto: `translate_page` (si la pagina no tiene texto propio, el OCR), `extract_chunk` (las paginas vacias toman el del OCR: el analisis y el
  agente ya funcionan en escaneados), y el lector en ingles (`GET /books/{slug}/ocr-url`, el navegador baja el JSON una vez). Un OCR en marcha NO cuenta como analisis (`analysisState`). Repetir el OCR con el otro motor
  esta en la tarjeta. IAM: Textract solo admite `*`; las Lambdas que lo llaman leen el PDF con su propio rol. Fallos arreglados de paso: el IndexedDB guardaba traducciones VACIAS (ahora no se guardan ni se leen) y el
  ambito de las traducciones incluye la version del OCR. **Sin probar en AWS real:** Textract con este PDF (CCITT/ICC, 3024x4836) y la vision de OpenAI; se prueba tras desplegar.

## Ideas apuntadas (sin implementar)

- **Libros escaneados / imagenes sueltas (para el FINAL, decidido con el usuario):** hoy un PDF sin texto acaba en `NEEDS_OCR`. Un solo paso de OCR por paginas
  (en paralelo, como el etiquetado) resolveria PDFs escaneados e imagenes sueltas: su texto por pagina entraria en el flujo en lugar del de pypdf, y la traduccion y el
  lector usarian ese texto cuando el PDF no tenga capa de texto. Opciones: Textract (~1,5 USD/1000 pags., pero creo que NO lee chino: verificar), Tesseract (gratis,
  peor con fotos) o vision de OpenAI (`gpt-4o`, ~0,01-0,015 USD/pag., lee chino; recomendada). Las imagenes se unirian en un PDF (Pillow) para que el visor no cambie;
  la web necesitaria elegir varias fotos, ORDENARLAS (el orden de subida es la numeracion de paginas) y subirlas. Un libro de 800 pags. pasaria de ~8 a ~18 USD.
  Fotos torcidas, con sombras o con dos paginas por foto dan peor OCR. Preguntas abiertas: fotos de movil o escaner, con o sin caracteres chinos, cuantas paginas.

- **Tope de amplitud del rango de anios (HECHO):** un rango de mas de `MAX_SPAN_YEARS` (2000 por defecto, configurable por entorno) va a
  pendientes con motivo `rango_demasiado_ancho` y no se pagan sus embeddings. Motivo: en la prueba con el Oxford Handbook 7 de 68 chunks
  tenian 3000-5050 anios (introducciones de capitulo: es el alcance del capitulo, no del fragmento) y metian ruido; el filtro anterior
  (`rango_igual_al_periodo_del_documento`, >= 90 % del periodo del libro) NO los atrapaba porque cubren solo ~63 %. Un rango ancho no excluye
  el chunk de las consultas (el filtro de china-history es por solapamiento): es ruido; uno estrecho pero equivocado si lo excluiria.
  Los 7 chunks de dev (`oxford-test`) se escribieron antes del tope y siguen ahi hasta reprocesar el libro.
- `--replace-source` en `write_mongo` (borrar solo `{source: slug}`, con confirmación en prod) para reprocesar un PDF
  que ahora da menos chunks sin dejar `chunkIndex` huérfanos.
- Base de pruebas `china_documents_dev` tiene 68 chunks de `source: oxford-test` (págs. 14-45 del Oxford Handbook).

- **Chino (`_zh`) no es determinista:** en dos ejecuciones el mismo nombre propio salio bien (发) y mal (法, 'ley'). Los lugares salen bien
  cuando el texto original trae los caracteres (el Oxford los incluye entre parentesis). Revisar `_zh` de nombres de personas.
- **Reprocesar hitos / libros que repiten hechos (resuelto en `write_mongo/dedupe.py`):** el `id` depende del titulo que escribe el
  modelo y varia entre ejecuciones, asi que no sirve para detectar repetidos. Se guarda SOLO LO NUEVO comparando con la base a la que se
  escribe (dev hoy, produccion cuando se promueva; en produccion vera los 1259 de china-history): candidatos con anio +-15 y <25 km;
  parecido por embeddings del texto en ingles (>=0.85 duplicado, <0.5 nuevo); zona gris -> un modelo juzga si es el mismo hecho.
  Calibrado con datos reales: el mismo hecho puntua 0.62-0.95 y hechos distintos 0.31-0.60 (se solapan, de ahi el juez). Con
  `--replace-source` los hitos viejos del mismo libro NO cuentan como existentes (si no, todo seria duplicado de la tanda anterior).
  `--keep-duplicates` lo desactiva. Los chunks NO se deduplican: que dos libros cuenten lo mismo es lo que quiere la busqueda y las citas.
- **Heredar el anio de los vecinos en chunks sin fecha propia (DESCARTADO por ahora, decidido por el usuario):** hoy un chunk sin anio fiable va a pendientes
  aunque su entorno este claro. Medido a mano: capitulo de los Zhou, 10 pendientes de 58 (6 notas al pie/bibliografia + ~4 de contenido, de los que ~3
  pasarian una regla de vecinos coherentes); Oxford pags. 14-45, 18 de 86 (9 editorial + 9 de contenido, p. ej. Dadiwan o ceramica Yangshao). Es ~5 % del
  libro, muestra pequena y clasificacion a ojo. Propuesta si se retoma: campo `es_contenido` en el etiquetado (editorial/notas/bibliografia/pies no heredan),
  heredar solo si hay un vecino fechado antes y otro despues (a <= 3 chunks), sus rangos se solapan y la union <= MAX_SPAN_YEARS, y marcar `añoOrigen: "vecinos"`.
  No hay forma de usar el periodo del capitulo porque no se detectan capitulos (el periodo del libro es demasiado ancho). Se mantiene la regla: todo anio
  guardado sale del propio chunk. Retomar con el libro completo si se pierde mas de lo esperado.
- **Mas cobertura de ubicacion:** ampliar `yacimientos.json` de china-history con los sitios que salgan sin resolver, o probar
  otra fuente (p. ej. Nominatim/OSM) si Wikidata se queda corto en otros libros.
- **Para china-history (verificado en dev):** si prefiltran por caja geografica con un `$and` simple, los chunks SIN ubicacion
  (la mayoria: 53 de 68 en la prueba) quedan excluidos, igual que los que no tienen anyo. Para no perderlos hay que usar
  `{"$or": [{"$and": [caja lat/lng]}, {"lat": {"$exists": false}}]}` (funciona en `$vectorSearch`; `{"lat": {"$eq": null}}` NO).
  Un `$or` que contiene un `$and` funciona; un `$and` anidado directamente dentro de otro `$and` devolvio vacio.
  Aun asi, los chunks sin ubicacion siguen compitiendo solo por significado y epoca, asi que el fallo de Puyang/Wangchenggang
  puede persistir con ellos; una opcion es copiar su estrategia de margenes (primero solo los localizados en la caja, luego relajar).
- Aprendizaje: una comprobación "la provincia coincide" dejó pasar el centro de la provincia como si fuera el sitio
  (Erlitou, Jiahu, Yangshao... con las mismas coordenadas). Ante un resultado de geocodificación, validar también el *tipo* de
  lugar y que el nombre se parezca; y revisar a mano una muestra antes de fiarse de las métricas. Otros fallos que solo salieron revisando datos a mano:
  el pie de una foto de 1925 tomado como epoca del contenido (el prompt ahora lo excluye); un museo o una estacion de metro
  del mismo nombre tomados por el yacimiento (se priorizan los candidatos que parecen yacimiento).

- **Login compartido con china-history (idea, no hecho):** hoy china-history es PUBLICA y sin login (comprobado: ni guards en NestJS ni auth en
  Next.js), y solo esta app tiene login (Cognito). Si algun dia china-history lo necesita: mismo user pool de Cognito, un guard en su API que
  valide el JWT con las claves publicas de Cognito (sin credenciales de AWS; el API sigue en DigitalOcean), y un DOMINIO PROPIO (sin el no se
  puede compartir sesion entre las dos). Dos URLs (subdominios) o una (CloudFront con rutas /historia y /documentos y `basePath` en Next.js).
  Para SSO estandar entre apps distintas lo normal es la Hosted UI de Cognito (pantalla de AWS con la marca propia), que contradice el
  formulario propio decidido para el front: decidirlo ese dia. Una pantalla con un icono por app seria una pagina de dos enlaces tras el login.

## Comandos

```bash
# backend (desde backend/)
python -m venv .venv && .venv\Scripts\activate && pip install -r requirements.txt
python -m lambdas.extract_chunk.cli ruta\al\libro.pdf

# frontend (desde frontend/)
npm install && npm run dev      # build: npm run build
```

En Windows la consola puede mostrar mal los acentos al imprimir (`�`); es de la codificación de la terminal,
no del código.

## Cómo trabajar con el usuario

- Es desarrollador full-stack JS/TS (React, Node, NestJS, Next, MongoDB). **Python y AWS los está aprendiendo.**
- Aprende con el porqué: antes de implementar un paso, explicar brevemente la idea y las decisiones abiertas.
  Sin preguntas de examen.
- Paso a paso: probar en local cada Lambda antes de pasar a la siguiente. No encadenar pasos sin que el anterior
  funcione solo.
- Para cada paso nuevo: proponer diseño y **esperar su OK** antes de implementar.
- Español en explicaciones y comentarios del código.
- Una Lambda = una carpeta con su lógica pura + `cli.py` para probarla en local + `handler` fino para AWS.
