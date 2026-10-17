"""Punto de entrada de AWS Lambda para el paso 1-2: del PDF en S3 a los chunks en S3.

event  = {"jobId": "...", "bucket": "...", "key": "uploads/<jobId>/libro.pdf"}
return = {"jobId", "bucket", "chunksKey", "chunkCount", "pageCount", "emptyPages", "scanned"}

No devuelve los chunks: los deja en `jobs/<jobId>/chunks.json` y devuelve dónde están, porque Step Functions no admite
más de 256 KB entre pasos (ver shared/storage.py). Los pasos siguientes leen ese fichero.
"""

import tempfile
from dataclasses import asdict
from pathlib import Path

from shared.storage import S3Storage, Storage

from .chunker import chunk_pages
from .pdf_reader import extract_pages

# Si más de la mitad de las páginas no tiene texto, es un PDF escaneado (imágenes): hará falta OCR (Textract).
SCANNED_RATIO = 0.5


def handler(event: dict, _context=None, storage: Storage | None = None) -> dict:
    storage = storage or S3Storage()
    job_id, bucket, key = event["jobId"], event["bucket"], event["key"]

    # /tmp es el único sitio donde una Lambda puede escribir: se descarga el PDF y se borra al salir.
    with tempfile.TemporaryDirectory() as tmp:
        pdf_path = Path(tmp) / "input.pdf"
        storage.download(bucket, key, pdf_path)
        pages = extract_pages(pdf_path)

    chunks = chunk_pages(pages)
    empty = sum(1 for page in pages if not page.text)
    scanned = bool(pages) and empty / len(pages) > SCANNED_RATIO

    chunks_key = f"jobs/{job_id}/chunks.json"
    storage.put_json(bucket, chunks_key, [asdict(chunk) for chunk in chunks])

    return {
        "jobId": job_id,
        "bucket": bucket,
        "chunksKey": chunks_key,
        "chunkCount": len(chunks),
        "pageCount": len(pages),
        "emptyPages": empty,
        # El flujo debe cortar aquí si es escaneado o no hay texto: no tiene sentido pagar embeddings de nada.
        "scanned": scanned or not chunks,
    }
