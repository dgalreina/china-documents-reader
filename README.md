# china-documents-reader

Aplicación donde el usuario sube PDFs y un backend con IA los analiza: extrae su texto, genera embeddings y
saca hitos históricos con lugar, año y coordenadas. Los resultados se guardan en MongoDB Atlas, de donde los
lee `china-history`.

```
frontend/   React (Vite + TypeScript): subir PDF, ver progreso, ver hitos, preguntar al agente
backend/    Python: una Lambda por paso del análisis
infra/      AWS CDK: S3, Lambdas, API Gateway, Step Functions, DynamoDB
```

## Contrato con china-history

- Esta app es la **única que escribe** en `book_chunks` y `hitos_conocidos`. `china-history` solo lee.
- Embeddings siempre con `text-embedding-3-small` (1536 dimensiones). Cambiar de modelo obliga a reindexar todo.
- **Nunca se escribe un chunk sin `añoInicio` y `añoFin`.** Los que no se puedan etiquetar quedan fuera.
- Cada chunk guarda la página de origen y el modelo de embeddings usado — campos nuevos que el schema de
  Mongoose de `china-history-api` (`BookChunk`) todavía no declara. No rompe nada (Mongo no es estricto con
  campos extra), pero si se quieren consultar desde allí hay que añadirlos al schema primero.
- Los hitos usan el formato de `hitos_conocidos`: `id`, `lugar`, `año`, `titulo`, `descripcion`, `fuente` y
  `location` (GeoJSON Point). Opcionales pero esperados por el frontend si se quiere texto en otro idioma:
  `lugar_en`/`titulo_en`/`descripcion_en` y `_zh` — sin ellos, un hito se ve igualmente pero siempre en
  español, aunque el usuario esté en inglés/chino.
- **`id` debe ser estable y determinista** (hash de `titulo+año+lugar+coordenadas`, mismo criterio que
  `scripts/extract-hitos.mjs` en `china-history-api`) — nunca un UUID aleatorio. Los escritos a
  `hitos_conocidos` deben ser **upsert por `id`** (`updateOne` con `upsert: true`), nunca un `deleteMany`
  sin filtro: `china-history-api/scripts/import-hitos.mjs` ya se corrigió para hacerlo así (antes borraba
  la colección entera en cada reimportación, lo que habría destruido todo lo que escribiera esta app).

## Estado

- [x] Extraer texto del PDF por páginas y trocear en chunks (`backend/lambdas/extract_chunk`)
- [x] Esqueleto del frontend (Vite + React + TypeScript)
- [x] Embeddings con OpenAI (`backend/lambdas/embed_label`, pendiente de prueba real con la API)
- [x] Etiquetar `añoInicio` / `añoFin` (idem)
- [ ] Extraer hitos y geocodificar el lugar
- [ ] Escribir en MongoDB
- [ ] Infraestructura en AWS (CDK) y subida con URL prefirmada
- [ ] Pantalla de subida y listado de hitos
- [ ] Agente para preguntar sobre los documentos, con citas
- [ ] CI/CD con GitHub Actions
