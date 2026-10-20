"""Punto de entrada de AWS Lambda detrás de API Gateway (HTTP API): GET /books/{slug}/pages/{page}/translation

El navegador NO manda texto: solo dice qué libro y qué página quiere. La Lambda busca ella el PDF en S3, saca esa página y la traduce,
así que solo se pueden traducir páginas de libros que ya subió el dueño (con el texto en el body, cualquiera con sesión podría usar la
clave de OpenAI de traductor). Es independiente del pipeline de chunks/hitos: no toca Mongo.

Orden: 1) caché (si la página ya está traducida, se devuelve sin abrir el PDF ni llamar a OpenAI); 2) si no, descarga el PDF, saca solo esa
página con pypdf y pide la traducción; 3) la guarda. El front precarga las páginas siguientes pidiéndolas por adelantado.

Convención: el PDF vive en `books/<slug>/original.pdf` del bucket BOOKS_BUCKET (variable de entorno que pondrá el CDK), y las traducciones
en `translations/<slug>/...` del mismo bucket. OJO: hoy el PDF subido va a `uploads/<jobId>/...`, que caduca a los 30 días; copiarlo a
`books/<slug>/original.pdf` sin caducidad es un paso pendiente. Si se reprocesa un libro con OTRO PDF bajo el mismo slug, hay que borrar
`translations/<slug>/` (si no, se servirían las traducciones del PDF anterior).
"""

import json
import os
import re
import tempfile
from pathlib import Path

from pypdf import PdfReader

from shared.storage import S3Storage, Storage

from ..ocr.core import ocr_key
from ..ocr.core import page_text as ocr_page_text
from .translator import is_valid_slug, lookup_cached, translate_page

HEADERS = {"Content-Type": "application/json"}
PAGE = re.compile(r"^[0-9]{1,5}$")
# Copia del PDF en /tmp: una Lambda "caliente" reutiliza su /tmp entre peticiones y así no se baja el libro (hasta 100 MB) cada vez.
CACHE_DIR = Path(tempfile.gettempdir()) / "books"


class NotFound(Exception):
    pass


def pdf_key(slug: str) -> str:
    return f"books/{slug}/original.pdf"


def _response(status: int, body: dict) -> dict:
    return {"statusCode": status, "headers": HEADERS, "body": json.dumps(body, ensure_ascii=False)}


def _is_missing(err: Exception) -> bool:
    if isinstance(err, FileNotFoundError):
        return True
    code = getattr(err, "response", {}).get("Error", {}).get("Code")  # los errores de boto3 traen el código en .response
    return code in ("404", "NoSuchKey", "NotFound")


def _local_pdf(storage: Storage, bucket: str, slug: str, cache_dir: Path) -> Path:
    path = cache_dir / f"{slug}.pdf"
    if path.exists():
        return path
    cache_dir.mkdir(parents=True, exist_ok=True)
    partial = cache_dir / f"{slug}.pdf.{os.getpid()}.part"  # se descarga aparte y se renombra: nunca queda un PDF a medias
    try:
        storage.download(bucket, pdf_key(slug), partial)
    except Exception as err:
        partial.unlink(missing_ok=True)
        if _is_missing(err):
            raise NotFound("No existe ese libro.") from err
        raise
    os.replace(partial, path)
    return path


def _ocr_document(storage: Storage, bucket: str, slug: str) -> dict | None:
    """El texto OCR del libro (un JSON con el de cada página), o None si no se hizo OCR."""
    return storage.try_get_json(bucket, ocr_key(slug))


def page_text(storage: Storage, bucket: str, slug: str, page: int, cache_dir: Path = CACHE_DIR) -> str:
    reader = PdfReader(str(_local_pdf(storage, bucket, slug, cache_dir)))
    if page > len(reader.pages):
        raise NotFound("Esa página no existe en el libro.")
    text = (reader.pages[page - 1].extract_text() or "").strip()  # solo esa página: no se lee el libro entero
    if text:
        return text
    return ocr_page_text(_ocr_document(storage, bucket, slug), page)  # un PDF escaneado no tiene texto propio: se usa el del OCR, si se hizo


def handler(event: dict, _context=None, storage: Storage | None = None, client=None, cache_dir: Path = CACHE_DIR) -> dict:
    params = event.get("pathParameters") or {}
    slug, raw_page = params.get("slug", ""), params.get("page", "")
    if not is_valid_slug(slug):
        return _response(400, {"error": "Identificador de libro no válido."})
    if not PAGE.match(raw_page) or int(raw_page) < 1:
        return _response(400, {"error": "Número de página no válido."})
    page = int(raw_page)

    storage = storage or S3Storage()
    bucket = os.environ["BOOKS_BUCKET"]
    try:
        result = lookup_cached(storage, bucket, slug, page)
        if result is None:
            text = page_text(storage, bucket, slug, page, cache_dir)
            if client is None:
                from ..embed_label.pipeline import make_client  # importación tardía: la caché no necesita OpenAI

                client = make_client()
            result = translate_page(client, storage, bucket, slug, page, text)
    except NotFound as err:
        return _response(404, {"error": str(err)})
    except Exception as err:  # fallo de OpenAI o de S3: al navegador solo le llega un aviso genérico (el detalle queda en los logs)
        print(f"translate_page falló ({slug} pág. {page}): {type(err).__name__}")
        return _response(502, {"error": "No se pudo traducir la página. Inténtalo de nuevo."})
    return _response(200, {"page": result.page, "text": result.text, "cached": result.cached})
