"""Puntos de entrada de AWS Lambda para embed_label. Dos, porque Step Functions reparte el libro en tandas:

1. `period_handler`: UNA vez por libro. Lee el arranque de los chunks y guarda el periodo general (jobs/<jobId>/period.json).
2. `handler`: una vez por TANDA de chunks (varias en paralelo). Etiqueta, geocodifica y calcula embeddings de su trozo y lo deja
   en jobs/<jobId>/labeled/<inicio>.json. Devuelve solo un resumen pequeño (los vectores no caben entre pasos: ver shared/storage.py).

Por qué en tandas: un libro de 2.100 chunks tarda ≈ 30 min en serie y una Lambda dura como máximo 15. Con tandas de 100 chunks cada
una tarda 1-2 min, y el contador de uso de cada tanda se devuelve para sumar el coste del trabajo entero.
"""

import os

from shared.storage import S3Storage, Storage
from shared.usage import TRACKER

from ..extract_chunk.chunker import Chunk
from .labeler import DocumentPeriod
from .pipeline import DEFAULT_LABEL_MODEL, embed_and_label, infer_period_from_chunks, make_client


def _read_chunks(storage: Storage, bucket: str, chunks_key: str) -> list[Chunk]:
    return [Chunk(**c) for c in storage.get_json(bucket, chunks_key)]


def period_handler(event: dict, _context=None, storage: Storage | None = None, client=None) -> dict:
    """event = {"jobId", "bucket", "chunksKey"}  ->  {"jobId", "bucket", "periodKey", "period": {...} | None}"""
    storage = storage or S3Storage()
    job_id, bucket = event["jobId"], event["bucket"]
    chunks = _read_chunks(storage, bucket, event["chunksKey"])
    model = os.environ.get("LABEL_MODEL", DEFAULT_LABEL_MODEL)
    period = infer_period_from_chunks(chunks, client or make_client(), model)
    period_key = f"jobs/{job_id}/period.json"
    storage.put_json(bucket, period_key, period.model_dump() if period else None)
    return {"jobId": job_id, "bucket": bucket, "periodKey": period_key, "period": period.model_dump() if period else None}


def handler(event: dict, _context=None, storage: Storage | None = None, client=None, geocoder=None) -> dict:
    """event = {"jobId", "bucket", "chunksKey", "periodKey", "start", "size"}: procesa chunks[start : start + size].

    return = {"jobId", "bucket", "labeledKey", "start", "chunks", "ready", "pending", "located", "usage"}
    """
    storage = storage or S3Storage()
    job_id, bucket = event["jobId"], event["bucket"]
    start, size = int(event["start"]), int(event["size"])
    chunks = _read_chunks(storage, bucket, event["chunksKey"])[start : start + size]

    period_data = storage.get_json(bucket, event["periodKey"]) if event.get("periodKey") else None
    period = DocumentPeriod(**period_data) if period_data else None

    TRACKER.reset()  # una Lambda "caliente" se reutiliza: el contador de esta tanda empieza de cero
    result = embed_and_label(chunks, client=client, geocoder=geocoder, document_period=period, infer_period=False)

    labeled_key = f"jobs/{job_id}/labeled/{start:06d}.json"
    storage.put_json(bucket, labeled_key, {"ready": result.ready, "pending": result.pending})
    return {
        "jobId": job_id,
        "bucket": bucket,
        "labeledKey": labeled_key,
        "start": start,
        "chunks": len(chunks),
        "ready": len(result.ready),
        "pending": len(result.pending),
        "located": sum(1 for doc in result.ready if "ubicacion" in doc),
        "usage": TRACKER.summary(),
    }
