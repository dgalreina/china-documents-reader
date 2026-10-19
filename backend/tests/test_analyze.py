"""POST /books/{slug}/analyze con tablas y Step Functions falsos (sin AWS)."""

import json
from datetime import datetime, timezone

import pytest

from lambdas.analyze.handler import handler

NOW = 1_800_000_000.0


class Books:
    def __init__(self, items=()):
        self.items = {item["slug"]: item for item in items}

    def get_item(self, Key):
        item = self.items.get(Key["slug"])
        return {"Item": item} if item else {}


class Jobs:
    def __init__(self, existing=()):
        self.items = {job["jobId"]: dict(job) for job in existing}

    def scan(self, **kwargs):
        return {"Items": list(self.items.values())}

    def put_item(self, Item):
        self.items[Item["jobId"]] = dict(Item)

    def update_item(self, Key, UpdateExpression, ExpressionAttributeNames, ExpressionAttributeValues):
        job = self.items[Key["jobId"]]
        job["status"], job["error"] = ExpressionAttributeValues[":s"], ExpressionAttributeValues[":e"]


class Sfn:
    def __init__(self, fail=False):
        self.calls, self.fail = [], fail

    def start_execution(self, **kwargs):
        if self.fail:
            raise RuntimeError("AccessDenied: arn:aws:states:secreto")
        self.calls.append(kwargs)


@pytest.fixture(autouse=True)
def env(monkeypatch):
    monkeypatch.setenv("BOOKS_BUCKET", "libros")
    monkeypatch.setenv("UPLOAD_BUCKET", "datos")
    monkeypatch.setenv("STATE_MACHINE_ARN", "arn:aws:states:eu-west-1:1:stateMachine:Pipeline")


def book(analysis="none", **extra):
    base = {"slug": "mi-libro", "title": "Mi libro", "filename": "libro.pdf", "pageCount": 20}
    return {**base, **({} if analysis is None else {"analysis": analysis}), **extra}


def call(slug="mi-libro", books=None, jobs=None, sfn=None):
    event = {"routeKey": "POST /books/{slug}/analyze", "pathParameters": {"slug": slug}}
    response = handler(event, books_table=books or Books([book()]), jobs_table=jobs or Jobs(), sfn=sfn or Sfn(), now=NOW, new_id=lambda: "j" * 32)
    return response["statusCode"], json.loads(response["body"])


def test_arranca_el_flujo_con_el_pdf_ya_guardado_y_apunta_el_trabajo():
    jobs, sfn = Jobs(), Sfn()
    status, body = call(jobs=jobs, sfn=sfn)
    assert status == 202 and body["jobId"] == "j" * 32
    (execution,) = sfn.calls
    assert execution["name"] == "j" * 32  # el nombre de la ejecución es el jobId: no se puede lanzar dos veces el mismo
    assert json.loads(execution["input"]) == {"bucket": "datos", "pdfBucket": "libros", "key": "books/mi-libro/original.pdf", "jobId": "j" * 32}  # intermedios en datos, PDF en libros
    job = jobs.items["j" * 32]
    assert (job["kind"], job["status"], job["source"], job["title"], job["filename"]) == ("analysis", "RUNNING", "mi-libro", "Mi libro", "libro.pdf")


def test_el_primer_analisis_no_reemplaza_nada_y_un_reanalisis_si():
    for analysis, expected in [("none", False), ("done", True), (None, True)]:  # None = libro antiguo, sin el campo: ya estaba analizado
        jobs = Jobs()
        call(books=Books([book(analysis)]), jobs=jobs)
        assert jobs.items["j" * 32]["replaceSource"] is expected


def test_un_libro_que_no_existe_es_404_y_no_arranca_nada():
    sfn = Sfn()
    assert call("fantasma", sfn=sfn)[0] == 404 and sfn.calls == []


def recent(status, age=60, source="mi-libro"):
    return {"jobId": "x" * 32, "status": status, "source": source, "createdAt": datetime.fromtimestamp(NOW - age, timezone.utc).isoformat()}


@pytest.mark.parametrize("busy", [recent("RUNNING"), recent("UPLOADING")])
def test_no_se_analiza_dos_veces_a_la_vez_ni_durante_una_subida(busy):
    sfn, jobs = Sfn(), Jobs([busy])
    status, body = call(jobs=jobs, sfn=sfn)
    assert status == 409 and "ya se está" in body["error"]
    assert sfn.calls == [] and len(jobs.items) == 1


def test_un_analisis_anterior_terminado_o_fallido_o_de_otro_libro_no_estorba():
    jobs = Jobs([recent("DONE"), recent("FAILED"), recent("RUNNING", source="otro-libro")])
    assert call(jobs=jobs)[0] == 202


def test_si_no_se_puede_arrancar_el_flujo_el_trabajo_queda_fallido_y_no_se_filtran_detalles():
    jobs = Jobs()
    status, body = call(jobs=jobs, sfn=Sfn(fail=True))
    assert status == 502 and "secreto" not in json.dumps(body) and "arn:" not in json.dumps(body)
    assert jobs.items["j" * 32]["status"] == "FAILED"


@pytest.mark.parametrize("slug", ["../x", "a/b", "A mayusculas", "x", ""])
def test_slugs_peligrosos_son_400(slug):
    sfn = Sfn()
    assert call(slug, sfn=sfn)[0] == 400 and sfn.calls == []


def test_una_ruta_distinta_es_404():
    assert handler({"routeKey": "GET /books"})["statusCode"] == 404
