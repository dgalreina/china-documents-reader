"""Paso 2: trocear las páginas en chunks que recuerdan de qué páginas vienen.

Un chunk es el fragmento de texto que se convertirá en un vector. Si es muy
corto pierde contexto; si es muy largo mezcla temas y la búsqueda pierde
precisión. Aquí se agrupan párrafos hasta ~max_chars, con un solapamiento
entre chunks consecutivos para que una idea cortada en el límite no se pierda.
"""

import re
from dataclasses import dataclass

from .pdf_reader import Page


@dataclass(frozen=True)
class Chunk:
    index: int
    text: str
    page_start: int
    page_end: int
    # Página de CADA párrafo del chunk (los párrafos van separados por una línea en blanco en `text`). Un chunk cruza el límite de página con
    # frecuencia, y con solo page_start/page_end no se sabe en cuál de las dos está una frase; con esto sí (lo usa extract_hitos para dar la
    # página exacta de cada hito). Vacío en chunks antiguos: entonces se usa page_start.
    paragraph_pages: tuple[int, ...] = ()


def _split_long(text: str, max_chars: int):
    """Parte un párrafo más largo que max_chars, preferiblemente tras un punto."""
    while len(text) > max_chars:
        cut = text.rfind(". ", 0, max_chars)
        cut = cut + 1 if cut > max_chars // 2 else max_chars
        yield text[:cut].strip()
        text = text[cut:].strip()
    if text:
        yield text


def _units(pages: list[Page], max_chars: int):
    """Genera (párrafo, nº de página) en orden de lectura."""
    for page in pages:
        for paragraph in re.split(r"\n\s*\n", page.text):
            paragraph = " ".join(paragraph.split())
            if not paragraph:
                continue
            for piece in _split_long(paragraph, max_chars):
                yield piece, page.number


def _tail(units: list[tuple[str, int]], overlap_chars: int):
    """Últimos párrafos del chunk anterior que caben en el solapamiento."""
    tail: list[tuple[str, int]] = []
    total = 0
    for text, page in reversed(units):
        if total + len(text) > overlap_chars:
            break
        tail.insert(0, (text, page))
        total += len(text) + 1
    return tail


def _build(index: int, units: list[tuple[str, int]]) -> Chunk:
    return Chunk(
        index=index,
        text="\n\n".join(text for text, _ in units),
        page_start=units[0][1],
        page_end=units[-1][1],
        paragraph_pages=tuple(page for _, page in units),
    )


def chunk_pages(
    pages: list[Page], max_chars: int = 1200, overlap_chars: int = 200
) -> list[Chunk]:
    chunks: list[Chunk] = []
    current: list[tuple[str, int]] = []
    size = 0

    for text, page in _units(pages, max_chars):
        if current and size + len(text) + 1 > max_chars:
            chunks.append(_build(len(chunks), current))
            current = _tail(current, overlap_chars)
            size = sum(len(t) + 1 for t, _ in current)
        current.append((text, page))
        size += len(text) + 1

    if current:
        chunks.append(_build(len(chunks), current))
    return chunks
