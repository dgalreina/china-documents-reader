"""Punto de entrada de AWS Lambda detrás de API Gateway (HTTP API) para LEER los hitos y personajes de un libro. Solo lectura. Cuatro rutas (`routeKey`):

  GET /books/{slug}/hitos       ->  {"hitos": [...]}       los hitos del libro por año, con el nombre de sus personajes ya resuelto
  GET /books/{slug}/personajes  ->  {"personajes": [...]}  los personajes que intervienen en esos hitos, con los hitos del libro en que salen
  GET /hitos, GET /personajes   ->  lo mismo de TODOS los libros de esta app; cada hito lleva su libro (`fuente`) y cada personaje los suyos (`fuentes`)

Lee de la base a la que apunta MONGO_TARGET (dev por defecto: lo decide el despliegue, nunca la petición). Filtra SIEMPRE por la marca de origen de esta
app y por el libro: nunca devuelve hitos de china-history. La petición ya llega autenticada (Cognito). No escribe nada.
"""

import json
import os

from ..presign.signer import SOURCE_PATTERN
from ..write_mongo.store import ORIGIN, cached_database

MAX_HITOS = 500  # tope por libro: la respuesta cabe de sobra en API Gateway y la pantalla no pinta más
MAX_HITOS_ALL = 3000  # tope de la lista de todos los libros (~0,5 KB por hito: ~1,5 MB, muy por debajo de los 6 MB de una Lambda)
HEADERS = {"Content-Type": "application/json"}

def _database():
    return cached_database(os.environ.get("MONGO_TARGET", "dev"))


def _response(status: int, body: dict) -> dict:
    return {"statusCode": status, "headers": HEADERS, "body": json.dumps(body, ensure_ascii=False, default=str)}


def _load(db, slug: str | None) -> tuple[list[dict], dict[str, dict]]:
    """Los hitos del libro (por año; de todos los libros de esta app si `slug` es None) y las fichas de los personajes que enlazan."""
    query = {"origen": ORIGIN} if slug is None else {"origen": ORIGIN, "fuente": slug}
    hitos = list(
        db["hitos_conocidos"]
        .find(query, {"_id": 0, "id": 1, "titulo": 1, "descripcion": 1, "año": 1, "lugar": 1, "pagina": 1, "personajes": 1, "fuente": 1})
        .sort("año", 1)
        .limit(MAX_HITOS if slug is not None else MAX_HITOS_ALL)
    )
    ids = sorted({link["id"] for hito in hitos for link in hito.get("personajes") or [] if link.get("id")})
    fichas = {}
    if ids:
        fichas = {
            ficha["id"]: ficha
            for ficha in db["personajes"].find(
                {"origen": ORIGIN, "id": {"$in": ids}},
                {"_id": 0, "id": 1, "nombre": 1, "nombre_zh": 1, "rol": 1, "estado": 1, "descripcion": 1, "nacimiento": 1, "muerte": 1},
            )
        }
    return hitos, fichas


def _hitos_view(hitos: list[dict], fichas: dict[str, dict], with_book: bool = False) -> list[dict]:
    out = []
    for hito in hitos:
        links = []
        for link in hito.get("personajes") or []:
            ficha = fichas.get(link.get("id"))
            # Sin ficha (borrada o de otro origen) se enseña la mención tal como sale en el hito, que siempre existe.
            name = (ficha or {}).get("nombre") or (link.get("menciones") or {}).get("es") or "Personaje"
            links.append({"id": link.get("id"), "nombre": name, "rol": link.get("rol") or (ficha or {}).get("rol") or ""})
        view = {key: hito[key] for key in ("id", "titulo", "descripcion", "año", "lugar") if key in hito}
        if isinstance(hito.get("pagina"), int):
            view["pagina"] = hito["pagina"]
        if with_book and hito.get("fuente"):
            view["fuente"] = hito["fuente"]  # el libro del hito (solo en la lista de todos los libros)
        view["personajes"] = links
        out.append(view)
    return out


def _personajes_view(hitos: list[dict], fichas: dict[str, dict], with_book: bool = False) -> list[dict]:
    hito_ids: dict[str, list[str]] = {}
    books: dict[str, set[str]] = {}
    for hito in hitos:
        for link in hito.get("personajes") or []:
            if link.get("id"):
                hito_ids.setdefault(link["id"], []).append(hito["id"])
                if hito.get("fuente"):
                    books.setdefault(link["id"], set()).add(hito["fuente"])
    out = []
    for person_id, ficha in fichas.items():
        view = (
            {
                "id": person_id,
                "nombre": ficha.get("nombre") or "Personaje",
                "nombre_zh": ficha.get("nombre_zh"),
                "rol": ficha.get("rol") or "",
                "estado": ficha.get("estado"),
                "descripcion": ficha.get("descripcion") or "",
                "nacimiento": ficha.get("nacimiento"),
                "muerte": ficha.get("muerte"),
                "hitoIds": hito_ids.get(person_id, []),
            }
        )
        if with_book:
            view["fuentes"] = sorted(books.get(person_id, ()))  # en qué libros sale (un personaje puede salir en varios)
        out.append(view)
    return sorted(out, key=lambda person: person["nombre"].lower())


def handler(event: dict, _context=None, db=None) -> dict:
    route = event.get("routeKey", "")
    everything = route in ("GET /hitos", "GET /personajes")
    slug = None if everything else (event.get("pathParameters") or {}).get("slug", "")
    if not everything and route not in ("GET /books/{slug}/hitos", "GET /books/{slug}/personajes"):
        return _response(404, {"error": "Ruta desconocida."})
    if not everything and not SOURCE_PATTERN.match(slug):
        return _response(400, {"error": "Identificador de libro no válido."})
    try:
        hitos, fichas = _load(db if db is not None else _database(), slug)
    except Exception as error:  # noqa: BLE001 — Atlas caído o credenciales: al navegador solo un aviso genérico, el detalle queda en los logs
        print(f"read_hitos falló ({slug or 'todos'}): {type(error).__name__}")
        return _response(502, {"error": "No se pudieron leer los datos del libro. Inténtalo de nuevo."})
    if route.endswith("/hitos"):
        return _response(200, {"hitos": _hitos_view(hitos, fichas, with_book=everything)})
    return _response(200, {"personajes": _personajes_view(hitos, fichas, with_book=everything)})
