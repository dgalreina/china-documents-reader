"""Tests de embed_label con un cliente de OpenAI falso (sin red, sin coste)."""

from types import SimpleNamespace

from lambdas.embed_label.embedder import EMBEDDING_DIMENSIONS, EMBEDDING_MODEL
from lambdas.embed_label.labeler import (
    BatchLabels,
    ChunkYears,
    DocumentPeriod,
    valid_range,
)
from lambdas.embed_label.pipeline import embed_and_label
from lambdas.extract_chunk.chunker import Chunk
from shared.geocoder import Geocoder


class FakeClient:
    """Imita client.embeddings.create y client.chat.completions.parse."""

    def __init__(self, answers: dict[int, tuple]):
        # answers: posición global del chunk (0-based) -> (inicio, fin) o (inicio, fin, sitio, provincia)
        self.answers = answers
        self.embedded: list[str] = []
        self.embeddings = SimpleNamespace(create=self._embed)
        self.chat = SimpleNamespace(completions=SimpleNamespace(parse=self._parse))

    def _embed(self, model, input):
        assert model == EMBEDDING_MODEL
        self.embedded.extend(input)
        data = [SimpleNamespace(index=i, embedding=[0.0] * EMBEDDING_DIMENSIONS) for i in range(len(input))]
        return SimpleNamespace(data=data)

    def _parse(self, model, messages, response_format):
        if response_format is DocumentPeriod:
            parsed = DocumentPeriod(resumen="China Tang", ano_inicio=618, ano_fin=907)
        else:
            user = messages[1]["content"]
            labels = []
            for n, text in enumerate(self._fragments(user), start=1):
                ini, fin, *geo = self.answers[int(text)]
                site, province = (geo + [None, None])[:2]
                labels.append(ChunkYears(indice=n, ano_inicio=ini, ano_fin=fin, sitio_principal=site, provincia=province))
            parsed = BatchLabels(etiquetas=labels)
        return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(parsed=parsed))])

    @staticmethod
    def _fragments(user: str):
        # El texto de cada chunk de prueba es su propio número global.
        parts = user.split("--- Fragmento ")[1:]
        return [p.split("---\n", 1)[1].strip() for p in parts]


def make_chunks(n: int) -> list[Chunk]:
    return [Chunk(index=i, text=str(i), page_start=i + 1, page_end=i + 1) for i in range(n)]


def test_valid_range_respeta_el_contrato():
    assert valid_range(-221, -206) is not None
    assert valid_range(618, 907) is not None
    assert valid_range(None, 100) is None
    assert valid_range(900, 100) is None  # inicio > fin
    assert valid_range(-20000, -5000) is None  # fuera de -10000..3000
    assert valid_range(100, 5000) is None


def test_chunks_sin_ano_valido_van_a_pendientes_y_no_se_embeben():
    answers = {0: (618, 700), 1: (None, None), 2: (900, 100), 3: (800, 907)}
    client = FakeClient(answers)
    result = embed_and_label(make_chunks(4), client=client, label_model="fake", geocoder=Geocoder())

    assert [d["chunkIndex"] for d in result.ready] == [0, 3]
    assert [d["chunkIndex"] for d in result.pending] == [1, 2]
    # Solo se pagan embeddings de los que se guardan.
    assert client.embedded == ["0", "3"]


def test_los_docs_listos_cumplen_el_contrato():
    client = FakeClient({0: (618, 907)})
    doc = embed_and_label(make_chunks(1), client=client, label_model="fake", geocoder=Geocoder()).ready[0]
    assert doc["añoInicio"] == 618 and doc["añoFin"] == 907
    assert doc["embeddingModel"] == "text-embedding-3-small"
    assert len(doc["embedding"]) == 1536
    assert doc["pageStart"] == 1


def test_mas_de_un_lote():
    n = 25  # 3 lotes de 10
    client = FakeClient({i: (100, 200) for i in range(n)})
    result = embed_and_label(make_chunks(n), client=client, label_model="fake", geocoder=Geocoder())
    assert len(result.ready) == n and not result.pending


def test_rango_igual_al_periodo_del_documento_es_pendiente():
    # El FakeClient devuelve periodo 618..907 (span 289 < 500): no se aplica el filtro.
    from lambdas.embed_label.labeler import YearRange, is_uninformative

    largo = DocumentPeriod(resumen="x", ano_inicio=-5000, ano_fin=-250)
    assert is_uninformative(YearRange(-5000, -250), largo)
    assert is_uninformative(YearRange(-4900, -300), largo)  # casi todo
    assert not is_uninformative(YearRange(-4000, -3500), largo)
    corto = DocumentPeriod(resumen="x", ano_inicio=618, ano_fin=907)
    assert not is_uninformative(YearRange(618, 907), corto)
    assert not is_uninformative(YearRange(618, 907), None)


def test_ubicacion_es_opcional_y_no_bloquea_el_chunk():
    # Chunk con sitio que nadie sabe resolver: se guarda igual, sin ubicación.
    client = FakeClient({0: (-4000, -3500, "Sitio Inventado", "Henan"), 1: (100, 200)})
    result = embed_and_label(make_chunks(2), client=client, label_model="fake", geocoder=Geocoder())
    assert len(result.ready) == 2
    assert all("ubicacion" not in d and "lat" not in d for d in result.ready)
    assert result.geo_unresolved[("Sitio Inventado", "Henan")] == 1


def test_ubicacion_resuelta_se_guarda_en_geojson_lng_lat(tmp_path):
    import json

    (tmp_path / "yacimientos").mkdir()
    (tmp_path / "yacimientos" / "yacimientos.json").write_text(
        json.dumps([{"nombre": "Erlitou", "nombre_en": "Erlitou", "lugar_en": "Yanshi, Henan", "geometry": {"type": "Point", "coordinates": [112.7, 34.7]}}]),
        encoding="utf-8",
    )
    client = FakeClient({0: (-1900, -1500, "Erlitou site", "Henan")})
    result = embed_and_label(make_chunks(1), client=client, label_model="fake", geocoder=Geocoder(curated_dir=tmp_path))
    doc = result.ready[0]
    assert doc["ubicacion"] == {"type": "Point", "coordinates": [112.7, 34.7]}  # [lng, lat]
    assert (doc["lat"], doc["lng"]) == (34.7, 112.7)
    assert doc["ubicacionFuente"] == "curated" and doc["ubicacionNombre"] == "Erlitou site"


def test_un_rango_de_mas_de_2000_anios_va_a_pendientes_con_su_motivo():
    # Los casos reales de las introducciones de capítulo del Oxford Handbook: -6000..-2000 (4000 años) y -6550..-1500 (5050).
    client = FakeClient({0: (-6000, -2000), 1: (-6550, -1500), 2: (-3000, -1600), 3: (-221, -206)})
    result = embed_and_label(make_chunks(4), client=client, label_model="fake", geocoder=Geocoder())
    assert [d["chunkIndex"] for d in result.ready] == [2, 3]
    assert {d["chunkIndex"]: d["reason"] for d in result.pending} == {0: "rango_demasiado_ancho", 1: "rango_demasiado_ancho"}
    assert client.embedded == ["2", "3"]  # y no se pagan sus embeddings


def test_el_tope_es_exacto_y_configurable(monkeypatch):
    from lambdas.embed_label.labeler import YearRange, is_too_wide, max_span_years

    monkeypatch.delenv("MAX_SPAN_YEARS", raising=False)
    assert max_span_years() == 2000
    assert not is_too_wide(YearRange(-2000, 0))  # justo 2000: se acepta
    assert is_too_wide(YearRange(-2001, 0))
    monkeypatch.setenv("MAX_SPAN_YEARS", "500")
    assert is_too_wide(YearRange(100, 700)) and not is_too_wide(YearRange(100, 600))


def test_un_rango_igual_a_todo_el_libro_y_ademas_ancho_se_explica_por_lo_primero():
    # El mismo chunk cumple las dos reglas (cubre el libro entero y supera 2000 años): el motivo es el del periodo del libro.
    libro_largo = DocumentPeriod(resumen="x", ano_inicio=-5000, ano_fin=-250)
    client = FakeClient({0: (-5000, -250), 1: (-6000, -2000)})
    result = embed_and_label(
        make_chunks(2), client=client, label_model="fake", geocoder=Geocoder(), document_period=libro_largo, infer_period=False
    )
    assert {d["chunkIndex"]: d["reason"] for d in result.pending} == {
        0: "rango_igual_al_periodo_del_documento",  # cubre el 100 % del libro
        1: "rango_demasiado_ancho",  # 4000 años pero solo el 63 % del libro: no lo atrapa el filtro anterior
    }
