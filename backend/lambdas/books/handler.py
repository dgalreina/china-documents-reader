"""Punto de entrada de AWS Lambda detrás de API Gateway (HTTP API) para el catálogo de libros. Dos rutas, que se distinguen por `routeKey`:

  GET /books                  ->  {"books": [...]}  los libros ya procesados, el más nuevo primero
  GET /books/{slug}/pdf-url   ->  {"url", "expiresInSeconds"}  una URL firmada de LECTURA del PDF, para que el visor (pdf.js) lo abra directo desde S3

El catálogo vive en la tabla BOOKS_TABLE (permanente, a diferencia de los trabajos, que caducan a los 30 días): la escribe `store_book` al final
del flujo. El PDF vive en `books/<slug>/original.pdf` del bucket BOOKS_BUCKET, que es privado: la única forma de leerlo desde el navegador es esta
URL temporal, y solo se firma si el libro está en el catálogo. La petición ya llega autenticada (Cognito).
"""

import json
import os
from decimal import Decimal

from ..presign.signer import SOURCE_PATTERN

PDF_URL_SECONDS = 6 * 3600  # pdf.js pide trozos del PDF mientras lees: la URL debe durar lo que una sesión de lectura
PUBLIC_FIELDS = ("slug", "title", "filename", "pageCount", "analysis", "textLayer", "ocr", "jobId", "createdAt", "updatedAt", "stats")
OCR_URL_SECONDS = 3600  # el navegador lo baja entero una vez al abrir el libro
COVER_URL_SECONDS = 6 * 3600  # como la del PDF: dura lo que una sesión
HEADERS = {"Content-Type": "application/json"}


def _json_default(value):
    if isinstance(value, Decimal):  # DynamoDB devuelve los números como Decimal
        return int(value) if value == value.to_integral_value() else float(value)
    raise TypeError(f"no serializable: {type(value)}")


def _response(status: int, body: dict) -> dict:
    return {"statusCode": status, "headers": HEADERS, "body": json.dumps(body, ensure_ascii=False, default=_json_default)}


def _books_table():
    import boto3

    return boto3.resource("dynamodb").Table(os.environ["BOOKS_TABLE"])


def _s3_client():
    import boto3
    from botocore.config import Config

    # Firma v4 y dirección por bucket, como en presign: con la global, S3 puede redirigir y el navegador no lo sigue.
    return boto3.client("s3", config=Config(signature_version="s3v4", s3={"addressing_style": "virtual"}))


def _list_books(table, s3_client) -> dict:
    """La tabla es pequeña (un registro por libro): basta con recorrerla entera. Cada libro con portada lleva su URL firmada de lectura."""
    items, kwargs = [], {}
    while True:
        page = table.scan(**kwargs)
        items.extend(page.get("Items", []))
        if "LastEvaluatedKey" not in page:
            break
        kwargs["ExclusiveStartKey"] = page["LastEvaluatedKey"]
    items.sort(key=lambda item: item.get("createdAt", ""), reverse=True)
    books = []
    for item in items:
        book = {key: item[key] for key in PUBLIC_FIELDS if key in item}
        if item.get("hasCover"):
            book["coverUrl"] = s3_client.generate_presigned_url(
                "get_object",
                Params={"Bucket": os.environ["BOOKS_BUCKET"], "Key": f"books/{item['slug']}/cover.jpg", "ResponseContentType": "image/jpeg"},
                ExpiresIn=COVER_URL_SECONDS,
            )
        books.append(book)
    return _response(200, {"books": books})


def _pdf_url(slug: str, table, s3_client) -> dict:
    if not SOURCE_PATTERN.match(slug):
        return _response(400, {"error": "Identificador de libro no válido."})
    if not table.get_item(Key={"slug": slug}).get("Item"):  # solo se firma para libros del catálogo
        return _response(404, {"error": "No existe ese libro."})
    url = s3_client.generate_presigned_url(
        "get_object",
        Params={"Bucket": os.environ["BOOKS_BUCKET"], "Key": f"books/{slug}/original.pdf", "ResponseContentType": "application/pdf"},
        ExpiresIn=PDF_URL_SECONDS,
    )
    return _response(200, {"url": url, "expiresInSeconds": PDF_URL_SECONDS})


def _ocr_url(slug: str, table, s3_client) -> dict:
    """GET /books/{slug}/ocr-url: URL firmada del texto OCR del libro (un JSON con el texto de cada página), solo si el libro tiene OCR hecho."""
    if not SOURCE_PATTERN.match(slug):
        return _response(400, {"error": "Identificador de libro no válido."})
    item = table.get_item(Key={"slug": slug}).get("Item")
    if not item:
        return _response(404, {"error": "No existe ese libro."})
    if not item.get("ocr"):
        return _response(404, {"error": "Este libro no tiene OCR."})
    url = s3_client.generate_presigned_url(
        "get_object",
        Params={"Bucket": os.environ["BOOKS_BUCKET"], "Key": f"books/{slug}/ocr.json", "ResponseContentType": "application/json"},
        ExpiresIn=OCR_URL_SECONDS,
    )
    return _response(200, {"url": url, "expiresInSeconds": OCR_URL_SECONDS})


def handler(event: dict, _context=None, books_table=None, s3_client=None) -> dict:
    route = event.get("routeKey", "")
    if route == "GET /books":
        return _list_books(books_table or _books_table(), s3_client or _s3_client())
    if route == "GET /books/{slug}/pdf-url":
        slug = (event.get("pathParameters") or {}).get("slug", "")
        return _pdf_url(slug, books_table or _books_table(), s3_client or _s3_client())
    if route == "GET /books/{slug}/ocr-url":
        slug = (event.get("pathParameters") or {}).get("slug", "")
        return _ocr_url(slug, books_table or _books_table(), s3_client or _s3_client())
    return _response(404, {"error": "Ruta desconocida."})
