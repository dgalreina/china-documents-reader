"""Punto de entrada de AWS Lambda: solo traduce el evento a la lógica pura."""

from .pipeline import extract_hitos


def handler(event: dict, _context) -> dict:
    """event = {"source": slug, "chunks": [chunks `ready` de embed_label, con texto y años]}"""
    result = extract_hitos(event["chunks"], event["source"])
    return {"hitos": result.hitos, "descartados": result.discarded, "stats": dict(result.stats)}
