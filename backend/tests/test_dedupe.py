"""Tests de 'guardar solo lo nuevo' con embeddings y juez falsos (sin red, sin coste)."""

from lambdas.write_mongo.dedupe import SameEvent, classify, filter_new, is_close
from lambdas.write_mongo.store import ORIGIN, mongo_neighbors


def h(id_, año=-1046, titulo="Battle of Muye", lng=113.9, lat=35.3, **over):
    return {
        "id": id_, "año": año, "titulo": titulo, "titulo_en": titulo, "descripcion": "d", "descripcion_en": "d",
        "location": {"type": "Point", "coordinates": [lng, lat]}, **over,
    }


class Fakes:
    """Vectores a medida: el primer texto es el hito nuevo, el resto los vecinos, con el parecido que se pida."""

    def __init__(self, similarities, verdict=False):
        self.similarities, self.verdict, self.judged = similarities, verdict, 0

    def embed(self, texts):
        # coseno(nuevo, vecino_i) = similarities[i], con vectores 2D unitarios
        import math

        vectors = [[1.0, 0.0]]
        for s in self.similarities[: len(texts) - 1]:
            vectors.append([s, math.sqrt(max(0.0, 1 - s * s))])
        return vectors

    def judge(self, new, old):
        self.judged += 1
        return SameEvent(mismo_hecho=self.verdict, motivo="x")


def decide(similarities, verdict=False):
    f = Fakes(similarities, verdict)
    neighbors = [h(f"v{i}") for i in range(len(similarities))]
    return classify(h("nuevo"), neighbors, client=None, embed_fn=f.embed, judge_fn=f.judge), f


def test_sin_vecinos_es_nuevo_sin_llamar_a_nadie():
    f = Fakes([])
    d = classify(h("nuevo"), [], client=None, embed_fn=f.embed, judge_fn=f.judge)
    assert d.duplicate_of is None and d.via == "sin_vecinos" and f.judged == 0


def test_parecido_alto_es_duplicado_sin_gastar_el_juez():
    d, f = decide([0.95])
    assert d.duplicate_of["id"] == "v0" and d.via == "parecido_alto" and f.judged == 0


def test_parecido_bajo_es_nuevo_sin_gastar_el_juez():
    d, f = decide([0.40])
    assert d.duplicate_of is None and d.via == "parecido_bajo" and f.judged == 0


def test_zona_gris_decide_el_juez_en_ambos_sentidos():
    d, f = decide([0.70], verdict=True)
    assert d.duplicate_of["id"] == "v0" and d.via == "juez" and f.judged == 1
    d, f = decide([0.70], verdict=False)
    assert d.duplicate_of is None and f.judged == 1


def test_en_la_zona_gris_se_pregunta_por_cada_vecino_plausible_hasta_encontrar_uno():
    # Fallo visto en datos reales: el mismo hecho puntúa 0,62-0,72 frente a otro texto del mismo hecho.
    d, f = decide([0.66, 0.55, 0.30], verdict=True)
    assert d.duplicate_of["id"] == "v0" and f.judged == 1  # basta el primero que lo confirme
    d, f = decide([0.66, 0.55, 0.30], verdict=False)
    assert d.duplicate_of is None and f.judged == 2  # el de 0,30 ni se pregunta


def test_dentro_de_la_misma_tanda_el_segundo_se_compara_con_el_primero():
    f = Fakes([0.97])
    nuevos, dups = filter_new([h("a"), h("b", titulo="Muye battle")], lambda hito: [], client=None, embed_fn=f.embed, judge_fn=f.judge)
    assert [x["id"] for x in nuevos] == ["a"] and dups[0]["id"] == "b" and dups[0]["duplicado_de"] == "a"


def test_hitos_lejanos_o_de_otra_epoca_de_la_misma_tanda_no_se_comparan():
    assert not is_close(h("a", año=-1046), h("b", año=-900))  # 146 años
    assert not is_close(h("a", lng=113.9, lat=35.3), h("b", lng=108.9, lat=34.3))  # ~460 km
    assert is_close(h("a", año=-1046), h("b", año=-1040, lng=114.0, lat=35.31))


class FakeCollection:
    def __init__(self):
        self.query = None

    def find(self, query, projection):
        self.query = query
        return self

    def limit(self, n):
        return []


def test_al_reemplazar_el_libro_sus_hitos_viejos_no_cuentan_como_existentes():
    # Si no, reprocesar un libro marcaría todo como duplicado de la tanda anterior y luego se borraría la anterior.
    col = FakeCollection()
    mongo_neighbors(col, "mi-libro", replace_source=True)(h("x"))
    same = {"id": "x", "origen": ORIGIN, "fuente": "mi-libro"}
    assert col.query["$nor"] == [same, {"origen": ORIGIN, "fuente": "mi-libro"}]
    col2 = FakeCollection()
    mongo_neighbors(col2, "mi-libro", replace_source=False)(h("x"))
    assert col2.query["$nor"] == [same]  # sin reemplazar, sí se compara con la tanda anterior; solo se excluye el mismo hito de este libro


def test_un_hito_con_el_mismo_id_de_otro_libro_cuenta_como_vecino_y_no_se_sobrescribe():
    # Fallo real: la extracción de un libro de prueba generó el mismo hecho con el mismo título (= mismo id) que un hito de otro
    # libro; se excluía como "el mismo hito", se reemplazaba y luego la limpieza lo borraba.
    col = FakeCollection()
    mongo_neighbors(col, "libro-b", replace_source=False)(h("x"))
    (excluded,) = col.query["$nor"]
    assert excluded["fuente"] == "libro-b"  # solo se excluye el mismo hito de ESTE libro: uno de "libro-a" con el mismo id se compara
    other_book_doc = {"id": "x", "origen": ORIGIN, "fuente": "libro-a"}
    assert any(other_book_doc.get(k) != v for k, v in excluded.items())  # no coincide con la exclusión -> sí es vecino


def test_la_escritura_no_reemplaza_un_hito_de_otro_libro_con_el_mismo_id():
    from lambdas.write_mongo.store import build_hito_operations
    from tests.test_write_mongo import hito_doc

    (op,) = build_hito_operations("libro-b", [hito_doc(fuente="libro-b")])
    assert op._filter["fuente"] == "libro-b"  # si el id existe en libro-a, el upsert no casa e intenta insertar: el índice único lo rechaza
