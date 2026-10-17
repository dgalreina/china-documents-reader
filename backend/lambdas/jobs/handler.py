"""Punto de entrada de AWS Lambda detrás de API Gateway: GET /jobs/{jobId}  ->  estado de un trabajo (para que el front consulte el progreso).

Lee el registro que apuntó `presign` y que va actualizando Step Functions: estado (UPLOADING, RUNNING, DONE, FAILED, NEEDS_OCR), nombre del
libro y, al terminar, el resumen (chunks, hitos, personajes, coste estimado). Solo lectura. La petición ya llega autenticada (Cognito).
"""

import json
import os
import re
from decimal import Decimal

JOB_ID = re.compile(r"^[0-9a-f]{32}$")
# Campos que se devuelven. El resto del registro (expiresAt, etc.) es interno.
PUBLIC_FIELDS = ("jobId", "status", "filename", "source", "replaceSource", "createdAt", "startedAt", "finishedAt", "error")
HEADERS = {"Content-Type": "application/json"}


def _jobs_table():
    import boto3

    return boto3.resource("dynamodb").Table(os.environ["JOBS_TABLE"])


def _json_default(value):
    if isinstance(value, Decimal):  # DynamoDB devuelve los números como Decimal
        return int(value) if value == value.to_integral_value() else float(value)
    raise TypeError(f"no serializable: {type(value)}")


def _response(status: int, body: dict) -> dict:
    return {"statusCode": status, "headers": HEADERS, "body": json.dumps(body, ensure_ascii=False, default=_json_default)}


def handler(event: dict, _context=None, jobs_table=None) -> dict:
    job_id = (event.get("pathParameters") or {}).get("jobId", "")
    if not JOB_ID.match(job_id):
        return _response(400, {"error": "jobId no válido."})
    item = (jobs_table or _jobs_table()).get_item(Key={"jobId": job_id}).get("Item")
    if not item:
        return _response(404, {"error": "No existe ese trabajo."})
    body = {key: item[key] for key in PUBLIC_FIELDS if key in item}
    if item.get("summary"):  # Step Functions lo guarda como texto JSON
        try:
            body["summary"] = json.loads(item["summary"])
        except (TypeError, ValueError):
            body["summary"] = None
    return _response(200, body)
