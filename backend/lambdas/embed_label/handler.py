"""Punto de entrada de AWS Lambda: solo traduce el evento a la lógica pura."""

from ..extract_chunk.chunker import Chunk
from .pipeline import embed_and_label


def handler(event: dict, _context) -> dict:
    """event = {"chunks": [{"index", "text", "page_start", "page_end"}, ...]}"""
    chunks = [Chunk(**c) for c in event["chunks"]]
    result = embed_and_label(chunks)
    return {"ready": result.ready, "pending": result.pending}
