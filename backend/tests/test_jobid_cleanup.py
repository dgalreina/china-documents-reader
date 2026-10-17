"""Tests de jobId, marca de origen y limpieza de ejecuciones anteriores, con un Mongo falso (sin red)."""

from types import SimpleNamespace

import pytest

from lambdas.write_mongo import store
from lambdas.write_mongo.store import ORIGIN, ContractError, Target, build_hito_operations, build_operations, cleanup_old_runs, mongo_neighbors
from tests.test_write_mongo import doc, hito_doc


class FakeCollection:
    def __init__(self, foreign=0):
        self.foreign, self.deleted, self.written = foreign, [], []

    def count_documents(self, query, **kwargs):
        return self.foreign if query.get("origen") == {"$ne": ORIGIN} else 0

    def bulk_write(self, operations, ordered=False):
        self.written.extend(operations)
        return SimpleNamespace(upserted_count=len(operations), matched_count=0)

    def delete_many(self, query):
        self.deleted.append(query)
        return SimpleNamespace(deleted_count=3)


class FakeDb:
    def __init__(self, collections):
        self.collections = collections

    def __getitem__(self, name):
        return self.collections.setdefault(name, FakeCollection())


class FakeMongo:
    collections: dict = {}

    def __init__(self, uri, **kwargs):
        pass

    def __getitem__(self, db_name):  # cliente[base][coleccion], como el real
        return FakeDb(FakeMongo.collections)

    def close(self):
        pass


@pytest.fixture
def fake_mongo(monkeypatch):
    FakeMongo.collections = {}
    monkeypatch.setattr(store, "MongoClient", FakeMongo)
    return FakeMongo.collections


TARGET = Target("dev", "mongodb://x", "china_documents_dev")


def test_los_chunks_se_escriben_con_origen_y_jobid():
    (op,) = build_operations("libro", [doc(0)], job_id="job-7")
    assert op._doc["origen"] == ORIGIN and op._doc["jobId"] == "job-7" and op._doc["source"] == "libro"


def test_sin_jobid_no_se_anade_el_campo():
    (op,) = build_operations("libro", [doc(0)])
    assert "jobId" not in op._doc and op._doc["origen"] == ORIGIN


def test_los_hitos_llevan_el_jobid():
    (op,) = build_hito_operations("mi-libro", [hito_doc()], job_id="job-7")
    assert op._doc["jobId"] == "job-7" and op._doc["origen"] == ORIGIN


def test_no_se_escribe_si_el_source_ya_tiene_chunks_ajenos(fake_mongo):
    # Un libro de china-history con el mismo slug: reemplazar por (source, chunkIndex) lo pisaría.
    fake_mongo["book_chunks"] = FakeCollection(foreign=5)
    with pytest.raises(ContractError, match="ya tiene chunks que no son de esta app"):
        store.write_chunks(TARGET, "gernet-mundo-chino", [doc(0)], write=True)
    assert fake_mongo["book_chunks"].written == []


def test_con_source_propio_los_chunks_se_escriben_y_no_se_borra_nada_por_tanda(fake_mongo):
    report = store.write_chunks(TARGET, "libro", [doc(0), doc(1)], write=True, job_id="job-7")
    assert report.written == 2 and fake_mongo["book_chunks"].deleted == []
    assert all(op._doc["jobId"] == "job-7" for op in fake_mongo["book_chunks"].written)


def test_una_tanda_de_hitos_con_jobid_nunca_borra_huerfanos(fake_mongo):
    # Si cada tanda borrase "lo que no está en mi lote", borraría lo que escribió la anterior.
    store.write_hitos(TARGET, "mi-libro", [hito_doc()], write=True, replace_source=True, dedupe=False, personajes=False, job_id="job-7")
    assert fake_mongo["hitos_conocidos"].deleted == [] and fake_mongo["hitos_conocidos"].written[0]._doc["jobId"] == "job-7"


def test_sin_jobid_el_cli_sigue_borrando_los_huerfanos_del_lote(fake_mongo):
    store.write_hitos(TARGET, "mi-libro", [hito_doc()], write=True, replace_source=True, dedupe=False, personajes=False)
    assert len(fake_mongo["hitos_conocidos"].deleted) == 1


def test_los_vecinos_excluyen_solo_ejecuciones_anteriores_cuando_hay_jobid():
    consultas = []

    class Col:
        def find(self, query, projection):
            consultas.append(query)
            return self

        def limit(self, n):
            return []

    hito = {"id": "x", "año": -1046, "location": {"type": "Point", "coordinates": [113.9, 35.3]}}
    mongo_neighbors(Col(), "mi-libro", replace_source=True, job_id="job-7")(hito)
    same = {"id": "x", "origen": ORIGIN, "fuente": "mi-libro"}
    assert consultas[-1]["$nor"] == [same, {"origen": ORIGIN, "fuente": "mi-libro", "jobId": {"$ne": "job-7"}}]
    mongo_neighbors(Col(), "mi-libro", replace_source=True)(hito)  # CLI: se excluyen todos los del libro
    assert consultas[-1]["$nor"] == [same, {"origen": ORIGIN, "fuente": "mi-libro"}]


def test_la_limpieza_final_solo_borra_lo_de_esta_app_y_de_otras_ejecuciones(fake_mongo):
    result = cleanup_old_runs(TARGET, "mi-libro", "job-7")
    assert fake_mongo["book_chunks"].deleted == [{"source": "mi-libro", "origen": ORIGIN, "jobId": {"$ne": "job-7"}}]
    assert fake_mongo["hitos_conocidos"].deleted == [{"origen": ORIGIN, "fuente": "mi-libro", "jobId": {"$ne": "job-7"}}]
    assert result == {"chunksBorrados": 3, "hitosBorrados": 3}


def test_la_limpieza_exige_el_jobid(fake_mongo):
    with pytest.raises(ContractError):
        cleanup_old_runs(TARGET, "mi-libro", "")
    assert fake_mongo == {}
