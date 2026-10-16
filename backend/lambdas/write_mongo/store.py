"""Paso 4: escribir los chunks en MongoDB. Única pieza del repo que toca la base de datos.

Protecciones (ver "Pruebas y base de datos de producción" en CLAUDE.md):
- El destino (dev/prod) es obligatorio y cada uno tiene su propia URI y base de datos.
- Por defecto no escribe (dry-run); hay que pedirlo con write=True.
- Antes de escribir se vuelve a validar el contrato. Si UN documento falla, no se escribe NINGUNO:
  así un bug en un paso anterior no puede colar chunks sin época en book_chunks.
"""

import os
import re
from dataclasses import dataclass

from pymongo import GEOSPHERE, MongoClient, ReplaceOne
from pymongo.errors import BulkWriteError, OperationFailure

from shared.geocoder import LAT_RANGE, LNG_RANGE

from ..embed_label.embedder import EMBEDDING_DIMENSIONS, EMBEDDING_MODEL
from ..embed_label.labeler import MAX_YEAR, MIN_YEAR

COLLECTION = "book_chunks"
VECTOR_INDEX_NAME = "book_chunks_vector_index"

# Base de datos por defecto de cada destino. Se pueden cambiar por entorno.
DEFAULT_DB = {"dev": "china_documents_dev", "prod": "china-history"}

REQUIRED_FIELDS = ("text", "embedding", "chunkIndex", "añoInicio", "añoFin", "embeddingModel")


class ContractError(Exception):
    """Un documento no cumple el contrato con china-history."""


@dataclass(frozen=True)
class Target:
    name: str  # "dev" | "prod"
    uri: str
    db_name: str


def resolve_target(name: str) -> Target:
    """Lee la URI y la base del destino. No hay valor por defecto: hay que elegir."""
    if name not in DEFAULT_DB:
        raise ContractError(f"Destino desconocido {name!r}: usa 'dev' o 'prod'")
    upper = name.upper()
    uri = os.environ.get(f"MONGODB_URI_{upper}")
    if not uri:
        raise ContractError(f"Falta MONGODB_URI_{upper} en el .env")
    db_name = os.environ.get(f"MONGODB_DB_{upper}", DEFAULT_DB[name])
    # Si dev y prod comparten usuario de Atlas, Atlas no impide que dev toque producción:
    # esta comprobación es entonces la última barrera.
    if name == "dev":
        prod_db = os.environ.get("MONGODB_DB_PROD", DEFAULT_DB["prod"])
        if db_name == prod_db:
            raise ContractError(f"MONGODB_DB_DEV apunta a la base de producción ({prod_db!r}): usa otra para pruebas")
    return Target(name=name, uri=uri, db_name=db_name)


def validate_doc(doc: dict) -> None:
    """Lanza ContractError si el documento no se puede escribir en book_chunks."""
    where = f"chunk {doc.get('chunkIndex', '?')}"
    for field in REQUIRED_FIELDS:
        if doc.get(field) is None:
            raise ContractError(f"{where}: falta {field}")
    ini, fin = doc["añoInicio"], doc["añoFin"]
    if not (isinstance(ini, int) and isinstance(fin, int)) or isinstance(ini, bool) or isinstance(fin, bool):
        raise ContractError(f"{where}: los años deben ser enteros")
    if ini > fin:
        raise ContractError(f"{where}: añoInicio ({ini}) > añoFin ({fin})")
    if ini < MIN_YEAR or fin > MAX_YEAR:
        raise ContractError(f"{where}: años fuera de {MIN_YEAR}..{MAX_YEAR}")
    if doc["embeddingModel"] != EMBEDDING_MODEL:
        raise ContractError(f"{where}: embeddingModel {doc['embeddingModel']!r}, debe ser {EMBEDDING_MODEL!r}")
    if len(doc["embedding"]) != EMBEDDING_DIMENSIONS:
        raise ContractError(f"{where}: embedding de {len(doc['embedding'])} dimensiones, deben ser {EMBEDDING_DIMENSIONS}")
    if not doc["text"].strip():
        raise ContractError(f"{where}: texto vacío")
    validate_location(doc, where)


LOCATION_FIELDS = ("ubicacion", "lat", "lng", "ubicacionNombre", "ubicacionFuente")


def validate_location(doc: dict, where: str) -> None:
    """La ubicación es OPCIONAL, pero si está debe estar completa y ser coherente."""
    present = [f for f in LOCATION_FIELDS if doc.get(f) is not None]
    if not present:
        return
    if len(present) != len(LOCATION_FIELDS):
        raise ContractError(f"{where}: ubicación incompleta (faltan {sorted(set(LOCATION_FIELDS) - set(present))})")
    lat, lng = doc["lat"], doc["lng"]
    if not (LAT_RANGE[0] <= lat <= LAT_RANGE[1] and LNG_RANGE[0] <= lng <= LNG_RANGE[1]):
        raise ContractError(f"{where}: ubicación fuera de China ({lat}, {lng})")
    point = doc["ubicacion"]
    if point.get("type") != "Point" or point.get("coordinates") != [lng, lat]:
        raise ContractError(f"{where}: ubicacion no coincide con lat/lng (GeoJSON es [lng, lat])")


def build_operations(source: str, docs: list[dict]) -> list[ReplaceOne]:
    """Valida todo y devuelve los reemplazos (upsert) por (source, chunkIndex). Todo o nada."""
    if not source or not source.strip():
        raise ContractError("source (slug del documento) vacío")
    seen: set[int] = set()
    for doc in docs:
        validate_doc(doc)
        if doc["chunkIndex"] in seen:
            raise ContractError(f"chunkIndex {doc['chunkIndex']} repetido")
        seen.add(doc["chunkIndex"])
    return [
        # Reemplaza el documento ENTERO (no $set): si al reprocesar ya no hay ubicación, no debe quedar la antigua.
        ReplaceOne(
            {"source": source, "chunkIndex": doc["chunkIndex"]},
            {**doc, "source": source},
            upsert=True,
        )
        for doc in docs
    ]


@dataclass
class WriteReport:
    target: str
    db_name: str
    source: str
    dry_run: bool
    total: int
    existing: int  # ya existían (se reemplazarían)
    written: int = 0
    orphans: int = 0  # chunkIndex de ese source que ya no existen y se borrarían/borraron (--replace-source)


def write_chunks(
    target: Target,
    source: str,
    docs: list[dict],
    write: bool = False,
    ensure_index: bool = False,
    replace_source: bool = False,
) -> WriteReport:
    operations = build_operations(source, docs)  # valida antes de abrir ninguna conexión
    client = MongoClient(target.uri, serverSelectionTimeoutMS=8000)
    try:
        collection = client[target.db_name][COLLECTION]
        indexes = [d["chunkIndex"] for d in docs]
        existing = collection.count_documents({"source": source, "chunkIndex": {"$in": indexes}})
        report = WriteReport(target.name, target.db_name, source, not write, len(docs), existing)
        if replace_source:
            # Solo los chunks de ESTE source que ya no están en la nueva tanda. Se cuentan siempre (dry-run
            # incluido) y se borran después del upsert, para no dejar nunca el documento vacío.
            orphan_filter = {"source": source, "chunkIndex": {"$nin": indexes}}
            report.orphans = collection.count_documents(orphan_filter)
        if ensure_index:
            if target.name != "dev":
                raise ContractError("--ensure-index solo funciona con --target dev (el índice de prod lo gestionas en Atlas)")
            if write:
                ensure_dev_indexes(collection)
        if write and operations:
            result = collection.bulk_write(operations, ordered=False)
            report.written = result.upserted_count + result.matched_count
            if replace_source:
                collection.delete_many(orphan_filter)
        return report
    finally:
        client.close()


def ensure_dev_indexes(collection) -> None:
    """Crea en la base de PRUEBAS el índice único y el vectorial (el de prod más lat/lng como filtros).

    Si el vectorial ya existe, lo actualiza (Atlas lo reconstruye). El índice de prod lo gestiona el dueño de china-history.
    """
    collection.create_index([("source", 1), ("chunkIndex", 1)], unique=True)
    definition = {
        "fields": [
            {"type": "vector", "path": "embedding", "numDimensions": EMBEDDING_DIMENSIONS, "similarity": "cosine"},
            {"type": "filter", "path": "añoInicio"},
            {"type": "filter", "path": "añoFin"},
            # Para que china-history pueda prefiltrar por caja geográfica dentro de $vectorSearch
            # (el filtro admite rangos numéricos, no operadores geoespaciales).
            {"type": "filter", "path": "lat"},
            {"type": "filter", "path": "lng"},
        ]
    }
    try:
        collection.create_search_index({"name": VECTOR_INDEX_NAME, "type": "vectorSearch", "definition": definition})
    except OperationFailure as err:
        if "already exists" not in str(err).lower() and getattr(err, "code", None) != 68:
            raise
        collection.update_search_index(VECTOR_INDEX_NAME, definition)


# =====================================================================================================
# hitos_conocidos
# =====================================================================================================

HITOS_COLLECTION = "hitos_conocidos"
# Marca propia de los hitos que escribe esta app. Los 1259 de china-history no la llevan: así nunca se
# reemplaza ni se borra uno de los suyos aunque coincida un id o un nombre de libro.
ORIGIN = "china-documents-reader"
HITO_REQUIRED = ("id", "lugar", "año", "titulo", "descripcion", "fuente", "location", "origen")
HITO_ID = re.compile(r"^[0-9a-f]{10}$")
CJK = re.compile(r"[\u4e00-\u9fff]")


def validate_hito(hito: dict) -> None:
    """Lanza ContractError si el hito no se puede escribir en hitos_conocidos."""
    where = f"hito {hito.get('id', '?')}"
    for field_name in HITO_REQUIRED:
        if hito.get(field_name) in (None, ""):
            raise ContractError(f"{where}: falta {field_name}")
    if not HITO_ID.match(hito["id"]):
        raise ContractError(f"{where}: id con formato inválido (10 caracteres hex)")
    if hito["origen"] != ORIGIN:
        raise ContractError(f"{where}: origen debe ser {ORIGIN!r}")
    year = hito["año"]
    if not isinstance(year, int) or isinstance(year, bool) or not (MIN_YEAR <= year <= MAX_YEAR):
        raise ContractError(f"{where}: año fuera de {MIN_YEAR}..{MAX_YEAR}")
    for field_name in ("lugar", "titulo", "descripcion"):
        if not hito[field_name].strip():
            raise ContractError(f"{where}: {field_name} vacío")
    location = hito["location"]
    coords = location.get("coordinates") if isinstance(location, dict) else None
    if location.get("type") != "Point" or not coords or len(coords) != 2:
        raise ContractError(f"{where}: location debe ser un Point GeoJSON [lng, lat]")
    lng, lat = coords
    if not (LAT_RANGE[0] <= lat <= LAT_RANGE[1] and LNG_RANGE[0] <= lng <= LNG_RANGE[1]):
        raise ContractError(f"{where}: location fuera de China ({lat}, {lng}); ¿[lng, lat] al revés?")
    for field_name in ("lugar_zh", "titulo_zh", "descripcion_zh"):
        if hito.get(field_name) and not CJK.search(hito[field_name]):
            raise ContractError(f"{where}: {field_name} no contiene caracteres chinos")


def build_hito_operations(source: str, hitos: list[dict]) -> list[ReplaceOne]:
    """Valida todo y devuelve los reemplazos por id. Todo o nada."""
    if not source or not source.strip():
        raise ContractError("source (slug del libro) vacío")
    seen: set[str] = set()
    for hito in hitos:
        validate_hito(hito)
        if hito["fuente"] != source:
            raise ContractError(f"hito {hito['id']}: fuente {hito['fuente']!r} no coincide con --source {source!r}")
        if hito["id"] in seen:
            raise ContractError(f"id {hito['id']} repetido")
        seen.add(hito["id"])
    # El filtro incluye `origen`: si el id ya existe pero es de otro origen (un hito de china-history), el upsert
    # intentaría insertar un duplicado y el índice único lo rechaza. Nunca se pisa un hito ajeno.
    clean = [{k: v for k, v in hito.items() if not k.startswith("_")} for hito in hitos]  # _evidencia es solo de revisión
    return [ReplaceOne({"id": hito["id"], "origen": ORIGIN}, hito, upsert=True) for hito in clean]


def write_hitos(
    target: Target, source: str, hitos: list[dict], write: bool = False, ensure_index: bool = False, replace_source: bool = False
) -> WriteReport:
    operations = build_hito_operations(source, hitos)  # valida antes de abrir ninguna conexión
    client = MongoClient(target.uri, serverSelectionTimeoutMS=8000)
    try:
        collection = client[target.db_name][HITOS_COLLECTION]
        ids = [h["id"] for h in hitos]
        existing = collection.count_documents({"id": {"$in": ids}, "origen": ORIGIN})
        report = WriteReport(target.name, target.db_name, source, not write, len(hitos), existing)
        orphan_filter = {"origen": ORIGIN, "fuente": source, "id": {"$nin": ids}}
        if replace_source:
            report.orphans = collection.count_documents(orphan_filter)
        if ensure_index:
            if target.name != "dev":
                raise ContractError("--ensure-index solo funciona con --target dev")
            if write:
                collection.create_index([("location", GEOSPHERE)])
                collection.create_index("año")
                collection.create_index("id", unique=True)
        if write and operations:
            try:
                result = collection.bulk_write(operations, ordered=False)
            except BulkWriteError as err:
                raise ContractError(f"colisión de id con un hito que no es de esta app (no se pisa): {err.details['writeErrors'][:1]}")
            report.written = result.upserted_count + result.matched_count
            if replace_source:
                collection.delete_many(orphan_filter)
        return report
    finally:
        client.close()
