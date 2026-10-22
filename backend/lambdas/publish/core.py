"""Publicar un libro en PRODUCCIÓN (la base que lee china-history): lógica pura. Lo llaman `handler.py` (AWS) y las pruebas.

Qué hace, y qué NO:
  - Copia de la base de dev a la de producción SOLO los chunks (`book_chunks`) y los hitos (`hitos_conocidos`) de UN libro. Nada más: ni la colección de
    personajes (china-history aún no sabe leerla), ni nada que no lleve la marca de origen de esta app.
  - Reutiliza `write_chunks` y `write_hitos` de `write_mongo`: el mismo contrato, la misma doble validación, el mismo upsert por clave (reemplazo entero, nunca `$set`)
    y la misma deduplicación contra lo que ya hay en producción (los 1.259 hitos de china-history incluidos: lo que ya existe no se vuelve a escribir).
  - Publicar de nuevo REEMPLAZA la copia anterior de ese libro: se escribe lo nuevo con un `jobId` nuevo y al final se borra lo de ejecuciones anteriores (con nuestra marca).
  - Si en producción ya hay chunks de ese `source` SIN nuestra marca (un libro de china-history con el mismo slug), se rechaza antes de escribir nada.
  - `write=False` es la VISTA PREVIA: hace todo menos escribir (incluida la deduplicación, que llama al modelo y cuesta unos céntimos) y cuenta lo que pasaría.
  - Despublicar borra de producción lo de ese libro con nuestra marca; es el "deshacer" de publicar.

Los hitos se publican SIN enlaces a personajes: los enlaces apuntarían a fichas que en producción no existen.
"""

import os
from dataclasses import dataclass, field

from shared.secrets import get_secret

from ..write_mongo import store
from ..write_mongo.store import ORIGIN, ContractError, Target

CHUNKS = "book_chunks"
HITOS = "hitos_conocidos"
MAX_DUPLICATES_SHOWN = 40  # cuántos duplicados se detallan en el resumen del trabajo (DynamoDB admite 400 KB por registro)


@dataclass
class PublishReport:
    slug: str
    dry_run: bool
    db_name: str
    chunks: int = 0  # chunks que se publican (o publicarían)
    chunks_replaced: int = 0  # de ellos, los que ya estaban publicados
    hitos: int = 0
    hitos_replaced: int = 0
    previous_chunks: int = 0  # lo que había publicado de este libro antes de esta ejecución
    previous_hitos: int = 0
    removed_chunks: int = 0  # lo anterior que ya no está en el libro y se borró (solo si no es vista previa)
    removed_hitos: int = 0
    duplicates: list = field(default_factory=list)
    unlocated_hitos: int = 0

    def summary(self) -> dict:
        """El resumen que se guarda en el trabajo y se enseña en la web."""
        return {
            "dryRun": self.dry_run,
            "base": self.db_name,
            "chunks": self.chunks,
            "chunksReemplazados": self.chunks_replaced,
            "hitos": self.hitos,
            "hitosReemplazados": self.hitos_replaced,
            "hitosDuplicados": len(self.duplicates),
            "duplicados": self.duplicates[:MAX_DUPLICATES_SHOWN],
            "publicadoAntes": {"chunks": self.previous_chunks, "hitos": self.previous_hitos},
            "chunksRetirados": self.removed_chunks,
            "hitosRetirados": self.removed_hitos,
        }


def prod_target() -> Target:
    """La base de producción. Si no hay `MONGODB_URI_PROD` se usa la de dev: el usuario de Atlas es el mismo y ya llega a las dos bases; lo que decide
    DÓNDE se escribe es el nombre de la base (`MONGODB_DB_PROD`, por defecto `china-history`), y nunca puede coincidir con la de dev."""
    try:
        target = store.resolve_target("prod")
    except ContractError:
        uri = get_secret("MONGODB_URI_DEV")
        if not uri:
            raise
        target = Target("prod", uri, os.environ.get("MONGODB_DB_PROD", store.DEFAULT_DB["prod"]))
    dev_db = os.environ.get("MONGODB_DB_DEV", store.DEFAULT_DB["dev"])
    if target.db_name == dev_db:
        raise ContractError(f"la base de producción ({target.db_name!r}) es la misma que la de dev: no se publica")
    return target


def _clean(doc: dict) -> dict:
    """Quita lo que no se copia: el `_id` de Mongo (en producción el documento tendrá el suyo; con el de dev el reemplazo falla)."""
    return {k: v for k, v in doc.items() if k != "_id"}


def read_book(dev_db, slug: str) -> tuple[list[dict], list[dict]]:
    """Los chunks y los hitos del libro en dev, solo los de esta app. Quita los enlaces a personajes de los hitos."""
    chunks = [_clean(d) for d in dev_db[CHUNKS].find({"source": slug, "origen": ORIGIN}).sort("chunkIndex", 1)]
    hitos = []
    for raw in dev_db[HITOS].find({"fuente": slug, "origen": ORIGIN}).sort("año", 1):
        hito = _clean(raw)
        hito.pop("personajes", None)  # sin fichas en producción, un enlace quedaría colgando
        hitos.append(hito)
    return chunks, hitos


def _count(target: Target, slug: str) -> tuple[int, int]:
    """Cuánto hay ya publicado de este libro (con nuestra marca)."""
    client = store.MongoClient(target.uri, serverSelectionTimeoutMS=8000)
    try:
        db = client[target.db_name]
        return (
            db[CHUNKS].count_documents({"source": slug, "origen": ORIGIN}),
            db[HITOS].count_documents({"fuente": slug, "origen": ORIGIN}),
        )
    finally:
        client.close()


def publish_book(
    dev_db,
    target: Target,
    slug: str,
    job_id: str,
    write: bool = False,
    client=None,
    write_chunks_fn=None,
    write_hitos_fn=None,
    cleanup_fn=None,
) -> PublishReport:
    """Publica (o simula publicar, con write=False) un libro. Lanza ContractError si algo lo impide; nada se escribe antes de comprobarlo."""
    write_chunks_fn = write_chunks_fn or store.write_chunks
    write_hitos_fn = write_hitos_fn or store.write_hitos
    cleanup_fn = cleanup_fn or store.cleanup_old_runs

    chunks, hitos = read_book(dev_db, slug)
    if not chunks:
        raise ContractError("el libro no tiene fragmentos analizados en dev: analízalo antes de publicarlo")

    report = PublishReport(slug, not write, target.db_name)
    report.previous_chunks, report.previous_hitos = _count(target, slug)

    # Primero los chunks: si el slug está ocupado por un libro ajeno, `write_chunks` lo rechaza ANTES de escribir nada (también en la vista previa).
    chunk_report = write_chunks_fn(target, slug, chunks, write=write, job_id=job_id)
    report.chunks, report.chunks_replaced = chunk_report.total, chunk_report.existing

    # Los hitos: `dedupe` compara con TODO lo que hay en producción y se queda con lo nuevo; sin personajes. El jobId nuevo hace que los de la publicación
    # anterior no cuenten como vecinos (se van a reemplazar) y permite borrarlos al final.
    hito_report = write_hitos_fn(
        target, slug, hitos, write=write, replace_source=True, dedupe=True, client=client, personajes=False, job_id=job_id
    )
    report.hitos, report.hitos_replaced = hito_report.total, hito_report.existing
    report.duplicates = hito_report.duplicates

    if write:
        # Lo de publicaciones anteriores de este libro (otro jobId, nuestra marca): un chunk que ya no existe, un hito que ahora es duplicado...
        removed = cleanup_fn(target, slug, job_id)
        report.removed_chunks, report.removed_hitos = removed["chunksBorrados"], removed["hitosBorrados"]
    return report


def unpublish_book(target: Target, slug: str) -> dict:
    """El "deshacer": borra de producción los chunks y los hitos de ese libro con nuestra marca. Nunca toca lo de china-history ni otros libros."""
    client = store.MongoClient(target.uri, serverSelectionTimeoutMS=8000)
    try:
        db = client[target.db_name]
        chunks = db[CHUNKS].delete_many({"source": slug, "origen": ORIGIN}).deleted_count
        hitos = db[HITOS].delete_many({"fuente": slug, "origen": ORIGIN}).deleted_count
        return {"chunks": chunks, "hitos": hitos}
    finally:
        client.close()
