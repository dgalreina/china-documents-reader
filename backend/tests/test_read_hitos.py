"""GET /books/{slug}/hitos y /personajes con una base de datos falsa en memoria (sin Mongo ni red)."""

import json

import pytest

from lambdas.read_hitos.handler import MAX_HITOS, handler
from lambdas.write_mongo.store import ORIGIN


class FakeCursor:
    def __init__(self, docs):
        self.docs = docs

    def sort(self, key, direction):
        self.docs = sorted(self.docs, key=lambda d: d[key], reverse=direction < 0)
        return self

    def limit(self, n):
        self.docs = self.docs[:n]
        return self

    def __iter__(self):
        return iter(self.docs)


class FakeCollection:
    """Entiende solo lo que usa la Lambda: igualdad y $in. Aplica la proyección como Mongo (solo los campos pedidos)."""

    def __init__(self, docs):
        self.docs, self.queries = docs, []

    def find(self, query, projection=None):
        self.queries.append(query)

        def matches(doc):
            for key, want in query.items():
                if isinstance(want, dict) and "$in" in want:
                    if doc.get(key) not in want["$in"]:
                        return False
                elif doc.get(key) != want:
                    return False
            return True

        keep = [key for key, flag in (projection or {}).items() if flag and key != "_id"]
        return FakeCursor([{k: v for k, v in doc.items() if k in keep} if keep else dict(doc) for doc in self.docs if matches(doc)])


class FakeDB:
    def __init__(self, hitos=(), personajes=()):
        self.collections = {"hitos_conocidos": FakeCollection(list(hitos)), "personajes": FakeCollection(list(personajes))}

    def __getitem__(self, name):
        return self.collections[name]


def link(person_id, rol="rey", mencion="Rey Wu"):
    return {"id": person_id, "rol": rol, "rol_en": "king", "menciones": {"es": mencion, "en": "King Wu"}}


def h(hito_id, year, slug="mi-libro", **extra):
    return {"id": hito_id, "titulo": f"Hito {hito_id}", "descripcion": "Algo pasó.", "año": year, "lugar": "Luoyi, Henan", "fuente": slug, "origen": ORIGIN,
            "location": {"type": "Point", "coordinates": [112.4, 34.6]}, "evidencia": "frase", **extra}


def p(person_id, nombre, **extra):
    return {"id": person_id, "nombre": nombre, "nombre_zh": None, "rol": "rey", "estado": "Zhou", "descripcion": "Un rey.", "nacimiento": None, "muerte": None,
            "origen": ORIGIN, **extra}


def call(route, db, slug="mi-libro"):
    response = handler({"routeKey": route, "pathParameters": {"slug": slug}}, db=db)
    return response["statusCode"], json.loads(response["body"])


def hitos_of(db, slug="mi-libro"):
    return call("GET /books/{slug}/hitos", db, slug)


def test_los_hitos_salen_por_ano_con_su_pagina_y_sin_campos_internos():
    db = FakeDB([h("b" * 10, -770, pagina=15), h("a" * 10, -1046, pagina=3), h("c" * 10, -1039)])
    status, body = hitos_of(db)
    assert status == 200 and [x["año"] for x in body["hitos"]] == [-1046, -1039, -770]
    first = body["hitos"][0]
    assert first["pagina"] == 3 and first["lugar"] == "Luoyi, Henan"
    assert "location" not in first and "evidencia" not in first and "fuente" not in first  # la web solo recibe lo que pinta


def test_un_hito_sin_pagina_sale_sin_el_campo():
    (hito,) = hitos_of(FakeDB([h("a" * 10, -770)]))[1]["hitos"]
    assert "pagina" not in hito


def test_los_personajes_del_hito_llevan_su_nombre_resuelto_desde_la_ficha():
    db = FakeDB([h("a" * 10, -1046, personajes=[link("1111111111")])], [p("1111111111", "Rey Wu de Zhou")])
    (hito,) = hitos_of(db)[1]["hitos"]
    assert hito["personajes"] == [{"id": "1111111111", "nombre": "Rey Wu de Zhou", "rol": "rey"}]


def test_sin_ficha_se_enseña_la_mencion_del_propio_hito():
    (hito,) = hitos_of(FakeDB([h("a" * 10, -1046, personajes=[link("9999999999", mencion="Rey Ping")])]))[1]["hitos"]
    assert hito["personajes"][0]["nombre"] == "Rey Ping"


def test_solo_devuelve_hitos_de_este_libro_y_de_esta_app():
    db = FakeDB([
        h("a" * 10, -1046),
        h("b" * 10, -1046, slug="otro-libro"),
        {**h("c" * 10, -1046), "origen": "china-history"},  # un hito de china-history con el mismo slug: no debe salir nunca
        {k: v for k, v in h("d" * 10, -1046).items() if k != "origen"},  # sin marca de origen
    ])
    status, body = hitos_of(db)
    assert [x["id"] for x in body["hitos"]] == ["a" * 10]
    query = db["hitos_conocidos"].queries[0]
    assert query == {"origen": ORIGIN, "fuente": "mi-libro"}


def test_las_fichas_tambien_se_filtran_por_origen():
    db = FakeDB([h("a" * 10, -1046, personajes=[link("1111111111")])], [p("1111111111", "Ajeno", origen="otro")])
    (hito,) = hitos_of(db)[1]["hitos"]
    assert hito["personajes"][0]["nombre"] == "Rey Wu"  # la ficha ajena se ignora; cae a la mención
    assert db["personajes"].queries[0]["origen"] == ORIGIN


def test_los_personajes_traen_los_hitos_de_este_libro_en_que_salen_y_van_por_orden_alfabetico():
    db = FakeDB(
        [h("a" * 10, -1046, personajes=[link("2222222222"), link("1111111111")]), h("b" * 10, -770, personajes=[link("1111111111")])],
        [p("1111111111", "Rey Wu", nombre_zh="周武王", nacimiento=-1087), p("2222222222", "Dan Fu")],
    )
    status, body = call("GET /books/{slug}/personajes", db)
    assert status == 200 and [x["nombre"] for x in body["personajes"]] == ["Dan Fu", "Rey Wu"]
    wu = body["personajes"][1]
    assert wu["hitoIds"] == ["a" * 10, "b" * 10] and wu["nombre_zh"] == "周武王" and wu["nacimiento"] == -1087
    assert "origen" not in wu


def test_un_libro_sin_hitos_es_una_lista_vacia_no_un_error():
    assert hitos_of(FakeDB()) == (200, {"hitos": []})
    assert call("GET /books/{slug}/personajes", FakeDB()) == (200, {"personajes": []})


def test_tiene_un_tope_de_hitos_por_libro():
    db = FakeDB([h(f"{i:010x}", -1000 + i) for i in range(MAX_HITOS + 20)])
    assert len(hitos_of(db)[1]["hitos"]) == MAX_HITOS


@pytest.mark.parametrize("slug", ["../x", "a/b", "A mayusculas", "x", ""])
def test_slugs_peligrosos_son_400_y_no_consultan_nada(slug):
    db = FakeDB()
    assert hitos_of(db, slug)[0] == 400
    assert db["hitos_conocidos"].queries == []


def test_un_fallo_de_la_base_de_datos_es_502_sin_detalles():
    class Broken:
        def __getitem__(self, name):
            raise RuntimeError("mongodb+srv://usuario:secreta@cluster inaccesible")

    status, body = hitos_of(Broken())
    assert status == 502 and "secreta" not in json.dumps(body) and "mongodb" not in json.dumps(body)


def test_una_ruta_desconocida_es_404():
    assert handler({"routeKey": "GET /otra"}, db=FakeDB())["statusCode"] == 404
