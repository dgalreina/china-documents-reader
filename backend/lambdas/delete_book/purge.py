"""Qué se borra al borrar un libro, y en qué orden. Lógica pura: recibe las colecciones, el almacenamiento y las tablas ya abiertos.

TODO lo que es solo de ese libro:
  - Mongo: sus chunks y sus hitos, y los personajes que ya no aparecen en ningún otro libro.
  - S3: el PDF, la portada, las traducciones y los ficheros intermedios de sus análisis.
  - DynamoDB: sus registros de trabajos y, el ÚLTIMO, su entrada del catálogo.

Lo que NUNCA se toca: nada que no lleve la marca de origen de esta app (los datos de china-history), nada de otros libros, y las fichas de personajes
que otro libro todavía usa (a esas solo se les quita este libro de `fuentes`).

El orden es de lo derivado a lo básico y el catálogo va el último: si algo falla a medias, el libro SIGUE en la lista y se puede reintentar. Todo es idempotente
(repetirlo no rompe nada).
"""

from ..write_mongo.store import ORIGIN

CHUNKS = "book_chunks"
HITOS = "hitos_conocidos"
PERSONAJES = "personajes"


def purge_mongo(db, slug: str) -> dict:
    """Borra los chunks y los hitos del libro y limpia los personajes. Devuelve cuántos documentos de cada tipo."""
    hitos, chunks, people = db[HITOS], db[CHUNKS], db[PERSONAJES]
    own_hitos = {"origen": ORIGIN, "fuente": slug}

    # Los personajes que tocar: los que enlazan los hitos de este libro y los que tienen este libro entre sus fuentes (por si quedó alguno suelto).
    candidates: set[str] = set()
    for hito in hitos.find(own_hitos, {"_id": 0, "personajes.id": 1}):
        candidates.update(link["id"] for link in hito.get("personajes") or [] if link.get("id"))
    candidates.update(ficha["id"] for ficha in people.find({"origen": ORIGIN, "fuentes": slug}, {"_id": 0, "id": 1}))

    deleted_chunks = chunks.delete_many({"source": slug, "origen": ORIGIN}).deleted_count
    deleted_hitos = hitos.delete_many(own_hitos).deleted_count

    deleted_people = kept_people = 0
    for person_id in sorted(candidates):
        if hitos.count_documents({"origen": ORIGIN, "personajes.id": person_id}, limit=1):
            # Otro libro suyo todavía lo usa: se queda, sin este libro entre sus fuentes.
            people.update_one({"id": person_id, "origen": ORIGIN}, {"$pull": {"fuentes": slug}})
            kept_people += 1
        else:
            deleted_people += people.delete_one({"id": person_id, "origen": ORIGIN}).deleted_count
    return {"chunks": deleted_chunks, "hitos": deleted_hitos, "personajesBorrados": deleted_people, "personajesConservados": kept_people}


def purge_storage(storage, books_bucket: str, data_bucket: str, slug: str, job_ids: list[str]) -> dict:
    """Borra del almacenamiento todo lo del libro. Los prefijos acaban en '/': borrar 'mi-libro/' no toca 'mi-libro-2/'."""
    books = storage.delete_prefix(books_bucket, f"books/{slug}/")
    translations = storage.delete_prefix(books_bucket, f"translations/{slug}/")
    intermediates = 0
    for job_id in job_ids:  # lo intermedio de cada análisis y la subida original (caducarían solos a los 30 días: se limpia ya)
        intermediates += storage.delete_prefix(data_bucket, f"jobs/{job_id}/")
        intermediates += storage.delete_prefix(data_bucket, f"uploads/{job_id}/")
    return {"libro": books, "traducciones": translations, "intermedios": intermediates}
