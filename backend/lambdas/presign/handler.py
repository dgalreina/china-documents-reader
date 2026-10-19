"""Punto de entrada de AWS Lambda detrás de API Gateway (HTTP API): POST /uploads  {"filename": "libro.pdf", "source"?, "title"?, "replaceSource"?}

Hace dos cosas: (1) firma un permiso temporal para subir el PDF a S3 y (2) APUNTA EL TRABAJO en DynamoDB (estado UPLOADING, nombre del
libro, si es un reproceso). Cuando el PDF llegue a S3, Step Functions arranca solo y lee ese registro: así el `source` y el reemplazo
no tienen que viajar dentro del propio fichero.

Responde en el formato que espera API Gateway: {"statusCode", "headers", "body"}. Los nombres de bucket y tabla vienen de variables de
entorno que pone el CDK (UPLOAD_BUCKET, JOBS_TABLE). El CORS y el login (Cognito) se configuran en API Gateway, no aquí.
"""

import base64
import json
import os
import time
from datetime import datetime, timezone

from .signer import InvalidUpload, create_upload, resolve_source, resolve_title

HEADERS = {"Content-Type": "application/json"}
JOB_TTL_DAYS = 30  # DynamoDB borra el registro solo tras este tiempo (atributo expiresAt)


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


def handler(event: dict, _context=None, s3_client=None, jobs_table=None) -> dict:
    try:
        data = _parse_body(event)
        filename = data.get("filename")
        source = resolve_source(data.get("source"), filename)
        title = resolve_title(data.get("title"), filename)
        replace_source = data.get("replaceSource", False)
        if not isinstance(replace_source, bool):
            raise InvalidUpload("replaceSource debe ser true o false.")

        upload = create_upload(s3_client or _s3_client(), os.environ["UPLOAD_BUCKET"], filename)
        # Se apunta el trabajo ANTES de devolver la URL: si el PDF llegara a S3 sin registro, el flujo no sabría de qué libro es.
        (jobs_table or _jobs_table()).put_item(
            Item={
                "jobId": upload["jobId"],
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
