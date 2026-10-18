"""Paso final del flujo: guarda el PDF del libro de forma PERMANENTE y borra sus traducciones antiguas.

event  = {"bucket": "<bucket de subidas>", "key": "uploads/<jobId>/libro.pdf", "source": "<slug del libro>"}
return = {"booksKey": "books/<slug>/original.pdf", "translationsRemoved": N}

Por qué existe: el PDF que sube el usuario cae en `uploads/<jobId>/...`, que caduca a los 30 días como todo lo intermedio. Pero el visor y el
traductor necesitan el PDF mientras el libro exista, así que se copia (dentro de S3, sin bajarlo) al bucket de libros, que NO caduca, con una
clave que lleva el nombre del libro: `books/<slug>/original.pdf`. Esa es la clave que busca `translate_page`.

Si se reprocesa un libro con otro PDF bajo el mismo `source`, las traducciones guardadas (`translations/<slug>/`) son del PDF anterior y se
servirían mal: por eso se borran aquí, DESPUÉS de copiar el PDF nuevo (si la copia falla, no se pierde nada). No toca Mongo.
"""

import os

from shared.storage import S3Storage, Storage

from ..presign.signer import SOURCE_PATTERN


def handler(event: dict, _context=None, storage: Storage | None = None) -> dict:
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
    return {"booksKey": books_key, "translationsRemoved": removed}
