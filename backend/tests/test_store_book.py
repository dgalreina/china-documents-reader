"""store_book (último paso del análisis): marca el libro como analizado en el catálogo, sin tocar nada más. Tabla falsa, sin AWS."""

from decimal import Decimal

import pytest

from lambdas.store_book.handler import handler, stats_from_summary

SUMMARY = {"chunks": 58, "chunksListos": 42, "chunksPendientes": 16, "hitosEscritos": 2, "personajes": {"Rey Wu": 1, "Rey Cheng": 2}, "costeEstimadoUsd": 0.1971}
EVENT = {"source": "mi-libro", "jobId": "job1", "summary": SUMMARY}


class BooksTable:
    def __init__(self, items=(), fail_condition=False):
        self.items = {item["slug"]: dict(item) for item in items}
        self.fail_condition, self.calls = fail_condition, []

    def update_item(self, Key, UpdateExpression, ConditionExpression, ExpressionAttributeValues):
        self.calls.append((Key, UpdateExpression, ConditionExpression))
        if self.fail_condition or Key["slug"] not in self.items:
            error = Exception("condición no cumplida")
            error.response = {"Error": {"Code": "ConditionalCheckFailedException"}}
            raise error
        item = self.items[Key["slug"]]
        values = ExpressionAttributeValues
        item.update(analysis=values[":a"], stats=values[":s"], jobId=values[":j"], analyzedAt=values[":t"], updatedAt=values[":t"])

    def put_item(self, Item):  # si el código llamara a esto, estaría reemplazando el registro entero: los tests lo prohíben
        raise AssertionError("store_book no debe reemplazar el registro del libro")


def book():
    return {"slug": "mi-libro", "title": "Mi libro", "filename": "libro.pdf", "pageCount": 20, "hasCover": True, "analysis": "none", "createdAt": "2026-10-19T00:00:00+00:00"}


def test_marca_el_libro_como_analizado_y_guarda_sus_estadisticas():
    table = BooksTable([book()])
    assert handler(EVENT, books_table=table) == {"catalogued": True}
    item = table.items["mi-libro"]
    assert (item["analysis"], item["jobId"]) == ("done", "job1")
    assert item["stats"]["chunks"] == 58 and item["stats"]["hitos"] == 2 and item["stats"]["personajes"] == 2


def test_conserva_lo_que_ya_tenia_el_registro_titulo_paginas_portada_y_fecha():
    table = BooksTable([book()])
    handler(EVENT, books_table=table)
    item = table.items["mi-libro"]
    assert (item["title"], item["pageCount"], item["hasCover"], item["createdAt"]) == ("Mi libro", 20, True, "2026-10-19T00:00:00+00:00")


def test_no_vuelve_a_crear_un_libro_que_se_borro_mientras_se_analizaba():
    table = BooksTable()  # el libro ya no está
    assert handler(EVENT, books_table=table) == {"catalogued": False}
    assert table.items == {}
    assert table.calls[0][2] == "attribute_exists(slug)"


def test_los_decimales_del_coste_van_como_decimal_porque_dynamodb_no_acepta_float():
    stats = stats_from_summary(SUMMARY)
    assert stats["costeEstimadoUsd"] == Decimal("0.1971") and isinstance(stats["costeEstimadoUsd"], Decimal)
    assert not any(isinstance(value, float) for value in stats.values())


def test_un_resumen_vacio_o_ausente_no_rompe():
    assert stats_from_summary(None)["hitos"] == 0 and stats_from_summary({})["personajes"] == 0


def test_un_error_distinto_de_la_condicion_si_se_propaga_para_que_el_flujo_lo_marque_como_fallo():
    class Roto(BooksTable):
        def update_item(self, **kwargs):
            raise RuntimeError("DynamoDB no responde")

    with pytest.raises(RuntimeError):
        handler(EVENT, books_table=Roto([book()]))


@pytest.mark.parametrize("source", ["../x", "a/b", "A mayusculas", "x", ""])
def test_rechaza_nombres_de_libro_peligrosos(source):
    with pytest.raises(ValueError):
        handler({"source": source, "jobId": "j"}, books_table=BooksTable())


def test_sin_tabla_configurada_no_hace_nada(monkeypatch):
    monkeypatch.delenv("BOOKS_TABLE", raising=False)
    assert handler(EVENT) == {"catalogued": False}
