"""Publicar un libro en producción, con bases falsas (sin red, sin AWS): qué se copia, qué no, y las protecciones."""

import json
from types import SimpleNamespace

import pytest

from lambdas.publish import handler as publish_handler
from lambdas.publish.core import prod_target, publish_book, read_book, unpublish_book
from lambdas.write_mongo import store
from lambdas.write_mongo.store import ORIGIN, ContractError, Target
from tests.test_write_mongo import doc, hito_doc

NOW = 1_800_000_000.0
PROD = Target("prod", "mongodb://prod", "china-history")


class Cursor(list):
    def sort(self, *args, **kwargs):
        return self


class DevCollection:
    def __init__(self, docs):
        self.docs = docs

    def find(self, query):
        return Cursor(d for d in self.docs if all(d.get(k) == v for k, v in query.items()))


class DevDb:
    def __init__(self, chunks=(), hitos=()):
        self.cols = {"book_chunks": DevCollection(list(chunks)), "hitos_conocidos": DevCollection(list(hitos))}

    def __getitem__(self, name):
        return self.cols[name]


def dev_chunk(i, **over):
    return {**doc(i), "_id": f"oid{i}", "source": "mi-libro", "origen": ORIGIN, "jobId": "analisis-1", **over}


def dev_hito(**over):
    return {**hito_doc(), "_id": "oidh", "personajes": [{"id": "p1", "rol": "rey"}], "_personajes": [{"nombre": "x"}], **over}


class ProdCollection:
    def __init__(self, foreign=0, ours=0):
        self.foreign, self.ours, self.deleted, self.written = foreign, ours, [], []

    def count_documents(self, query, **kwargs):
        if query.get("origen") == {"$ne": ORIGIN}:
            return self.foreign
        return self.ours

    def bulk_write(self, operations, ordered=False):
        self.written.extend(operations)
        return SimpleNamespace(upserted_count=len(operations), matched_count=0)

    def delete_many(self, query):
        self.deleted.append(query)
        return SimpleNamespace(deleted_count=4)


class ProdMongo:
    cols: dict = {}

    def __init__(self, uri, **kwargs):
        pass

    def __getitem__(self, db_name):
        assert db_name == "china-history"
        return _Db(ProdMongo.cols)

    def close(self):
        pass


class _Db:
    def __init__(self, cols):
        self.cols = cols

    def __getitem__(self, name):
        return self.cols.setdefault(name, ProdCollection())


@pytest.fixture
def prod(monkeypatch):
    ProdMongo.cols = {}
    monkeypatch.setattr(store, "MongoClient", ProdMongo)
    return ProdMongo.cols


# --- leer el libro en dev

def test_read_book_quita_id_y_enlaces_a_personajes():
    chunks, hitos = read_book(DevDb([dev_chunk(0), dev_chunk(1, source="otro")], [dev_hito(), dev_hito(fuente="otro"), dev_hito(origen="china-history")]), "mi-libro")
    assert [c["chunkIndex"] for c in chunks] == [0] and "_id" not in chunks[0]
    assert len(hitos) == 1 and "_id" not in hitos[0] and "personajes" not in hitos[0]


# --- publicar

def fake_fns(calls, duplicates=()):
    def chunks(target, slug, docs, **kw):
        calls.append(("chunks", target.db_name, slug, len(docs), kw))
        return SimpleNamespace(total=len(docs), existing=1)

    def hitos(target, slug, items, **kw):
        calls.append(("hitos", target.db_name, slug, len(items), kw))
        return SimpleNamespace(total=len(items), existing=0, duplicates=list(duplicates))

    def cleanup(target, slug, job_id):
        calls.append(("cleanup", slug, job_id))
        return {"chunksBorrados": 2, "hitosBorrados": 1}

    return chunks, hitos, cleanup


def test_la_vista_previa_no_escribe_ni_limpia(prod):
    calls = []
    c, h, k = fake_fns(calls)
    report = publish_book(DevDb([dev_chunk(0), dev_chunk(1)], [dev_hito()]), PROD, "mi-libro", "job1", write=False, write_chunks_fn=c, write_hitos_fn=h, cleanup_fn=k)
    assert report.dry_run and report.chunks == 2 and report.hitos == 1
    assert [call[0] for call in calls] == ["chunks", "hitos"]  # sin cleanup
    assert all(call[4]["write"] is False for call in calls)


def test_publicar_pasa_job_id_sin_personajes_y_limpia_lo_anterior(prod):
    calls = []
    c, h, k = fake_fns(calls, duplicates=[{"id": "x"}])
    report = publish_book(DevDb([dev_chunk(0)], [dev_hito()]), PROD, "mi-libro", "job1", write=True, write_chunks_fn=c, write_hitos_fn=h, cleanup_fn=k)
    chunks_call, hitos_call, cleanup_call = calls
    assert chunks_call[4]["job_id"] == "job1" and chunks_call[4]["write"] is True
    assert hitos_call[4]["personajes"] is False and hitos_call[4]["dedupe"] is True and hitos_call[4]["job_id"] == "job1"
    assert cleanup_call == ("cleanup", "mi-libro", "job1")
    assert (report.removed_chunks, report.removed_hitos, len(report.duplicates)) == (2, 1, 1)
    assert report.summary()["hitosDuplicados"] == 1 and report.summary()["dryRun"] is False


def test_un_libro_sin_fragmentos_no_se_publica(prod):
    with pytest.raises(ContractError, match="no tiene fragmentos"):
        publish_book(DevDb(), PROD, "mi-libro", "job1", write=True)


def test_con_la_escritura_real_no_viaja_el_id_de_dev_y_lleva_marca_y_jobid(prod):
    prod["book_chunks"] = ProdCollection()
    prod["hitos_conocidos"] = ProdCollection()
    chunks, hitos = read_book(DevDb([dev_chunk(0)], [dev_hito()]), "mi-libro")
    store.write_chunks(PROD, "mi-libro", chunks, write=True, job_id="job1")
    (op,) = prod["book_chunks"].written
    assert "_id" not in op._doc and op._doc["origen"] == ORIGIN and op._doc["jobId"] == "job1"
    store.write_hitos(PROD, "mi-libro", hitos, write=True, dedupe=False, personajes=False, job_id="job1")
    (hop,) = prod["hitos_conocidos"].written
    assert "_id" not in hop._doc and "personajes" not in hop._doc and hop._doc["jobId"] == "job1"
    assert hop._filter["origen"] == ORIGIN


def test_si_el_slug_esta_ocupado_por_un_libro_ajeno_se_rechaza_sin_escribir(prod):
    prod["book_chunks"] = ProdCollection(foreign=5)
    prod["hitos_conocidos"] = ProdCollection()
    with pytest.raises(ContractError, match="no son de esta app"):
        publish_book(DevDb([dev_chunk(0)], [dev_hito()]), PROD, "mi-libro", "job1", write=True)
    assert prod["book_chunks"].written == [] and prod["hitos_conocidos"].written == []


# --- despublicar y destino

def test_despublicar_solo_borra_con_nuestra_marca_y_en_las_dos_colecciones(prod):
    assert unpublish_book(PROD, "mi-libro") == {"chunks": 4, "hitos": 4}
    assert prod["book_chunks"].deleted == [{"source": "mi-libro", "origen": ORIGIN}]
    assert prod["hitos_conocidos"].deleted == [{"fuente": "mi-libro", "origen": ORIGIN}]
    assert set(prod) == {"book_chunks", "hitos_conocidos"}  # ninguna otra colección (personajes incluida)


def test_prod_usa_la_uri_de_dev_si_no_hay_una_de_prod(monkeypatch):
    monkeypatch.delenv("MONGODB_URI_PROD", raising=False)
    monkeypatch.delenv("SSM_PREFIX", raising=False)
    monkeypatch.setenv("MONGODB_URI_DEV", "mongodb://dev")
    target = prod_target()
    assert (target.uri, target.db_name) == ("mongodb://dev", "china-history")


def test_prod_nunca_puede_ser_la_base_de_dev(monkeypatch):
    monkeypatch.setenv("MONGODB_URI_DEV", "mongodb://dev")
    monkeypatch.delenv("MONGODB_URI_PROD", raising=False)
    monkeypatch.setenv("MONGODB_DB_PROD", "china_documents_dev")
    with pytest.raises(ContractError, match="misma que la de dev"):
        prod_target()


# --- la API

class Books:
    def __init__(self, items=()):
        self.items = {i["slug"]: dict(i) for i in items}
        self.updates = []

    def get_item(self, Key):
        item = self.items.get(Key["slug"])
        return {"Item": item} if item else {}

    def update_item(self, Key, UpdateExpression, **kwargs):
        self.updates.append((Key["slug"], UpdateExpression, kwargs.get("ExpressionAttributeValues")))


class Jobs:
    def __init__(self, existing=()):
        self.items = {j["jobId"]: dict(j) for j in existing}

    def scan(self, **kwargs):
        return {"Items": list(self.items.values())}

    def put_item(self, Item):
        self.items[Item["jobId"]] = dict(Item)

    def update_item(self, Key, UpdateExpression, ExpressionAttributeNames, ExpressionAttributeValues):
        job = self.items[Key["jobId"]]
        job["status"] = ExpressionAttributeValues[":s"]
        job["error"] = ExpressionAttributeValues.get(":e")
        job["summary"] = ExpressionAttributeValues.get(":m")


class Lam:
    def __init__(self, fail=False):
        self.calls, self.fail = [], fail

    def invoke(self, **kwargs):
        if self.fail:
            raise RuntimeError("AccessDenied")
        self.calls.append(kwargs)


@pytest.fixture(autouse=True)
def env(monkeypatch):
    monkeypatch.setenv("PUBLISH_RUN_FUNCTION", "run-fn")


def call(method="POST", body=None, books=None, jobs=None, lam=None, slug="mi-libro"):
    event = {"routeKey": f"{method} /books/{{slug}}/publish", "pathParameters": {"slug": slug}}
    if body is not None:
        event["body"] = json.dumps(body)
    books = books if books is not None else Books([{"slug": "mi-libro", "title": "Mi libro", "analysis": "done"}])
    jobs = jobs if jobs is not None else Jobs()
    lam = lam or Lam()
    response = publish_handler.handler(event, books_table=books, jobs_table=jobs, lambda_client=lam, target=PROD, now=NOW, new_id=lambda: "a" * 32)
    return response, books, jobs, lam


def test_sin_dryrun_explicito_no_hace_nada():
    for body in (None, {}, {"dryRun": "false"}, {"dryRun": 0}):
        response, _, jobs, lam = call(body=body)
        assert response["statusCode"] == 400 and not jobs.items and not lam.calls


def test_pedir_la_vista_previa_apunta_el_trabajo_y_lanza_la_ejecucion_asincrona():
    response, _, jobs, lam = call(body={"dryRun": True})
    assert response["statusCode"] == 202 and json.loads(response["body"]) == {"jobId": "a" * 32, "dryRun": True}
    assert jobs.items["a" * 32]["kind"] == "publish" and jobs.items["a" * 32]["dryRun"] is True
    (invocation,) = lam.calls
    assert invocation["InvocationType"] == "Event" and json.loads(invocation["Payload"]) == {"jobId": "a" * 32, "slug": "mi-libro", "dryRun": True}


def test_libro_inexistente_sin_analizar_u_ocupado():
    assert call(body={"dryRun": True}, books=Books())[0]["statusCode"] == 404
    assert call(body={"dryRun": True}, books=Books([{"slug": "mi-libro", "analysis": "none"}]))[0]["statusCode"] == 409
    running = Jobs([{"jobId": "b" * 32, "source": "mi-libro", "status": "RUNNING"}])
    assert call(body={"dryRun": True}, jobs=running)[0]["statusCode"] == 409


def test_si_no_se_puede_lanzar_el_trabajo_queda_como_fallido():
    response, _, jobs, _ = call(body={"dryRun": False}, lam=Lam(fail=True))
    assert response["statusCode"] == 502 and jobs.items["a" * 32]["status"] == "FAILED"
    assert "AccessDenied" not in response["body"]


def test_despublicar_borra_y_quita_la_marca_del_catalogo(prod):
    response, books, _, _ = call("DELETE")
    assert response["statusCode"] == 200 and json.loads(response["body"]) == {"removed": {"chunks": 4, "hitos": 4}}
    assert books.updates == [("mi-libro", "REMOVE published", None)]


# --- la ejecución

def test_ejecucion_real_apunta_el_resumen_y_el_catalogo(prod, monkeypatch):
    calls = []
    c, h, k = fake_fns(calls)
    monkeypatch.setattr(publish_handler, "publish_book", lambda *a, **kw: publish_book(*a, write_chunks_fn=c, write_hitos_fn=h, cleanup_fn=k, **kw))
    jobs = Jobs([{"jobId": "j1", "status": "RUNNING"}])
    books = Books([{"slug": "mi-libro"}])
    result = publish_handler.run_handler({"jobId": "j1", "slug": "mi-libro", "dryRun": False}, jobs_table=jobs, books_table=books, dev_db=DevDb([dev_chunk(0)], [dev_hito()]), target=PROD)
    assert result == {"ok": True} and jobs.items["j1"]["status"] == "DONE"
    assert json.loads(jobs.items["j1"]["summary"])["chunks"] == 1
    (slug, expression, values) = books.updates[0]
    assert expression == "SET published = :p" and values[":p"]["chunks"] == 1 and values[":p"]["jobId"] == "j1"


def test_la_vista_previa_no_toca_el_catalogo(prod, monkeypatch):
    calls = []
    c, h, k = fake_fns(calls)
    monkeypatch.setattr(publish_handler, "publish_book", lambda *a, **kw: publish_book(*a, write_chunks_fn=c, write_hitos_fn=h, cleanup_fn=k, **kw))
    jobs, books = Jobs([{"jobId": "j1", "status": "RUNNING"}]), Books([{"slug": "mi-libro"}])
    publish_handler.run_handler({"jobId": "j1", "slug": "mi-libro", "dryRun": True}, jobs_table=jobs, books_table=books, dev_db=DevDb([dev_chunk(0)], [dev_hito()]), target=PROD)
    assert books.updates == [] and json.loads(jobs.items["j1"]["summary"])["dryRun"] is True


def test_un_error_de_contrato_queda_en_el_trabajo_sin_relanzarse(prod):
    jobs = Jobs([{"jobId": "j1", "status": "RUNNING"}])
    result = publish_handler.run_handler({"jobId": "j1", "slug": "mi-libro", "dryRun": False}, jobs_table=jobs, books_table=Books(), dev_db=DevDb(), target=PROD)
    assert result == {"ok": False} and jobs.items["j1"]["status"] == "FAILED" and "no tiene fragmentos" in jobs.items["j1"]["error"]
