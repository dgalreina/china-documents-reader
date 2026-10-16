# backend

Python. Cada paso del análisis es una Lambda independiente en `lambdas/`.

| Lambda | Qué hace | Estado |
|---|---|---|
| `presign` | Devuelve la URL prefirmada para subir el PDF a S3 | pendiente |
| `extract_chunk` | Extrae texto por páginas y trocea en chunks | hecho (lógica) |
| `embed_label` | Embeddings y etiquetado de `añoInicio` / `añoFin` | hecho (lógica, probada con cliente falso; falta prueba real con la API) |
| `write_mongo` | Valida el contrato y hace upsert en `book_chunks` (dry-run por defecto, `--target dev\|prod`) | hecho (lógica y validación con tests; falta probar contra `china_documents_dev`) |
| `extract_hitos` | Hitos con título, año, lugar y coordenadas | pendiente |
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
