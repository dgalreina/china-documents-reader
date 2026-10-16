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
- Escritura en `book_chunks`: **upsert por `(source, chunkIndex)`**, para que reprocesar un PDF no duplique.
- Los campos extra de `book_chunks` (página de origen, modelo de embeddings) no están en el schema Mongoose
  `BookChunk` de `china-history-api`; no rompe nada, pero hay que añadirlos allí si se quieren consultar.
- `hitos_conocidos`: `id`, `lugar`, `año`, `titulo`, `descripcion`, `fuente`, `location` (GeoJSON Point,
  `coordinates: [lng, lat]`). Opcionales: `lugar_en`/`titulo_en`/`descripcion_en` y `_zh` (sin ellos el hito se ve
  siempre en español). Los hitos necesitan geocodificar el lugar.
- `id` de un hito: **mismo algoritmo que `scripts/extract-hitos.mjs` de china-history-api**:
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
- Pendiente, en este orden: `extract_hitos` → `presign` → infra CDK →
  UI de subida/hitos → agente `ask` → CI/CD. Ver `README.md` y `backend/README.md`.

## Ideas apuntadas (sin implementar)

- **Tope de amplitud del rango de años.** En la prueba con el Oxford Handbook (págs. 14-45, `gpt-4o`), mediana de 1000
  años por chunk; 7 de 68 chunks tenían 4000-5000 años (introducciones de capítulo: es el alcance del capítulo, no del
  fragmento) y meten ruido en las búsquedas. Propuesta: parámetro `MAX_SPAN_YEARS` (≈2000; con 1500 caerían 10 chunks
  más) y mandar los que lo superen a pendientes con motivo `rango_demasiado_ancho`. Un rango ancho no excluye el chunk
  (el filtro de china-history es por solapamiento), solo añade ruido; uno estrecho pero equivocado sí lo excluye de
  las consultas correctas. Decidir el valor tras probar con más libros.
- `--replace-source` en `write_mongo` (borrar solo `{source: slug}`, con confirmación en prod) para reprocesar un PDF
  que ahora da menos chunks sin dejar `chunkIndex` huérfanos.
- Base de pruebas `china_documents_dev` tiene 68 chunks de `source: oxford-test` (págs. 14-45 del Oxford Handbook).

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
