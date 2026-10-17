"""Tests del contador de uso de OpenAI (sin red, sin coste)."""

from types import SimpleNamespace

import pytest

from shared.usage import TrackedClient, UsageTracker, price_for


def test_coste_de_gpt_4o_y_embeddings():
    t = UsageTracker()
    t.record("gpt-4o", 1_000_000, 100_000)  # 2.50 + 1.00
    t.record("text-embedding-3-small", 1_000_000)  # 0.02
    cost, unknown = t.cost()
    assert cost == pytest.approx(3.52) and unknown == []


def test_una_version_con_fecha_usa_el_precio_del_prefijo_mas_largo():
    # gpt-4o-mini-2024-07-18 empieza por "gpt-4o": no debe cobrarse como gpt-4o.
    assert price_for("gpt-4o-mini-2024-07-18") == price_for("gpt-4o-mini")
    assert price_for("gpt-4o-2024-08-06") == price_for("gpt-4o")
    assert price_for("modelo-que-no-existe") is None


def test_un_modelo_sin_precio_no_cuenta_en_el_coste_pero_se_avisa():
    t = UsageTracker()
    t.record("modelo-raro", 5000, 1000)
    t.record("gpt-4o", 1_000_000)
    cost, unknown = t.cost()
    assert cost == pytest.approx(2.50) and unknown == ["modelo-raro"]
    assert "modelo-raro" in t.report() and "sin precio conocido" in t.report()


def test_los_precios_se_pueden_sobrescribir_por_entorno(monkeypatch):
    monkeypatch.setenv("OPENAI_PRICES", '{"gpt-4o": [5, 15], "modelo-nuevo": [1, 2]}')
    assert price_for("gpt-4o") == (5.0, 15.0) and price_for("modelo-nuevo") == (1.0, 2.0)


def test_el_cliente_envuelto_cuenta_chat_y_embeddings_y_devuelve_lo_mismo():
    chat_result = SimpleNamespace(usage=SimpleNamespace(prompt_tokens=1200, completion_tokens=300), choices=["x"])
    emb_result = SimpleNamespace(usage=SimpleNamespace(prompt_tokens=800, total_tokens=800), data=["v"])
    inner = SimpleNamespace(
        chat=SimpleNamespace(completions=SimpleNamespace(parse=lambda **kw: chat_result)),
        embeddings=SimpleNamespace(create=lambda **kw: emb_result),
    )
    tracker = UsageTracker()
    client = TrackedClient(inner, tracker)
    assert client.chat.completions.parse(model="gpt-4o", messages=[]) is chat_result
    assert client.embeddings.create(model="text-embedding-3-small", input=["a"]) is emb_result
    assert tracker.by_model["gpt-4o"] == {"calls": 1, "input": 1200, "output": 300}
    assert tracker.by_model["text-embedding-3-small"] == {"calls": 1, "input": 800, "output": 0}


def test_si_la_respuesta_no_trae_usage_no_falla():
    inner = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(parse=lambda **kw: SimpleNamespace(choices=[]))))
    tracker = UsageTracker()
    TrackedClient(inner, tracker).chat.completions.parse(model="gpt-4o", messages=[])
    assert tracker.by_model == {}


def test_el_informe_sin_llamadas():
    assert "ninguna llamada" in UsageTracker().report()
