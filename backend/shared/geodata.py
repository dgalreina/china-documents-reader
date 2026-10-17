"""Datos geográficos externos (CHGIS, yacimientos y capitales de china-history) en AWS: de un bucket PRIVADO de S3 a /tmp.

En tu ordenador se leen de las carpetas que apuntan `CHGIS_DIR` y `CHINA_HISTORY_DATA_DIR` (.env). En AWS no existen esas carpetas:
si `GEODATA_BUCKET` está definido, al arrancar la Lambda se descargan una vez a /tmp y se apuntan esas dos variables allí.

CHGIS no se puede redistribuir (licencia), por eso el bucket es privado y los ficheros no están en el repositorio: se suben a mano
una vez (ver CLAUDE.md, "Licencias y datos externos").
"""

import os
from pathlib import Path

from .storage import S3Storage, Storage

# (clave en el bucket, ruta relativa al directorio local). La estructura local imita la de china-history/data.
GEODATA_FILES = [
    ("chgis/county-points.json", "chgis/county-points.json"),
    ("chgis/prefecture-points.json", "chgis/prefecture-points.json"),
    ("yacimientos/yacimientos.json", "yacimientos/yacimientos.json"),
    ("capitales/capitales.json", "capitales/capitales.json"),
]

_prepared = False


def ensure_geodata(storage: Storage | None = None, dest: str | Path = "/tmp/geodata") -> list[str]:
    """Descarga los datos si hay GEODATA_BUCKET y aún no se hizo en este proceso. Devuelve los ficheros que no pudo descargar."""
    global _prepared
    bucket = os.environ.get("GEODATA_BUCKET")
    if _prepared or not bucket:
        return []
    storage = storage or S3Storage()
    prefix = os.environ.get("GEODATA_PREFIX", "geodata").strip("/")
    dest = Path(dest)
    missing: list[str] = []
    for key, relative in GEODATA_FILES:
        target = dest / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        try:
            storage.download(bucket, f"{prefix}/{key}", target)
        except Exception:  # noqa: BLE001 — un fichero ausente no debe tumbar el paso: esa fuente se salta
            missing.append(key)
    os.environ["CHGIS_DIR"] = str(dest / "chgis")
    os.environ["CHINA_HISTORY_DATA_DIR"] = str(dest)
    _prepared = True
    return missing


def reset() -> None:
    global _prepared
    _prepared = False
