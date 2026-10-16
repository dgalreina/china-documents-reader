"""Paso 1: extraer el texto de un PDF, página a página."""

from dataclasses import dataclass
from pathlib import Path

from pypdf import PdfReader


@dataclass(frozen=True)
class Page:
    number: int  # empieza en 1, como se cita en un libro
    text: str


def extract_pages(pdf_path: str | Path) -> list[Page]:
    """Devuelve una Page por cada página del PDF.

    Un PDF escaneado (imágenes sin capa de texto) devuelve páginas con texto
    vacío. Eso se detecta en la CLI y es el caso que más adelante resolverá
    Textract (OCR) en AWS.
    """
    reader = PdfReader(str(pdf_path))
    return [
        Page(number=i, text=(page.extract_text() or "").strip())
        for i, page in enumerate(reader.pages, start=1)
    ]
