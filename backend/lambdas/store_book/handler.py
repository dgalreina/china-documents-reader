"""Último paso del ANÁLISIS: apunta en el catálogo que el libro ya está analizado, con sus estadísticas.

event  = {"source": "<slug>", "jobId": "<id del análisis>", "summary"?: {resumen de Finalize}}
return = {"catalogued": bool}

El PDF y la portada ya están guardados de forma permanente desde la subida (los deja `ingest` en `books/<slug>/`): aquí no se copia nada. Solo se ACTUALIZA el registro
del libro (no se reemplaza entero: conserva su título, páginas y portada) con `analysis: "done"`, las estadísticas del resumen y el id del análisis. Si el libro ya no
está en el catálogo (se borró mientras tanto) no se crea de nuevo. No toca Mongo.
"""

import os
from datetime import datetime, timezone
from decimal import Decimal

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


def stats_from_summary(summary: dict | None) -> dict:
    summary = summary or {}
    return {
        "chunks": _number(summary.get("chunks", 0)),
        "chunksListos": _number(summary.get("chunksListos", 0)),
        "chunksPendientes": _number(summary.get("chunksPendientes", 0)),
        "hitos": _number(summary.get("hitosEscritos", 0)),
        "personajes": len(summary.get("personajes") or {}),
        "costeEstimadoUsd": _number(summary.get("costeEstimadoUsd", 0)),
    }


def handler(event: dict, _context=None, books_table=None) -> dict:
    source, job_id = event["source"], str(event.get("jobId") or "")
    if not SOURCE_PATTERN.match(str(source)):
        raise ValueError(f"source no válido: {source!r}")

    # Sin tabla configurada (pruebas locales) no se apunta nada; en AWS siempre hay tabla.
    table = books_table or (_books_table() if os.environ.get("BOOKS_TABLE") else None)
    if table is None:
        return {"catalogued": False}
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    try:
        table.update_item(
            Key={"slug": source},
            UpdateExpression="SET analysis = :a, stats = :s, jobId = :j, analyzedAt = :t, updatedAt = :t",
            ConditionExpression="attribute_exists(slug)",
            ExpressionAttributeValues={":a": "done", ":s": stats_from_summary(event.get("summary")), ":j": job_id, ":t": now},
        )
    except Exception as error:  # noqa: BLE001
        if getattr(error, "response", {}).get("Error", {}).get("Code") == "ConditionalCheckFailedException":
            return {"catalogued": False}  # el libro se borró mientras se analizaba: no se vuelve a crear
        raise
    return {"catalogued": True}
