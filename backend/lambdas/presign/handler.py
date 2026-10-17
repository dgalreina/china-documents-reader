"""Punto de entrada de AWS Lambda detrás de API Gateway (HTTP API): POST /uploads  {"filename": "libro.pdf"}

Responde en el formato que espera API Gateway: {"statusCode", "headers", "body"}. El bucket viene de la variable de entorno
UPLOAD_BUCKET, que pondrá el CDK. Los permisos de CORS (qué web puede llamar) se configuran en API Gateway, no aquí.
"""

import base64
import json
import os

from .signer import InvalidUpload, create_upload

HEADERS = {"Content-Type": "application/json"}


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


def handler(event: dict, _context=None, s3_client=None) -> dict:
    try:
        filename = _parse_body(event).get("filename")
        bucket = os.environ["UPLOAD_BUCKET"]
        return _response(200, create_upload(s3_client or _s3_client(), bucket, filename))
    except InvalidUpload as err:
        return _response(400, {"error": str(err)})
