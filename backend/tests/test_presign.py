"""Tests de presign: la lógica de la URL firmada y el handler HTTP, con un cliente de S3 falso (sin AWS)."""

import base64
import json
import time

import pytest

from lambdas.presign.handler import handler
from lambdas.presign.signer import InvalidUpload, create_upload, max_bytes, resolve_title, safe_filename


class FakeTable:
    def __init__(self):
        self.items = []

    def put_item(self, Item):
        self.items.append(Item)


class FakeS3:
    def __init__(self):
        self.calls = []

    def generate_presigned_post(self, **kwargs):
        self.calls.append(kwargs)
        return {"url": f"https://{kwargs['Bucket']}.s3.eu-west-1.amazonaws.com/", "fields": {"key": kwargs["Key"], **kwargs["Fields"]}}


def event(body, **extra):
    return {"body": body if isinstance(body, str) else json.dumps(body), **extra}


@pytest.fixture(autouse=True)
def bucket(monkeypatch):
    monkeypatch.setenv("UPLOAD_BUCKET", "mi-bucket")
    monkeypatch.delenv("MAX_PDF_MB", raising=False)


@pytest.mark.parametrize(
    "name, expected",
    [
        ("libro.pdf", "libro.pdf"),
        ("Historia de China (2ª ed.).PDF", "Historia-de-China-2a-ed.pdf"),
        ("../../etc/passwd.pdf", "passwd.pdf"),  # sin rutas
        (r"C:\Users\yo\Mi libro.pdf", "Mi-libro.pdf"),
        ("中国历史.pdf", "documento.pdf"),  # sin caracteres válidos queda un nombre genérico
        ("a" * 300 + ".pdf", "a" * 96 + ".pdf"),  # longitud acotada
    ],
)
def test_safe_filename(name, expected):
    assert safe_filename(name) == expected


@pytest.mark.parametrize("name", ["libro.txt", "libro.pdf.exe", "", "   ", None, 42, "sinextension"])
def test_nombres_no_validos_se_rechazan(name):
    with pytest.raises(InvalidUpload):
        safe_filename(name)


def test_la_url_firmada_limita_tipo_tamano_y_duracion():
    s3 = FakeS3()
    result = create_upload(s3, "mi-bucket", "libro.pdf")
    (call,) = s3.calls
    assert call["Bucket"] == "mi-bucket" and call["Key"] == result["key"]
    assert result["key"] == f"uploads/{result['jobId']}/libro.pdf"
    assert {"Content-Type": "application/pdf"} in call["Conditions"]
    assert ["content-length-range", 1, 100 * 1024 * 1024] in call["Conditions"]
    assert call["ExpiresIn"] == 300 and result["maxBytes"] == 100 * 1024 * 1024
    assert result["upload"]["url"].startswith("https://mi-bucket.s3.")


def test_cada_subida_tiene_su_propio_jobid_y_clave():
    s3 = FakeS3()
    a, b = create_upload(s3, "b", "x.pdf"), create_upload(s3, "b", "x.pdf")
    assert a["jobId"] != b["jobId"] and a["key"] != b["key"]


def test_el_limite_de_tamano_se_configura_por_entorno(monkeypatch):
    monkeypatch.setenv("MAX_PDF_MB", "20")
    assert max_bytes() == 20 * 1024 * 1024


def test_handler_responde_200_con_lo_que_necesita_el_navegador():
    response = handler(event({"filename": "libro.pdf"}), s3_client=FakeS3(), jobs_table=FakeTable())
    body = json.loads(response["body"])
    assert response["statusCode"] == 200 and response["headers"]["Content-Type"] == "application/json"
    assert {"jobId", "key", "maxBytes", "upload"} <= body.keys() and {"url", "fields"} <= body["upload"].keys()


def test_handler_acepta_cuerpo_en_base64():
    raw = base64.b64encode(json.dumps({"filename": "libro.pdf"}).encode()).decode()
    assert handler(event(raw, isBase64Encoded=True), s3_client=FakeS3(), jobs_table=FakeTable())["statusCode"] == 200


@pytest.mark.parametrize(
    "ev",
    [
        {},  # sin cuerpo
        event("esto no es json"),
        event(json.dumps(["no", "objeto"])),
        event({}),  # sin filename
        event({"filename": "libro.exe"}),
    ],
)
def test_handler_responde_400_con_un_mensaje_claro(ev):
    response = handler(ev, s3_client=FakeS3(), jobs_table=FakeTable())
    assert response["statusCode"] == 400 and json.loads(response["body"])["error"]


def test_handler_no_firma_nada_si_la_peticion_no_es_valida():
    s3 = FakeS3()
    table = FakeTable()
    handler(event({"filename": "libro.exe"}), s3_client=s3, jobs_table=table)
    assert s3.calls == [] and table.items == []  # nada firmado y nada registrado


# ---- registro del trabajo en DynamoDB y slug del libro -----------------------------------------------


def test_el_handler_apunta_el_trabajo_antes_de_devolver_la_url():
    table = FakeTable()
    response = handler(event({"filename": "Historia de China (2ª ed.).pdf"}), s3_client=FakeS3(), jobs_table=table)
    body = json.loads(response["body"])
    (item,) = table.items
    assert item["jobId"] == body["jobId"] and item["status"] == "UPLOADING" and item["replaceSource"] is False
    assert item["source"] == "historia-de-china-2a-ed" == body["source"]  # derivado del nombre del archivo
    assert item["filename"] == "Historia de China (2ª ed.).pdf" and item["expiresAt"] > time.time() + 29 * 86400  # caduca en ~30 días


def test_el_usuario_puede_fijar_el_slug_y_pedir_reemplazo():
    table = FakeTable()
    handler(event({"filename": "x.pdf", "source": "oxford-handbook", "replaceSource": True}), s3_client=FakeS3(), jobs_table=table)
    assert table.items[0]["source"] == "oxford-handbook" and table.items[0]["replaceSource"] is True


@pytest.mark.parametrize("extra", [{"source": "Con Mayúsculas"}, {"source": "a"}, {"source": "../x"}, {"source": 5}, {"replaceSource": "si"}])
def test_slug_o_reemplazo_invalidos_dan_400_y_no_registran_nada(extra):
    table = FakeTable()
    response = handler(event({"filename": "x.pdf", **extra}), s3_client=FakeS3(), jobs_table=table)
    assert response["statusCode"] == 400 and table.items == []


def test_sin_slug_derivable_pide_indicarlo():
    table = FakeTable()
    response = handler(event({"filename": "中国历史.pdf"}), s3_client=FakeS3(), jobs_table=table)  # sin letras latinas
    assert response["statusCode"] == 400 and "source" in json.loads(response["body"])["error"]


def test_el_titulo_se_deriva_del_nombre_del_archivo_si_no_se_indica():
    assert resolve_title(None, "historia_de_china.pdf") == "Historia de china"
    assert resolve_title(None, "C:\\libros\\El mundo chino.pdf") == "El mundo chino"


def test_el_usuario_puede_poner_su_titulo_y_se_limpia_de_espacios():
    assert resolve_title("  El   mundo \n chino ", "x.pdf") == "El mundo chino"


@pytest.mark.parametrize("title", ["", "   ", 5, "x" * 121, "con\x00control"])
def test_titulos_invalidos_se_rechazan(title):
    with pytest.raises(InvalidUpload):
        resolve_title(title, "x.pdf")


def test_el_titulo_se_apunta_en_el_trabajo_y_se_devuelve():
    table = FakeTable()
    response = handler(event({"filename": "libro.pdf", "title": "Mi libro"}), s3_client=FakeS3(), jobs_table=table)
    assert table.items[0]["title"] == "Mi libro" == json.loads(response["body"])["title"]


def test_un_titulo_invalido_da_400_y_no_registra_nada():
    table = FakeTable()
    response = handler(event({"filename": "libro.pdf", "title": "x" * 500}), s3_client=FakeS3(), jobs_table=table)
    assert response["statusCode"] == 400 and table.items == []


# ---- subir no analiza: no se pisa un libro sin querer y la subida queda marcada como tal ------------------------------------------------------


class Books:
    def __init__(self, slugs=()):
        self.slugs = set(slugs)

    def get_item(self, Key):
        return {"Item": {"slug": Key["slug"]}} if Key["slug"] in self.slugs else {}


class ScanJobs(FakeTable):
    def __init__(self, existing=()):
        super().__init__()
        self.existing = list(existing)

    def scan(self, **kwargs):
        return {"Items": self.existing}


def upload(body, books, jobs):
    return handler(event(body), s3_client=FakeS3(), jobs_table=jobs, books_table=books, now=NOW)


NOW = 1_800_000_000.0


def recent(job_id, status, source, age=60):
    from datetime import datetime, timezone

    return {"jobId": job_id, "status": status, "source": source, "createdAt": datetime.fromtimestamp(NOW - age, timezone.utc).isoformat()}


def test_la_subida_se_apunta_como_subida_para_no_confundirla_con_un_analisis():
    jobs = ScanJobs()
    assert upload({"filename": "libro.pdf"}, Books(), jobs)["statusCode"] == 200
    assert jobs.items[0]["kind"] == "upload" and jobs.items[0]["status"] == "UPLOADING"


def test_un_libro_que_ya_existe_pide_confirmar_el_reemplazo_y_no_firma_ni_apunta_nada():
    jobs, s3 = ScanJobs(), FakeS3()
    response = handler(event({"filename": "libro.pdf"}), s3_client=s3, jobs_table=jobs, books_table=Books({"libro"}), now=NOW)
    assert response["statusCode"] == 409 and "reemplazar" in json.loads(response["body"])["error"]
    assert s3.calls == [] and jobs.items == []


def test_con_el_reemplazo_confirmado_si_se_puede_subir_sobre_un_libro_existente():
    jobs = ScanJobs()
    response = upload({"filename": "libro.pdf", "replaceSource": True}, Books({"libro"}), jobs)
    assert response["statusCode"] == 200 and jobs.items[0]["replaceSource"] is True


@pytest.mark.parametrize("busy", [("RUNNING", 60), ("UPLOADING", 60)])
def test_no_se_sube_sobre_un_libro_que_se_esta_analizando_o_subiendo(busy):
    status, age = busy
    jobs = ScanJobs([recent("j1", status, "libro", age)])
    response = upload({"filename": "libro.pdf", "replaceSource": True}, Books({"libro"}), jobs)
    assert response["statusCode"] == 409 and "analizando o subiendo" in json.loads(response["body"])["error"]
    assert jobs.items == []


def test_una_subida_abandonada_hace_rato_o_de_otro_libro_no_estorba():
    jobs = ScanJobs([recent("j1", "UPLOADING", "libro", age=3600), recent("j2", "RUNNING", "otro-libro")])
    assert upload({"filename": "libro.pdf"}, Books(), jobs)["statusCode"] == 200
