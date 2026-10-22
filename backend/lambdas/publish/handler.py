"""Punto de entrada de AWS Lambda para publicar un libro en producción. Dos funciones:

  `handler` (detrás de API Gateway, ya autenticada con Cognito y limitada a 2 por segundo):
    POST   /books/{slug}/publish   cuerpo {"dryRun": true|false}  ->  apunta un trabajo (kind "publish") y lanza `run_handler` SIN esperarla (202 + jobId).
                                   `dryRun` es OBLIGATORIO y booleano: nunca se escribe por omisión. true = vista previa; false = publicar de verdad.
    DELETE /books/{slug}/publish   ->  despublica (borra de producción lo de ese libro con nuestra marca). Es rápido: se hace en la propia petición.
  `run_handler`: hace el trabajo largo (leer dev, deduplicar contra producción con el modelo, escribir). API Gateway corta a los 29 s, por eso no va en la petición.

El resultado queda en el registro del trabajo (`summary`), que la web consulta con GET /jobs. Al publicar de verdad se apunta en el catálogo `published`.
Qué se copia y qué no está en `core.py`.
"""

import json
import os
import time
import uuid
from datetime import datetime, timezone

from shared.jobstate import busy_job

from ..presign.signer import SOURCE_PATTERN
from ..write_mongo.store import ContractError, cached_database
from .core import prod_target, publish_book, unpublish_book

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


def _lambda_client():
    import boto3

    return boto3.client("lambda")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _parse_dry_run(event: dict) -> bool | None:
    try:
        body = json.loads(event.get("body") or "{}")
    except ValueError:
        return None
    value = body.get("dryRun") if isinstance(body, dict) else None
    return value if isinstance(value, bool) else None


def handler(event: dict, _context=None, books_table=None, jobs_table=None, lambda_client=None, target=None, now: float | None = None, new_id=None) -> dict:
    route = event.get("routeKey")
    if route not in ("POST /books/{slug}/publish", "DELETE /books/{slug}/publish"):
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
        return _response(409, {"error": "Este libro se está procesando ahora mismo. Espera a que termine."})

    if route.startswith("DELETE"):
        try:
            removed = unpublish_book(target or prod_target(), slug)
            books_table.update_item(Key={"slug": slug}, UpdateExpression="REMOVE published")
        except Exception as error:  # noqa: BLE001 — al navegador solo un aviso; el detalle, en los logs
            print(f"publish: no se pudo despublicar ({slug}): {type(error).__name__}")
            return _response(502, {"error": "No se pudo quitar el libro de producción. Es seguro repetirlo."})
        return _response(200, {"removed": removed})

    dry_run = _parse_dry_run(event)
    if dry_run is None:
        return _response(400, {"error": 'Falta "dryRun" (true para la vista previa, false para publicar).'})
    if item.get("analysis", "done") != "done":
        return _response(409, {"error": "El libro no está analizado: no hay nada que publicar."})

    job_id = new_id() if new_id else uuid.uuid4().hex
    created = _now()
    jobs_table.put_item(
        Item={
            "jobId": job_id,
            "kind": "publish",
            "status": "RUNNING",
            "dryRun": dry_run,
            "source": slug,
            "title": item.get("title", slug),
            "filename": item.get("filename", ""),
            "createdAt": created,
            "startedAt": created,
            "expiresAt": int(time.time()) + JOB_TTL_DAYS * 86400,
        }
    )
    try:
        (lambda_client or _lambda_client()).invoke(
            FunctionName=os.environ["PUBLISH_RUN_FUNCTION"],
            InvocationType="Event",  # asíncrona: no espera a que termine
            Payload=json.dumps({"jobId": job_id, "slug": slug, "dryRun": dry_run}).encode(),
        )
    except Exception as error:  # noqa: BLE001
        print(f"publish: no se pudo lanzar el trabajo ({slug}): {type(error).__name__}")
        _finish(jobs_table, job_id, error="No se pudo arrancar la publicación. Inténtalo de nuevo.")
        return _response(502, {"error": "No se pudo arrancar la publicación. Inténtalo de nuevo."})
    return _response(202, {"jobId": job_id, "dryRun": dry_run})


def _finish(jobs_table, job_id: str, summary: dict | None = None, error: str | None = None) -> None:
    names, values = {"#s": "status"}, {":s": "FAILED" if error else "DONE", ":t": _now()}
    expression = "SET #s = :s, finishedAt = :t"
    if error:
        names["#e"], values[":e"] = "error", error
        expression += ", #e = :e"
    if summary is not None:
        values[":m"] = json.dumps(summary, ensure_ascii=False)
        expression += ", summary = :m"
    jobs_table.update_item(Key={"jobId": job_id}, UpdateExpression=expression, ExpressionAttributeNames=names, ExpressionAttributeValues=values)


def run_handler(event: dict, _context=None, jobs_table=None, books_table=None, dev_db=None, target=None, client=None) -> dict:
    """Invocada por `handler`. Los errores no se relanzan (Lambda reintentaría una invocación asíncrona y publicaría dos veces): quedan en el trabajo."""
    job_id, slug, dry_run = event["jobId"], event["slug"], bool(event.get("dryRun", True))
    jobs_table = jobs_table or _jobs_table()
    try:
        target = target or prod_target()
        dev = dev_db if dev_db is not None else cached_database(os.environ.get("MONGO_TARGET", "dev"))
        report = publish_book(dev, target, slug, job_id, write=not dry_run, client=client)
    except ContractError as error:
        _finish(jobs_table, job_id, error=f"No se puede publicar: {error}"[:400])
        return {"ok": False}
    except Exception as error:  # noqa: BLE001
        print(f"publish: falló ({slug}): {type(error).__name__}: {str(error)[:300]}")
        _finish(jobs_table, job_id, error="La publicación falló. Es seguro repetirla.")
        return {"ok": False}
    summary = report.summary()
    if not dry_run:
        (books_table or _books_table()).update_item(
            Key={"slug": slug},
            UpdateExpression="SET published = :p",
            ConditionExpression="attribute_exists(slug)",
            ExpressionAttributeValues={":p": {"at": _now(), "chunks": report.chunks, "hitos": report.hitos, "personajes": report.personajes, "jobId": job_id}},
        )
    _finish(jobs_table, job_id, summary=summary)
    return {"ok": True}
