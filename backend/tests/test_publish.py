"""Publicar un libro en producción, con bases falsas (sin red, sin AWS): qué se copia, qué no, el orden y las protecciones."""

import json
from types import SimpleNamespace

import pytest

from lambdas.publish import core
from lambdas.publish import handler as publish_handler
from lambdas.publish.core import prod_target, publish_book, read_book, unpublish_book
from lambdas.write_mongo import store
from lambdas.write_mongo.store import ORIGIN, ContractError, Target
from tests.test_write_mongo import doc, hito_doc

NOW = 1_800_000_000.0
PROD = Target("prod", "mongodb://prod", "china-history")


def matches(document, query):
    for key, want in query.items():
        have = document.get(key)
        if isinstance(want, dict) and "$in" in want:
            if have not in want["$in"]:
                return False
        elif isinstance(have, list) and not isinstance(want, list):  # como Mongo: buscar un valor dentro de un array
            if want not in have:
                return False
        elif have != want:
            return False
    return True


class Cursor(list):
    def sort(self, *args, **kwargs):
        return self


class DevCollection:
    def __init__(self, docs):
        self.docs = docs

    def find(self, query, projection=None):
        return Cursor(d for d in self.docs if matches(d, query))


class DevDb:
    def __init__(self, chunks=(), hitos=(), personajes=()):
        self.cols = {"book_chunks": DevCollection(list(chunks)), "hitos_conocidos": DevCollection(list(hitos)), "personajes": DevCollection(list(personajes))}

    def __getitem__(self, name):
        return self.cols[name]


LINK = {"id": "rey-wu-aaaaaa", "rol": "rey", "rol_en": "king", "menciones": {"es": "Rey Wu", "en": "King Wu"}}


def dev_chunk(i, **over):
    return {**doc(i), "_id": f"oid{i}", "source": "mi-libro", "origen": ORIGIN, "jobId": "analisis-1", **over}


def dev_hito(**over):
    return {**hito_doc(), "_id": "oidh", "personajes": [LINK], "_personajes": [{"nombre": "x"}], **over}


def dev_ficha(**over):
    return {
        "_id": "oidp", "id": "rey-wu-aaaaaa", "nombre": "Rey Wu", "nombre_en": "King Wu", "rol": "rey", "rol_en": "king", "descripcion": "d", "descripcion_en": "d",
        "fuente": "mi-libro", "fuentes": ["mi-libro"], "origen": ORIGIN, "aliases": {"es": ["Rey Wu"], "en": ["King Wu"], "zh": []}, "aliasNorm": ["rey wu"], **over,
    }


class ProdCollection:
    def __init__(self, foreign=0, ours=0, found=()):
        self.foreign, self.ours, self.found = foreign, ours, list(found)
        self.deleted, self.written, self.updates = [], [], []

    def count_documents(self, query, **kwargs):
        if query.get("origen") == {"$ne": ORIGIN}:
            return self.foreign
        return self.ours

    def find(self, query, projection=None):
        return Cursor(d for d in self.found if matches(d, query))

    def bulk_write(self, operations, ordered=False):
        self.written.extend(operations)
        return SimpleNamespace(upserted_count=len(operations), matched_count=0)

    def delete_many(self, query):
        self.deleted.append(query)
        return SimpleNamespace(deleted_count=4)

    def delete_one(self, query):
        self.deleted.append(query)
        return SimpleNamespace(deleted_count=1)

    def update_one(self, query, update):
        self.updates.append((query, update))


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

def test_read_book_quita_id_y_deja_los_enlaces_y_las_fichas_que_usan():
    chunks, hitos, fichas = read_book(
        DevDb([dev_chunk(0), dev_chunk(1, source="otro")], [dev_hito(), dev_hito(fuente="otro"), dev_hito(origen="china-history")], [dev_ficha(), dev_ficha(id="otro-bbbbbb")]), "mi-libro"
    )
    assert [c["chunkIndex"] for c in chunks] == [0] and "_id" not in chunks[0]
    assert len(hitos) == 1 and "_id" not in hitos[0] and hitos[0]["personajes"] == [LINK]
    assert list(fichas) == ["rey-wu-aaaaaa"] and "_id" not in fichas["rey-wu-aaaaaa"]  # solo las fichas que enlazan los hitos


# --- publicar

def fakes(calls, duplicates=(), keep=True):
    def chunks(target, slug, docs, **kw):
        calls.append(("chunks", len(docs), kw))
        return SimpleNamespace(total=len(docs), existing=1)

    def hitos(target, slug, items, **kw):
        calls.append(("hitos", len(items), kw))
        return SimpleNamespace(total=len(items), existing=0)

    def cleanup(target, slug, job_id):
        calls.append(("cleanup", slug, job_id))
        return {"chunksBorrados": 2, "hitosBorrados": 1}

    def dedupe(target, slug, items, job_id, client):
        calls.append(("dedupe", len(items)))
        return (items if keep else []), list(duplicates)

    def people(target, slug, docs, write):
        calls.append(("personajes", [d["id"] for d in docs], write))
        return 1

    def prune(target, slug, kept):
        calls.append(("prune", sorted(kept)))
        return 2

    return dict(write_chunks_fn=chunks, write_hitos_fn=hitos, cleanup_fn=cleanup, dedupe_fn=dedupe, write_personajes_fn=people, prune_fn=prune)


def dev_world():
    return DevDb([dev_chunk(0), dev_chunk(1)], [dev_hito()], [dev_ficha()])


def test_la_vista_previa_calcula_todo_pero_no_limpia_ni_poda(prod):
    calls = []
    report = publish_book(dev_world(), PROD, "mi-libro", "job1", write=False, **fakes(calls))
    assert report.dry_run and (report.chunks, report.hitos, report.personajes, report.personajes_replaced) == (2, 1, 1, 1)
    assert [c[0] for c in calls] == ["chunks", "dedupe", "personajes", "hitos"]  # sin cleanup ni prune
    assert calls[0][2]["write"] is False and calls[2][2] is False and calls[3][2]["write"] is False


def test_publicar_escribe_fichas_antes_que_hitos_y_limpia_lo_anterior(prod):
    calls = []
    report = publish_book(dev_world(), PROD, "mi-libro", "job1", write=True, **fakes(calls, duplicates=[{"id": "x"}]))
    assert [c[0] for c in calls] == ["chunks", "dedupe", "personajes", "hitos", "cleanup", "prune"]  # fichas ANTES que hitos: ningún enlace colgando
    hitos_call = calls[3]
    assert hitos_call[2]["dedupe"] is False and hitos_call[2]["personajes"] is False and hitos_call[2]["job_id"] == "job1"
    assert calls[2][1] == ["rey-wu-aaaaaa"] and calls[5][1] == ["rey-wu-aaaaaa"]
    assert (report.removed_chunks, report.removed_hitos, report.removed_personajes, len(report.duplicates)) == (2, 1, 2, 1)
    summary = report.summary()
    assert summary["hitosDuplicados"] == 1 and summary["personajesPublicados"] == 1 and summary["dryRun"] is False


def test_un_hito_descartado_por_duplicado_no_arrastra_sus_fichas(prod):
    calls = []
    report = publish_book(dev_world(), PROD, "mi-libro", "job1", write=True, **fakes(calls, keep=False))
    assert report.hitos == 0 and report.personajes == 0 and calls[2][1] == []


def test_un_enlace_a_una_ficha_que_no_existe_en_dev_se_rechaza_antes_de_escribir_hitos(prod):
    calls = []
    with pytest.raises(ContractError, match="no existen en dev"):
        publish_book(DevDb([dev_chunk(0)], [dev_hito()], []), PROD, "mi-libro", "job1", write=True, **fakes(calls))
    assert "hitos" not in [c[0] for c in calls] and "personajes" not in [c[0] for c in calls]


def test_un_libro_sin_fragmentos_no_se_publica(prod):
    with pytest.raises(ContractError, match="no tiene fragmentos"):
        publish_book(DevDb(), PROD, "mi-libro", "job1", write=True)


def test_la_escritura_real_deja_marca_jobid_sin_id_de_dev_y_con_los_enlaces(prod):
    prod["book_chunks"], prod["hitos_conocidos"], prod["personajes"] = ProdCollection(), ProdCollection(), ProdCollection()
    chunks, hitos, fichas = read_book(dev_world(), "mi-libro")
    store.write_chunks(PROD, "mi-libro", chunks, write=True, job_id="job1")
    assert len(prod["book_chunks"].written) == 2
    op = prod["book_chunks"].written[0]
    assert "_id" not in op._doc and op._doc["origen"] == ORIGIN and op._doc["jobId"] == "job1"
    assert core.write_personajes(PROD, "mi-libro", list(fichas.values()), write=True) == 0
    (pop,) = prod["personajes"].written
    assert "_id" not in pop._doc and pop._filter == {"id": "rey-wu-aaaaaa", "origen": ORIGIN}
    store.write_hitos(PROD, "mi-libro", hitos, write=True, dedupe=False, personajes=False, job_id="job1")
    (hop,) = prod["hitos_conocidos"].written
    assert "_id" not in hop._doc and hop._doc["jobId"] == "job1" and hop._doc["personajes"] == [LINK]
    assert hop._filter["origen"] == ORIGIN


def test_una_ficha_ya_publicada_por_otro_libro_conserva_sus_fuentes(prod):
    prod["personajes"] = ProdCollection(found=[{"id": "rey-wu-aaaaaa", "origen": ORIGIN, "fuentes": ["otro-libro"]}])
    existing = core.write_personajes(PROD, "mi-libro", [dev_ficha()], write=True)
    (op,) = prod["personajes"].written
    assert existing == 1 and op._doc["fuentes"] == ["mi-libro", "otro-libro"]


def test_la_vista_previa_valida_las_fichas_aunque_no_escriba(prod):
    with pytest.raises(ContractError):
        core.write_personajes(PROD, "mi-libro", [dev_ficha(nombre_en="")], write=False)
    assert "personajes" not in prod or prod["personajes"].written == []


def test_republicar_poda_las_fichas_que_el_libro_ya_no_usa(prod):
    prod["personajes"] = ProdCollection(found=[{"id": "viejo-cccccc", "origen": ORIGIN, "fuentes": ["mi-libro"]}, {"id": "compartido-dddddd", "origen": ORIGIN, "fuentes": ["mi-libro"]}, {"id": "rey-wu-aaaaaa", "origen": ORIGIN, "fuentes": ["mi-libro"]}])

    class Hitos(ProdCollection):
        def count_documents(self, query, **kwargs):
            return 1 if query.get("personajes.id") == "compartido-dddddd" else 0

    prod["hitos_conocidos"] = Hitos()
    assert core.prune_personajes(PROD, "mi-libro", {"rey-wu-aaaaaa"}) == 1
    assert prod["personajes"].deleted == [{"id": "viejo-cccccc", "origen": ORIGIN}]  # la que nadie usa se borra
    assert prod["personajes"].updates == [({"id": "compartido-dddddd", "origen": ORIGIN}, {"$pull": {"fuentes": "mi-libro"}})]  # la de otro libro, solo pierde este


def test_si_el_slug_esta_ocupado_por_un_libro_ajeno_se_rechaza_sin_escribir(prod):
    prod["book_chunks"] = ProdCollection(foreign=5)
    prod["hitos_conocidos"], prod["personajes"] = ProdCollection(), ProdCollection()
    with pytest.raises(ContractError, match="no son de esta app"):
        publish_book(dev_world(), PROD, "mi-libro", "job1", write=True, dedupe_fn=lambda *a: (_ for _ in ()).throw(AssertionError("no debe llegar")))
    assert all(c.written == [] for c in prod.values())


# --- despublicar y destino

def test_despublicar_usa_la_rutina_de_borrado_solo_con_nuestra_marca(prod):
    prod["hitos_conocidos"] = ProdCollection(found=[{"fuente": "mi-libro", "origen": ORIGIN, "personajes": [{"id": "rey-wu-aaaaaa"}]}])
    prod["personajes"] = ProdCollection(found=[{"id": "rey-wu-aaaaaa", "origen": ORIGIN, "fuentes": ["mi-libro"]}])
    result = unpublish_book(PROD, "mi-libro")
    assert result["chunks"] == 4 and result["hitos"] == 4
    assert prod["book_chunks"].deleted == [{"source": "mi-libro", "origen": ORIGIN}]
    assert prod["hitos_conocidos"].deleted == [{"origen": ORIGIN, "fuente": "mi-libro"}]
    assert set(prod) == {"book_chunks", "hitos_conocidos", "personajes"}


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
    assert response["statusCode"] == 200 and json.loads(response["body"])["removed"]["chunks"] == 4
    assert books.updates == [("mi-libro", "REMOVE published", None)]


# --- la ejecución

def patched(monkeypatch):
    calls = []
    f = fakes(calls)
    monkeypatch.setattr(publish_handler, "publish_book", lambda *a, **kw: publish_book(*a, **f, **kw))
    return calls


def test_ejecucion_real_apunta_el_resumen_y_el_catalogo(prod, monkeypatch):
    patched(monkeypatch)
    jobs = Jobs([{"jobId": "j1", "status": "RUNNING"}])
    books = Books([{"slug": "mi-libro"}])
    result = publish_handler.run_handler({"jobId": "j1", "slug": "mi-libro", "dryRun": False}, jobs_table=jobs, books_table=books, dev_db=dev_world(), target=PROD)
    assert result == {"ok": True} and jobs.items["j1"]["status"] == "DONE"
    summary = json.loads(jobs.items["j1"]["summary"])
    assert summary["chunks"] == 2 and summary["personajesPublicados"] == 1
    (slug, expression, values) = books.updates[0]
    assert expression == "SET published = :p" and values[":p"]["chunks"] == 2 and values[":p"]["personajes"] == 1 and values[":p"]["jobId"] == "j1"


def test_la_vista_previa_no_toca_el_catalogo(prod, monkeypatch):
    patched(monkeypatch)
    jobs, books = Jobs([{"jobId": "j1", "status": "RUNNING"}]), Books([{"slug": "mi-libro"}])
    publish_handler.run_handler({"jobId": "j1", "slug": "mi-libro", "dryRun": True}, jobs_table=jobs, books_table=books, dev_db=dev_world(), target=PROD)
    assert books.updates == [] and json.loads(jobs.items["j1"]["summary"])["dryRun"] is True


def test_un_error_de_contrato_queda_en_el_trabajo_sin_relanzarse(prod):
    jobs = Jobs([{"jobId": "j1", "status": "RUNNING"}])
    result = publish_handler.run_handler({"jobId": "j1", "slug": "mi-libro", "dryRun": False}, jobs_table=jobs, books_table=Books(), dev_db=DevDb(), target=PROD)
    assert result == {"ok": False} and jobs.items["j1"]["status"] == "FAILED" and "no tiene fragmentos" in jobs.items["j1"]["error"]
