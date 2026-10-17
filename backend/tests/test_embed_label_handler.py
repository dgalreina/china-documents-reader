"""Tests de los handlers de embed_label por tandas (OpenAI y geocodificador falsos, almacenamiento local: sin AWS ni coste)."""

import json

import pytest

from lambdas.embed_label.handler import handler, period_handler
from shared.geocoder import Geocoder
from shared.storage import LocalStorage
from tests.test_embed_label import FakeClient


@pytest.fixture
def storage(tmp_path):
    st = LocalStorage(tmp_path / "s3")
    # chunks.json como lo deja extract_chunk: el texto de cada chunk de prueba es su propio número (lo que espera FakeClient)
    st.put_json("b", "jobs/j1/chunks.json", [{"index": i, "text": str(i), "page_start": i + 1, "page_end": i + 1} for i in range(25)])
    return st


def event(**over):
    return {"jobId": "j1", "bucket": "b", "chunksKey": "jobs/j1/chunks.json", "periodKey": "jobs/j1/period.json", "start": 0, "size": 10, **over}


def test_el_periodo_se_calcula_una_vez_y_se_guarda(storage):
    out = period_handler({"jobId": "j1", "bucket": "b", "chunksKey": "jobs/j1/chunks.json"}, storage=storage, client=FakeClient({}))
    assert out["periodKey"] == "jobs/j1/period.json" and out["period"]["ano_inicio"] == 618
    assert storage.get_json("b", "jobs/j1/period.json")["ano_fin"] == 907


def test_una_tanda_procesa_solo_su_trozo_y_devuelve_un_resumen_pequeno(storage):
    storage.put_json("b", "jobs/j1/period.json", {"resumen": "China Tang", "ano_inicio": 618, "ano_fin": 907})
    client = FakeClient({i: (700, 800) for i in range(25)})
    out = handler(event(start=10, size=10), storage=storage, client=client, geocoder=Geocoder())
    assert out["labeledKey"] == "jobs/j1/labeled/000010.json" and out["chunks"] == 10 and out["ready"] == 10 and out["pending"] == 0
    labeled = storage.get_json("b", out["labeledKey"])
    assert [d["chunkIndex"] for d in labeled["ready"]] == list(range(10, 20))  # el trozo 10..19, con sus índices globales
    assert len(json.dumps(out)) < 2000  # cabe de sobra entre pasos
    assert client.embedded == [str(i) for i in range(10, 20)]  # solo se pagan los embeddings de su trozo


def test_la_ultima_tanda_puede_ser_mas_corta(storage):
    storage.put_json("b", "jobs/j1/period.json", None)
    out = handler(event(start=20, size=10), storage=storage, client=FakeClient({i: (1, 2) for i in range(25)}), geocoder=Geocoder())
    assert out["chunks"] == 5 and out["ready"] == 5


def test_no_vuelve_a_inferir_el_periodo_con_su_trozo(storage):
    # Si cada tanda lo recalculase, usaría sus propios chunks (que no son el arranque del libro) y daría un periodo distinto.
    storage.put_json("b", "jobs/j1/period.json", {"resumen": "fijo", "ano_inicio": 1, "ano_fin": 2})
    asked = []
    client = FakeClient({i: (1, 2) for i in range(25)})
    original = client._parse
    client.chat.completions.parse = lambda model, messages, response_format: (asked.append(response_format.__name__), original(model, messages, response_format))[1]
    handler(event(), storage=storage, client=client, geocoder=Geocoder())
    assert "DocumentPeriod" not in asked


def test_los_pendientes_tambien_se_guardan_con_su_indice_global(storage):
    storage.put_json("b", "jobs/j1/period.json", None)
    answers = {i: (None, None) if i == 12 else (10, 20) for i in range(25)}
    out = handler(event(start=10, size=5), storage=storage, client=FakeClient(answers), geocoder=Geocoder())
    labeled = storage.get_json("b", out["labeledKey"])
    assert out["pending"] == 1 and [d["chunkIndex"] for d in labeled["pending"]] == [12]


def test_falta_un_campo_del_evento_da_error(storage):
    with pytest.raises(KeyError):
        handler({"jobId": "j1", "bucket": "b"}, storage=storage)
