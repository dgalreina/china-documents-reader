"""Estado de los trabajos de un libro, compartido por las Lambdas que deben saber si hay algo en curso antes de actuar (subir, analizar, borrar)."""

from datetime import datetime

UPLOAD_GRACE_SECONDS = 15 * 60  # una subida sin terminar tras este tiempo no va a llegar (el permiso de subida dura 5 min): ya no cuenta como "en curso"


def scan_all(table) -> list[dict]:
    """Todos los registros de una tabla pequeña (la de trabajos caduca a los 30 días, la del catálogo tiene un registro por libro)."""
    items, kwargs = [], {}
    while True:
        page = table.scan(**kwargs)
        items.extend(page.get("Items", []))
        if "LastEvaluatedKey" not in page:
            return items
        kwargs["ExclusiveStartKey"] = page["LastEvaluatedKey"]


def is_busy(job: dict, now: float) -> bool:
    """¿Este trabajo está haciendo algo ahora? Un análisis en marcha, o una subida reciente que aún puede llegar."""
    if job.get("status") == "RUNNING":
        return True
    if job.get("status") == "UPLOADING":
        try:
            created = datetime.fromisoformat(str(job.get("createdAt", "")).replace("Z", "+00:00")).timestamp()
        except ValueError:
            return False
        return now - created < UPLOAD_GRACE_SECONDS
    return False


def busy_job(jobs_table, slug: str, now: float) -> dict | None:
    """Un trabajo en curso de ese libro, si lo hay."""
    return next((job for job in scan_all(jobs_table) if job.get("source") == slug and is_busy(job, now)), None)
