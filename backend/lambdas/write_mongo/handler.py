"""Punto de entrada de AWS Lambda: solo traduce el evento a la lógica pura."""

from .store import resolve_target, write_chunks


def handler(event: dict, _context) -> dict:
    """event = {"source", "target", "docs": [...], "write": bool}. Por defecto no escribe."""
    report = write_chunks(
        resolve_target(event["target"]), event["source"], event["docs"], write=bool(event.get("write", False))
    )
    return report.__dict__
