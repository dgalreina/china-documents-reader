"""ingest: el PDF recién subido pasa a ser un libro sin analizar. S3 local, tablas y Mongo falsos, PDFs generados (sin red)."""

import io

import pytest
from pypdf import PdfWriter

from lambdas.extract_chunk.cover import COVER_KEY_NAME
from lambdas.ingest import handler as ingest_module
from lambdas.ingest.handler import handler
from shared.storage import LocalStorage

from .test_delete_book import FakeDB, chunk, hito, person
from .test_extract_chunk_handler import make_pdf

JOB = "a" * 32
KEY = f"uploads/{JOB}/libro.pdf"


class JobsTable:
    def __init__(self, item=None):
        self.items = {item["jobId"]: dict(item)} if item else {}
        self.updates = []

    def get_item(self, Key):
        item = self.items.get(Key["jobId"])
        return {"Item": item} if item else {}

    def update_item(self, Key, UpdateExpression, ExpressionAttributeNames, ExpressionAttributeValues):
        values = {name.lstrip(":"): value for name, value in ExpressionAttributeValues.items()}
        self.updates.append(values)
        job = self.items[Key["jobId"]]
        job["status"] = values["s"]
        if "e" in values:
            job["error"] = values["e"]
        if "p" in values:
            job["pageCount"] = values["p"]


class BooksTable:
    def __init__(self, items=()):
        self.items = {item["slug"]: dict(item) for item in items}

    def get_item(self, Key):
        item = self.items.get(Key["slug"])
        return {"Item": item} if item else {}

    def put_item(self, Item):
        self.items[Item["slug"]] = dict(Item)


def upload_job(**extra):
    return {"jobId": JOB, "kind": "upload", "status": "UPLOADING", "source": "mi-libro", "title": "Mi libro", "filename": "libro.pdf", "replaceSource": False, **extra}


@pytest.fixture(autouse=True)
def env(monkeypatch):
    monkeypatch.setenv("BOOKS_BUCKET", "libros")


@pytest.fixture
def storage(tmp_path):
    return LocalStorage(tmp_path / "s3")


def put_upload(storage, tmp_path, data=None, pages=3):
    local = tmp_path / "libro.pdf"
    make_pdf(local, pages=pages)
    target = storage._path("subidas", KEY)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(data if data is not None else local.read_bytes())


def run(storage, jobs, books, db=None):
    return handler({"bucket": "subidas", "key": KEY}, storage=storage, jobs_table=jobs, books_table=books, db=db)


def test_una_subida_nueva_queda_guardada_con_portada_y_sin_analizar(storage, tmp_path):
    put_upload(storage, tmp_path, pages=3)
    jobs, books = JobsTable(upload_job()), BooksTable()
    result = run(storage, jobs, books)
    assert result == {"ok": True, "slug": "mi-libro", "pageCount": 3, "hasCover": True, "textLayer": True}  # el PDF de prueba tiene texto: no es un escaneado
    assert storage.exists("libros", "books/mi-libro/original.pdf")
    assert storage._path("libros", f"books/mi-libro/{COVER_KEY_NAME}").read_bytes()[:2] == b"\xff\xd8"
    item = books.items["mi-libro"]
    assert (item["analysis"], item["pageCount"], item["hasCover"], item["title"], item["filename"]) == ("none", 3, True, "Mi libro", "libro.pdf")
    assert "stats" not in item  # aún no hay nada que contar
    assert jobs.items[JOB]["status"] == "DONE" and jobs.items[JOB]["pageCount"] == 3
    assert not storage.exists("subidas", KEY)  # la copia de subidas ya no hace falta


def test_un_pdf_danado_se_rechaza_con_un_motivo_y_no_crea_el_libro(storage, tmp_path):
    put_upload(storage, tmp_path, data=b"esto no es un pdf")
    jobs, books = JobsTable(upload_job()), BooksTable()
    result = run(storage, jobs, books)
    assert result["ok"] is False and "dañado" in result["error"]
    assert jobs.items[JOB]["status"] == "FAILED" and "dañado" in jobs.items[JOB]["error"]
    assert books.items == {} and not storage.exists("libros", "books/mi-libro/original.pdf")


def test_un_pdf_protegido_con_contrasena_se_rechaza(storage, tmp_path):
    writer = PdfWriter()
    writer.add_blank_page(width=200, height=200)
    writer.encrypt("secreto", algorithm="RC4-128")
    buffer = io.BytesIO()
    writer.write(buffer)
    put_upload(storage, tmp_path, data=buffer.getvalue())
    jobs = JobsTable(upload_job())
    assert "contraseña" in run(storage, jobs, BooksTable())["error"]
    assert jobs.items[JOB]["status"] == "FAILED"


def test_si_la_portada_falla_el_libro_se_guarda_igual(storage, tmp_path, monkeypatch):
    put_upload(storage, tmp_path)

    def boom(_path):
        raise RuntimeError("pdfium no pudo")

    monkeypatch.setattr(ingest_module, "render_cover", boom)
    books = BooksTable()
    assert run(storage, JobsTable(upload_job()), books)["hasCover"] is False
    assert books.items["mi-libro"]["hasCover"] is False and storage.exists("libros", "books/mi-libro/original.pdf")


# ---- reemplazar un libro que ya existía -----------------------------------------------------------------------------------------------------


def existing_book():
    return {"slug": "mi-libro", "title": "Viejo", "analysis": "done", "stats": {"hitos": 3}, "hasCover": True, "pageCount": 99}


def populated(storage):
    for key in ["books/mi-libro/original.pdf", f"books/mi-libro/{COVER_KEY_NAME}", "translations/mi-libro/v2/0001.json", "books/otro-libro/original.pdf"]:
        target = storage._path("libros", key)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(b"viejo")
    return FakeDB(
        chunks=[chunk("mi-libro", 0), chunk("otro-libro", 0)],
        hitos=[hito("h1", "mi-libro", ["solo"]), hito("h2", "otro-libro")],
        personajes=[person("solo", ["mi-libro"])],
    )


def test_reemplazar_un_libro_borra_lo_que_se_derivo_del_pdf_anterior_y_lo_deja_sin_analizar(storage, tmp_path):
    put_upload(storage, tmp_path, pages=2)
    db = populated(storage)
    books = BooksTable([existing_book()])
    result = run(storage, JobsTable(upload_job(replaceSource=True)), books, db)
    assert result["ok"] is True
    item = books.items["mi-libro"]
    assert item["analysis"] == "none" and item["pageCount"] == 2 and item["title"] == "Mi libro" and "stats" not in item  # el registro entero se reemplaza
    assert [c["source"] for c in db["book_chunks"].docs] == ["otro-libro"]  # los de este libro se fueron, los de otro no
    assert [h["id"] for h in db["hitos_conocidos"].docs] == ["h2"] and db["personajes"].docs == []
    assert not storage.exists("libros", "translations/mi-libro/v2/0001.json")
    assert storage._path("libros", "books/mi-libro/original.pdf").read_bytes() != b"viejo"  # el PDF nuevo
    assert storage.exists("libros", "books/otro-libro/original.pdf")


def test_si_el_libro_existe_y_la_subida_no_declaro_el_reemplazo_no_se_pisa_nada(storage, tmp_path):
    put_upload(storage, tmp_path)
    db = populated(storage)
    books = BooksTable([existing_book()])
    jobs = JobsTable(upload_job(replaceSource=False))
    result = run(storage, jobs, books, db)
    assert result["ok"] is False and "Ya existe" in jobs.items[JOB]["error"]
    assert books.items["mi-libro"]["analysis"] == "done"
    assert storage._path("libros", "books/mi-libro/original.pdf").read_bytes() == b"viejo"
    assert len(db["book_chunks"].docs) == 2


# ---- lo que no es suyo se ignora ------------------------------------------------------------------------------------------------------------


@pytest.mark.parametrize("key", ["uploads/corto.pdf", "jobs/" + JOB + "/chunks.json", "uploads/no-es-un-id/libro.pdf", "books/mi-libro/original.pdf"])
def test_una_clave_que_no_es_una_subida_se_ignora(storage, key):
    jobs = JobsTable(upload_job())
    assert handler({"bucket": "subidas", "key": key}, storage=storage, jobs_table=jobs, books_table=BooksTable())["ok"] is False
    assert jobs.updates == []


def test_un_pdf_sin_registro_de_subida_o_de_otro_tipo_no_se_toca(storage, tmp_path):
    put_upload(storage, tmp_path)
    books = BooksTable()
    assert "ignored" in run(storage, JobsTable(), books)  # nadie lo registró
    assert "ignored" in run(storage, JobsTable({**upload_job(), "kind": "analysis"}), books)
    assert books.items == {} and storage.exists("subidas", KEY)


def test_repetir_la_entrega_del_mismo_evento_no_rompe_nada(storage, tmp_path):
    put_upload(storage, tmp_path)
    jobs, books = JobsTable(upload_job()), BooksTable()
    assert run(storage, jobs, books)["ok"] is True
    again = run(storage, jobs, books)  # EventBridge puede entregar dos veces
    assert again == {"ok": False, "ignored": "subida ya procesada"}
    assert jobs.items[JOB]["status"] == "DONE"  # la segunda entrega NO convierte en fallo una subida que fue bien
    assert books.items["mi-libro"]["analysis"] == "none"
