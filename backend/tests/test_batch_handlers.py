"""Tests de los handlers por tandas de extract_hitos y write_mongo (fakes y almacenamiento local: sin AWS, sin red)."""

import json
from types import SimpleNamespace

import pytest

from lambdas.extract_hitos.handler import handler as hitos_batch
from lambdas.write_mongo import handler as wh
from shared.storage import LocalStorage
from tests.test_extract_hitos import FakeClient, FakeGeocoder, LUOYANG, chunks, hito


@pytest.fixture
def storage(tmp_path):
    st = LocalStorage(tmp_path / "s3")
    ready = [{**c, "embedding": [0.0] * 4} for c in chunks(n=2)]  # chunks listos, como los deja embed_label
    st.put_json("b", "jobs/j1/labeled/000000.json", {"ready": ready, "pending": []})
    return st


def test_extract_hitos_lee_la_tanda_y_deja_sus_hitos_en_s3(storage):
    out = hitos_batch(
        {"jobId": "j1", "bucket": "b", "labeledKey": "jobs/j1/labeled/000000.json", "source": "libro"},
        storage=storage, client=FakeClient([hito()]), geocoder=FakeGeocoder({"Luoyang": LUOYANG}),
    )
    assert out["hitosKey"] == "jobs/j1/hitos/000000.json" and out["chunks"] == 2 and out["hitos"] == 1
    saved = storage.get_json("b", out["hitosKey"])
    assert saved["hitos"][0]["fuente"] == "libro" and saved["hitos"][0]["_personajes"] == []
    assert len(json.dumps(out)) < 2000  # solo un resumen: los hitos viven en S3


# ---- write_mongo ---------------------------------------------------------------------------------------


@pytest.fixture
def mongo(monkeypatch):
    calls = []
    monkeypatch.setattr(wh, "_target", lambda: SimpleNamespace(name="dev"))
    monkeypatch.setattr(
        wh, "write_chunks", lambda target, source, docs, **kw: (calls.append(("chunks", source, len(docs), kw)), SimpleNamespace(target="dev", written=len(docs)))[1]
    )
    monkeypatch.setattr(
        wh, "write_hitos",
        lambda target, source, hitos, **kw: (calls.append(("hitos", source, len(hitos), kw)), SimpleNamespace(target="dev", written=len(hitos), duplicates=[], personajes_stats={"personajes_nuevos": 1}))[1],
    )
    monkeypatch.setattr(wh, "cleanup_old_runs", lambda target, source, job_id: (calls.append(("cleanup", source, job_id)), {"chunksBorrados": 1, "hitosBorrados": 2})[1])
    return calls


def test_el_handler_de_chunks_escribe_con_el_jobid_y_sin_replace_por_tanda(storage, mongo):
    out = wh.chunks_handler({"jobId": "j1", "bucket": "b", "labeledKey": "jobs/j1/labeled/000000.json", "source": "libro"}, storage=storage)
    kind, source, n, kw = mongo[0]
    assert (kind, source, n) == ("chunks", "libro", 2) and kw == {"write": True, "job_id": "j1"}  # sin replace_source por tanda
    assert out == {"jobId": "j1", "target": "dev", "chunksEscritos": 2}


def test_el_handler_de_hitos_guarda_solo_lo_nuevo_y_resuelve_personajes(storage, mongo):
    storage.put_json("b", "jobs/j1/hitos/000000.json", {"hitos": [{"id": "x"}], "descartados": []})
    out = wh.hitos_handler({"jobId": "j1", "bucket": "b", "hitosKey": "jobs/j1/hitos/000000.json", "source": "libro", "replaceSource": True}, storage=storage)
    _, _, n, kw = mongo[0]
    assert n == 1 and kw["dedupe"] is True and kw["personajes"] is True and kw["job_id"] == "j1" and kw["replace_source"] is True and kw["write"] is True
    assert out["hitosEscritos"] == 1 and out["personajes"] == {"personajes_nuevos": 1}


def test_la_limpieza_solo_actua_si_se_pidio_el_reemplazo(mongo):
    assert wh.cleanup_handler({"jobId": "j1", "source": "libro"})["limpieza"] == "no solicitada" and mongo == []
    out = wh.cleanup_handler({"jobId": "j1", "source": "libro", "replaceSource": True})
    assert mongo == [("cleanup", "libro", "j1")] and out == {"jobId": "j1", "chunksBorrados": 1, "hitosBorrados": 2}


def test_el_destino_es_dev_por_defecto_y_solo_el_entorno_lo_cambia(monkeypatch):
    monkeypatch.setenv("MONGODB_URI_DEV", "mongodb://dev")
    monkeypatch.setenv("MONGODB_URI_PROD", "mongodb://prod")
    monkeypatch.delenv("MONGO_TARGET", raising=False)
    assert wh._target().name == "dev" and wh._target().db_name == "china_documents_dev"
    monkeypatch.setenv("MONGO_TARGET", "prod")  # solo el despliegue puede ponerlo: ningún handler lee el destino del evento
    assert wh._target().name == "prod" and wh._target().db_name == "china-history"


def test_ningun_handler_de_escritura_lee_el_destino_del_evento():
    import inspect

    source = inspect.getsource(wh)
    assert 'event["target"]' not in source and 'event.get("target")' not in source


# ---- finalize ------------------------------------------------------------------------------------------


def usage(cost, unknown=()):
    return {"estimatedCostUsd": cost, "unknownModels": list(unknown)}


def test_finalize_suma_recuentos_costes_y_personajes_de_todas_las_tandas(mongo):
    event = {
        "jobId": "j1", "source": "libro", "replaceSource": True,
        "period": {"usage": usage(0.01)},
        "embedResults": [
            {"chunks": 100, "ready": 60, "pending": 40, "located": 8, "usage": usage(0.05)},
            {"chunks": 58, "ready": 40, "pending": 18, "located": 5, "usage": usage(0.03)},
        ],
        "hitosResults": [{"hitos": 5, "usage": usage(0.07)}, {"hitos": 2, "usage": usage(0.04, ["modelo-raro"])}],
        "writeResults": [
            {"hitosEscritos": 3, "duplicadosDescartados": 2, "personajes": {"personajes_nuevos": 2}, "usage": usage(0.02)},
            {"hitosEscritos": 1, "duplicadosDescartados": 1, "personajes": {"personajes_nuevos": 1, "personajes_unidos": 3}, "usage": usage(0.01)},
        ],
    }
    out = wh.finalize_handler(event)
    assert (out["chunks"], out["chunksListos"], out["chunksPendientes"], out["chunksConUbicacion"]) == (158, 100, 58, 13)
    assert (out["hitosExtraidos"], out["hitosEscritos"], out["duplicadosDescartados"]) == (7, 4, 3)
    assert out["personajes"] == {"personajes_nuevos": 3, "personajes_unidos": 3}
    assert out["costeEstimadoUsd"] == pytest.approx(0.23) and out["modelosSinPrecio"] == ["modelo-raro"]
    assert out["limpieza"]["chunksBorrados"] == 1 and mongo == [("cleanup", "libro", "j1")]  # reproceso: se limpia
    assert len(json.dumps(out)) < 2000


def test_finalize_sin_reemplazo_no_borra_nada(mongo):
    out = wh.finalize_handler({"jobId": "j1", "source": "libro", "embedResults": [], "hitosResults": [], "writeResults": []})
    assert out["limpieza"]["limpieza"] == "no solicitada" and mongo == [] and out["costeEstimadoUsd"] == 0
