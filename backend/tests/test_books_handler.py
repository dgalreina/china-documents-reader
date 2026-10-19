"""GET /books y GET /books/{slug}/pdf-url con una tabla y un S3 falsos (sin AWS)."""

import json
from decimal import Decimal

import pytest

from lambdas.books.handler import PDF_URL_SECONDS, handler


class FakeTable:
    def __init__(self, items=()):
        self.items = {item["slug"]: item for item in items}
        self.scans = 0

    def scan(self, **kwargs):
        self.scans += 1
        values = list(self.items.values())
        if "ExclusiveStartKey" not in kwargs and len(values) > 1:  # simula la paginación de DynamoDB: la primera página trae solo uno
            return {"Items": values[:1], "LastEvaluatedKey": {"slug": values[0]["slug"]}}
        return {"Items": values[1:] if len(values) > 1 else values}

    def get_item(self, Key):
        item = self.items.get(Key["slug"])
        return {"Item": item} if item else {}


class FakeS3:
    def __init__(self):
        self.calls = []

    def generate_presigned_url(self, operation, Params, ExpiresIn):
        self.calls.append((operation, Params, ExpiresIn))
        return f"https://{Params['Bucket']}.s3.eu-west-1.amazonaws.com/{Params['Key']}?X-Amz-Signature=firma"


@pytest.fixture(autouse=True)
def env(monkeypatch):
    monkeypatch.setenv("BOOKS_BUCKET", "libros")


def book(slug, created, **extra):
    return {"slug": slug, "title": slug.title(), "status": "DONE", "createdAt": created, "pageCount": Decimal(20),
            "stats": {"hitos": Decimal(2), "costeEstimadoUsd": Decimal("0.1971")}, "secretoInterno": "no", **extra}


def get_books(table):
    response = handler({"routeKey": "GET /books"}, books_table=table)
    return response["statusCode"], json.loads(response["body"])


def get_pdf_url(slug, table, s3=None):
    event = {"routeKey": "GET /books/{slug}/pdf-url", "pathParameters": {"slug": slug}}
    response = handler(event, books_table=table, s3_client=s3 or FakeS3())
    return response["statusCode"], json.loads(response["body"])


def test_lista_los_libros_del_mas_nuevo_al_mas_viejo_y_recorre_todas_las_paginas():
    table = FakeTable([book("viejo", "2026-01-01T00:00:00Z"), book("nuevo", "2026-06-01T00:00:00Z")])
    status, body = get_books(table)
    assert status == 200 and [b["slug"] for b in body["books"]] == ["nuevo", "viejo"]
    assert table.scans == 2  # leyó las dos páginas


def test_los_numeros_de_dynamodb_salen_como_numeros_normales_y_no_se_filtran_campos_internos():
    _, body = get_books(FakeTable([book("libro", "2026-01-01T00:00:00Z")]))
    item = body["books"][0]
    assert item["pageCount"] == 20 and item["stats"] == {"hitos": 2, "costeEstimadoUsd": 0.1971}
    assert "secretoInterno" not in item


def test_sin_libros_devuelve_una_lista_vacia():
    assert get_books(FakeTable()) == (200, {"books": []})


def test_firma_una_url_de_lectura_del_pdf_del_libro_del_catalogo():
    s3 = FakeS3()
    status, body = get_pdf_url("mi-libro", FakeTable([book("mi-libro", "2026-01-01T00:00:00Z")]), s3)
    assert status == 200 and body["expiresInSeconds"] == PDF_URL_SECONDS
    operation, params, expires = s3.calls[0]
    assert (operation, params["Bucket"], params["Key"], expires) == ("get_object", "libros", "books/mi-libro/original.pdf", PDF_URL_SECONDS)
    assert "books/mi-libro/original.pdf" in body["url"]


def test_no_firma_nada_para_un_libro_que_no_esta_en_el_catalogo():
    s3 = FakeS3()
    assert get_pdf_url("fantasma", FakeTable([book("otro", "2026-01-01T00:00:00Z")]), s3)[0] == 404
    assert s3.calls == []


@pytest.mark.parametrize("slug", ["../x", "a/b", "A mayusculas", "x", ""])
def test_slugs_peligrosos_son_400_y_no_firman(slug):
    s3 = FakeS3()
    assert get_pdf_url(slug, FakeTable(), s3)[0] == 400 and s3.calls == []


def test_una_ruta_desconocida_es_404():
    assert handler({"routeKey": "GET /otra"}, books_table=FakeTable())["statusCode"] == 404
