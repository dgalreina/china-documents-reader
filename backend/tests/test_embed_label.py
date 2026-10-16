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


class FakeClient:
    """Imita client.embeddings.create y client.chat.completions.parse."""

    def __init__(self, answers: dict[int, tuple[int | None, int | None]]):
        # answers: posición global del chunk (0-based) -> (inicio, fin)
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
                ini, fin = self.answers[int(text)]
                labels.append(ChunkYears(indice=n, ano_inicio=ini, ano_fin=fin))
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
    result = embed_and_label(make_chunks(4), client=client, label_model="fake")

    assert [d["chunkIndex"] for d in result.ready] == [0, 3]
    assert [d["chunkIndex"] for d in result.pending] == [1, 2]
    # Solo se pagan embeddings de los que se guardan.
    assert client.embedded == ["0", "3"]


def test_los_docs_listos_cumplen_el_contrato():
    client = FakeClient({0: (618, 907)})
    doc = embed_and_label(make_chunks(1), client=client, label_model="fake").ready[0]
    assert doc["añoInicio"] == 618 and doc["añoFin"] == 907
    assert doc["embeddingModel"] == "text-embedding-3-small"
    assert len(doc["embedding"]) == 1536
    assert doc["pageStart"] == 1


def test_mas_de_un_lote():
    n = 25  # 3 lotes de 10
    client = FakeClient({i: (100, 200) for i in range(n)})
    result = embed_and_label(make_chunks(n), client=client, label_model="fake")
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
