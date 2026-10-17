# backend

Python. Cada paso del análisis es una Lambda independiente en `lambdas/`.

| Lambda | Qué hace | Estado |
|---|---|---|
| `presign` | Devuelve la URL prefirmada para subir el PDF a S3 (solo PDF, máx. 100 MB, 5 min) | hecho (lógica y handler, probado en local) |
| `extract_chunk` | Extrae texto por páginas y trocea en chunks; el handler lee el PDF de S3 y deja los chunks en S3 | hecho (lógica y handler, probado en local) |
| `embed_label` | Embeddings y etiquetado de `añoInicio` / `añoFin` | hecho (años y ubicación de cada chunk; probado con la API real y el Oxford Handbook) |
| `write_mongo` | Valida el contrato y hace upsert en `book_chunks` (dry-run por defecto, `--target dev\|prod`) | hecho (chunks y hitos; probado contra `china_documents_dev`, nunca contra producción) |
| `extract_hitos` | Hitos (es/en/zh) con lugar, año y coordenadas; cada uno con la cita que lo respalda | hecho (local, probado con el Oxford Handbook; se escribe con `write_mongo --kind hitos`) |
| `ask` | Agente que responde sobre los documentos, con citas | pendiente |

## Probar en local

```bash
python -m venv .venv
.venv\Scripts\activate        # Windows
pip install -r requirements.txt
python -m lambdas.extract_chunk.cli ruta\al\libro.pdf
```

Embeddings y años (gasta céntimos de OpenAI; necesita `OPENAI_API_KEY` en un `.env`, en `backend/` o en la raíz):

```bash
python -m lambdas.embed_label.cli ruta\al\libro.pdf --limit 20
pip install -r requirements-dev.txt && python -m pytest    # tests sin red ni coste
```

El modelo de etiquetado es `gpt-4o` por defecto (mini falló con culturas poco conocidas); se cambia con la variable `LABEL_MODEL`.

Se ejecuta desde la carpeta `backend`. Si más de la mitad de las páginas salen sin texto, el PDF es un escaneado
y necesitará OCR (Textract) más adelante.
