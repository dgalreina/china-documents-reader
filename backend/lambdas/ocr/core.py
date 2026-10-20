"""OCR de libros escaneados: la lógica pura (sin AWS ni OpenAI), que se prueba con datos falsos.

Un PDF escaneado es una foto por página, sin texto: ni la traducción, ni el lector en inglés, ni el análisis tienen nada que leer. El OCR convierte esas fotos en
texto y lo guarda en `books/<slug>/ocr.json` (un documento por libro; ver `build_document`). Quien necesita el texto de una página lo pide con `page_text`:
si el PDF tiene capa de texto se usa esa y el OCR no hace falta.

Dos motores, que elige el usuario:
  - "textract": Amazon Textract, ~1,5 USD por 1.000 páginas. El de por defecto. Muy bueno con texto impreso en inglés, español, francés, alemán, italiano o portugués; NO lee chino.
  - "openai":   visión de OpenAI (gpt-4o), ~0,013 USD por página. Lee chino y caracteres raros, pero cuesta ~9 veces más.
"""

from datetime import datetime, timezone
from pathlib import Path

ENGINES = ("textract", "openai")
OCR_FILE = "ocr.json"

# USD por página. Textract: precio de lista de DetectDocumentText (~1,5 USD por 1.000 páginas). OpenAI: estimación para una imagen de página + su transcripción con gpt-4o
# (el coste real se mide y se enseña en el resumen del trabajo). Son ESTIMACIONES para avisar antes de empezar, no una factura.
USD_PER_PAGE = {"textract": 0.0015, "openai": 0.013}

# Una página cuenta como "con texto" si tiene al menos esto; menos suele ser un número de página suelto o ruido de un escaneo.
MIN_TEXT_CHARS = 20
TEXT_LAYER_SAMPLES = 8  # páginas que se miran para decidir si el PDF tiene capa de texto (repartidas por todo el libro)


def ocr_key(slug: str) -> str:
    return f"books/{slug}/{OCR_FILE}"


def estimate_cost(engine: str, pages: int) -> float:
    return round(USD_PER_PAGE[engine] * max(0, pages), 4)


def detect_text_layer(pdf_path: str | Path, samples: int = TEXT_LAYER_SAMPLES) -> bool:
    """¿Tiene el PDF texto de verdad? Se miran unas cuantas páginas repartidas (las primeras suelen ser portada o imágenes aunque el libro sí tenga texto).

    True si alguna de las páginas muestreadas tiene texto. Un PDF escaneado da False; si no se puede leer, se asume que SÍ lo tiene (no se ofrece OCR por error).
    """
    from pypdf import PdfReader

    try:
        reader = PdfReader(str(pdf_path))
        total = len(reader.pages)
        if total == 0:
            return True
        step = max(1, total // samples)
        for index in range(0, total, step):
            if len((reader.pages[index].extract_text() or "").strip()) >= MIN_TEXT_CHARS:
                return True
        return False
    except Exception:  # noqa: BLE001 — un fallo al leer no debe convertirse en "ofrecer OCR": ya lo dirá el resto de la web
        return True


def lines_to_text(lines: list[dict]) -> str:
    """Las líneas de una página (cada una con `text`, `top` y `height` relativos a la página, 0 a 1) en un texto con párrafos.

    Textract da líneas, no párrafos: un hueco vertical claramente mayor que el alto de la línea es un salto de párrafo (línea en blanco). Una palabra cortada con
    guion al final de línea se une con la siguiente (así el traductor no ve "inter- esting").
    """
    out: list[str] = []
    previous: dict | None = None
    for line in lines:
        text = str(line.get("text", "")).strip()
        if not text:
            continue
        if previous is not None:
            gap = float(line.get("top", 0)) - (float(previous.get("top", 0)) + float(previous.get("height", 0)))
            height = float(previous.get("height", 0)) or 0.01
            if gap > height * 0.9:
                out.append("\n\n")
            elif out and out[-1].endswith("-") and out[-1][-2:-1].isalpha() and text[:1].islower():
                out[-1] = out[-1][:-1]  # une la palabra partida: "inter-" + "esting"
            else:
                out.append("\n")
        out.append(text)
        previous = line
    return "".join(out).strip()


def lines_by_page(blocks: list[dict], accumulated: dict[int, list[dict]] | None = None) -> dict[int, list[dict]]:
    """Reparte los bloques LINE de Textract por página (1, 2, 3...). Se puede llamar varias veces (cada respuesta paginada) con el mismo `accumulated`."""
    pages = accumulated if accumulated is not None else {}
    for block in blocks:
        if block.get("BlockType") != "LINE":
            continue
        box = (block.get("Geometry") or {}).get("BoundingBox") or {}
        pages.setdefault(int(block.get("Page") or 1), []).append(
            {"text": block.get("Text", ""), "top": box.get("Top", 0.0), "height": box.get("Height", 0.0)}
        )
    return pages


def build_document(engine: str, texts: list[str], now: datetime | None = None) -> dict:
    """El documento que se guarda en S3: el motor, cuándo se hizo y el texto de cada página (índice 0 = página 1)."""
    stamp = (now or datetime.now(timezone.utc)).isoformat(timespec="seconds")
    return {"engine": engine, "createdAt": stamp, "pageCount": len(texts), "pages": texts}


def page_text(document: dict | None, page: int) -> str:
    """El texto OCR de una página (1 = la primera); vacío si no hay OCR o la página no existe."""
    if not document:
        return ""
    pages = document.get("pages") or []
    return str(pages[page - 1]) if 1 <= page <= len(pages) else ""


def merge_parts(parts: list[dict], page_count: int) -> list[str]:
    """Une los trozos que dejó cada tanda (`{"start": n, "texts": [...]}`) en una lista con un texto por página, en orden. Una página sin trozo queda vacía."""
    texts = [""] * page_count
    for part in sorted(parts, key=lambda item: int(item["start"])):
        for offset, text in enumerate(part.get("texts", [])):
            index = int(part["start"]) + offset
            if 0 <= index < page_count:
                texts[index] = str(text)
    return texts
