"""Preparar un libro recién subido (NO es el análisis y no cuesta nada): se ejecuta cuando el PDF llega a `uploads/<jobId>/` (lo arranca EventBridge).

event  = {"bucket": "<bucket de subidas>", "key": "uploads/<jobId>/libro.pdf"}
return = {"ok": bool, "slug", "pageCount", "hasCover"} (si falla, {"ok": False, "error"}; el motivo también queda en el registro de la subida)

Qué hace, en este orden:
  1. Comprueba que el PDF se puede abrir (no está dañado ni protegido con contraseña) y cuenta sus páginas.
  2. Dibuja la portada (la primera página): es opcional, si falla el libro se guarda igual.
  3. Guarda el PDF y la portada de forma PERMANENTE en `books/<slug>/` (la subida original caduca a los 30 días).
  4. Si REEMPLAZA un libro que ya existía: borra lo que se derivó del PDF anterior (hitos, personajes, chunks, traducciones), porque ya no corresponde a este PDF.
  5. Apunta el libro en el catálogo como `analysis: "none"` (sin analizar): ya se puede leer y traducir. El análisis lo arranca el usuario con `analyze`.

Subir solo se permite sobre un libro existente si la subida lo declaró (`replaceSource`, que `presign` comprueba): aquí se vuelve a comprobar, por si acaso.
Un fallo NO se reintenta solo (una subida mala no mejora): se marca FAILED con un motivo que la web enseña.
"""

import os
import re
import tempfile
from datetime import datetime, timezone
from pathlib import Path

from pypdf import PdfReader

from shared.storage import S3Storage, Storage

from ..delete_book.purge import purge_mongo
from ..extract_chunk.cover import COVER_KEY_NAME, render_cover
from ..presign.signer import SOURCE_PATTERN
from ..write_mongo.store import cached_database

JOB_ID = re.compile(r"^[0-9a-f]{32}$")


class IngestError(Exception):
    """El PDF no se puede aceptar; el mensaje se enseña al usuario tal cual."""


def _jobs_table():
    import boto3

    return boto3.resource("dynamodb").Table(os.environ["JOBS_TABLE"])


def _books_table():
    import boto3

    return boto3.resource("dynamodb").Table(os.environ["BOOKS_TABLE"])


def count_pages(path: Path) -> int:
    """Número de páginas, o IngestError si el PDF está dañado o protegido."""
    try:
        reader = PdfReader(str(path))
        if reader.is_encrypted:
            try:
                opened = reader.decrypt("")  # muchos PDFs "protegidos" lo están solo contra copiar, sin contraseña real
            except Exception:  # noqa: BLE001 — sin la librería de cifrado o con un cifrado raro: se trata como protegido
                opened = 0
            if not opened:
                raise IngestError("El PDF está protegido con contraseña: no se puede leer.")
        pages = len(reader.pages)
    except IngestError:
        raise
    except Exception as error:  # noqa: BLE001
        raise IngestError("No se pudo leer el PDF: puede estar dañado.") from error
    if pages < 1:
        raise IngestError("El PDF no tiene páginas.")
    return pages


def _try_cover(path: Path) -> bytes | None:
    try:
        return render_cover(path)
    except Exception as error:  # noqa: BLE001 — la portada es un extra
        print(f"ingest: no se pudo dibujar la portada ({type(error).__name__})")
        return None


def _finish(jobs_table, job_id: str, status: str, error: str | None = None, page_count: int | None = None) -> None:
    names = {"#s": "status"}
    values: dict = {":s": status, ":t": datetime.now(timezone.utc).isoformat(timespec="seconds")}
    expression = "SET #s = :s, finishedAt = :t"
    if error is not None:
        names["#e"] = "error"
        values[":e"] = error
        expression += ", #e = :e"
    if page_count is not None:
        values[":p"] = page_count
        expression += ", pageCount = :p"
    jobs_table.update_item(Key={"jobId": job_id}, UpdateExpression=expression, ExpressionAttributeNames=names, ExpressionAttributeValues=values)


def _ingest(job: dict, bucket: str, key: str, storage: Storage, books_table, db) -> dict:
    slug = job["source"]
    books_bucket = os.environ["BOOKS_BUCKET"]
    existing = books_table.get_item(Key={"slug": slug}).get("Item")
    if existing and not job.get("replaceSource"):
        raise IngestError("Ya existe un libro con ese identificador.")

    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "input.pdf"
        storage.download(bucket, key, path)
        pages = count_pages(path)
        cover = _try_cover(path)

    # El PDF y la portada nuevos, ya en su sitio permanente.
    storage.copy(bucket, key, books_bucket, f"books/{slug}/original.pdf")
    cover_key = f"books/{slug}/{COVER_KEY_NAME}"
    if cover is not None:
        storage.put_bytes(books_bucket, cover_key, cover, "image/jpeg")
    else:
        storage.delete_prefix(books_bucket, cover_key)  # no puede quedar la portada de otra edición

    if existing:  # lo derivado del PDF anterior ya no corresponde a este
        purge_mongo(db if db is not None else cached_database(os.environ.get("MONGO_TARGET", "dev")), slug)
        storage.delete_prefix(books_bucket, f"translations/{slug}/")

    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    books_table.put_item(
        Item={
            "slug": slug,
            "title": str(job.get("title") or slug),
            "filename": str(job.get("filename") or ""),
            "pageCount": pages,
            "analysis": "none",
            "hasCover": cover is not None,
            "createdAt": now,
            "updatedAt": now,
        }
    )
    storage.delete_prefix(bucket, f"uploads/{key.split('/')[1]}/")  # la copia original ya no hace falta (y caducaría igualmente)
    return {"slug": slug, "pageCount": pages, "hasCover": cover is not None}


def handler(event: dict, _context=None, storage: Storage | None = None, jobs_table=None, books_table=None, db=None) -> dict:
    bucket, key = event["bucket"], str(event["key"])
    parts = key.split("/")
    if len(parts) < 3 or parts[0] != "uploads" or not JOB_ID.match(parts[1]):
        return {"ok": False, "ignored": "clave de subida no válida"}
    job_id = parts[1]

    jobs_table = jobs_table or _jobs_table()
    job = jobs_table.get_item(Key={"jobId": job_id}).get("Item")
    if not job or job.get("kind") != "upload" or not SOURCE_PATTERN.match(str(job.get("source", ""))):
        return {"ok": False, "ignored": "sin registro de subida"}  # un PDF que no subió `presign`: no se toca
    if job.get("status") != "UPLOADING":
        return {"ok": False, "ignored": "subida ya procesada"}  # EventBridge puede entregar el mismo evento dos veces: la segunda no debe deshacer la primera

    try:
        info = _ingest(job, bucket, key, storage or S3Storage(), books_table or _books_table(), db)
    except IngestError as error:
        _finish(jobs_table, job_id, "FAILED", error=str(error))
        return {"ok": False, "error": str(error)}
    except Exception as error:  # noqa: BLE001 — el detalle queda en los logs; al usuario, un mensaje genérico
        print(f"ingest falló ({job.get('source')}): {type(error).__name__}")
        _finish(jobs_table, job_id, "FAILED", error="No se pudo preparar el libro. Vuelve a subirlo.")
        return {"ok": False, "error": "interno"}
    _finish(jobs_table, job_id, "DONE", page_count=info["pageCount"])
    return {"ok": True, **info}
