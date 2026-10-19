"""Punto de entrada de AWS Lambda detrás de API Gateway (HTTP API): DELETE /books/{slug}  ->  borra el libro y TODO lo que es solo suyo.

Qué se borra y qué no está explicado en `purge.py`. Aquí van las protecciones de la petición:
  - el libro tiene que existir (en el catálogo o con algún trabajo suyo);
  - no se borra mientras se está analizando o subiendo (409): el análisis seguiría escribiendo datos de un libro que ya no está;
  - la base de Mongo es la que decide el despliegue (MONGO_TARGET, dev por defecto), nunca la petición.
La petición ya llega autenticada (Cognito) y limitada a 2 por segundo.
"""

import json
import os
import time

from shared.jobstate import busy_job, scan_all
from shared.storage import S3Storage, Storage

from ..presign.signer import SOURCE_PATTERN
from ..write_mongo.store import cached_database
from .purge import purge_mongo, purge_storage

HEADERS = {"Content-Type": "application/json"}


def _response(status: int, body: dict) -> dict:
    return {"statusCode": status, "headers": HEADERS, "body": json.dumps(body, ensure_ascii=False)}


def _books_table():
    import boto3

    return boto3.resource("dynamodb").Table(os.environ["BOOKS_TABLE"])


def _jobs_table():
    import boto3

    return boto3.resource("dynamodb").Table(os.environ["JOBS_TABLE"])


def handler(
    event: dict,
    _context=None,
    db=None,
    books_table=None,
    jobs_table=None,
    storage: Storage | None = None,
    now: float | None = None,
) -> dict:
    slug = (event.get("pathParameters") or {}).get("slug", "")
    if event.get("routeKey") != "DELETE /books/{slug}":
        return _response(404, {"error": "Ruta desconocida."})
    if not SOURCE_PATTERN.match(slug):
        return _response(400, {"error": "Identificador de libro no válido."})

    books_table = books_table or _books_table()
    jobs_table = jobs_table or _jobs_table()
    try:
        item = books_table.get_item(Key={"slug": slug}).get("Item")
        jobs = [job for job in scan_all(jobs_table) if job.get("source") == slug]
        if not item and not jobs:
            return _response(404, {"error": "No existe ese libro."})
        if busy_job(jobs_table, slug, now if now is not None else time.time()):
            return _response(409, {"error": "El libro se está analizando o subiendo. Espera a que termine para borrarlo."})

        mongo = purge_mongo(db if db is not None else cached_database(os.environ.get("MONGO_TARGET", "dev")), slug)
        files = purge_storage(storage or S3Storage(), os.environ["BOOKS_BUCKET"], os.environ["UPLOAD_BUCKET"], slug, [job["jobId"] for job in jobs])
        for job in jobs:
            jobs_table.delete_item(Key={"jobId": job["jobId"]})
        books_table.delete_item(Key={"slug": slug})  # el catálogo, el ÚLTIMO: si algo falló antes, el libro sigue en la lista y se puede reintentar
    except Exception as error:  # noqa: BLE001 — al navegador solo un aviso genérico; el detalle queda en los logs
        print(f"delete_book falló ({slug}): {type(error).__name__}")
        return _response(502, {"error": "No se pudo borrar el libro del todo. Vuelve a intentarlo: es seguro repetirlo."})
    return _response(200, {"deleted": {"mongo": mongo, "archivos": files, "trabajos": len(jobs)}})
