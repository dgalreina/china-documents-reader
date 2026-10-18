"""store_book: copia el PDF al bucket permanente y borra las traducciones viejas. S3 local, sin AWS."""

import pytest

from lambdas.store_book.handler import handler
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
    assert result == {"booksKey": "books/mi-libro/original.pdf", "translationsRemoved": 0}
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
