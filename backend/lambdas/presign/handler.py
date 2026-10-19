"""Punto de entrada de AWS Lambda detrás de API Gateway (HTTP API): POST /uploads  {"filename": "libro.pdf", "source"?, "title"?, "replaceSource"?}

Hace tres cosas: (1) COMPRUEBA que no se pisa un libro sin querer, (2) firma un permiso temporal para subir el PDF a S3 y (3) APUNTA LA SUBIDA en
DynamoDB (kind "upload", estado UPLOADING, nombre del libro, si reemplaza uno existente). Subir NO analiza: cuando el PDF llega a S3, `ingest` lo
guarda de forma permanente, dibuja su portada y lo apunta en el catálogo como "sin analizar"; el análisis (que cuesta dinero) lo arranca el usuario
con `analyze`. El `source` y el reemplazo viajan en este registro y no dentro del fichero.

Si el libro ya existe hay que decir que se reemplaza (409 si no): reemplazar el PDF borra su análisis, hitos y traducciones. Y no se sube sobre un libro
que se está analizando o subiendo (409).

Responde en el formato que espera API Gateway: {"statusCode", "headers", "body"}. Los nombres de bucket y tabla vienen de variables de
entorno que pone el CDK (UPLOAD_BUCKET, JOBS_TABLE). El CORS y el login (Cognito) se configuran en API Gateway, no aquí.
"""

import base64
import json
import os
import time
from datetime import datetime, timezone

from shared.jobstate import busy_job

from .signer import InvalidUpload, create_upload, resolve_source, resolve_title

HEADERS = {"Content-Type": "application/json"}
JOB_TTL_DAYS = 30  # DynamoDB borra el registro solo tras este tiempo (atributo expiresAt)


class UploadConflict(Exception):
    """La subida chocaría con lo que ya hay (se responde 409 con el mensaje)."""


def _response(status: int, body: dict) -> dict:
    return {"statusCode": status, "headers": HEADERS, "body": json.dumps(body, ensure_ascii=False)}


def _parse_body(event: dict) -> dict:
    raw = event.get("body")
    if raw is None:
        raise InvalidUpload("Falta el cuerpo de la petición.")
    if event.get("isBase64Encoded"):
        raw = base64.b64decode(raw).decode("utf-8")
    try:
        data = json.loads(raw)
    except (TypeError, ValueError) as err:
        raise InvalidUpload("El cuerpo debe ser JSON.") from err
    if not isinstance(data, dict):
        raise InvalidUpload("El cuerpo debe ser un objeto JSON con 'filename'.")
    return data


def _s3_client():
    import boto3
    from botocore.config import Config

    # Firma v4 y dirección por bucket: con la global, S3 puede redirigir la subida y el navegador no sigue redirecciones de POST.
    return boto3.client("s3", config=Config(signature_version="s3v4", s3={"addressing_style": "virtual"}))


def _jobs_table():
    import boto3

    return boto3.resource("dynamodb").Table(os.environ["JOBS_TABLE"])


def _books_table():
    if not os.environ.get("BOOKS_TABLE"):  # sin tabla configurada (pruebas locales) no hay con qué comparar
        return None
    import boto3

    return boto3.resource("dynamodb").Table(os.environ["BOOKS_TABLE"])


def _check_conflicts(slug: str, replace_source: bool, books_table, jobs_table, now: float) -> None:
    if books_table is None:
        return
    if books_table.get_item(Key={"slug": slug}).get("Item") and not replace_source:
        raise UploadConflict(
            "Ya existe un libro con ese identificador. Para reemplazar su PDF hay que confirmarlo: se borrarán su análisis, sus hitos y sus traducciones."
        )
    if busy_job(jobs_table, slug, now):
        raise UploadConflict("Ese libro se está analizando o subiendo. Espera a que termine.")


def handler(event: dict, _context=None, s3_client=None, jobs_table=None, books_table=None, now: float | None = None) -> dict:
    try:
        data = _parse_body(event)
        filename = data.get("filename")
        source = resolve_source(data.get("source"), filename)
        title = resolve_title(data.get("title"), filename)
        replace_source = data.get("replaceSource", False)
        if not isinstance(replace_source, bool):
            raise InvalidUpload("replaceSource debe ser true o false.")

        jobs_table = jobs_table or _jobs_table()
        _check_conflicts(source, replace_source, books_table if books_table is not None else _books_table(), jobs_table, now if now is not None else time.time())

        upload = create_upload(s3_client or _s3_client(), os.environ["UPLOAD_BUCKET"], filename)
        # Se apunta el trabajo ANTES de devolver la URL: si el PDF llegara a S3 sin registro, el flujo no sabría de qué libro es.
        jobs_table.put_item(
            Item={
                "jobId": upload["jobId"],
                "kind": "upload",
                "status": "UPLOADING",
                "filename": filename,
                "source": source,
                "title": title,
                "replaceSource": replace_source,
                "createdAt": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                "expiresAt": int(time.time()) + JOB_TTL_DAYS * 86400,
            }
        )
        return _response(200, {**upload, "source": source, "title": title, "replaceSource": replace_source})
    except InvalidUpload as err:
        return _response(400, {"error": str(err)})
    except UploadConflict as err:
        return _response(409, {"error": str(err)})
