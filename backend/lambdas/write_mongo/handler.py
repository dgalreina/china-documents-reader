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


def _sum_counts(items: list[dict], key: str) -> int:
    return sum(int(item.get(key, 0) or 0) for item in items)


def finalize_handler(event: dict, _context=None) -> dict:
    """Último paso: suma los recuentos y el coste de TODAS las tandas y, si es un reproceso, borra lo de ejecuciones anteriores.

    event = {"jobId", "source", "replaceSource"?, "period"?, "embedResults": [...], "hitosResults": [...], "writeResults": [...]}
    Devuelve un resumen pequeño que Step Functions guarda en DynamoDB (estado DONE).
    """
    embed, extracted, written = event.get("embedResults", []), event.get("hitosResults", []), event.get("writeResults", [])
    usages = [r.get("usage", {}) for r in [event.get("period") or {}, *embed, *extracted, *written]]
    cost = round(sum(float(u.get("estimatedCostUsd", 0) or 0) for u in usages), 4)
    unknown = sorted({m for u in usages for m in u.get("unknownModels", [])})

    people: dict[str, int] = {}
    for item in written:
        for name, count in (item.get("personajes") or {}).items():
            people[name] = people.get(name, 0) + int(count)

    summary = {
        "jobId": event["jobId"],
        "chunks": _sum_counts(embed, "chunks"),
        "chunksListos": _sum_counts(embed, "ready"),
        "chunksPendientes": _sum_counts(embed, "pending"),
        "chunksConUbicacion": _sum_counts(embed, "located"),
        "hitosExtraidos": _sum_counts(extracted, "hitos"),
        "hitosEscritos": _sum_counts(written, "hitosEscritos"),
        "duplicadosDescartados": _sum_counts(written, "duplicadosDescartados"),
        "personajes": people,
        "costeEstimadoUsd": cost,
        "modelosSinPrecio": unknown,
        "limpieza": cleanup_handler({"jobId": event["jobId"], "source": event["source"], "replaceSource": event.get("replaceSource")}),
    }
    return summary
