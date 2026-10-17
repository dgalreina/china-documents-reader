"""Tests de presign: la lógica de la URL firmada y el handler HTTP, con un cliente de S3 falso (sin AWS)."""

import base64
import json

import pytest

from lambdas.presign.handler import handler
from lambdas.presign.signer import InvalidUpload, create_upload, max_bytes, safe_filename


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
    response = handler(event({"filename": "libro.pdf"}), s3_client=FakeS3())
    body = json.loads(response["body"])
    assert response["statusCode"] == 200 and response["headers"]["Content-Type"] == "application/json"
    assert {"jobId", "key", "maxBytes", "upload"} <= body.keys() and {"url", "fields"} <= body["upload"].keys()


def test_handler_acepta_cuerpo_en_base64():
    raw = base64.b64encode(json.dumps({"filename": "libro.pdf"}).encode()).decode()
    assert handler(event(raw, isBase64Encoded=True), s3_client=FakeS3())["statusCode"] == 200


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
    response = handler(ev, s3_client=FakeS3())
    assert response["statusCode"] == 400 and json.loads(response["body"])["error"]


def test_handler_no_firma_nada_si_la_peticion_no_es_valida():
    s3 = FakeS3()
    handler(event({"filename": "libro.exe"}), s3_client=s3)
    assert s3.calls == []
