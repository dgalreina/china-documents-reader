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
