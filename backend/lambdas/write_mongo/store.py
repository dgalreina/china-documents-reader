"""Paso 4: escribir los chunks en MongoDB. Única pieza del repo que toca la base de datos.

Protecciones (ver "Pruebas y base de datos de producción" en CLAUDE.md):
- El destino (dev/prod) es obligatorio y cada uno tiene su propia URI y base de datos.
- Por defecto no escribe (dry-run); hay que pedirlo con write=True.
- Antes de escribir se vuelve a validar el contrato. Si UN documento falla, no se escribe NINGUNO:
  así un bug en un paso anterior no puede colar chunks sin época en book_chunks.
"""

import os
import re
from dataclasses import dataclass, field

from pymongo import GEOSPHERE, MongoClient, ReplaceOne
from pymongo.errors import BulkWriteError, OperationFailure

from shared.geocoder import LAT_RANGE, LNG_RANGE
from shared.secrets import get_secret

from ..embed_label.embedder import EMBEDDING_DIMENSIONS, EMBEDDING_MODEL
from ..embed_label.labeler import MAX_YEAR, MIN_YEAR

COLLECTION = "book_chunks"
VECTOR_INDEX_NAME = "book_chunks_vector_index"

# Base de datos por defecto de cada destino. Se pueden cambiar por entorno.
DEFAULT_DB = {"dev": "china_documents_dev", "prod": "china-history"}

REQUIRED_FIELDS = ("text", "embedding", "chunkIndex", "añoInicio", "añoFin", "embeddingModel")


_databases: dict = {}


def cached_database(target_name: str):
    """La base de datos del destino, con el cliente de Mongo REUTILIZADO entre peticiones de una Lambda "caliente" (conectar cuesta ~1 s).
    Para las Lambdas de la web (leer y borrar); las de escritura del flujo abren y cierran su propia conexión."""
    if target_name not in _databases:
        target = resolve_target(target_name)
        _databases[target_name] = MongoClient(target.uri, serverSelectionTimeoutMS=8000)[target.db_name]
    return _databases[target_name]


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
    uri = get_secret(f"MONGODB_URI_{upper}")
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


def build_operations(source: str, docs: list[dict], job_id: str | None = None) -> list[ReplaceOne]:
    """Valida todo y devuelve los reemplazos (upsert) por (source, chunkIndex). Todo o nada.

    Cada chunk que escribe esta app lleva `origen` (para no confundirlo nunca con los de china-history) y, en AWS, `jobId`
    (para saber qué ejecución lo escribió y poder borrar al final los de ejecuciones anteriores).
    """
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
            {**doc, "source": source, "origen": ORIGIN, **({"jobId": job_id} if job_id else {})},
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
    personajes_stats: dict = field(default_factory=dict)  # nuevos / unidos / llamadas al juez
    personajes_log: list = field(default_factory=list)  # qué se creó y qué se unió, para revisar
    duplicates: list = field(default_factory=list)  # hitos que ya existían (con otras palabras) y no se escriben


def write_chunks(
    target: Target,
    source: str,
    docs: list[dict],
    write: bool = False,
    ensure_index: bool = False,
    replace_source: bool = False,
    job_id: str | None = None,
) -> WriteReport:
    operations = build_operations(source, docs, job_id)  # valida antes de abrir ninguna conexión
    client = MongoClient(target.uri, serverSelectionTimeoutMS=8000)
    try:
        collection = client[target.db_name][COLLECTION]
        # Protección: si ya hay chunks de ese `source` que NO son de esta app (p. ej. un libro de china-history con el mismo
        # slug), reemplazar por (source, chunkIndex) los pisaría. Se rechaza antes de escribir nada.
        if collection.count_documents({"source": source, "origen": {"$ne": ORIGIN}}, limit=1):
            raise ContractError(
                f"el source {source!r} ya tiene chunks que no son de esta app (sin marca de origen): usa otro slug"
            )
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
    # Campos propios de esta app (china-history los ignora): la página del libro y la frase que respalda el hecho. Los dos son opcionales.
    pagina = hito.get("pagina")
    if pagina is not None and (not isinstance(pagina, int) or isinstance(pagina, bool) or pagina < 1):
        raise ContractError(f"{where}: pagina debe ser un entero >= 1")
    if hito.get("evidencia") is not None and not isinstance(hito["evidencia"], str):
        raise ContractError(f"{where}: evidencia debe ser texto")
    for link in hito.get("personajes", []):
        if not isinstance(link, dict) or not PERSONAJE_ID.match(str(link.get("id", ""))) or not link.get("rol") or not link.get("menciones"):
            raise ContractError(f"{where}: enlace a personaje inválido {link!r}")


def build_hito_operations(source: str, hitos: list[dict], job_id: str | None = None) -> list[ReplaceOne]:
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
    # El filtro incluye `origen` Y `fuente`: si el id ya existe pero es de otro origen (un hito de china-history) o de OTRO LIBRO
    # (el mismo hecho con el mismo título sale con el mismo id), el upsert intentaría insertar un duplicado y el índice único
    # lo rechaza. Nunca se pisa un hito ajeno.
    clean = [  # los campos con _ (p. ej. _personajes, ya resueltos) son de trabajo y no se escriben; jobId marca qué ejecución lo escribió
        {**{k: v for k, v in hito.items() if not k.startswith("_")}, **({"jobId": job_id} if job_id else {})} for hito in hitos
    ]
    return [ReplaceOne({"id": hito["id"], "origen": ORIGIN, "fuente": source}, hito, upsert=True) for hito in clean]


PERSONAJES_COLLECTION = "personajes"
PERSONAJE_ID = re.compile(r"^[a-z0-9-]+-[0-9a-f]{6}$")


def validate_personaje(doc: dict) -> None:
    """Lanza ContractError si la ficha no se puede escribir en personajes."""
    where = f"personaje {doc.get('id', '?')}"
    for field_name in ("id", "nombre", "nombre_en", "rol", "rol_en", "descripcion", "descripcion_en", "fuente", "origen", "aliases", "aliasNorm"):
        if doc.get(field_name) in (None, "", [], {}):
            raise ContractError(f"{where}: falta {field_name}")
    if not PERSONAJE_ID.match(doc["id"]):
        raise ContractError(f"{where}: id con formato inválido")
    if doc["origen"] != ORIGIN:
        raise ContractError(f"{where}: origen debe ser {ORIGIN!r}")
    if not all(isinstance(doc["aliases"].get(lang), list) for lang in ("es", "en", "zh")):
        raise ContractError(f"{where}: aliases debe tener listas es, en y zh")
    for field_name in ("nacimiento", "muerte", "actividadInicio", "actividadFin"):
        value = doc.get(field_name)
        if value is not None and (not isinstance(value, int) or isinstance(value, bool) or not (MIN_YEAR <= value <= MAX_YEAR)):
            raise ContractError(f"{where}: {field_name} fuera de {MIN_YEAR}..{MAX_YEAR}")
    for field_name in ("nombre_zh", "rol_zh", "estado_zh", "descripcion_zh"):
        if doc.get(field_name) and not CJK.search(doc[field_name]):
            raise ContractError(f"{where}: {field_name} no contiene caracteres chinos")


def build_personaje_operations(docs: list[dict]) -> list[ReplaceOne]:
    """Reemplazo por (id, origen): nunca se pisa una ficha que no sea de esta app."""
    seen: set[str] = set()
    for doc in docs:
        validate_personaje(doc)
        if doc["id"] in seen:
            raise ContractError(f"id de personaje {doc['id']} repetido")
        seen.add(doc["id"])
    return [ReplaceOne({"id": doc["id"], "origen": ORIGIN}, doc, upsert=True) for doc in docs]


HITO_NEIGHBOR_FIELDS = {"_id": 0, "id": 1, "titulo": 1, "titulo_en": 1, "descripcion": 1, "descripcion_en": 1, "año": 1, "fuente": 1, "location": 1}


def mongo_neighbors(collection, source: str, replace_source: bool, job_id: str | None = None):
    """Función que devuelve los hitos ya guardados cerca (en año y lugar) de uno dado. Solo lectura."""
    from .dedupe import RADIUS_KM, YEAR_WINDOW, is_close

    def neighbors(hito: dict) -> list[dict]:
        lng, lat = hito["location"]["coordinates"]
        query = {"año": {"$gte": hito["año"] - YEAR_WINDOW, "$lte": hito["año"] + YEAR_WINDOW}}
        # Se excluye el MISMO hito de ESTE libro (se va a reemplazar: no es un vecino). Un hito con el mismo id pero de otro libro
        # SÍ es vecino: es el mismo hecho que ya está guardado y debe salir como duplicado, no sobrescribirse.
        excluded = [{"id": hito["id"], "origen": ORIGIN, "fuente": source}]
        if replace_source:
            # Los de este mismo libro se van a reemplazar: compararse con ellos marcaría todo como duplicado. Con jobId (AWS,
            # por tandas) solo se excluyen los de EJECUCIONES ANTERIORES: los que escribieron tandas previas de esta sí cuentan.
            old = {"origen": ORIGIN, "fuente": source}
            excluded.append({**old, "jobId": {"$ne": job_id}} if job_id else old)
        query["$nor"] = excluded
        try:
            near = {"location": {"$near": {"$geometry": {"type": "Point", "coordinates": [lng, lat]}, "$maxDistance": RADIUS_KM * 1000}}}
            return list(collection.find({**query, **near}, HITO_NEIGHBOR_FIELDS).limit(10))
        except OperationFailure:
            # Sin índice 2dsphere (base nueva): se filtra por año en Mongo y por distancia aquí.
            return [h for h in collection.find(query, HITO_NEIGHBOR_FIELDS).limit(200) if h.get("location") and is_close(hito, h)]

    return neighbors


def write_hitos(
    target: Target,
    source: str,
    hitos: list[dict],
    write: bool = False,
    ensure_index: bool = False,
    replace_source: bool = False,
    dedupe: bool = True,
    client=None,
    personajes: bool = True,
    job_id: str | None = None,
) -> WriteReport:
    build_hito_operations(source, hitos, job_id)  # valida TODO antes de abrir ninguna conexión
    mongo = MongoClient(target.uri, serverSelectionTimeoutMS=8000)
    try:
        collection = mongo[target.db_name][HITOS_COLLECTION]
        duplicates: list[dict] = []
        if dedupe:
            # Guardar solo lo nuevo: se compara con lo que YA hay en la base de destino (y con la propia tanda).
            from ..embed_label.pipeline import make_client
            from .dedupe import filter_new

            hitos, duplicates = filter_new(hitos, mongo_neighbors(collection, source, replace_source, job_id), client or make_client())
        personaje_docs: list[dict] = []
        personajes_stats: dict = {}
        personajes_log: list = []
        if personajes and hitos:
            # Resolución de identidad contra las fichas de la base de destino; las fichas nuevas o ampliadas se escriben
            # ANTES que los hitos, para que ningún enlace quede apuntando a una ficha que no existe.
            from ..embed_label.pipeline import make_client
            from .personajes import resolve_personajes

            people = mongo[target.db_name][PERSONAJES_COLLECTION]
            find = lambda forms: list(people.find({"aliasNorm": {"$in": sorted(forms)}}, {"_id": 0}))  # noqa: E731
            resolution = resolve_personajes(hitos, source, find, client or make_client())
            hitos, personaje_docs = resolution.hitos, resolution.docs
            personajes_stats, personajes_log = dict(resolution.stats), resolution.log
        elif not personajes:
            hitos = [{k: v for k, v in h.items() if k != "_personajes"} for h in hitos]
        personaje_operations = build_personaje_operations(personaje_docs)
        operations = build_hito_operations(source, hitos, job_id) if hitos else []
        ids = [h["id"] for h in hitos]
        existing = collection.count_documents({"id": {"$in": ids}, "origen": ORIGIN})
        report = WriteReport(
            target.name, target.db_name, source, not write, len(hitos), existing, duplicates=duplicates,
            personajes_stats=personajes_stats, personajes_log=personajes_log,
        )
        orphan_filter = {"origen": ORIGIN, "fuente": source, "id": {"$nin": ids}}
        # Con job_id (AWS, por tandas) el borrado lo hace cleanup_old_runs al final, no cada tanda: si no, cada una
        # borraría lo que escribió la anterior.
        batch_replace = replace_source and job_id is None
        if batch_replace:
            report.orphans = collection.count_documents(orphan_filter)
        if ensure_index:
            if target.name != "dev":
                raise ContractError("--ensure-index solo funciona con --target dev")
            if write:
                collection.create_index([("location", GEOSPHERE)])
                collection.create_index("año")
                collection.create_index("id", unique=True)
                people_col = mongo[target.db_name][PERSONAJES_COLLECTION]
                people_col.create_index("id", unique=True)
                people_col.create_index("aliasNorm")
        if write and personaje_operations:
            mongo[target.db_name][PERSONAJES_COLLECTION].bulk_write(personaje_operations, ordered=False)
        if write and operations:
            try:
                result = collection.bulk_write(operations, ordered=False)
            except BulkWriteError as err:
                raise ContractError(f"colisión de id con un hito que no es de esta app (no se pisa): {err.details['writeErrors'][:1]}")
            report.written = result.upserted_count + result.matched_count
            if batch_replace:
                collection.delete_many(orphan_filter)
        return report
    finally:
        mongo.close()


def cleanup_old_runs(target: Target, source: str, job_id: str) -> dict:
    """Al terminar un trabajo con reemplazo: borra los chunks y hitos de EJECUCIONES ANTERIORES de ese libro.

    Solo toca documentos con la marca de esta app (`origen`) y de otro `jobId`: nunca los de china-history, aunque coincida el
    nombre del libro. Los de esta ejecución (los acaban de escribir las tandas) se conservan.
    """
    if not job_id:
        raise ContractError("cleanup_old_runs necesita el jobId de la ejecución actual")
    client = MongoClient(target.uri, serverSelectionTimeoutMS=8000)
    try:
        db = client[target.db_name]
        chunks = db[COLLECTION].delete_many({"source": source, "origen": ORIGIN, "jobId": {"$ne": job_id}}).deleted_count
        hitos = db[HITOS_COLLECTION].delete_many({"origen": ORIGIN, "fuente": source, "jobId": {"$ne": job_id}}).deleted_count
        return {"chunksBorrados": chunks, "hitosBorrados": hitos}
    finally:
        client.close()
