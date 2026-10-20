"""Punto de entrada de AWS Lambda para el paso 1-2: del PDF en S3 a los chunks en S3.

event  = {"jobId": "...", "bucket": "<intermedios>", "key": "books/<slug>/original.pdf", "pdfBucket"?: "<donde está el PDF>"}
         El PDF se lee de `pdfBucket` (el bucket permanente de libros; si falta, de `bucket`, como antes) y los ficheros intermedios van a `bucket`, que caduca.
return = {"jobId", "bucket", "chunksKey", "chunkCount", "pageCount", "emptyPages", "scanned"}

No devuelve los chunks: los deja en `jobs/<jobId>/chunks.json` y devuelve dónde están, porque Step Functions no admite
más de 256 KB entre pasos (ver shared/storage.py). Los pasos siguientes leen ese fichero.


Se lanza el handler con el event, storage.
de event sacamos el job_id, bucket y key
se crea archivo temporal tmp de la lambda, se crea la ruta de tmp, se descarga ahi lo que hay en s3, y se divide en paginas

de las paginas saco los chunks
creo la chunk key
meto los chunks en storage

devuelvo un resumen
"""

import tempfile
from dataclasses import asdict
from pathlib import Path

from shared.storage import S3Storage, Storage

from .chunker import chunk_pages
from ..ocr.core import ocr_key
from ..ocr.core import page_text as ocr_page_text
from .pdf_reader import Page, extract_pages

# Si más de la mitad de las páginas no tiene texto, es un PDF escaneado (imágenes): hará falta OCR (Textract).
SCANNED_RATIO = 0.5


def _with_ocr(pages: list[Page], storage: Storage, pdf_bucket: str, key: str) -> list[Page]:
    """Si el libro tiene OCR (`books/<slug>/ocr.json`), las páginas SIN texto toman el del OCR: así un escaneado ya se puede analizar. Las que sí tienen texto, no se tocan."""
    parts = key.split("/")
    if len(parts) < 3 or parts[0] != "books" or not any(not page.text for page in pages):
        return pages
    document = storage.try_get_json(pdf_bucket, ocr_key(parts[1]))
    if not document:
        return pages
    return [page if page.text else Page(number=page.number, text=ocr_page_text(document, page.number).strip()) for page in pages]


def handler(event: dict, _context=None, storage: Storage | None = None) -> dict:
    storage = storage or S3Storage()
    job_id, bucket, key = event["jobId"], event["bucket"], event["key"]
    pdf_bucket = event.get("pdfBucket") or bucket  # dónde está el PDF; los intermedios se escriben siempre en `bucket`

    # /tmp es el único sitio donde una Lambda puede escribir: se descarga el PDF y se borra al salir.
    with tempfile.TemporaryDirectory() as tmp:
        pdf_path = Path(tmp) / "input.pdf"
        storage.download(pdf_bucket, key, pdf_path)
        pages = extract_pages(pdf_path)
    pages = _with_ocr(pages, storage, pdf_bucket, key)

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
