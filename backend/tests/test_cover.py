"""Portadas: se dibujan en el primer paso, se copian al catálogo y las Lambdas de lectura dan su URL solo si existe. Sin AWS."""

import io
import json

import pytest
from PIL import Image

from lambdas.books.handler import handler as books_handler
from lambdas.extract_chunk import handler as extract_module
from lambdas.extract_chunk.cover import COVER_WIDTH, render_cover
from lambdas.jobs.handler import handler as jobs_handler
from lambdas.store_book.handler import handler as store_handler
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


# ---- el primer paso la deja en S3 y un fallo no tumba el análisis ---------------------------------------------------------------------------


def test_el_primer_paso_deja_la_portada_junto_a_los_chunks(storage, tmp_path):
    put_pdf(storage, tmp_path)
    result = extract_module.handler({"jobId": "job1", "bucket": "subidas", "key": "uploads/job1/libro.pdf"}, storage=storage)
    assert result["hasCover"] is True
    assert storage.exists("subidas", "jobs/job1/cover.jpg")
    assert storage._path("subidas", "jobs/job1/cover.jpg").read_bytes()[:2] == b"\xff\xd8"


def test_si_la_portada_falla_el_libro_se_analiza_igual(storage, tmp_path, monkeypatch):
    put_pdf(storage, tmp_path)

    def boom(_path):
        raise RuntimeError("pdfium no pudo")

    monkeypatch.setattr(extract_module, "render_cover", boom)
    result = extract_module.handler({"jobId": "job1", "bucket": "subidas", "key": "uploads/job1/libro.pdf"}, storage=storage)
    assert result["hasCover"] is False and result["chunkCount"] > 0
    assert storage.try_get_json("subidas", result["chunksKey"]) is not None  # los chunks sí se guardaron
    assert not storage.exists("subidas", "jobs/job1/cover.jpg")


# ---- store_book: la portada pasa al bucket permanente --------------------------------------------------------------------------------------


class FakeBooksTable:
    def __init__(self):
        self.items = []

    def put_item(self, Item):
        self.items.append(Item)


EVENT = {"bucket": "subidas", "key": "uploads/job1/libro.pdf", "source": "mi-libro", "title": "Mi libro", "jobId": "job1", "pageCount": 3}


@pytest.fixture(autouse=True)
def env(monkeypatch):
    monkeypatch.setenv("BOOKS_BUCKET", "libros")
    monkeypatch.setenv("UPLOAD_BUCKET", "subidas")


def test_la_portada_se_copia_junto_al_pdf_y_el_catalogo_lo_apunta(storage, tmp_path):
    put_pdf(storage, tmp_path)
    storage.put_bytes("subidas", "jobs/job1/cover.jpg", b"\xff\xd8portada", "image/jpeg")
    table = FakeBooksTable()
    result = store_handler(EVENT, storage=storage, books_table=table)
    assert result["hasCover"] is True and table.items[0]["hasCover"] is True
    assert storage._path("libros", "books/mi-libro/cover.jpg").read_bytes() == b"\xff\xd8portada"


def test_un_reproceso_sin_portada_nueva_borra_la_vieja_y_no_se_queda_con_la_de_otra_edicion(storage, tmp_path):
    put_pdf(storage, tmp_path)
    storage.put_bytes("libros", "books/mi-libro/cover.jpg", b"vieja", "image/jpeg")
    storage.put_bytes("libros", "books/otro-libro/cover.jpg", b"ajena", "image/jpeg")
    table = FakeBooksTable()
    result = store_handler(EVENT, storage=storage, books_table=table)  # este trabajo no tiene cover.jpg
    assert result["hasCover"] is False and table.items[0]["hasCover"] is False
    assert not storage.exists("libros", "books/mi-libro/cover.jpg")
    assert storage.exists("libros", "books/otro-libro/cover.jpg")  # la de otro libro no se toca


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


def jobs(items, s3):
    response = jobs_handler({}, jobs_table=FakeTable(items), s3_client=s3)
    return {job["jobId"]: job for job in json.loads(response["body"])["jobs"]}


def job(job_id, status, created):
    return {"jobId": job_id, "status": status, "source": "mi-libro", "createdAt": created}


def test_un_analisis_en_marcha_enseña_su_portada_y_uno_terminado_no_consulta_nada():
    s3 = FakeS3(existing={"jobs/running/cover.jpg"})
    result = jobs([job("running", "RUNNING", "3"), job("done", "DONE", "2"), job("uploading", "UPLOADING", "1")], s3)
    assert result["running"]["coverUrl"] == "https://s3.test/jobs/running/cover.jpg?firma"
    assert "coverUrl" not in result["done"] and "coverUrl" not in result["uploading"]
    assert s3.heads == [("subidas", "jobs/running/cover.jpg")]  # solo se pregunta por el que está en marcha


def test_si_la_portada_aun_no_existe_o_no_se_puede_comprobar_la_lista_sale_igual():
    assert "coverUrl" not in jobs([job("a", "RUNNING", "1")], FakeS3())["a"]  # 404
    assert "coverUrl" not in jobs([job("a", "RUNNING", "1")], FakeS3(fail_with=RuntimeError("sin permiso")))["a"]  # un fallo cualquiera


def test_sin_bucket_configurado_no_hay_portadas_en_marcha(monkeypatch):
    monkeypatch.delenv("UPLOAD_BUCKET")
    s3 = FakeS3(existing={"jobs/a/cover.jpg"})
    assert "coverUrl" not in jobs([job("a", "RUNNING", "1")], s3)["a"] and s3.heads == []


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
