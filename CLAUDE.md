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
  Sin resolver: Cishan, Miaodigou, Xipo (no estan en Wikidata con coordenadas o no pasan la comprobacion).
- `write_mongo` escribe con **ReplaceOne** (documento entero), no `$set`: con `$set` una ubicacion vieja sobrevivia al reprocesar.
- `write_mongo` ya tiene `--replace-source` (borra solo los chunkIndex huérfanos de ese source, tras el upsert) y
  `--ensure-index` actualiza el índice de dev con `lat`/`lng`. Dev (`china_documents_dev`): 68 chunks de `oxford-test` (15 con ubicacion).
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
