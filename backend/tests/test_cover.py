"""Portadas: se dibujan (al subir: ver test_ingest), el catálogo da su URL solo si existe y hay un script para los libros antiguos. Sin AWS."""

import io
import json

import pytest
from PIL import Image

from lambdas.books.handler import handler as books_handler
from lambdas.extract_chunk.cover import COVER_WIDTH, render_cover
from shared.storage import LocalStorage

from .test_extract_chunk_handler import make_pdf


@pytest.fixture
def storage(tmp_path):
    return LocalStorage(tmp_path / "s3")


def put_pdf(storage, tmp_path, key="uploads/job1/libro.pdf", **kwargs):
    local = tmp_path / "libro.pdf"
    make_pdf(local, **kwargs)
    target = storage._path("subidas", key)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(local.read_bytes())
    return local


# ---- dibujar la portada ---------------------------------------------------------------------------------------------------------------------


def test_la_portada_es_un_jpeg_pequeno_con_el_ancho_previsto(tmp_path):
    pdf = put_pdf(LocalStorage(tmp_path / "x"), tmp_path)
    data = render_cover(pdf)
    assert data[:2] == b"\xff\xd8"  # cabecera de JPEG
    image = Image.open(io.BytesIO(data))
    assert image.size[0] == COVER_WIDTH and image.size[1] > COVER_WIDTH  # una página vertical
    assert len(data) < 150_000


def test_un_pdf_que_no_se_puede_dibujar_lanza_una_excepcion(tmp_path):
    broken = tmp_path / "roto.pdf"
    broken.write_bytes(b"esto no es un pdf")
    with pytest.raises(Exception):
        render_cover(broken)


@pytest.fixture(autouse=True)
def env(monkeypatch):
    monkeypatch.setenv("BOOKS_BUCKET", "libros")


# ---- las Lambdas de lectura -----------------------------------------------------------------------------------------------------------------


class FakeTable:
    def __init__(self, items):
        self.items = items

    def scan(self, **kwargs):
        return {"Items": self.items}


class FakeS3:
    def __init__(self, existing=(), fail_with=None):
        self.existing, self.fail_with, self.heads, self.signed = set(existing), fail_with, [], []

    def head_object(self, Bucket, Key):
        self.heads.append((Bucket, Key))
        if self.fail_with:
            raise self.fail_with
        if Key not in self.existing:
            error = Exception("no existe")
            error.response = {"Error": {"Code": "404"}}
            raise error
        return {}

    def generate_presigned_url(self, operation, Params, ExpiresIn):
        self.signed.append(Params["Key"])
        return f"https://s3.test/{Params['Key']}?firma"


def books(items, s3):
    response = books_handler({"routeKey": "GET /books"}, books_table=FakeTable(items), s3_client=s3)
    return {book["slug"]: book for book in json.loads(response["body"])["books"]}


def test_solo_los_libros_con_portada_traen_su_url():
    s3 = FakeS3()
    result = books(
        [{"slug": "con", "createdAt": "2", "hasCover": True}, {"slug": "sin", "createdAt": "1", "hasCover": False}, {"slug": "antiguo", "createdAt": "0"}], s3
    )
    assert result["con"]["coverUrl"] == "https://s3.test/books/con/cover.jpg?firma"
    assert "coverUrl" not in result["sin"] and "coverUrl" not in result["antiguo"]
    assert s3.signed == ["books/con/cover.jpg"]
    assert "hasCover" not in result["con"]  # el indicador interno no se expone


# ---- el script para libros ya subidos ----------------------------------------------------------------------------------------------------------


class BackfillTable:
    def __init__(self, items):
        self.items, self.updates = items, []

    def scan(self, **kwargs):
        return {"Items": self.items}

    def update_item(self, Key, UpdateExpression, ExpressionAttributeValues):
        self.updates.append((Key, UpdateExpression, ExpressionAttributeValues))


class BackfillS3:
    def __init__(self, pdf_path, missing=()):
        self.pdf_path, self.missing, self.puts = pdf_path, set(missing), []

    def download_file(self, bucket, key, destination):
        if key in self.missing:
            raise FileNotFoundError(key)
        open(destination, "wb").write(open(self.pdf_path, "rb").read())

    def put_object(self, Bucket, Key, Body, ContentType):
        self.puts.append((Bucket, Key, Body[:2], ContentType))


def test_el_script_por_defecto_solo_muestra_lo_que_haria(tmp_path):
    from lambdas.store_book.backfill_covers import backfill

    pdf = put_pdf(LocalStorage(tmp_path / "x"), tmp_path)
    table, s3 = BackfillTable([{"slug": "a"}, {"slug": "b", "hasCover": True}]), BackfillS3(pdf)
    result = backfill(table, s3, "libros", write=False)
    assert result == {"yaTenian": 1, "generadas": ["a"], "fallidas": []}
    assert s3.puts == [] and table.updates == []  # sin --write no se escribe nada


def test_con_write_guarda_la_portada_y_marca_solo_ese_campo_y_un_libro_roto_no_frena_a_los_demas(tmp_path):
    from lambdas.store_book.backfill_covers import backfill

    pdf = put_pdf(LocalStorage(tmp_path / "x"), tmp_path)
    table = BackfillTable([{"slug": "a"}, {"slug": "perdido"}])
    s3 = BackfillS3(pdf, missing={"books/perdido/original.pdf"})
    result = backfill(table, s3, "libros", write=True)
    assert result["generadas"] == ["a"] and result["fallidas"] == ["perdido: FileNotFoundError"]
    assert s3.puts == [("libros", "books/a/cover.jpg", b"\xff\xd8", "image/jpeg")]
    assert table.updates == [({"slug": "a"}, "SET hasCover = :c", {":c": True})]
