"""Tests de GET /jobs/{jobId} con una tabla falsa (sin AWS)."""

import json
from decimal import Decimal

import pytest

from lambdas.jobs.handler import handler

JOB = "a" * 32


class FakeTable:
    def __init__(self, item=None):
        self.item, self.keys = item, []

    def get_item(self, Key):
        self.keys.append(Key)
        return {"Item": self.item} if self.item else {}


def call(job_id, table):
    return handler({"pathParameters": {"jobId": job_id}}, jobs_table=table)


def test_devuelve_el_estado_y_el_resumen_ya_en_json():
    item = {
        "jobId": JOB, "status": "DONE", "filename": "libro.pdf", "source": "libro", "replaceSource": False, "expiresAt": 123,
        "summary": json.dumps({"chunks": 158, "costeEstimadoUsd": 0.23}),
    }
    response = call(JOB, FakeTable(item))
    body = json.loads(response["body"])
    assert response["statusCode"] == 200 and body["status"] == "DONE" and body["summary"]["chunks"] == 158
    assert "expiresAt" not in body  # campo interno


def test_los_decimales_de_dynamodb_se_serializan():
    response = call(JOB, FakeTable({"jobId": JOB, "status": "RUNNING", "startedAt": Decimal("5"), "error": Decimal("1.5")}))
    assert json.loads(response["body"])["error"] == 1.5 and json.loads(response["body"])["startedAt"] == 5


def test_un_trabajo_inexistente_da_404():
    assert call(JOB, FakeTable())["statusCode"] == 404


@pytest.mark.parametrize("job_id", ["", "abc", "../../etc", "G" * 32, JOB + "0"])
def test_un_jobid_no_valido_da_400_y_no_consulta_la_tabla(job_id):
    table = FakeTable({"jobId": JOB})
    assert call(job_id, table)["statusCode"] == 400 and table.keys == []


def test_un_resumen_corrupto_no_rompe_la_respuesta():
    body = json.loads(call(JOB, FakeTable({"jobId": JOB, "status": "DONE", "summary": "{no es json"}))["body"])
    assert body["summary"] is None


class ScanTable:
    """Una tabla que devuelve sus registros en dos páginas, como DynamoDB cuando hay muchos."""

    def __init__(self, items):
        self.items, self.calls = items, 0

    def scan(self, **kwargs):
        self.calls += 1
        if "ExclusiveStartKey" not in kwargs:
            return {"Items": self.items[:1], "LastEvaluatedKey": {"jobId": "x"}}
        return {"Items": self.items[1:]}


def test_get_jobs_sin_id_lista_los_trabajos_recientes_el_mas_nuevo_primero():
    items = [
        {"jobId": "a" * 32, "status": "DONE", "source": "viejo", "title": "Viejo", "createdAt": "2026-01-01T00:00:00Z", "expiresAt": 1, "summary": json.dumps({"chunks": 3})},
        {"jobId": "b" * 32, "status": "RUNNING", "source": "nuevo", "title": "Nuevo", "createdAt": "2026-06-01T00:00:00Z", "expiresAt": 2},
    ]
    table = ScanTable(items)
    response = handler({}, jobs_table=table)  # sin pathParameters: es GET /jobs
    body = json.loads(response["body"])
    assert response["statusCode"] == 200 and table.calls == 2
    assert [j["source"] for j in body["jobs"]] == ["nuevo", "viejo"]
    assert body["jobs"][1]["summary"] == {"chunks": 3} and all("expiresAt" not in j for j in body["jobs"])


def test_la_lista_se_acota_a_los_mas_recientes():
    items = [{"jobId": f"{i:032d}", "status": "DONE", "source": f"l{i}", "createdAt": f"2026-01-{i + 1:02d}T00:00:00Z"} for i in range(31)]
    body = json.loads(handler({}, jobs_table=ScanTable(items))["body"])
    assert len(body["jobs"]) == 30
