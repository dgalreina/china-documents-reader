"""Punto de entrada de AWS Lambda para extract_hitos: UNA tanda de chunks ya etiquetados -> sus hitos en S3.

event  = {"jobId", "bucket", "labeledKey": "jobs/<jobId>/labeled/<inicio>.json", "source": "<slug del libro>"}
return = {"jobId", "bucket", "hitosKey", "chunks", "hitos", "descartados", "stats", "usage"}

Lee los chunks listos de esa tanda (los que dejó embed_label) y deja los hitos, con sus personajes sin resolver, en
`jobs/<jobId>/hitos/<inicio>.json`. NO escribe en Mongo: la resolución de personajes y el "guardar solo lo nuevo" necesitan ir de
una tanda en una, en orden, y eso lo hace write_mongo (ver write_mongo/handler.py).
"""

from shared.storage import S3Storage, Storage
from shared.usage import TRACKER

from .pipeline import extract_hitos


def handler(event: dict, _context=None, storage: Storage | None = None, client=None, geocoder=None) -> dict:
    storage = storage or S3Storage()
    job_id, bucket, labeled_key = event["jobId"], event["bucket"], event["labeledKey"]
    chunks = storage.get_json(bucket, labeled_key)["ready"]

    TRACKER.reset()  # una Lambda "caliente" se reutiliza: el contador de esta tanda empieza de cero
    result = extract_hitos(chunks, event["source"], client=client, geocoder=geocoder)

    hitos_key = labeled_key.replace("/labeled/", "/hitos/", 1)
    storage.put_json(bucket, hitos_key, {"hitos": result.hitos, "descartados": result.discarded})
    return {
        "jobId": job_id,
        "bucket": bucket,
        "hitosKey": hitos_key,
        "chunks": len(chunks),
        "hitos": len(result.hitos),
        "descartados": len(result.discarded),
        "stats": dict(result.stats),
        "usage": TRACKER.summary(),
    }
