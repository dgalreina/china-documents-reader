"""Traducción de páginas: caché, página vacía y validaciones. Con un cliente falso: no llama a OpenAI."""

from types import SimpleNamespace

import pytest

from lambdas.translate_page.translator import PROMPT_VERSION, cache_key, translate_page
from shared.storage import LocalStorage
from shared.usage import TrackedClient, UsageTracker


class FakeClient:
    """Imita client.chat.completions.create y cuenta cuántas veces se le llama."""

    def __init__(self, reply="Texto en español."):
        self.calls = []
        self.reply = reply
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self._create))

    def _create(self, **kwargs):
        self.calls.append(kwargs)
        return SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content=f"  {self.reply}  "))],
            usage=SimpleNamespace(prompt_tokens=100, completion_tokens=50),
        )


@pytest.fixture
def storage(tmp_path):
    return LocalStorage(tmp_path)


def test_traduce_y_guarda_en_cache(storage):
    client = FakeClient()
    first = translate_page(client, storage, "b", "libro", 3, "Some English text.")
    assert first.text == "Texto en español."  # sin los espacios sobrantes
    assert (first.cached, first.inputTokens, first.outputTokens) == (False, 100, 50)
    assert storage.try_get_json("b", cache_key("libro", 3))["text"] == "Texto en español."


def test_la_segunda_vez_sale_de_la_cache_sin_llamar_al_modelo(storage):
    client = FakeClient()
    translate_page(client, storage, "b", "libro", 3, "Some English text.")
    again = translate_page(client, storage, "b", "libro", 3, "Some English text.")
    assert len(client.calls) == 1
    assert again.cached and again.seconds == 0.0 and again.text == "Texto en español."


def test_cambiar_de_modelo_no_sirve_la_traduccion_de_otro(storage):
    client = FakeClient()
    translate_page(client, storage, "b", "libro", 3, "Text", model="gpt-4o")
    translate_page(client, storage, "b", "libro", 3, "Text", model="gpt-4o-mini")
    assert [c["model"] for c in client.calls] == ["gpt-4o", "gpt-4o-mini"]


def test_la_version_del_prompt_va_en_la_clave():
    assert f"/{PROMPT_VERSION}/" in cache_key("libro", 3)
    assert cache_key("libro", 3).endswith("/0003.json")


def test_una_pagina_sin_texto_no_se_paga_y_queda_en_cache(storage):
    client = FakeClient()
    result = translate_page(client, storage, "b", "libro", 7, "   \n ")
    assert result.text == "" and client.calls == []
    assert storage.try_get_json("b", cache_key("libro", 7)) is not None


@pytest.mark.parametrize("slug", ["../otro", "a/b", "A mayusculas", "x"])
def test_rechaza_slugs_que_se_saldrian_de_la_clave(storage, slug):
    with pytest.raises(ValueError):
        translate_page(FakeClient(), storage, "b", slug, 1, "Text")


def test_rechaza_paginas_invalidas(storage):
    with pytest.raises(ValueError):
        translate_page(FakeClient(), storage, "b", "libro", 0, "Text")


def test_el_prompt_pide_conservar_los_caracteres_chinos_y_las_notas():
    from lambdas.translate_page.translator import SYSTEM_PROMPT

    assert "NO se traducen" in SYSTEM_PROMPT and "pinyin" in SYSTEM_PROMPT
    assert "NÚMEROS DE NOTA" in SYSTEM_PROMPT


def test_el_contador_de_coste_cuenta_las_llamadas_de_texto_libre():
    tracker = UsageTracker()
    tracked = TrackedClient(FakeClient(), tracker)
    tracked.chat.completions.create(model="gpt-4o", messages=[])
    assert tracker.by_model["gpt-4o"] == {"calls": 1, "input": 100, "output": 50}


def test_try_get_json_devuelve_none_si_no_existe(storage):
    assert storage.try_get_json("b", "no/existe.json") is None
