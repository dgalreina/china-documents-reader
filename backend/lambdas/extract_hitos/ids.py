"""Id estable de un hito: mismo algoritmo que scripts/extract-hitos.mjs de china-history-api.

    id = sha1([titulo, año, lugar, lat, lng].join('|')).hex()[:10]
    si ya existe en el lote: se rehashea base + '|' + salt (salt = 1, 2, ...)

El detalle traicionero es el formato de los números: JavaScript escribe 34 y Python 34.0, y eso cambia el hash.
"""

import hashlib


def js_number(value: int | float) -> str:
    """Un número tal como lo escribiría JavaScript al hacer [..].join('|')."""
    if isinstance(value, float) and value.is_integer() and abs(value) < 1e21:
        return str(int(value))
    text = repr(value)
    # JS escribe 1e-7 y Python 1e-07; no debería pasar con coordenadas, pero se iguala por si acaso.
    if "e-0" in text or "e+0" in text:
        text = text.replace("e-0", "e-").replace("e+0", "e+")
    return text


def hito_id(titulo: str, ano: int, lugar: str, lat: float, lng: float, seen: set[str]) -> str:
    base = "|".join([titulo, js_number(ano), lugar, js_number(lat), js_number(lng)])
    salt = 0
    digest = hashlib.sha1(base.encode("utf-8")).hexdigest()[:10]
    while digest in seen:
        salt += 1
        digest = hashlib.sha1(f"{base}|{salt}".encode("utf-8")).hexdigest()[:10]
    seen.add(digest)
    return digest
