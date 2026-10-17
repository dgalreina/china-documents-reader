"""Tests del contrato de escritura. No abren ninguna conexión a Mongo."""

import pytest

from lambdas.write_mongo.store import ContractError, build_operations, resolve_target


def doc(i=0, **over):
    base = {
        "chunkIndex": i,
        "text": "texto",
        "embedding": [0.0] * 1536,
        "embeddingModel": "text-embedding-3-small",
        "añoInicio": 618,
        "añoFin": 907,
        "pageStart": 1,
        "pageEnd": 1,
    }
    return {**base, **over}


def test_documento_valido_genera_upsert_por_source_y_chunkindex():
    ops = build_operations("mi-libro", [doc(0), doc(1)])
    assert len(ops) == 2
    assert ops[0]._filter == {"source": "mi-libro", "chunkIndex": 0}
    assert ops[0]._upsert is True


@pytest.mark.parametrize(
    "over",
    [
        {"añoInicio": None},
        {"añoFin": None},
        {"añoInicio": 900, "añoFin": 100},
        {"añoInicio": -20000},
        {"añoFin": 5000},
        {"añoInicio": 1.5},
        {"embeddingModel": "otro-modelo"},
        {"embedding": [0.0] * 3},
        {"text": "  "},
    ],
)
def test_documento_invalido_se_rechaza(over):
    with pytest.raises(ContractError):
        build_operations("mi-libro", [doc(0, **over)])


def test_todo_o_nada_un_malo_aborta_el_lote():
    with pytest.raises(ContractError):
        build_operations("mi-libro", [doc(0), doc(1, añoInicio=None), doc(2)])


def test_source_vacio_y_chunkindex_repetido():
    with pytest.raises(ContractError):
        build_operations(" ", [doc(0)])
    with pytest.raises(ContractError):
        build_operations("x", [doc(0), doc(0)])


def test_el_destino_es_obligatorio_y_sin_uri_falla(monkeypatch):
    monkeypatch.delenv("MONGODB_URI_PROD", raising=False)
    with pytest.raises(ContractError):
        resolve_target("prod")
    with pytest.raises(ContractError):
        resolve_target("cualquiera")


def test_bases_por_defecto(monkeypatch):
    monkeypatch.setenv("MONGODB_URI_DEV", "mongodb://x")
    monkeypatch.setenv("MONGODB_URI_PROD", "mongodb://y")
    monkeypatch.delenv("MONGODB_DB_DEV", raising=False)
    monkeypatch.delenv("MONGODB_DB_PROD", raising=False)
    assert resolve_target("dev").db_name == "china_documents_dev"
    assert resolve_target("prod").db_name == "china-history"


def test_dev_no_puede_apuntar_a_la_base_de_produccion(monkeypatch):
    monkeypatch.setenv("MONGODB_URI_DEV", "mongodb://x")
    monkeypatch.setenv("MONGODB_DB_DEV", "china-history")
    monkeypatch.delenv("MONGODB_DB_PROD", raising=False)
    with pytest.raises(ContractError):
        resolve_target("dev")


def loc(**over):
    base = {
        "ubicacion": {"type": "Point", "coordinates": [112.7, 34.7]},
        "lat": 34.7, "lng": 112.7, "ubicacionNombre": "Erlitou", "ubicacionFuente": "curated",
    }
    return {**base, **over}


def test_ubicacion_completa_y_coherente_es_valida():
    build_operations("x", [doc(0, **loc())])  # no lanza


def test_documento_sin_ubicacion_sigue_siendo_valido():
    build_operations("x", [doc(0)])


@pytest.mark.parametrize(
    "over",
    [
        {"ubicacionFuente": None},  # incompleta
        {"lat": 48.8, "lng": 2.3, "ubicacion": {"type": "Point", "coordinates": [2.3, 48.8]}},  # fuera de China
        {"ubicacion": {"type": "Point", "coordinates": [34.7, 112.7]}},  # [lat, lng] al revés
    ],
)
def test_ubicacion_incorrecta_se_rechaza(over):
    with pytest.raises(ContractError):
        build_operations("x", [doc(0, **loc(**over))])


def test_reescribir_un_chunk_reemplaza_el_documento_entero():
    # Fallo real: con $set, una ubicación antigua sobrevivía cuando la nueva tanda ya no la traía.
    from pymongo import ReplaceOne

    (op,) = build_operations("x", [doc(0)])
    assert isinstance(op, ReplaceOne)
    assert "ubicacion" not in op._doc and op._upsert is True


# ---- hitos_conocidos ---------------------------------------------------------------------------------


def hito_doc(**over):
    base = {
        "id": "6e9d225ce7", "lugar": "Luoyang, Henan", "año": -1042, "titulo": "Fundación de Chengzhou",
        "descripcion": "Se funda la ciudad.", "fuente": "mi-libro", "origen": "china-documents-reader",
        "location": {"type": "Point", "coordinates": [112.45, 34.62]},
        "lugar_en": "Luoyang, Henan", "titulo_zh": "成周的建立",
    }
    return {**base, **over}


def test_hito_valido_genera_reemplazo_por_id_y_origen():
    from lambdas.write_mongo.store import build_hito_operations

    (op,) = build_hito_operations("mi-libro", [hito_doc()])
    assert op._filter == {"id": "6e9d225ce7", "origen": "china-documents-reader", "fuente": "mi-libro"} and op._upsert is True


@pytest.mark.parametrize(
    "over",
    [
        {"location": None},  # sin coordenadas: el schema de china-history lo exige
        {"location": {"type": "Point", "coordinates": [34.62, 112.45]}},  # [lat, lng] al revés
        {"location": {"type": "Point", "coordinates": [2.3, 48.8]}},  # fuera de China
        {"id": "no-es-hex"},
        {"año": 5000},
        {"origen": "china-history"},  # nunca se escribe como si fuera de otro
        {"titulo_zh": "Founding of Chengzhou"},  # el chino no contiene caracteres chinos
        {"fuente": "otro-libro"},
    ],
)
def test_hito_invalido_se_rechaza(over):
    from lambdas.write_mongo.store import build_hito_operations

    with pytest.raises(ContractError):
        build_hito_operations("mi-libro", [hito_doc(**over)])


def test_hito_con_id_repetido_se_rechaza():
    from lambdas.write_mongo.store import build_hito_operations

    with pytest.raises(ContractError):
        build_hito_operations("mi-libro", [hito_doc(), hito_doc()])


# ---- personajes ---------------------------------------------------------------------------------------


def ficha_doc(**over):
    base = {
        "id": "king-wu-of-zhou-4f2a1c", "nombre": "Rey Wu de Zhou", "nombre_en": "King Wu of Zhou", "nombre_zh": "周武王",
        "aliases": {"es": ["Fa"], "en": ["Fa"], "zh": ["周武王"]}, "aliasNorm": ["kingwu", "周武王"],
        "rol": "líder", "rol_en": "leader", "descripcion": "Fundador.", "descripcion_en": "Founder.",
        "fuente": "mi-libro", "origen": "china-documents-reader", "actividadInicio": -1046, "actividadFin": -1043,
    }
    return {**base, **over}


def test_ficha_valida_genera_reemplazo_por_id_y_origen():
    from lambdas.write_mongo.store import build_personaje_operations

    (op,) = build_personaje_operations([ficha_doc()])
    assert op._filter == {"id": "king-wu-of-zhou-4f2a1c", "origen": "china-documents-reader"} and op._upsert is True


@pytest.mark.parametrize(
    "over",
    [
        {"id": "sin-hash"},
        {"origen": "china-history"},
        {"aliases": {"es": [], "en": []}},  # falta zh
        {"aliasNorm": []},
        {"nombre_zh": "King Wu"},  # sin caracteres chinos
        {"muerte": 9000},
        {"descripcion": ""},
    ],
)
def test_ficha_invalida_se_rechaza(over):
    from lambdas.write_mongo.store import build_personaje_operations

    with pytest.raises(ContractError):
        build_personaje_operations([ficha_doc(**over)])


def test_enlace_a_personaje_invalido_en_un_hito_se_rechaza():
    from lambdas.write_mongo.store import build_hito_operations

    bueno = {"id": "king-wu-of-zhou-4f2a1c", "rol": "líder", "menciones": {"en": "King Wu"}}
    build_hito_operations("mi-libro", [hito_doc(personajes=[bueno])])  # no lanza
    for malo in ({**bueno, "id": "mal"}, {**bueno, "menciones": {}}, {**bueno, "rol": ""}):
        with pytest.raises(ContractError):
            build_hito_operations("mi-libro", [hito_doc(personajes=[malo])])
