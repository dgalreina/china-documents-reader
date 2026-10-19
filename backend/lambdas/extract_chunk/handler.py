"""Punto de entrada de AWS Lambda para el paso 1-2: del PDF en S3 a los chunks en S3.

event  = {"jobId": "...", "bucket": "...", "key": "uploads/<jobId>/libro.pdf"}
return = {"jobId", "bucket", "chunksKey", "chunkCount", "pageCount", "emptyPages", "scanned", "hasCover"}

Además deja la portada (la primera página como JPEG pequeño) en `jobs/<jobId>/cover.jpg`: es opcional, si no se puede dibujar el análisis sigue igual.

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
from .cover import COVER_KEY_NAME, render_cover
from .pdf_reader import extract_pages

# Si más de la mitad de las páginas no tiene texto, es un PDF escaneado (imágenes): hará falta OCR (Textract).
SCANNED_RATIO = 0.5


def cover_key(job_id: str) -> str:
    return f"jobs/{job_id}/{COVER_KEY_NAME}"


def _try_cover(pdf_path: Path) -> bytes | None:
    """La portada es un extra: si el PDF no se deja dibujar (cifrado, dañado...) el libro se analiza igual, solo que sin miniatura."""
    try:
        return render_cover(pdf_path)
    except Exception as error:  # noqa: BLE001
        print(f"extract_chunk: no se pudo dibujar la portada ({type(error).__name__})")
        return None


def handler(event: dict, _context=None, storage: Storage | None = None) -> dict:
    storage = storage or S3Storage()
    job_id, bucket, key = event["jobId"], event["bucket"], event["key"]

    # /tmp es el único sitio donde una Lambda puede escribir: se descarga el PDF y se borra al salir.
    with tempfile.TemporaryDirectory() as tmp:
        pdf_path = Path(tmp) / "input.pdf"
        storage.download(bucket, key, pdf_path)
        pages = extract_pages(pdf_path)
        cover = _try_cover(pdf_path)  # con el PDF aún en /tmp

    chunks = chunk_pages(pages)
    empty = sum(1 for page in pages if not page.text)
    scanned = bool(pages) and empty / len(pages) > SCANNED_RATIO

    chunks_key = f"jobs/{job_id}/chunks.json"
    storage.put_json(bucket, chunks_key, [asdict(chunk) for chunk in chunks])
    if cover is not None:
        storage.put_bytes(bucket, cover_key(job_id), cover, "image/jpeg")

    return {
        "jobId": job_id,
        "bucket": bucket,
        "chunksKey": chunks_key,
        "chunkCount": len(chunks),
        "pageCount": len(pages),
        "emptyPages": empty,
        # El flujo debe cortar aquí si es escaneado o no hay texto: no tiene sentido pagar embeddings de nada.
        "scanned": scanned or not chunks,
        "hasCover": cover is not None,
    }
