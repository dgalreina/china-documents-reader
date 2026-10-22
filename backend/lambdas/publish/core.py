"""Publicar un libro en PRODUCCIÓN (la base que lee china-history): lógica pura. Lo llaman `handler.py` (AWS) y las pruebas.

Qué hace, y qué NO:
  - Copia de la base de dev a la de producción SOLO los chunks (`book_chunks`), los hitos (`hitos_conocidos`) y las fichas de personajes (`personajes`) de UN libro,
    y nada que no lleve la marca de origen de esta app. Los personajes se publican AUNQUE china-history aún no los lea: así su agente puede ir preparándolo.
    Solo viajan las fichas que enlaza algún hito que se publica (los hitos descartados por duplicados no arrastran las suyas).
  - Reutiliza `write_chunks` y `write_hitos` de `write_mongo`: el mismo contrato, la misma doble validación, el mismo upsert por clave (reemplazo entero, nunca `$set`)
    y la misma deduplicación contra lo que ya hay en producción (los 1.259 hitos de china-history incluidos: lo que ya existe no se vuelve a escribir).
  - Publicar de nuevo REEMPLAZA la copia anterior de ese libro: se escribe lo nuevo con un `jobId` nuevo y al final se borra lo de ejecuciones anteriores (con nuestra marca).
  - Si en producción ya hay chunks de ese `source` SIN nuestra marca (un libro de china-history con el mismo slug), se rechaza antes de escribir nada.
  - `write=False` es la VISTA PREVIA: hace todo menos escribir (incluida la deduplicación, que llama al modelo y cuesta unos céntimos) y cuenta lo que pasaría.
  - Despublicar borra de producción lo de ese libro con nuestra marca; es el "deshacer" de publicar.

Orden de escritura: chunks, fichas, hitos (así ningún enlace de un hito apunta a una ficha que aún no está). Una ficha compartida con otro libro ya publicado conserva
las fuentes de los dos. Al republicar se retiran las fichas que el libro ya no usa (si otro libro las usa, solo se les quita este libro de `fuentes`).
"""

import os
from dataclasses import dataclass, field

from shared.secrets import get_secret

from ..delete_book.purge import purge_mongo
from ..write_mongo import store
from ..write_mongo.store import ORIGIN, ContractError, Target

CHUNKS = "book_chunks"
HITOS = "hitos_conocidos"
PERSONAJES = "personajes"
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
    personajes: int = 0  # fichas que se publican (o publicarían)
    personajes_replaced: int = 0
    previous_chunks: int = 0  # lo que había publicado de este libro antes de esta ejecución
    previous_hitos: int = 0
    previous_personajes: int = 0
    removed_chunks: int = 0  # lo anterior que ya no está en el libro y se borró (solo si no es vista previa)
    removed_hitos: int = 0
    removed_personajes: int = 0
    duplicates: list = field(default_factory=list)

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
            "personajesPublicados": self.personajes,
            "personajesReemplazados": self.personajes_replaced,
            "publicadoAntes": {"chunks": self.previous_chunks, "hitos": self.previous_hitos, "personajes": self.previous_personajes},
            "chunksRetirados": self.removed_chunks,
            "hitosRetirados": self.removed_hitos,
            "personajesRetirados": self.removed_personajes,
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


def read_book(dev_db, slug: str) -> tuple[list[dict], list[dict], dict[str, dict]]:
    """Los chunks, los hitos (con sus enlaces a personajes) y las fichas de personajes del libro en dev, solo los de esta app. Las fichas, por id."""
    chunks = [_clean(d) for d in dev_db[CHUNKS].find({"source": slug, "origen": ORIGIN}).sort("chunkIndex", 1)]
    hitos = [_clean(d) for d in dev_db[HITOS].find({"fuente": slug, "origen": ORIGIN}).sort("año", 1)]
    wanted = sorted({link["id"] for hito in hitos for link in hito.get("personajes") or [] if link.get("id")})
    fichas = {d["id"]: _clean(d) for d in dev_db[PERSONAJES].find({"origen": ORIGIN, "id": {"$in": wanted}})} if wanted else {}
    return chunks, hitos, fichas


def _count(target: Target, slug: str) -> tuple[int, int, int]:
    """Cuánto hay ya publicado de este libro (con nuestra marca): chunks, hitos y fichas."""
    client = store.MongoClient(target.uri, serverSelectionTimeoutMS=8000)
    try:
        db = client[target.db_name]
        return (
            db[CHUNKS].count_documents({"source": slug, "origen": ORIGIN}),
            db[HITOS].count_documents({"fuente": slug, "origen": ORIGIN}),
            db[PERSONAJES].count_documents({"fuentes": slug, "origen": ORIGIN}),
        )
    finally:
        client.close()


def dedupe_hitos(target: Target, slug: str, hitos: list[dict], job_id: str, client=None) -> tuple[list[dict], list[dict]]:
    """(nuevos, duplicados) comparando con TODO lo que hay en producción (los hitos de china-history incluidos) y con la propia tanda. Solo lectura (llama al modelo).
    Con `job_id` nuevo, lo publicado antes de este mismo libro no cuenta como vecino: se va a reemplazar."""
    from ..embed_label.pipeline import make_client
    from ..write_mongo.dedupe import filter_new

    mongo = store.MongoClient(target.uri, serverSelectionTimeoutMS=8000)
    try:
        neighbors = store.mongo_neighbors(mongo[target.db_name][HITOS], slug, True, job_id)
        return filter_new(hitos, neighbors, client or make_client())
    finally:
        mongo.close()


def write_personajes(target: Target, slug: str, docs: list[dict], write: bool) -> int:
    """Escribe las fichas (reemplazo por id y origen) y devuelve cuántas ya estaban. Una ficha ya publicada por otro libro conserva sus fuentes y suma la de este.
    Valida SIEMPRE (también en la vista previa): una ficha mala no puede descubrirse a mitad de la publicación."""
    store.build_personaje_operations(docs)
    client = store.MongoClient(target.uri, serverSelectionTimeoutMS=8000)
    try:
        collection = client[target.db_name][PERSONAJES]
        ids = [d["id"] for d in docs]
        before = {d["id"]: d for d in collection.find({"origen": ORIGIN, "id": {"$in": ids}}, {"_id": 0, "id": 1, "fuentes": 1})} if ids else {}
        if write and docs:
            merged = [{**d, "fuentes": sorted({*(before.get(d["id"], {}).get("fuentes") or []), *(d.get("fuentes") or []), slug})} for d in docs]
            collection.bulk_write(store.build_personaje_operations(merged), ordered=False)
        return len(before)
    finally:
        client.close()


def prune_personajes(target: Target, slug: str, kept_ids: set[str]) -> int:
    """Tras republicar: las fichas con este libro entre sus fuentes que ya no usa ningún hito de este libro. Si otro libro publicado aún las usa, solo se les quita este
    libro de `fuentes`; si no, se borran. Devuelve cuántas se borraron."""
    client = store.MongoClient(target.uri, serverSelectionTimeoutMS=8000)
    try:
        db = client[target.db_name]
        removed = 0
        for ficha in db[PERSONAJES].find({"origen": ORIGIN, "fuentes": slug}, {"_id": 0, "id": 1}):
            if ficha["id"] in kept_ids:
                continue
            if db[HITOS].count_documents({"origen": ORIGIN, "personajes.id": ficha["id"]}, limit=1):
                db[PERSONAJES].update_one({"id": ficha["id"], "origen": ORIGIN}, {"$pull": {"fuentes": slug}})
            else:
                removed += db[PERSONAJES].delete_one({"id": ficha["id"], "origen": ORIGIN}).deleted_count
        return removed
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
    dedupe_fn=None,
    write_personajes_fn=None,
    prune_fn=None,
) -> PublishReport:
    """Publica (o simula publicar, con write=False) un libro. Lanza ContractError si algo lo impide; nada se escribe antes de comprobarlo."""
    write_chunks_fn = write_chunks_fn or store.write_chunks
    write_hitos_fn = write_hitos_fn or store.write_hitos
    cleanup_fn = cleanup_fn or store.cleanup_old_runs
    dedupe_fn = dedupe_fn or dedupe_hitos
    write_personajes_fn = write_personajes_fn or write_personajes
    prune_fn = prune_fn or prune_personajes

    chunks, hitos, fichas = read_book(dev_db, slug)
    if not chunks:
        raise ContractError("el libro no tiene fragmentos analizados en dev: analízalo antes de publicarlo")

    report = PublishReport(slug, not write, target.db_name)
    report.previous_chunks, report.previous_hitos, report.previous_personajes = _count(target, slug)

    # Primero los chunks: si el slug está ocupado por un libro ajeno, `write_chunks` lo rechaza ANTES de escribir nada (también en la vista previa).
    chunk_report = write_chunks_fn(target, slug, chunks, write=write, job_id=job_id)
    report.chunks, report.chunks_replaced = chunk_report.total, chunk_report.existing

    # Los hitos que se publican son los NUEVOS (lo que ya existe en producción se descarta) y solo ellos arrastran sus fichas.
    kept, report.duplicates = dedupe_fn(target, slug, hitos, job_id, client)
    needed = sorted({link["id"] for hito in kept for link in hito.get("personajes") or [] if link.get("id")})
    missing = [pid for pid in needed if pid not in fichas]
    if missing:
        raise ContractError(f"hitos enlazados a personajes que no existen en dev: {missing[:3]}")
    docs = [fichas[pid] for pid in needed]

    # Orden: fichas y luego hitos, para que ningún enlace apunte a una ficha que aún no está.
    report.personajes, report.personajes_replaced = len(docs), write_personajes_fn(target, slug, docs, write)
    hito_report = write_hitos_fn(target, slug, kept, write=write, replace_source=True, dedupe=False, client=client, personajes=False, job_id=job_id)
    report.hitos, report.hitos_replaced = hito_report.total, hito_report.existing

    if write:
        # Lo de publicaciones anteriores de este libro (otro jobId, nuestra marca): un chunk que ya no existe, un hito que ahora es duplicado...
        removed = cleanup_fn(target, slug, job_id)
        report.removed_chunks, report.removed_hitos = removed["chunksBorrados"], removed["hitosBorrados"]
        report.removed_personajes = prune_fn(target, slug, set(needed))
    return report


def unpublish_book(target: Target, slug: str) -> dict:
    """El "deshacer": borra de producción los chunks, los hitos y las fichas de ese libro con nuestra marca (las fichas que otro libro publicado aún usa se conservan, sin
    este libro en sus fuentes). Es la MISMA rutina que borra un libro de dev. Nunca toca lo de china-history ni otros libros."""
    client = store.MongoClient(target.uri, serverSelectionTimeoutMS=8000)
    try:
        return purge_mongo(client[target.db_name], slug)
    finally:
        client.close()
