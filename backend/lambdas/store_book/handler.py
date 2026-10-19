"""Paso final del flujo: guarda el PDF del libro de forma PERMANENTE, borra sus traducciones antiguas y apunta el libro en el catálogo.

event  = {"bucket", "key": "uploads/<jobId>/libro.pdf", "source": "<slug>", "title"?, "filename"?, "jobId"?, "pageCount"?, "createdAt"?, "summary"?}
return = {"booksKey": "books/<slug>/original.pdf", "translationsRemoved": N, "catalogued": bool, "hasCover": bool}

Por qué existe: el PDF que sube el usuario cae en `uploads/<jobId>/...`, que caduca a los 30 días como todo lo intermedio. Pero el visor y el
traductor necesitan el PDF mientras el libro exista, así que se copia (dentro de S3, sin bajarlo) al bucket de libros, que NO caduca, con una
clave que lleva el nombre del libro: `books/<slug>/original.pdf`. Esa es la clave que busca `translate_page` y la que firma `books`.

Si se reprocesa un libro con otro PDF bajo el mismo `source`, las traducciones guardadas (`translations/<slug>/`) son del PDF anterior y se
servirían mal: por eso se borran aquí, DESPUÉS de copiar el PDF nuevo (si la copia falla, no se pierde nada).

La PORTADA (`jobs/<jobId>/cover.jpg`, que dibuja extract_chunk) se copia a `books/<slug>/cover.jpg`; si este trabajo no tiene, se borra la de un PDF anterior
(no puede quedar la portada de otra edición).

El catálogo (tabla BOOKS_TABLE, un registro por libro, permanente) se escribe lo ÚLTIMO: un libro solo aparece en la lista cuando su PDF ya
está guardado. Un reproceso lo reemplaza entero. No toca Mongo.
"""

import os
from datetime import datetime, timezone
from decimal import Decimal

from shared.storage import S3Storage, Storage

from ..extract_chunk.cover import COVER_KEY_NAME
from ..presign.signer import SOURCE_PATTERN


def _books_table():
    import boto3

    return boto3.resource("dynamodb").Table(os.environ["BOOKS_TABLE"])


def _number(value) -> int | Decimal:
    """DynamoDB no acepta float: los enteros van como int y los decimales (el coste) como Decimal."""
    if isinstance(value, bool):
        return int(value)
    if isinstance(value, int):
        return value
    try:
        return Decimal(str(round(float(value), 4)))
    except (TypeError, ValueError):
        return 0


def catalog_item(event: dict, has_cover: bool = False) -> dict:
    summary = event.get("summary") or {}
    stats = {
        "chunks": _number(summary.get("chunks", 0)),
        "chunksListos": _number(summary.get("chunksListos", 0)),
        "chunksPendientes": _number(summary.get("chunksPendientes", 0)),
        "hitos": _number(summary.get("hitosEscritos", 0)),
        "personajes": len(summary.get("personajes") or {}),
        "costeEstimadoUsd": _number(summary.get("costeEstimadoUsd", 0)),
    }
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    return {
        "slug": event["source"],
        "title": str(event.get("title") or event["source"]),
        "filename": str(event.get("filename") or ""),
        "jobId": str(event.get("jobId") or ""),
        "pageCount": int(event.get("pageCount") or 0),
        "status": "DONE",
        "hasCover": has_cover,
        "createdAt": str(event.get("createdAt") or now),
        "updatedAt": now,
        "stats": stats,
    }


def _copy_cover(storage: Storage, bucket: str, job_id: object, books_bucket: str, source: str) -> bool:
    """Copia la portada de este trabajo junto al PDF. Sin portada nueva, borra la vieja: no debe quedar la de otra edición del libro."""
    destination = f"books/{source}/{COVER_KEY_NAME}"
    origin = f"jobs/{job_id}/{COVER_KEY_NAME}" if job_id else None
    if origin and storage.exists(bucket, origin):
        storage.copy(bucket, origin, books_bucket, destination)
        return True
    storage.delete_prefix(books_bucket, destination)
    return False


def handler(event: dict, _context=None, storage: Storage | None = None, books_table=None) -> dict:
    bucket, key, source = event["bucket"], event["key"], event["source"]
    # El nombre del libro va dentro de una clave de S3 y de un prefijo que se borra: nada de '/', '..' ni cosas raras.
    if not SOURCE_PATTERN.match(str(source)):
        raise ValueError(f"source no válido: {source!r}")
    if not str(key).startswith("uploads/"):
        raise ValueError(f"clave de subida no válida: {key!r}")

    storage = storage or S3Storage()
    books_bucket = os.environ["BOOKS_BUCKET"]
    books_key = f"books/{source}/original.pdf"
    storage.copy(bucket, key, books_bucket, books_key)
    removed = storage.delete_prefix(books_bucket, f"translations/{source}/")
    has_cover = _copy_cover(storage, bucket, event.get("jobId"), books_bucket, source)

    # Sin tabla configurada (pruebas locales) no se apunta el libro; en AWS siempre hay tabla.
    table = books_table or (_books_table() if os.environ.get("BOOKS_TABLE") else None)
    if table is not None:
        table.put_item(Item=catalog_item(event, has_cover))
    return {"booksKey": books_key, "translationsRemoved": removed, "catalogued": table is not None, "hasCover": has_cover}
