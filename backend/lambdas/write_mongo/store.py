"""Paso 4: escribir los chunks en MongoDB. Única pieza del repo que toca la base de datos.

Protecciones (ver "Pruebas y base de datos de producción" en CLAUDE.md):
- El destino (dev/prod) es obligatorio y cada uno tiene su propia URI y base de datos.
- Por defecto no escribe (dry-run); hay que pedirlo con write=True.
- Antes de escribir se vuelve a validar el contrato. Si UN documento falla, no se escribe NINGUNO:
  así un bug en un paso anterior no puede colar chunks sin época en book_chunks.
"""

import os
from dataclasses import dataclass

from pymongo import MongoClient, UpdateOne
from pymongo.errors import OperationFailure

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


def build_operations(source: str, docs: list[dict]) -> list[UpdateOne]:
    """Valida todo y devuelve los upserts por (source, chunkIndex). Todo o nada."""
    if not source or not source.strip():
        raise ContractError("source (slug del documento) vacío")
    seen: set[int] = set()
    for doc in docs:
        validate_doc(doc)
        if doc["chunkIndex"] in seen:
            raise ContractError(f"chunkIndex {doc['chunkIndex']} repetido")
        seen.add(doc["chunkIndex"])
    return [
        UpdateOne(
            {"source": source, "chunkIndex": doc["chunkIndex"]},
            {"$set": {**doc, "source": source}},
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


def write_chunks(
    target: Target, source: str, docs: list[dict], write: bool = False, ensure_index: bool = False
) -> WriteReport:
    operations = build_operations(source, docs)  # valida antes de abrir ninguna conexión
    client = MongoClient(target.uri, serverSelectionTimeoutMS=8000)
    try:
        collection = client[target.db_name][COLLECTION]
        indexes = [d["chunkIndex"] for d in docs]
        existing = collection.count_documents({"source": source, "chunkIndex": {"$in": indexes}})
        report = WriteReport(target.name, target.db_name, source, not write, len(docs), existing)
        if ensure_index:
            if target.name != "dev":
                raise ContractError("--ensure-index solo funciona con --target dev (el índice de prod lo gestionas en Atlas)")
            if write:
                ensure_dev_indexes(collection)
        if write and operations:
            result = collection.bulk_write(operations, ordered=False)
            report.written = result.upserted_count + result.matched_count
        return report
    finally:
        client.close()


def ensure_dev_indexes(collection) -> None:
    """Crea en la base de PRUEBAS el índice único y el vectorial (misma definición que prod)."""
    collection.create_index([("source", 1), ("chunkIndex", 1)], unique=True)
    try:
        collection.create_search_index(
            {
                "name": VECTOR_INDEX_NAME,
                "type": "vectorSearch",
                "definition": {
                    "fields": [
                        {"type": "vector", "path": "embedding", "numDimensions": EMBEDDING_DIMENSIONS, "similarity": "cosine"},
                        {"type": "filter", "path": "añoInicio"},
                        {"type": "filter", "path": "añoFin"},
                    ]
                },
            }
        )
    except OperationFailure as err:
        if "already exists" not in str(err).lower() and getattr(err, "code", None) != 68:
            raise
