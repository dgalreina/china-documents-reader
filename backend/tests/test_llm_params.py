"""Tests de temperature=0 en todas las llamadas de chat y de que el etiquetado recibe el chunk ENTERO (sin red, sin coste)."""

from types import SimpleNamespace

import pytest

from lambdas.embed_label.labeler import BatchLabels, ChunkYears, DocumentPeriod, MAX_CHUNK_CHARS_SENT, _label_batch, infer_document_period
from lambdas.extract_hitos.extractor import HitosBatch, extract_group
from lambdas.write_mongo import dedupe, personajes
from shared.llm import chat_params


class Recorder:
    """Cliente falso que apunta con qué argumentos se le llama y devuelve una respuesta ya 'interpretada'."""

    def __init__(self, parsed):
        self.calls, self.parsed = [], parsed
        self.chat = SimpleNamespace(completions=SimpleNamespace(parse=self._parse))

    def _parse(self, **kwargs):
        self.calls.append(kwargs)
        return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(parsed=self.parsed))])


@pytest.fixture(autouse=True)
def sin_variable(monkeypatch):
    monkeypatch.delenv("OPENAI_TEMPERATURE", raising=False)


def test_por_defecto_la_temperatura_es_0():
    assert chat_params() == {"temperature": 0.0}


def test_la_temperatura_se_puede_cambiar_y_se_omite_si_esta_vacia(monkeypatch):
    monkeypatch.setenv("OPENAI_TEMPERATURE", "0.3")
    assert chat_params() == {"temperature": 0.3}
    monkeypatch.setenv("OPENAI_TEMPERATURE", "")  # modelos que no admiten el parámetro
    assert chat_params() == {}


def test_las_cinco_llamadas_de_chat_envian_temperature_0():
    period = Recorder(DocumentPeriod(resumen="x", ano_inicio=1, ano_fin=2))
    infer_document_period(period, "m", "texto")

    labels = Recorder(BatchLabels(etiquetas=[ChunkYears(indice=1, ano_inicio=1, ano_fin=2)]))
    _label_batch(labels, "m", None, ["texto"])

    hitos = Recorder(HitosBatch(hitos=[]))
    extract_group(hitos, "m", ["texto"])

    same_event = Recorder(dedupe.SameEvent(mismo_hecho=False, motivo="x"))
    dedupe.judge(same_event, "m", "nuevo", "existente")

    same_person = Recorder(personajes.SamePerson(misma_persona=False, motivo="x"))
    personajes.judge(same_person, "m", {"nombre_en": "A"}, {"nombre_en": "B"})

    for name, recorder in [("periodo", period), ("etiquetas", labels), ("hitos", hitos), ("juez de hitos", same_event), ("juez de personas", same_person)]:
        assert recorder.calls[0]["temperature"] == 0.0, f"{name} no envía temperature=0"


def user_message(recorder) -> str:
    return next(m["content"] for m in recorder.calls[0]["messages"] if m["role"] == "user")


def test_el_etiquetado_recibe_el_chunk_entero_no_solo_los_600_primeros():
    texto = "a" * 600 + "FINAL-DEL-CHUNK"  # el final está más allá de los 600 caracteres que se mandaban antes
    recorder = Recorder(BatchLabels(etiquetas=[ChunkYears(indice=1, ano_inicio=1, ano_fin=2)]))
    _label_batch(recorder, "m", None, [texto])
    assert "FINAL-DEL-CHUNK" in user_message(recorder)


def test_un_chunk_de_1200_caracteres_llega_completo():
    texto = "b" * 1200
    recorder = Recorder(BatchLabels(etiquetas=[ChunkYears(indice=1, ano_inicio=1, ano_fin=2)]))
    _label_batch(recorder, "m", None, [texto])
    assert texto in user_message(recorder)


def test_el_tope_de_seguridad_solo_corta_textos_absurdamente_largos():
    assert MAX_CHUNK_CHARS_SENT >= 3 * 1200  # no se aplica con los chunks de hoy
    recorder = Recorder(BatchLabels(etiquetas=[ChunkYears(indice=1, ano_inicio=1, ano_fin=2)]))
    _label_batch(recorder, "m", None, ["c" * (MAX_CHUNK_CHARS_SENT + 500)])
    assert "c" * MAX_CHUNK_CHARS_SENT in user_message(recorder) and "c" * (MAX_CHUNK_CHARS_SENT + 1) not in user_message(recorder)
