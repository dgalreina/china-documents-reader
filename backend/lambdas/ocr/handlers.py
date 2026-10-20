"""Los pasos del OCR: la Lambda que lo arranca (API) y las que ejecuta Step Functions.

  POST /books/{slug}/ocr  {"engine": "textract"|"openai"}  ->  start_handler: apunta el trabajo (kind "ocr") y arranca la máquina de estados.

La máquina de estados (infra/lib/china-documents-stack.ts) hace:
  prepare   -> textract: arranca el trabajo asíncrono de Textract sobre el PDF de S3 | openai: calcula las tandas de páginas
  textract:  espera 20 s -> check (¿ha terminado?) -> ... -> collect (lee el resultado entero y lo deja en S3)
  openai:    una tanda por Lambda (hasta 5 a la vez): dibuja cada página, se la manda al modelo y deja el texto en S3
  finish    -> junta los trozos en `books/<slug>/ocr.json`, borra las traducciones viejas del libro (estaban vacías) y lo apunta en el catálogo.

Los trozos intermedios (`jobs/<jobId>/ocr/<inicio>.json`) van al bucket de datos, que caduca. Todo se prueba con servicios falsos en backend/tests/test_ocr.py.
"""

import base64
import io
import json
import os
import tempfile
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path

from shared.jobstate import busy_job
from shared.storage import S3Storage, Storage

from ..presign.signer import SOURCE_PATTERN
from .core import ENGINES, build_document, estimate_cost, lines_by_page, lines_to_text, merge_parts, ocr_key

HEADERS = {"Content-Type": "application/json"}
JOB_TTL_DAYS = 30
PAGES_PER_BATCH = 8  # páginas por tanda con OpenAI: ~10 s por página con 4 en paralelo = ~20-30 s por tanda, de sobra dentro de los 15 min de una Lambda
PARALLEL_PAGES = 4
RENDER_LONG_SIDE = 2000  # px del lado largo de la imagen que se manda al modelo: lo justo para leer texto pequeño sin pagar de más
VISION_MODEL = "gpt-4o"

VISION_PROMPT = (
    "Transcribe TODO el texto de esta página de un libro escaneado, tal cual está escrito y en su idioma original (inglés, chino u otro). "
    "Reglas: respeta los párrafos (una línea en blanco entre ellos); une las palabras partidas con guion al final de línea; deja los caracteres chinos como están; "
    "incluye notas al pie y encabezados; NO traduzcas, NO resumas, NO añadas comentarios ni explicaciones. "
    "Si la página está en blanco o solo tiene una imagen sin texto, responde con una cadena vacía."
)


class OcrFailed(Exception):
    """El OCR no pudo hacerse (Textract lo rechazó, por ejemplo). El mensaje queda como motivo del trabajo."""


def _response(status: int, body: dict) -> dict:
    return {"statusCode": status, "headers": HEADERS, "body": json.dumps(body, ensure_ascii=False)}


def _dynamo(table_env: str):
    import boto3

    return boto3.resource("dynamodb").Table(os.environ[table_env])


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


# ------------------------------------------------------------------------------------------------------------------ arrancar


def start_handler(event: dict, _context=None, books_table=None, jobs_table=None, sfn=None, now: float | None = None, new_id=None) -> dict:
    if event.get("routeKey") != "POST /books/{slug}/ocr":
        return _response(404, {"error": "Ruta desconocida."})
    slug = (event.get("pathParameters") or {}).get("slug", "")
    if not SOURCE_PATTERN.match(slug):
        return _response(400, {"error": "Identificador de libro no válido."})
    try:
        body = json.loads(event.get("body") or "{}")
    except ValueError:
        return _response(400, {"error": "La petición no es válida."})
    engine = body.get("engine") if isinstance(body, dict) else None
    if engine not in ENGINES:
        return _response(400, {"error": "Motor de OCR no válido."})

    books_table = books_table or _dynamo("BOOKS_TABLE")
    jobs_table = jobs_table or _dynamo("JOBS_TABLE")
    item = books_table.get_item(Key={"slug": slug}).get("Item")
    if not item:
        return _response(404, {"error": "No existe ese libro."})
    if item.get("textLayer") is True:
        return _response(400, {"error": "Este libro ya tiene texto: no hace falta OCR."})
    if busy_job(jobs_table, slug, now if now is not None else time.time()):
        return _response(409, {"error": "Este libro ya se está procesando o subiendo. Espera a que termine."})

    job_id = new_id() if new_id else uuid.uuid4().hex
    created = _now()
    pages = int(item.get("pageCount", 0))
    jobs_table.put_item(
        Item={
            "jobId": job_id,
            "kind": "ocr",
            "status": "RUNNING",
            "filename": item.get("filename", ""),
            "source": slug,
            "title": item.get("title", slug),
            "engine": engine,
            "pageCount": pages,
            "createdAt": created,
            "expiresAt": int(time.time()) + JOB_TTL_DAYS * 86400,
        }
    )
    try:
        if sfn is None:
            import boto3

            sfn = boto3.client("stepfunctions")
        sfn.start_execution(
            stateMachineArn=os.environ["OCR_STATE_MACHINE_ARN"],
            name=job_id,
            input=json.dumps(
                {
                    "jobId": job_id,
                    "source": slug,
                    "engine": engine,
                    "pageCount": pages,
                    "bucket": os.environ["UPLOAD_BUCKET"],  # intermedios (caducan)
                    "pdfBucket": os.environ["BOOKS_BUCKET"],  # el PDF y, al final, el OCR
                    "key": f"books/{slug}/original.pdf",
                }
            ),
        )
    except Exception as error:  # noqa: BLE001
        print(f"ocr: no se pudo arrancar el flujo ({slug}): {type(error).__name__}")
        jobs_table.update_item(
            Key={"jobId": job_id},
            UpdateExpression="SET #s = :s, #e = :e, finishedAt = :t",
            ExpressionAttributeNames={"#s": "status", "#e": "error"},
            ExpressionAttributeValues={":s": "FAILED", ":e": "No se pudo arrancar el OCR. Inténtalo de nuevo.", ":t": created},
        )
        return _response(502, {"error": "No se pudo arrancar el OCR. Inténtalo de nuevo."})
    return _response(202, {"jobId": job_id, "engine": engine, "estimatedCostUsd": estimate_cost(engine, pages)})


# ------------------------------------------------------------------------------------------------------- pasos de la máquina


def prepare_handler(event: dict, _context=None, textract=None) -> dict:
    """event = {jobId, engine, pdfBucket, key, pageCount}. Textract: arranca su trabajo (asíncrono) y devuelve su id. OpenAI: las tandas de páginas."""
    engine = event["engine"]
    if engine == "textract":
        if textract is None:
            import boto3

            textract = boto3.client("textract")
        response = textract.start_document_text_detection(
            DocumentLocation={"S3Object": {"Bucket": event["pdfBucket"], "Name": event["key"]}},
            ClientRequestToken=str(event["jobId"])[:64],  # si el paso se repite, Textract no abre otro trabajo (y no se cobra dos veces)
        )
        return {"textractJobId": response["JobId"]}
    return {"starts": list(range(0, int(event["pageCount"]), PAGES_PER_BATCH))}


def check_handler(event: dict, _context=None, textract=None) -> dict:
    """event = {textractJobId}. {"status": "IN_PROGRESS"|"SUCCEEDED"}; si Textract falló, lanza OcrFailed (la máquina marca el trabajo como fallido con su motivo)."""
    if textract is None:
        import boto3

        textract = boto3.client("textract")
    response = textract.get_document_text_detection(JobId=event["textractJobId"], MaxResults=1)
    status = response.get("JobStatus")
    if status == "FAILED":
        raise OcrFailed(f"Textract no pudo leer el PDF: {response.get('StatusMessage') or 'motivo desconocido'}")
    return {"status": "SUCCEEDED" if status in ("SUCCEEDED", "PARTIAL_SUCCESS") else "IN_PROGRESS"}


def collect_handler(event: dict, _context=None, storage: Storage | None = None, textract=None) -> list[dict]:
    """event = {jobId, bucket, pageCount, textractJobId}. Lee TODAS las páginas del resultado y las deja en un trozo de S3. Devuelve la lista de trozos."""
    storage = storage or S3Storage()
    if textract is None:
        import boto3

        textract = boto3.client("textract")
    page_count = int(event["pageCount"])
    lines: dict[int, list[dict]] = {}
    token = None
    while True:
        args = {"JobId": event["textractJobId"], "MaxResults": 1000}
        if token:
            args["NextToken"] = token
        response = textract.get_document_text_detection(**args)
        lines_by_page(response.get("Blocks", []), lines)
        token = response.get("NextToken")
        if not token:
            break
    texts = [lines_to_text(lines.get(number, [])) for number in range(1, page_count + 1)]
    part_key = f"jobs/{event['jobId']}/ocr/0.json"
    storage.put_json(event["bucket"], part_key, {"start": 0, "texts": texts})
    return [{"partKey": part_key, "usage": {"estimatedCostUsd": estimate_cost("textract", page_count)}}]


def render_page_jpeg(document, index: int, long_side: int = RENDER_LONG_SIDE) -> bytes:
    """La página `index` (0 = primera) de un documento de pypdfium2 como JPEG con el lado largo de `long_side` píxeles."""
    page = document[index]
    scale = long_side / max(page.get_width(), page.get_height())
    image = page.render(scale=scale).to_pil().convert("RGB")
    buffer = io.BytesIO()
    image.save(buffer, "JPEG", quality=85, optimize=True)
    return buffer.getvalue()


def transcribe_page(client, jpeg: bytes, model: str = VISION_MODEL) -> str:
    url = "data:image/jpeg;base64," + base64.b64encode(jpeg).decode("ascii")
    completion = client.chat.completions.create(
        model=model,
        temperature=0,
        messages=[{"role": "user", "content": [{"type": "text", "text": VISION_PROMPT}, {"type": "image_url", "image_url": {"url": url, "detail": "high"}}]}],
    )
    return (completion.choices[0].message.content or "").strip()


def pages_handler(event: dict, _context=None, storage: Storage | None = None, client=None, render=render_page_jpeg) -> dict:
    """event = {jobId, bucket, pdfBucket, key, start, size, pageCount}. Una tanda con OpenAI: transcribe las páginas [start, start+size) y deja el texto en S3."""
    storage = storage or S3Storage()
    if client is None:
        from shared.usage import TRACKER

        from ..embed_label.pipeline import make_client

        TRACKER.reset()
        client = make_client()
    start, size, page_count = int(event["start"]), int(event["size"]), int(event["pageCount"])
    end = min(start + size, page_count)

    import pypdfium2 as pdfium  # importación tardía: solo la usa este paso

    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "input.pdf"
        storage.download(event["pdfBucket"], event["key"], path)
        document = pdfium.PdfDocument(str(path))
        try:
            images = [render(document, index) for index in range(start, end)]  # dibujar es rápido; lo lento es esperar al modelo (se hace en paralelo)
        finally:
            document.close()
    with ThreadPoolExecutor(max_workers=PARALLEL_PAGES) as pool:
        texts = list(pool.map(lambda jpeg: transcribe_page(client, jpeg), images))
    part_key = f"jobs/{event['jobId']}/ocr/{start}.json"
    storage.put_json(event["bucket"], part_key, {"start": start, "texts": texts})
    from shared.usage import TRACKER

    return {"partKey": part_key, "usage": {"estimatedCostUsd": TRACKER.summary()["estimatedCostUsd"]}}


def finish_handler(event: dict, _context=None, storage: Storage | None = None, books_table=None) -> dict:
    """event = {jobId, source, engine, bucket, pdfBucket, pageCount, results: [{partKey, usage}]}. Junta los trozos y deja el OCR del libro listo."""
    storage = storage or S3Storage()
    slug, engine, page_count = event["source"], event["engine"], int(event["pageCount"])
    results = event.get("results") or []
    parts = [storage.get_json(event["bucket"], result["partKey"]) for result in results]
    texts = merge_parts(parts, page_count)
    document = build_document(engine, texts)
    storage.put_json(event["pdfBucket"], ocr_key(slug), document)
    # Las páginas sin texto se tradujeron "vacías" antes del OCR y quedaron guardadas: se borran para que ahora se traduzcan de verdad.
    storage.delete_prefix(event["pdfBucket"], f"translations/{slug}/")

    with_text = sum(1 for text in texts if text.strip())
    cost = sum(float((result.get("usage") or {}).get("estimatedCostUsd", 0)) for result in results)
    books_table = books_table or _dynamo("BOOKS_TABLE")
    try:
        books_table.update_item(
            Key={"slug": slug},
            UpdateExpression="SET ocr = :o, updatedAt = :t",
            ConditionExpression="attribute_exists(slug)",  # si el libro se borró mientras tanto, no se vuelve a crear
            ExpressionAttributeValues={":o": {"engine": engine, "pages": page_count, "pagesWithText": with_text, "at": document["createdAt"]}, ":t": document["createdAt"]},
        )
    except Exception as error:  # noqa: BLE001 — el libro ya no existe: se limpia lo que se acaba de escribir
        if getattr(error, "response", {}).get("Error", {}).get("Code") != "ConditionalCheckFailedException":
            raise
        storage.delete_prefix(event["pdfBucket"], ocr_key(slug))
        return {"catalogued": False, "pages": page_count, "pagesWithText": with_text, "engine": engine, "estimatedCostUsd": round(cost, 4)}
    return {"catalogued": True, "pages": page_count, "pagesWithText": with_text, "engine": engine, "estimatedCostUsd": round(cost, 4)}
