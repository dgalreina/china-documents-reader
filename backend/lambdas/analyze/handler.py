"""Punto de entrada de AWS Lambda detrás de API Gateway (HTTP API): POST /books/{slug}/analyze  ->  arranca el análisis de un libro ya subido.

El análisis (etiquetar por épocas, embeddings, hitos, personajes) es lo que cuesta dinero en OpenAI (~0,01 USD por página), por eso NO arranca al subir: lo pide el
usuario. Un libro sin analizar ya se puede leer y traducir.

Qué hace:
  1. Comprueba que el libro existe en el catálogo y que no hay ya otro análisis o una subida en curso de ese libro (409).
  2. Apunta el trabajo en DynamoDB (kind "analysis"), con el título y si REEMPLAZA un análisis anterior (el flujo borra entonces lo viejo al terminar).
  3. Arranca Step Functions con el PDF que ya está en `books/<slug>/original.pdf` (el nombre de la ejecución es el jobId: no se puede lanzar dos veces el mismo).
La petición ya llega autenticada (Cognito) y limitada a 2 por segundo.
"""

import json
import os
import time
import uuid
from datetime import datetime, timezone

from shared.jobstate import busy_job

from ..presign.signer import SOURCE_PATTERN

HEADERS = {"Content-Type": "application/json"}
JOB_TTL_DAYS = 30


def _response(status: int, body: dict) -> dict:
    return {"statusCode": status, "headers": HEADERS, "body": json.dumps(body, ensure_ascii=False)}


def _books_table():
    import boto3

    return boto3.resource("dynamodb").Table(os.environ["BOOKS_TABLE"])


def _jobs_table():
    import boto3

    return boto3.resource("dynamodb").Table(os.environ["JOBS_TABLE"])


def _states_client():
    import boto3

    return boto3.client("stepfunctions")


def handler(event: dict, _context=None, books_table=None, jobs_table=None, sfn=None, now: float | None = None, new_id=None) -> dict:
    if event.get("routeKey") != "POST /books/{slug}/analyze":
        return _response(404, {"error": "Ruta desconocida."})
    slug = (event.get("pathParameters") or {}).get("slug", "")
    if not SOURCE_PATTERN.match(slug):
        return _response(400, {"error": "Identificador de libro no válido."})

    books_table = books_table or _books_table()
    jobs_table = jobs_table or _jobs_table()
    item = books_table.get_item(Key={"slug": slug}).get("Item")
    if not item:
        return _response(404, {"error": "No existe ese libro."})
    if busy_job(jobs_table, slug, now if now is not None else time.time()):
        return _response(409, {"error": "Este libro ya se está analizando o subiendo. Espera a que termine."})

    job_id = new_id() if new_id else uuid.uuid4().hex
    # Un libro con análisis previo (o antiguo, sin el campo: todos los de antes de esta función estaban analizados) se REEMPLAZA; el primero, no.
    replace = item.get("analysis", "done") != "none"
    created = datetime.now(timezone.utc).isoformat(timespec="seconds")
    jobs_table.put_item(
        Item={
            "jobId": job_id,
            "kind": "analysis",
            "status": "RUNNING",
            "filename": item.get("filename", ""),
            "source": slug,
            "title": item.get("title", slug),
            "replaceSource": replace,
            "createdAt": created,
            "expiresAt": int(time.time()) + JOB_TTL_DAYS * 86400,
        }
    )
    try:
        (sfn or _states_client()).start_execution(
            stateMachineArn=os.environ["STATE_MACHINE_ARN"],
            name=job_id,
            # `bucket` es donde el flujo deja sus ficheros intermedios (caducan a los 30 días); el PDF se lee de `pdfBucket`, el bucket permanente de libros.
            input=json.dumps({"bucket": os.environ["UPLOAD_BUCKET"], "pdfBucket": os.environ["BOOKS_BUCKET"], "key": f"books/{slug}/original.pdf", "jobId": job_id}),
        )
    except Exception as error:  # noqa: BLE001 — el detalle queda en los logs; al usuario, un mensaje claro y el trabajo marcado como fallido
        print(f"analyze: no se pudo arrancar el flujo ({slug}): {type(error).__name__}")
        jobs_table.update_item(
            Key={"jobId": job_id},
            UpdateExpression="SET #s = :s, #e = :e, finishedAt = :t",
            ExpressionAttributeNames={"#s": "status", "#e": "error"},
            ExpressionAttributeValues={":s": "FAILED", ":e": "No se pudo arrancar el análisis. Inténtalo de nuevo.", ":t": created},
        )
        return _response(502, {"error": "No se pudo arrancar el análisis. Inténtalo de nuevo."})
    return _response(202, {"jobId": job_id, "replaceSource": replace})
