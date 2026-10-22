"""Borrar un libro: qué se borra, qué NUNCA se toca y cómo se comporta si algo falla. Mongo, S3 y DynamoDB falsos en memoria (sin red)."""

import json
from types import SimpleNamespace

import pytest

from lambdas.delete_book.handler import handler
from lambdas.delete_book.purge import purge_mongo, purge_storage
from lambdas.write_mongo.store import ORIGIN
from shared.storage import LocalStorage


# ---- una Mongo falsa que entiende lo que usa el borrado --------------------------------------------------------------------------------------


def values(doc, path):
    """Los valores de un camino con puntos ('personajes.id'), atravesando listas como hace Mongo."""
    nodes = [doc]
    for part in path.split("."):
        nxt = []
        for node in nodes:
            for item in node if isinstance(node, list) else [node]:
                if isinstance(item, dict) and part in item:
                    nxt.append(item[part])
        nodes = nxt
    return [leaf for node in nodes for leaf in (node if isinstance(node, list) else [node])]


class FakeCollection:
    def __init__(self, docs):
        self.docs = list(docs)

    def _match(self, doc, query):
        return all(want in values(doc, key) for key, want in query.items())

    def find(self, query, projection=None):
        return [dict(doc) for doc in self.docs if self._match(doc, query)]

    def count_documents(self, query, limit=0):
        return sum(1 for doc in self.docs if self._match(doc, query))

    def delete_many(self, query):
        before = len(self.docs)
        self.docs = [doc for doc in self.docs if not self._match(doc, query)]
        return SimpleNamespace(deleted_count=before - len(self.docs))

    def delete_one(self, query):
        for i, doc in enumerate(self.docs):
            if self._match(doc, query):
                del self.docs[i]
                return SimpleNamespace(deleted_count=1)
        return SimpleNamespace(deleted_count=0)

    def update_one(self, query, update):
        for doc in self.docs:
            if self._match(doc, query):
                for key, value in update.get("$pull", {}).items():
                    doc[key] = [x for x in doc.get(key, []) if x != value]
                return


class FakeDB:
    def __init__(self, chunks=(), hitos=(), personajes=()):
        self.c = {"book_chunks": FakeCollection(chunks), "hitos_conocidos": FakeCollection(hitos), "personajes": FakeCollection(personajes)}

    def __getitem__(self, name):
        return self.c[name]


def chunk(source, index, origen=ORIGIN):
    return {"source": source, "chunkIndex": index, "origen": origen}


def hito(hito_id, fuente, people=(), origen=ORIGIN):
    return {"id": hito_id, "fuente": fuente, "origen": origen, "personajes": [{"id": p, "rol": "rey"} for p in people]}


def person(person_id, fuentes, origen=ORIGIN):
    return {"id": person_id, "fuentes": list(fuentes), "origen": origen}


def world():
    return FakeDB(
        chunks=[chunk("lib-a", 0), chunk("lib-a", 1), chunk("lib-b", 0), chunk("lib-a", 7, origen="china-history")],
        hitos=[hito("h1", "lib-a", ["solo-a", "compartido"]), hito("h2", "lib-b", ["compartido"]), hito("h3", "lib-a", origen="china-history")],
        personajes=[person("solo-a", ["lib-a"]), person("compartido", ["lib-a", "lib-b"]), person("ajeno", ["lib-a"], origen="china-history"), person("suelto", ["lib-a"])],
    )


# ---- Mongo ----------------------------------------------------------------------------------------------------------------------------------


def test_borra_los_chunks_y_los_hitos_de_ese_libro_y_nada_mas():
    db = world()
    result = purge_mongo(db, "lib-a")
    assert result["chunks"] == 2 and result["hitos"] == 1
    assert [(c["source"], c["origen"]) for c in db["book_chunks"].docs] == [("lib-b", ORIGIN), ("lib-a", "china-history")]  # el de otro libro y el de china-history siguen
    assert [h["id"] for h in db["hitos_conocidos"].docs] == ["h2", "h3"]  # el de china-history con el mismo slug sigue


def test_un_personaje_compartido_se_conserva_sin_este_libro_y_el_que_era_solo_suyo_se_borra():
    db = world()
    result = purge_mongo(db, "lib-a")
    ids = {p["id"]: p for p in db["personajes"].docs}
    assert "solo-a" not in ids and "suelto" not in ids  # el suelto: tenía el libro entre sus fuentes pero ningún hito lo usaba
    assert ids["compartido"]["fuentes"] == ["lib-b"]  # lo sigue usando el libro b
    assert "ajeno" in ids and ids["ajeno"]["fuentes"] == ["lib-a"]  # una ficha de china-history no se toca, aunque cite el slug
    assert result["personajesBorrados"] == 2 and result["personajesConservados"] == 1


def test_repetir_el_borrado_no_rompe_nada():
    db = world()
    purge_mongo(db, "lib-a")
    again = purge_mongo(db, "lib-a")
    assert again == {"chunks": 0, "hitos": 0, "personajesBorrados": 0, "personajesConservados": 0}


# ---- almacenamiento -------------------------------------------------------------------------------------------------------------------------


def put(storage, bucket, key):
    target = storage._path(bucket, key)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(b"x")


def test_borra_el_pdf_la_portada_las_traducciones_y_lo_intermedio_pero_no_un_libro_de_nombre_parecido(tmp_path):
    storage = LocalStorage(tmp_path / "s3")
    for key in ["books/lib-a/original.pdf", "books/lib-a/cover.jpg", "translations/lib-a/v2/0001.json", "books/lib-a-2/original.pdf", "translations/lib-a-2/v2/0001.json"]:
        put(storage, "libros", key)
    for key in ["jobs/j1/chunks.json", "uploads/j1/libro.pdf", "jobs/j2/chunks.json", "jobs/otro/chunks.json"]:
        put(storage, "datos", key)
    result = purge_storage(storage, "libros", "datos", "lib-a", ["j1", "j2"])
    assert result == {"libro": 2, "traducciones": 1, "intermedios": 3}
    assert storage.exists("libros", "books/lib-a-2/original.pdf") and storage.exists("libros", "translations/lib-a-2/v2/0001.json")  # 'lib-a-2' no es 'lib-a'
    assert storage.exists("datos", "jobs/otro/chunks.json")  # un análisis de otro libro no se toca
    assert not storage.exists("libros", "books/lib-a/cover.jpg") and not storage.exists("datos", "uploads/j1/libro.pdf")


# ---- la petición ----------------------------------------------------------------------------------------------------------------------------


class FakeTable:
    def __init__(self, items, key):
        self.items, self.key, self.deleted = {item[key]: item for item in items}, key, []

    def get_item(self, Key):
        item = self.items.get(Key[self.key])
        return {"Item": item} if item else {}

    def scan(self, **kwargs):
        return {"Items": list(self.items.values())}

    def delete_item(self, Key):
        self.deleted.append(Key[self.key])
        self.items.pop(Key[self.key], None)


NOW = 1_800_000_000.0


@pytest.fixture(autouse=True)
def env(monkeypatch):
    monkeypatch.setenv("BOOKS_BUCKET", "libros")
    monkeypatch.setenv("UPLOAD_BUCKET", "datos")


def setup(tmp_path, jobs=(), with_book=True):
    storage = LocalStorage(tmp_path / "s3")
    for key in ["books/lib-a/original.pdf", "translations/lib-a/v2/0001.json"]:
        put(storage, "libros", key)
    put(storage, "datos", "jobs/j1/chunks.json")
    books = FakeTable([{"slug": "lib-a", "title": "A"}] if with_book else [], "slug")
    jobs_table = FakeTable(list(jobs), "jobId")
    return storage, books, jobs_table


def delete(slug, storage, books, jobs_table, db=None):
    event = {"routeKey": "DELETE /books/{slug}", "pathParameters": {"slug": slug}}
    response = handler(event, db=db if db is not None else world(), books_table=books, jobs_table=jobs_table, storage=storage, now=NOW)
    return response["statusCode"], json.loads(response["body"])


def job(job_id, status, source="lib-a", age=60):
    from datetime import datetime, timezone

    return {"jobId": job_id, "status": status, "source": source, "createdAt": datetime.fromtimestamp(NOW - age, timezone.utc).isoformat()}


def test_borra_todo_y_el_catalogo_el_ultimo(tmp_path):
    storage, books, jobs_table = setup(tmp_path, [job("j1", "DONE"), job("j9", "DONE", source="otro")])
    status, body = delete("lib-a", storage, books, jobs_table)
    assert status == 200 and body["deleted"]["trabajos"] == 1 and body["deleted"]["mongo"]["chunks"] == 2
    assert books.deleted == ["lib-a"] and jobs_table.deleted == ["j1"]  # solo el trabajo de ESE libro
    assert not storage.exists("libros", "books/lib-a/original.pdf") and not storage.exists("datos", "jobs/j1/chunks.json")


def test_si_el_borrado_de_archivos_falla_el_libro_sigue_en_la_lista_para_reintentar(tmp_path):
    storage, books, jobs_table = setup(tmp_path, [job("j1", "DONE")])

    class Roto(LocalStorage):
        def delete_prefix(self, bucket, prefix):
            raise RuntimeError("S3 no responde (clave secreta)")

    status, body = delete("lib-a", Roto(tmp_path / "s3"), books, jobs_table)
    assert status == 502 and "secreta" not in json.dumps(body)
    assert books.deleted == [] and jobs_table.deleted == []  # nada del registro se borró: se puede repetir


def test_un_libro_que_no_existe_es_404_y_repetir_un_borrado_ya_hecho_tambien(tmp_path):
    storage, books, jobs_table = setup(tmp_path, with_book=False)
    assert delete("fantasma", storage, books, jobs_table)[0] == 404
    storage, books, jobs_table = setup(tmp_path, [job("j1", "DONE")])
    assert delete("lib-a", storage, books, jobs_table)[0] == 200
    assert delete("lib-a", storage, books, jobs_table)[0] == 404


def test_no_se_borra_mientras_se_analiza_ni_durante_una_subida_reciente(tmp_path):
    for busy in [job("j1", "RUNNING"), job("j1", "UPLOADING", age=60)]:
        storage, books, jobs_table = setup(tmp_path, [busy])
        status, body = delete("lib-a", storage, books, jobs_table)
        assert status == 409 and "analizando" in body["error"]
        assert books.deleted == [] and storage.exists("libros", "books/lib-a/original.pdf")


def test_una_subida_abandonada_hace_rato_no_impide_borrar(tmp_path):
    storage, books, jobs_table = setup(tmp_path, [job("j1", "UPLOADING", age=3600)])
    assert delete("lib-a", storage, books, jobs_table)[0] == 200


@pytest.mark.parametrize("slug", ["../x", "a/b", "A mayusculas", "x", ""])
def test_slugs_peligrosos_son_400_y_no_tocan_nada(tmp_path, slug):
    storage, books, jobs_table = setup(tmp_path)
    assert delete(slug, storage, books, jobs_table)[0] == 400
    assert books.deleted == [] and storage.exists("libros", "books/lib-a/original.pdf")


def test_una_ruta_distinta_es_404():
    assert handler({"routeKey": "GET /books/{slug}"})["statusCode"] == 404


def test_con_unpublish_tambien_se_quita_de_produccion_y_antes_que_lo_demas(tmp_path, monkeypatch):
    from lambdas.delete_book import handler as delete_handler

    calls = []
    monkeypatch.setattr(delete_handler, "unpublish_book", lambda target, slug: calls.append((target, slug)) or {"chunks": 3, "hitos": 1})
    storage, books, jobs_table = setup(tmp_path, [job("j1", "DONE")])
    event = {"routeKey": "DELETE /books/{slug}", "pathParameters": {"slug": "lib-a"}, "queryStringParameters": {"unpublish": "1"}}
    response = handler(event, db=world(), books_table=books, jobs_table=jobs_table, storage=storage, now=NOW, prod="PROD")
    assert calls == [("PROD", "lib-a")] and json.loads(response["body"])["deleted"]["produccion"] == {"chunks": 3, "hitos": 1}


def test_sin_unpublish_producción_no_se_toca(tmp_path, monkeypatch):
    from lambdas.delete_book import handler as delete_handler

    monkeypatch.setattr(delete_handler, "unpublish_book", lambda *a: pytest.fail("no debe tocar producción"))
    storage, books, jobs_table = setup(tmp_path, [job("j1", "DONE")])
    assert delete("lib-a", storage, books, jobs_table)[0] == 200
