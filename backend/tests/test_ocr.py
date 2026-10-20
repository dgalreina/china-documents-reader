"""OCR de libros escaneados: la lógica, los pasos con Textract y OpenAI FALSOS, y cómo lo usan la traducción, el análisis y el catálogo (sin red ni coste)."""

import json
from types import SimpleNamespace

import pytest
from pypdf import PdfWriter

from lambdas.books.handler import handler as books_handler
from lambdas.extract_chunk.handler import handler as extract_handler
from lambdas.ocr import handlers as ocr
from lambdas.ocr.core import (
    OCR_FILE,
    build_document,
    detect_text_layer,
    estimate_cost,
    lines_by_page,
    lines_to_text,
    merge_parts,
    ocr_key,
    page_text,
)
from lambdas.translate_page.handler import handler as translate_handler
from shared.storage import LocalStorage

from .test_extract_chunk_handler import make_pdf

SLUG = "mi-libro"


@pytest.fixture
def storage(tmp_path):
    return LocalStorage(tmp_path / "s3")


def blank_pdf(path, pages=3):
    writer = PdfWriter()
    for _ in range(pages):
        writer.add_blank_page(width=300, height=400)
    with open(path, "wb") as handle:
        writer.write(handle)


def line(text, top, height=0.02):
    return {"text": text, "top": top, "height": height}


# ----------------------------------------------------------------------------------------------------------------------- lógica


def test_el_ocr_de_un_libro_vive_en_su_carpeta():
    assert ocr_key("mi-libro") == f"books/mi-libro/{OCR_FILE}"


def test_el_coste_estimado_por_motor():
    assert estimate_cost("textract", 554) == pytest.approx(0.831, abs=0.001)  # ~0,83 USD el libro de 554 páginas
    assert estimate_cost("openai", 554) > 7  # unas 9 veces más
    assert estimate_cost("textract", 0) == 0


def test_un_pdf_con_texto_se_reconoce_y_uno_sin_texto_no(tmp_path):
    texto, vacio = tmp_path / "t.pdf", tmp_path / "v.pdf"
    make_pdf(texto, pages=4)
    blank_pdf(vacio, pages=20)
    assert detect_text_layer(texto) is True
    assert detect_text_layer(vacio) is False
    assert detect_text_layer(tmp_path / "no-existe.pdf") is True  # no se puede leer: no se ofrece OCR por error


def test_las_lineas_se_agrupan_en_parrafos_y_las_palabras_partidas_se_unen():
    lines = [line("The king rose against the inter-", 0.10), line("esting realm of Chu.", 0.125), line("A new paragraph begins here.", 0.20)]
    assert lines_to_text(lines) == "The king rose against the interesting realm of Chu.\n\nA new paragraph begins here."
    assert lines_to_text([line("   ", 0.1), line("Hola", 0.2)]) == "Hola"
    assert lines_to_text([]) == ""


def test_los_bloques_de_textract_se_reparten_por_pagina_ignorando_palabras():
    blocks = [
        {"BlockType": "PAGE", "Page": 1},
        {"BlockType": "LINE", "Page": 1, "Text": "uno", "Geometry": {"BoundingBox": {"Top": 0.1, "Height": 0.02}}},
        {"BlockType": "WORD", "Page": 1, "Text": "uno"},
        {"BlockType": "LINE", "Page": 2, "Text": "dos", "Geometry": {"BoundingBox": {"Top": 0.1, "Height": 0.02}}},
        {"BlockType": "LINE", "Text": "sin pagina (PDF de una hoja)", "Geometry": {}},
    ]
    pages = lines_by_page(blocks)
    assert [item["text"] for item in pages[1]] == ["uno", "sin pagina (PDF de una hoja)"] and [item["text"] for item in pages[2]] == ["dos"]


def test_los_trozos_se_unen_en_orden_y_una_pagina_sin_trozo_queda_vacia():
    parts = [{"start": 3, "texts": ["d", "e"]}, {"start": 0, "texts": ["a", "b", "c"]}]
    assert merge_parts(parts, 6) == ["a", "b", "c", "d", "e", ""]
    assert merge_parts(parts, 4) == ["a", "b", "c", "d"]  # lo que sobra se descarta


def test_el_texto_de_una_pagina_del_documento():
    document = build_document("textract", ["uno", "dos"])
    assert page_text(document, 2) == "dos" and page_text(document, 3) == "" and page_text(None, 1) == ""
    assert document["engine"] == "textract" and document["pageCount"] == 2


# ----------------------------------------------------------------------------------------------------------------------- arrancar


class Table:
    def __init__(self, items=()):
        self.items = {item.get("slug") or item.get("jobId"): dict(item) for item in items}
        self.updates = []

    def get_item(self, Key):
        item = self.items.get(Key.get("slug") or Key.get("jobId"))
        return {"Item": item} if item else {}

    def put_item(self, Item):
        self.items[Item.get("jobId") or Item["slug"]] = dict(Item)

    def update_item(self, **kwargs):
        self.updates.append(kwargs)
        if "ConditionExpression" in kwargs and kwargs["Key"].get("slug") not in self.items:
            error = Exception("condición")
            error.response = {"Error": {"Code": "ConditionalCheckFailedException"}}
            raise error
        key = kwargs["Key"].get("slug") or kwargs["Key"].get("jobId")
        if key in self.items and ":o" in kwargs.get("ExpressionAttributeValues", {}):
            self.items[key]["ocr"] = kwargs["ExpressionAttributeValues"][":o"]

    def scan(self, **_):
        return {"Items": list(self.items.values())}


class Sfn:
    def __init__(self, fail=False):
        self.calls, self.fail = [], fail

    def start_execution(self, **kwargs):
        if self.fail:
            raise RuntimeError("sin permiso")
        self.calls.append(kwargs)


def start(engine="textract", book=None, jobs=None, sfn=None, body=None):
    book = {"slug": SLUG, "title": "Mi libro", "filename": "l.pdf", "pageCount": 554, "textLayer": False} if book is None else book
    event = {"routeKey": "POST /books/{slug}/ocr", "pathParameters": {"slug": SLUG}, "body": json.dumps(body if body is not None else {"engine": engine})}
    books = Table([book] if book else [])
    jobs = jobs or Table()
    sfn = sfn or Sfn()
    response = ocr.start_handler(event, books_table=books, jobs_table=jobs, sfn=sfn, now=1_000.0, new_id=lambda: "j" * 32)
    return response["statusCode"], json.loads(response["body"]), jobs, sfn


@pytest.fixture(autouse=True)
def env(monkeypatch):
    for name, value in {"UPLOAD_BUCKET": "datos", "BOOKS_BUCKET": "libros", "OCR_STATE_MACHINE_ARN": "arn:ocr"}.items():
        monkeypatch.setenv(name, value)


def test_pedir_ocr_apunta_el_trabajo_y_arranca_la_maquina_con_el_coste_estimado():
    status, body, jobs, sfn = start("textract")
    assert status == 202 and body["estimatedCostUsd"] == pytest.approx(0.831, abs=0.001)
    job = jobs.items["j" * 32]
    assert (job["kind"], job["status"], job["source"], job["engine"]) == ("ocr", "RUNNING", SLUG, "textract")
    call = sfn.calls[0]
    assert call["stateMachineArn"] == "arn:ocr" and call["name"] == "j" * 32
    assert json.loads(call["input"]) == {
        "jobId": "j" * 32, "source": SLUG, "engine": "textract", "pageCount": 554, "bucket": "datos", "pdfBucket": "libros", "key": f"books/{SLUG}/original.pdf",
    }


@pytest.mark.parametrize("body", [{"engine": "tesseract"}, {}, {"engine": None}])
def test_un_motor_desconocido_es_400(body):
    assert start(body=body)[0] == 400


def test_un_libro_que_ya_tiene_texto_no_necesita_ocr_y_uno_inexistente_es_404():
    assert start(book={"slug": SLUG, "pageCount": 5, "textLayer": True})[0] == 400
    event = {"routeKey": "POST /books/{slug}/ocr", "pathParameters": {"slug": SLUG}, "body": '{"engine":"textract"}'}
    assert ocr.start_handler(event, books_table=Table(), jobs_table=Table(), sfn=Sfn())["statusCode"] == 404


def test_si_ya_hay_algo_en_curso_con_ese_libro_es_409():
    busy = Table([{"jobId": "otro", "source": SLUG, "status": "RUNNING"}])
    status, _, jobs, sfn = start(jobs=busy)
    assert status == 409 and sfn.calls == []


def test_si_la_maquina_no_arranca_el_trabajo_queda_fallido_con_un_mensaje_claro():
    status, body, jobs, _ = start(sfn=Sfn(fail=True))
    assert status == 502 and "sin permiso" not in json.dumps(body)
    assert jobs.updates and jobs.updates[0]["ExpressionAttributeValues"][":s"] == "FAILED"


# ------------------------------------------------------------------------------------------------------------------ pasos (Textract)


class FakeTextract:
    def __init__(self, status="SUCCEEDED", pages=None):
        self.status, self.pages, self.started = status, pages or {}, []

    def start_document_text_detection(self, **kwargs):
        self.started.append(kwargs)
        return {"JobId": "t-123"}

    def get_document_text_detection(self, JobId, MaxResults, NextToken=None):
        assert JobId == "t-123"
        if MaxResults == 1:
            return {"JobStatus": self.status, "StatusMessage": "PDF no soportado"}
        # dos respuestas paginadas
        blocks = [
            {"BlockType": "LINE", "Page": number, "Text": text, "Geometry": {"BoundingBox": {"Top": 0.1, "Height": 0.02}}}
            for number, text in sorted(self.pages.items())
        ]
        return {"Blocks": blocks[:1], "NextToken": "siguiente"} if NextToken is None else {"Blocks": blocks[1:]}


def test_textract_arranca_sobre_el_pdf_de_s3_y_con_un_token_que_evita_cobrar_dos_veces():
    textract = FakeTextract()
    result = ocr.prepare_handler({"jobId": "j" * 40, "engine": "textract", "pdfBucket": "libros", "key": f"books/{SLUG}/original.pdf", "pageCount": 554}, textract=textract)
    assert result["textractJobId"] == "t-123" and result["total"] == 12  # ~12 comprobaciones de 20 s esperadas para 554 páginas
    sent = textract.started[0]
    assert sent["DocumentLocation"] == {"S3Object": {"Bucket": "libros", "Name": f"books/{SLUG}/original.pdf"}}
    assert len(sent["ClientRequestToken"]) <= 64


def test_con_openai_las_tandas_cubren_todo_el_libro_sin_pasarse():
    result = ocr.prepare_handler({"jobId": "j", "engine": "openai", "pdfBucket": "libros", "key": "k", "pageCount": 554})
    assert result["starts"][0] == 0 and result["starts"][-1] == 552 and len(result["starts"]) == 70 and result["total"] == 70  # 554 / 8 = 69,25 -> 70 tandas
    assert ocr.prepare_handler({"jobId": "j", "engine": "openai", "pdfBucket": "x", "key": "k", "pageCount": 8})["starts"] == [0]


def test_comprobar_textract_distingue_en_marcha_terminado_y_fallido():
    assert ocr.check_handler({"textractJobId": "t-123"}, textract=FakeTextract("IN_PROGRESS")) == {"status": "IN_PROGRESS"}
    assert ocr.check_handler({"textractJobId": "t-123"}, textract=FakeTextract("SUCCEEDED")) == {"status": "SUCCEEDED"}
    assert ocr.check_handler({"textractJobId": "t-123"}, textract=FakeTextract("PARTIAL_SUCCESS")) == {"status": "SUCCEEDED"}
    with pytest.raises(ocr.OcrFailed, match="PDF no soportado"):
        ocr.check_handler({"textractJobId": "t-123"}, textract=FakeTextract("FAILED"))


def test_recoger_textract_lee_todas_las_paginas_aunque_vengan_en_varias_respuestas(storage):
    textract = FakeTextract(pages={1: "Primera página", 2: "Segunda página", 4: "Cuarta"})
    results = ocr.collect_handler({"jobId": "job1", "bucket": "datos", "pageCount": 4, "textractJobId": "t-123"}, storage=storage, textract=textract)
    part = storage.get_json("datos", results[0]["partKey"])
    assert part == {"start": 0, "texts": ["Primera página", "Segunda página", "", "Cuarta"]}  # la 3 no tenía texto: queda vacía
    assert results[0]["usage"]["estimatedCostUsd"] == pytest.approx(0.006)


# --------------------------------------------------------------------------------------------------------------------- pasos (OpenAI)


class FakeVision:
    def __init__(self):
        self.calls = []
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self._create))

    def _create(self, **kwargs):
        self.calls.append(kwargs)
        number = len(self.calls)
        return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=f"  texto {number}  "))])


def test_una_tanda_de_openai_dibuja_cada_pagina_y_deja_su_texto_en_s3(storage, tmp_path):
    local = tmp_path / "libro.pdf"
    blank_pdf(local, pages=5)
    target = storage.root / "libros" / f"books/{SLUG}/original.pdf"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(local.read_bytes())
    client = FakeVision()
    event = {"jobId": "job1", "bucket": "datos", "pdfBucket": "libros", "key": f"books/{SLUG}/original.pdf", "start": 2, "size": 8, "pageCount": 5}
    result = ocr.pages_handler(event, storage=storage, client=client, render=lambda document, index: f"jpeg-{index}".encode())
    assert result["partKey"] == "jobs/job1/ocr/2.json"
    part = storage.get_json("datos", result["partKey"])
    assert part["start"] == 2 and len(part["texts"]) == 3 and all(text.startswith("texto") for text in part["texts"])  # páginas 3, 4 y 5 (no se pasa del final)
    sent = client.calls[0]
    assert sent["model"] == "gpt-4o" and sent["temperature"] == 0
    content = sent["messages"][0]["content"]
    assert content[1]["image_url"]["url"].startswith("data:image/jpeg;base64,") and "NO traduzcas" in content[0]["text"]


# --------------------------------------------------------------------------------------------------------------------- terminar


def finish(storage, books, results, engine="textract", pages=4):
    return ocr.finish_handler(
        {"jobId": "job1", "source": SLUG, "engine": engine, "bucket": "datos", "pdfBucket": "libros", "pageCount": pages, "results": results},
        storage=storage,
        books_table=books,
    )


def test_terminar_deja_el_ocr_del_libro_borra_las_traducciones_viejas_y_lo_apunta_en_el_catalogo(storage):
    storage.put_json("datos", "jobs/job1/ocr/0.json", {"start": 0, "texts": ["a", "b", "", "d"]})
    storage.put_json("libros", f"translations/{SLUG}/v1/2.json", {"page": 2, "text": ""})  # una página que se tradujo "vacía" antes del OCR
    books = Table([{"slug": SLUG, "pageCount": 4}])
    summary = finish(storage, books, [{"partKey": "jobs/job1/ocr/0.json", "usage": {"estimatedCostUsd": 0.006}}])
    document = storage.get_json("libros", ocr_key(SLUG))
    assert document["pages"] == ["a", "b", "", "d"] and document["engine"] == "textract"
    assert not storage.exists("libros", f"translations/{SLUG}/v1/2.json")
    assert books.items[SLUG]["ocr"]["engine"] == "textract" and books.items[SLUG]["ocr"]["pagesWithText"] == 3
    assert summary == {"catalogued": True, "pages": 4, "pagesWithText": 3, "engine": "textract", "estimatedCostUsd": 0.006}


def test_si_el_libro_se_borro_mientras_tanto_no_se_deja_el_ocr_huerfano(storage):
    storage.put_json("datos", "jobs/job1/ocr/0.json", {"start": 0, "texts": ["a", "b", "c", "d"]})
    summary = finish(storage, Table(), [{"partKey": "jobs/job1/ocr/0.json", "usage": {}}])
    assert summary["catalogued"] is False and not storage.exists("libros", ocr_key(SLUG))


# ----------------------------------------------------------------------------------------------------------- cómo lo usan los demás


def put_book_pdf(storage, tmp_path, make):
    local = tmp_path / "libro.pdf"
    make(local)
    target = storage.root / "libros" / f"books/{SLUG}/original.pdf"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(local.read_bytes())


class Translator:
    def __init__(self):
        self.seen = []
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self._create))

    def _create(self, **kwargs):
        self.seen.append(kwargs["messages"])
        return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content="Traducido."))], usage=None)


def translate(storage, tmp_path, client):
    event = {"pathParameters": {"slug": SLUG, "page": "2"}}
    response = translate_handler(event, storage=storage, client=client, cache_dir=tmp_path / "cache")
    return response["statusCode"], json.loads(response["body"])


def test_la_traduccion_de_un_escaneado_usa_el_ocr_y_sin_ocr_dice_que_no_hay_texto(storage, tmp_path):
    put_book_pdf(storage, tmp_path, lambda path: blank_pdf(path, pages=3))
    client = Translator()
    status, body = translate(storage, tmp_path, client)
    assert status == 200 and body["text"] == "" and client.seen == []  # sin OCR: nada que traducir (y no se gasta)

    storage.delete_prefix("libros", f"translations/{SLUG}/")
    storage.put_json("libros", ocr_key(SLUG), build_document("textract", ["uno", "The king of Zhou conquered Shang.", "tres"]))
    status, body = translate(storage, tmp_path, client)
    assert status == 200 and body["text"] == "Traducido."
    assert "The king of Zhou conquered Shang." in json.dumps(client.seen[0])  # tradujo el texto del OCR de la página 2


def test_el_texto_propio_del_pdf_manda_sobre_el_ocr(storage, tmp_path):
    put_book_pdf(storage, tmp_path, lambda path: make_pdf(path, pages=3, text="Texto propio del PDF."))
    storage.put_json("libros", ocr_key(SLUG), build_document("textract", ["x", "TEXTO DEL OCR", "x"]))
    client = Translator()
    translate(storage, tmp_path, client)
    assert "Texto propio" in json.dumps(client.seen[0]) and "TEXTO DEL OCR" not in json.dumps(client.seen[0])


def test_el_analisis_de_un_escaneado_con_ocr_ya_no_es_necesita_ocr(storage, tmp_path):
    put_book_pdf(storage, tmp_path, lambda path: blank_pdf(path, pages=4))
    event = {"jobId": "job9", "bucket": "datos", "pdfBucket": "libros", "key": f"books/{SLUG}/original.pdf"}
    assert extract_handler(event, storage=storage)["scanned"] is True  # sin OCR, como antes

    texts = [f"Page {n}: the Zhou dynasty rose after the battle of Muye in 1046 BCE. " * 6 for n in range(1, 5)]
    storage.put_json("libros", ocr_key(SLUG), build_document("textract", texts))
    result = extract_handler(event, storage=storage)
    assert result["scanned"] is False and result["chunkCount"] > 0 and result["emptyPages"] == 0
    chunks = storage.get_json("datos", result["chunksKey"])
    assert min(chunk["page_start"] for chunk in chunks) == 1 and max(chunk["page_end"] for chunk in chunks) == 4  # las páginas de los chunks siguen siendo las del PDF


def test_el_catalogo_enseña_si_tiene_capa_de_texto_y_el_ocr_y_firma_su_url():
    item = {"slug": SLUG, "title": "T", "pageCount": 5, "analysis": "none", "textLayer": False, "ocr": {"engine": "textract", "pages": 5}, "createdAt": "2026-10-20T00:00:00+00:00"}

    class S3:
        def generate_presigned_url(self, op, Params, ExpiresIn):
            return f"https://s3.test/{Params['Key']}?x"

    listed = json.loads(books_handler({"routeKey": "GET /books"}, books_table=Table([item]), s3_client=S3())["body"])["books"][0]
    assert listed["textLayer"] is False and listed["ocr"]["engine"] == "textract"

    url = books_handler({"routeKey": "GET /books/{slug}/ocr-url", "pathParameters": {"slug": SLUG}}, books_table=Table([item]), s3_client=S3())
    assert url["statusCode"] == 200 and json.loads(url["body"])["url"] == f"https://s3.test/books/{SLUG}/ocr.json?x"
    sin_ocr = {**item, "ocr": None}
    assert books_handler({"routeKey": "GET /books/{slug}/ocr-url", "pathParameters": {"slug": SLUG}}, books_table=Table([sin_ocr]), s3_client=S3())["statusCode"] == 404
    assert books_handler({"routeKey": "GET /books/{slug}/ocr-url", "pathParameters": {"slug": "../x"}}, books_table=Table([item]), s3_client=S3())["statusCode"] == 400


def test_los_fallos_de_textract_que_el_usuario_puede_resolver_salen_en_espanol_y_sin_tecnicismos():
    class Rejecting:
        def __init__(self, code):
            self.code = code

        def start_document_text_detection(self, **_):
            error = Exception("An error occurred")
            error.response = {"Error": {"Code": self.code}}
            raise error

    event = {"jobId": "j", "engine": "textract", "pdfBucket": "libros", "key": "k", "pageCount": 3}
    with pytest.raises(ocr.OcrFailed, match="no está activado en esta cuenta"):
        ocr.prepare_handler(event, textract=Rejecting("SubscriptionRequiredException"))
    with pytest.raises(ocr.OcrFailed, match="UnsupportedDocumentException.*OpenAI"):
        ocr.prepare_handler(event, textract=Rejecting("UnsupportedDocumentException"))
    with pytest.raises(Exception) as other:  # un fallo desconocido NO se disfraza: sigue siendo el error técnico (queda en los registros)
        ocr.prepare_handler(event, textract=Rejecting("ValorRaro"))
    assert not isinstance(other.value, ocr.OcrFailed)
