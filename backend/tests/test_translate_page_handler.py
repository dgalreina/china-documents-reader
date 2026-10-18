"""GET /books/{slug}/pages/{page}/translation con S3 local, un PDF generado y un cliente falso (sin AWS ni OpenAI)."""

import json

import pytest
from reportlab.pdfgen import canvas

from lambdas.translate_page.handler import handler, pdf_key
from lambdas.translate_page.translator import cache_key
from shared.storage import LocalStorage

from .test_translate_page import FakeClient


def make_pdf(path, pages=3):
    pdf = canvas.Canvas(str(path))
    for number in range(1, pages + 1):
        pdf.drawString(72, 750, f"Texto de la pagina {number}.")
        pdf.showPage()
    pdf.save()


@pytest.fixture(autouse=True)
def bucket(monkeypatch):
    monkeypatch.setenv("BOOKS_BUCKET", "libros")


@pytest.fixture
def storage(tmp_path):
    return LocalStorage(tmp_path / "s3")


@pytest.fixture
def libro(storage, tmp_path):
    local = tmp_path / "original.pdf"
    make_pdf(local)
    target = storage._path("libros", pdf_key("mi-libro"))
    target.parent.mkdir(parents=True)
    target.write_bytes(local.read_bytes())


def call(slug, page, storage, client, tmp_path):
    event = {"pathParameters": {"slug": slug, "page": page}}
    response = handler(event, storage=storage, client=client, cache_dir=tmp_path / "tmp-books")
    return response["statusCode"], json.loads(response["body"])


def test_traduce_la_pagina_pedida_y_solo_esa(libro, storage, tmp_path):
    client = FakeClient("Hola")
    status, body = call("mi-libro", "2", storage, client, tmp_path)
    assert (status, body) == (200, {"page": 2, "text": "Hola", "cached": False})
    sent = client.calls[0]["messages"][1]["content"]
    assert "pagina 2" in sent and "pagina 1" not in sent and "pagina 3" not in sent


def test_la_segunda_peticion_sale_de_la_cache_sin_abrir_el_pdf(libro, storage, tmp_path):
    client = FakeClient("Hola")
    call("mi-libro", "2", storage, client, tmp_path)
    storage._path("libros", pdf_key("mi-libro")).unlink()  # ya no hace falta el PDF
    status, body = call("mi-libro", "2", storage, client, tmp_path / "otro")
    assert status == 200 and body["cached"] is True and len(client.calls) == 1


def test_un_libro_inexistente_es_404_y_no_gasta(storage, tmp_path):
    client = FakeClient()
    status, body = call("no-existe", "1", storage, client, tmp_path)
    assert status == 404 and "libro" in body["error"] and client.calls == []


def test_una_pagina_fuera_del_libro_es_404(libro, storage, tmp_path):
    client = FakeClient()
    status, _ = call("mi-libro", "99", storage, client, tmp_path)
    assert status == 404 and client.calls == [] and storage.try_get_json("libros", cache_key("mi-libro", 99)) is None


@pytest.mark.parametrize("slug,page", [("../x", "1"), ("a/b", "1"), ("mi-libro", "0"), ("mi-libro", "-1"), ("mi-libro", "abc"), ("mi-libro", "")])
def test_parametros_invalidos_son_400(storage, tmp_path, slug, page):
    status, _ = call(slug, page, storage, FakeClient(), tmp_path)
    assert status == 400


def test_un_fallo_del_modelo_es_502_sin_detalles(libro, storage, tmp_path):
    class Roto(FakeClient):
        def _create(self, **kwargs):
            raise RuntimeError("clave sk-secreta rechazada")

    status, body = call("mi-libro", "1", storage, Roto(), tmp_path)
    assert status == 502 and "sk-" not in json.dumps(body)
