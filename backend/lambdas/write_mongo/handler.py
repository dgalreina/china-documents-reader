"""Puntos de entrada de AWS Lambda para write_mongo, el ÚNICO paso que escribe en MongoDB. Tres, uno por fase del trabajo:

- `chunks_handler`   una vez por tanda (varias a la vez): escribe los chunks de `labeled/<n>.json` en book_chunks.
- `hitos_handler`    una vez por tanda, DE UNA EN UNA y en orden: guarda solo lo nuevo, resuelve personajes y escribe hitos. No puede
                     ir en paralelo: dos tandas a la vez podrían crear dos fichas de la misma persona o no ver un duplicado.
- `cleanup_handler`  una vez, al final, solo si el trabajo es un reproceso: borra lo de ejecuciones anteriores de ese libro.

DESTINO: dev o producción lo decide SOLO la variable de entorno MONGO_TARGET del despliegue (por defecto "dev"); el evento no puede
cambiarlo. En la nube no hay confirmación interactiva como en el CLI, así que producción exige haberlo configurado a propósito.
"""

import os

from shared.storage import S3Storage, Storage
from shared.usage import TRACKER

from .store import Target, cleanup_old_runs, resolve_target, write_chunks, write_hitos


def _target() -> Target:
    return resolve_target(os.environ.get("MONGO_TARGET", "dev"))


def chunks_handler(event: dict, _context=None, storage: Storage | None = None) -> dict:
    """event = {"jobId", "bucket", "labeledKey", "source"}"""
    storage = storage or S3Storage()
    docs = storage.get_json(event["bucket"], event["labeledKey"])["ready"]
    report = write_chunks(_target(), event["source"], docs, write=True, job_id=event["jobId"])
    return {"jobId": event["jobId"], "target": report.target, "chunksEscritos": report.written}


def hitos_handler(event: dict, _context=None, storage: Storage | None = None, client=None) -> dict:
    """event = {"jobId", "bucket", "hitosKey", "source", "replaceSource"?}"""
    storage = storage or S3Storage()
    hitos = storage.get_json(event["bucket"], event["hitosKey"])["hitos"]
    TRACKER.reset()
    report = write_hitos(
        _target(), event["source"], hitos, write=True, dedupe=True, personajes=True, client=client,
        replace_source=bool(event.get("replaceSource")), job_id=event["jobId"],
    )
    return {
        "jobId": event["jobId"],
        "target": report.target,
        "hitosEscritos": report.written,
        "duplicadosDescartados": len(report.duplicates),
        "personajes": report.personajes_stats,
        "usage": TRACKER.summary(),
    }


def cleanup_handler(event: dict, _context=None) -> dict:
    """event = {"jobId", "source", "replaceSource"?}. Sin replaceSource no borra nada."""
    if not event.get("replaceSource"):
        return {"jobId": event["jobId"], "limpieza": "no solicitada"}
    return {"jobId": event["jobId"], **cleanup_old_runs(_target(), event["source"], event["jobId"])}
