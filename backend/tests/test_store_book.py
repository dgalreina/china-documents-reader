"""store_book: copia el PDF al bucket permanente y borra las traducciones viejas. S3 local, sin AWS."""

import pytest

from lambdas.store_book.handler import catalog_item, handler
from shared.storage import LocalStorage


@pytest.fixture(autouse=True)
def env(monkeypatch):
    monkeypatch.setenv("BOOKS_BUCKET", "libros")


@pytest.fixture
def storage(tmp_path):
    return LocalStorage(tmp_path / "s3")


def put(storage, bucket, key, data=b"x"):
    target = storage._path(bucket, key)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(data)


def event(source="mi-libro", key="uploads/job1/libro.pdf"):
    return {"bucket": "subidas", "key": key, "source": source}


def test_copia_el_pdf_a_la_clave_del_libro(storage):
    put(storage, "subidas", "uploads/job1/libro.pdf", b"%PDF-nuevo")
    result = handler(event(), storage=storage)
    assert result["booksKey"] == "books/mi-libro/original.pdf" and result["translationsRemoved"] == 0
    assert storage._path("libros", "books/mi-libro/original.pdf").read_bytes() == b"%PDF-nuevo"
    assert storage._path("subidas", "uploads/job1/libro.pdf").exists()  # el original de uploads/ no se toca (caduca solo)


def test_reprocesar_con_otro_pdf_borra_las_traducciones_de_ese_libro_y_solo_de_ese(storage):
    put(storage, "subidas", "uploads/job2/libro.pdf", b"%PDF-v2")
    put(storage, "libros", "books/mi-libro/original.pdf", b"%PDF-v1")
    put(storage, "libros", "translations/mi-libro/v2/0001.json")
    put(storage, "libros", "translations/mi-libro/v2/0002.json")
    put(storage, "libros", "translations/mi-libro-2/v2/0001.json")  # otro libro cuyo nombre EMPIEZA igual: no debe tocarse
    result = handler(event(key="uploads/job2/libro.pdf"), storage=storage)
    assert result["translationsRemoved"] == 2
    assert storage._path("libros", "books/mi-libro/original.pdf").read_bytes() == b"%PDF-v2"
    assert not storage._path("libros", "translations/mi-libro/v2/0001.json").exists()
    assert storage._path("libros", "translations/mi-libro-2/v2/0001.json").exists()


def test_si_la_copia_falla_no_se_borra_nada(storage):
    put(storage, "libros", "translations/mi-libro/v2/0001.json")
    with pytest.raises(FileNotFoundError):  # no existe el PDF de subida
        handler(event(), storage=storage)
    assert storage._path("libros", "translations/mi-libro/v2/0001.json").exists()


@pytest.mark.parametrize("source", ["../x", "a/b", "A mayusculas", "x", ""])
def test_rechaza_nombres_de_libro_peligrosos(storage, source):
    with pytest.raises(ValueError):
        handler(event(source=source), storage=storage)


def test_rechaza_claves_que_no_son_subidas(storage):
    with pytest.raises(ValueError):
        handler(event(key="jobs/job1/chunks.json"), storage=storage)


def test_delete_prefix_no_acepta_prefijo_vacio(storage):
    with pytest.raises(ValueError):
        storage.delete_prefix("libros", "")


class FakeBooksTable:
    def __init__(self):
        self.items = []

    def put_item(self, Item):
        self.items.append(Item)


FULL_EVENT = {
    "bucket": "subidas", "key": "uploads/job1/libro.pdf", "source": "mi-libro", "title": "Mi libro", "filename": "libro.pdf",
    "jobId": "job1", "pageCount": 20, "createdAt": "2026-10-19T00:23:02Z",
    "summary": {"chunks": 58, "chunksListos": 42, "chunksPendientes": 16, "hitosEscritos": 2, "personajes": {"Rey Wu": 1, "Rey Cheng": 2}, "costeEstimadoUsd": 0.1971},
}


def test_apunta_el_libro_en_el_catalogo_al_final_con_sus_estadisticas(storage):
    put(storage, "subidas", "uploads/job1/libro.pdf")
    table = FakeBooksTable()
    result = handler(FULL_EVENT, storage=storage, books_table=table)
    assert result["catalogued"] is True and len(table.items) == 1
    item = table.items[0]
    assert (item["slug"], item["title"], item["status"], item["pageCount"], item["jobId"]) == ("mi-libro", "Mi libro", "DONE", 20, "job1")
    assert item["createdAt"] == "2026-10-19T00:23:02Z"
    assert item["stats"]["chunks"] == 58 and item["stats"]["hitos"] == 2 and item["stats"]["personajes"] == 2


def test_los_decimales_del_coste_van_como_decimal_porque_dynamodb_no_acepta_float():
    from decimal import Decimal

    stats = catalog_item(FULL_EVENT)["stats"]
    assert stats["costeEstimadoUsd"] == Decimal("0.1971") and isinstance(stats["costeEstimadoUsd"], Decimal)
    assert not any(isinstance(v, float) for v in stats.values())


def test_si_la_copia_del_pdf_falla_el_libro_no_aparece_en_el_catalogo(storage):
    table = FakeBooksTable()
    with pytest.raises(FileNotFoundError):  # no existe el PDF de subida
        handler(FULL_EVENT, storage=storage, books_table=table)
    assert table.items == []


def test_sin_tabla_configurada_no_apunta_nada_pero_guarda_el_pdf(storage, monkeypatch):
    monkeypatch.delenv("BOOKS_TABLE", raising=False)
    put(storage, "subidas", "uploads/job1/libro.pdf")
    assert handler(FULL_EVENT, storage=storage)["catalogued"] is False
    assert storage._path("libros", "books/mi-libro/original.pdf").exists()
