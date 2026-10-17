"""Tests del handler de extract_chunk con almacenamiento local (sin AWS) y un PDF generado."""

import json

import pytest
from reportlab.pdfgen import canvas

from lambdas.extract_chunk.handler import handler
from shared.storage import LocalStorage


def make_pdf(path, pages=3, text="La dinastia Tang fue fundada en el ano 618 en Chang'an."):
    c = canvas.Canvas(str(path))
    for p in range(pages):
        t = c.beginText(50, 780)
        for i in range(40):
            t.textLine(f"Pagina {p + 1} linea {i}: {text}" if text else "")
        c.drawText(t)
        c.showPage()
    c.save()


@pytest.fixture
def storage(tmp_path):
    return LocalStorage(tmp_path / "s3")


def put_pdf(storage, tmp_path, key="uploads/job1/libro.pdf", **kwargs):
    local = tmp_path / "libro.pdf"
    make_pdf(local, **kwargs)
    target = storage.root / "bucket" / key
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(local.read_bytes())


def test_deja_los_chunks_en_s3_y_devuelve_solo_la_referencia(storage, tmp_path):
    put_pdf(storage, tmp_path)
    result = handler({"jobId": "job1", "bucket": "bucket", "key": "uploads/job1/libro.pdf"}, storage=storage)
    assert result["chunksKey"] == "jobs/job1/chunks.json" and result["chunkCount"] > 0
    assert result["pageCount"] == 3 and result["emptyPages"] == 0 and result["scanned"] is False
    assert "chunks" not in result and len(json.dumps(result)) < 1000  # cabe de sobra en los 256 KB de Step Functions
    chunks = storage.get_json("bucket", result["chunksKey"])
    assert len(chunks) == result["chunkCount"] and {"index", "text", "page_start", "page_end"} <= chunks[0].keys()


def test_un_pdf_sin_texto_se_marca_como_escaneado(storage, tmp_path):
    put_pdf(storage, tmp_path, text="")  # páginas en blanco: como un escaneado sin capa de texto
    result = handler({"jobId": "job1", "bucket": "bucket", "key": "uploads/job1/libro.pdf"}, storage=storage)
    assert result["scanned"] is True and result["chunkCount"] == 0 and result["emptyPages"] == 3


def test_los_chunks_conservan_la_pagina_de_origen(storage, tmp_path):
    put_pdf(storage, tmp_path, pages=3)
    result = handler({"jobId": "job1", "bucket": "bucket", "key": "uploads/job1/libro.pdf"}, storage=storage)
    chunks = storage.get_json("bucket", result["chunksKey"])
    assert chunks[0]["page_start"] == 1 and chunks[-1]["page_end"] == 3
    assert [c["index"] for c in chunks] == list(range(len(chunks)))


def test_falta_un_campo_del_evento_da_error(storage):
    with pytest.raises(KeyError):
        handler({"jobId": "job1", "bucket": "bucket"}, storage=storage)


def test_el_almacenamiento_local_no_permite_salirse_de_su_carpeta(storage):
    with pytest.raises(ValueError):
        storage.get_json("bucket", "../../secreto.json")
