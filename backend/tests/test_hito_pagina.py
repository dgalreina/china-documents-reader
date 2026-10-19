"""La página de cada hito: el chunk recuerda la página de cada párrafo, la cita se localiza en uno de ellos y la página se guarda con el hito."""

import json
from dataclasses import asdict

import pytest

from lambdas.extract_chunk.chunker import Chunk, chunk_pages
from lambdas.extract_chunk.pdf_reader import Page
from lambdas.extract_hitos.extractor import locate_page
from lambdas.extract_hitos.pipeline import extract_hitos
from lambdas.write_mongo.store import ORIGIN, ContractError, build_hito_operations, validate_hito
from shared.geocoder import GeoResult

from .test_extract_hitos import FakeClient, FakeGeocoder, hito

LUOYANG = GeoResult(34.62, 112.45, "wikidata", "Luoyang (Q1)")


# ---- el chunk recuerda la página de cada párrafo -------------------------------------------------------------------------------------------


def test_cada_parrafo_del_chunk_lleva_su_pagina():
    pages = [Page(1, "Primero.\n\nSegundo."), Page(2, "Tercero.")]
    (chunk,) = chunk_pages(pages, max_chars=1000, overlap_chars=0)
    assert chunk.paragraph_pages == (1, 1, 2)
    assert (chunk.page_start, chunk.page_end) == (1, 2)
    assert len(chunk.text.split("\n\n")) == len(chunk.paragraph_pages)  # alineados: el párrafo i está en la página i


def test_el_chunk_sobrevive_a_pasar_por_json_y_los_chunks_antiguos_siguen_valiendo():
    (chunk,) = chunk_pages([Page(3, "Uno.\n\nDos.")], max_chars=1000, overlap_chars=0)
    again = Chunk(**json.loads(json.dumps(asdict(chunk))))  # como lo guarda y lo lee extract_chunk -> embed_label
    assert list(again.paragraph_pages) == [3, 3]
    old = {"index": 0, "text": "x", "page_start": 1, "page_end": 1}  # un chunk guardado antes de existir el campo
    assert Chunk(**old).paragraph_pages == ()


# ---- localizar la página de una cita -------------------------------------------------------------------------------------------------------

STRADDLING = {
    "chunkIndex": 0, "pageStart": 4, "pageEnd": 5,
    "text": "The king moved the capital to Luoyang after the invasion.\n\nLater the court returned to Haojing on the river.",
    "paragraphPages": [4, 5],
}


def test_la_cita_se_localiza_en_el_parrafo_que_la_contiene_aunque_el_chunk_cruce_de_pagina():
    assert locate_page("The king moved the capital to Luoyang after the invasion.", [STRADDLING]) == 4
    assert locate_page("Later the court returned to Haojing on the river.", [STRADDLING]) == 5


def test_sin_paginas_por_parrafo_se_usa_la_de_inicio_del_chunk():
    old = {k: v for k, v in STRADDLING.items() if k != "paragraphPages"}
    assert locate_page("Later the court returned to Haojing on the river.", [old]) == 4


def test_en_un_empate_gana_el_primero_y_con_chunks_solapados_no_cambia():
    overlap = {"chunkIndex": 1, "pageStart": 5, "pageEnd": 5, "text": "Later the court returned to Haojing on the river.", "paragraphPages": [5]}
    assert locate_page("Later the court returned to Haojing on the river.", [STRADDLING, overlap]) == 5


def test_una_cita_que_no_se_parece_a_nada_no_da_pagina():
    assert locate_page("Armies crossed the Yellow River at dawn and burned the granaries", [STRADDLING]) is None
    assert locate_page("", [STRADDLING]) is None


def test_tolera_los_guiones_de_maquetacion_de_la_cita():
    assert locate_page("The king moved the cap- ital to Luoyang after the invasion.", [STRADDLING]) == 4


# ---- el hito sale con su página y su cita --------------------------------------------------------------------------------------------------

GROUP = [
    {"chunkIndex": 0, "añoInicio": -1100, "añoFin": -900, "pageStart": 7, "pageEnd": 8,
     "text": "Introduction to the period.\n\nThe court moved to Luoyang after the invasion.", "paragraphPages": [7, 8]},
]


def run(hitos, chunks=GROUP):
    return extract_hitos(chunks, "mi-libro", client=FakeClient(hitos), model="fake", geocoder=FakeGeocoder({"Luoyang": LUOYANG}))


def test_el_hito_guarda_su_pagina_y_la_frase_que_lo_respalda():
    (h,) = run([hito()]).hitos
    assert h["pagina"] == 8
    assert h["evidencia"] == "The court moved to Luoyang after the invasion."
    assert "_evidencia" not in h


def test_si_no_se_encuentra_la_pagina_el_hito_se_guarda_igual_y_queda_contado():
    chunks = [{**GROUP[0], "text": "Nothing about it here at all.\n\nThe court moved to Luoyang after the invasion", "paragraphPages": [7, 8]}]
    result = run([hito(evidencia="The court moved to Luoyang after the invasion.")], chunks)
    assert result.hitos[0]["pagina"] == 8
    no_pages = [{"chunkIndex": 0, "añoInicio": -1100, "añoFin": -900, "text": "The court moved to Luoyang after the invasion"}]  # sin ninguna página
    result = run([hito()], no_pages)
    assert len(result.hitos) == 1 and "pagina" not in result.hitos[0] and result.stats["sin_pagina"] == 1


def test_una_cita_muy_larga_se_recorta_al_guardar():
    long_quote = "The court moved to Luoyang after the invasion. " + "and the army marched west " * 60
    chunks = [{**GROUP[0], "text": long_quote, "paragraphPages": [7]}]
    (h,) = run([hito(evidencia=long_quote)], chunks).hitos
    assert len(h["evidencia"]) <= 500


# ---- el contrato: la página y la cita llegan a Mongo, y se validan --------------------------------------------------------------------------


def valid_hito(**extra):
    return {
        "id": "0123456789", "lugar": "Luoyang, Henan", "año": -1042, "titulo": "Fundación", "descripcion": "Se funda.", "fuente": "mi-libro",
        "origen": ORIGIN, "location": {"type": "Point", "coordinates": [112.45, 34.62]}, **extra,
    }


@pytest.mark.parametrize("pagina", [0, -3, "4", 4.5, True])
def test_una_pagina_invalida_no_se_escribe(pagina):
    with pytest.raises(ContractError, match="pagina"):
        validate_hito(valid_hito(pagina=pagina))


def test_pagina_y_evidencia_son_opcionales_pero_si_estan_se_validan():
    validate_hito(valid_hito())
    validate_hito(valid_hito(pagina=12, evidencia="Una frase."))
    with pytest.raises(ContractError, match="evidencia"):
        validate_hito(valid_hito(evidencia=5))


def test_la_pagina_y_la_cita_llegan_al_documento_que_se_escribe_pero_los_campos_de_trabajo_no():
    doc = valid_hito(pagina=12, evidencia="Una frase.", _personajes=[{"x": 1}])
    (operation,) = build_hito_operations("mi-libro", [doc])
    written = operation._doc
    assert written["pagina"] == 12 and written["evidencia"] == "Una frase."
    assert "_personajes" not in written
