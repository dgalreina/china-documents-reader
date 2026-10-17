"""Contador de uso (y coste estimado) de la API de OpenAI.

El cliente de OpenAI que usan todos los pasos se envuelve en `TrackedClient`, que apunta los tokens de cada llamada sin cambiar
la lógica de los pasos. Cada paso (embed_label, extract_hitos, write_mongo) imprime al final su propio resumen.

IMPORTANTE: el coste es una ESTIMACIÓN. Los precios de abajo salen de comparadores de terceros (mayo de 2026), no de la web de
OpenAI: comprueba https://openai.com/api/pricing antes de fiarte, y si cambian, sobrescríbelos sin tocar código con la variable
de entorno OPENAI_PRICES, p. ej. OPENAI_PRICES='{"gpt-4o": [2.5, 10]}' (USD por millón de tokens: entrada, salida).
"""

import json
import os
import threading
from types import SimpleNamespace

# USD por millón de tokens: (entrada, salida). Los embeddings solo cobran entrada.
PRICES: dict[str, tuple[float, float]] = {
    "gpt-4o": (2.50, 10.00),
    "gpt-4o-mini": (0.15, 0.60),
    "text-embedding-3-small": (0.02, 0.0),
}
PRICES_DATE = "mayo de 2026"


def prices() -> dict[str, tuple[float, float]]:
    table = dict(PRICES)
    override = os.environ.get("OPENAI_PRICES")
    if override:
        table.update({model: (float(p[0]), float(p[1])) for model, p in json.loads(override).items()})
    return table


def price_for(model: str) -> tuple[float, float] | None:
    """Precio de un modelo. Las versiones con fecha (gpt-4o-2024-08-06) usan el precio del prefijo MÁS LARGO que coincida,
    para que gpt-4o-mini-2024-07-18 no se cobre como gpt-4o."""
    table = prices()
    if model in table:
        return table[model]
    matches = [name for name in table if model.startswith(name)]
    return table[max(matches, key=len)] if matches else None


class UsageTracker:
    def __init__(self):
        self._lock = threading.Lock()  # en AWS, varias llamadas pueden ir en paralelo
        self.by_model: dict[str, dict[str, int]] = {}

    def record(self, model: str, input_tokens: int, output_tokens: int = 0) -> None:
        with self._lock:
            entry = self.by_model.setdefault(model, {"calls": 0, "input": 0, "output": 0})
            entry["calls"] += 1
            entry["input"] += int(input_tokens or 0)
            entry["output"] += int(output_tokens or 0)

    def reset(self) -> None:
        with self._lock:
            self.by_model.clear()

    def cost(self) -> tuple[float, list[str]]:
        """(coste estimado en USD, modelos sin precio conocido: sus tokens no están en el coste)."""
        total, unknown = 0.0, []
        for model, entry in self.by_model.items():
            price = price_for(model)
            if price is None:
                unknown.append(model)
                continue
            total += entry["input"] / 1e6 * price[0] + entry["output"] / 1e6 * price[1]
        return total, unknown

    def summary(self) -> dict:
        cost, unknown = self.cost()
        return {"models": {m: dict(e) for m, e in self.by_model.items()}, "estimatedCostUsd": round(cost, 4), "unknownModels": unknown}

    def report(self) -> str:
        if not self.by_model:
            return "Uso de OpenAI: ninguna llamada."
        lines = [f"Uso de OpenAI (coste ESTIMADO con precios de {PRICES_DATE}; compruébalos en openai.com/api/pricing):"]
        for model, e in sorted(self.by_model.items()):
            price = price_for(model)
            cost = f"≈ ${e['input'] / 1e6 * price[0] + e['output'] / 1e6 * price[1]:.3f}" if price else "sin precio conocido"
            lines.append(f"  {model:<24} {e['calls']:>5} llamadas  {e['input']:>9,} tokens de entrada  {e['output']:>8,} de salida  {cost}")
        total, unknown = self.cost()
        lines.append(f"  TOTAL ≈ ${total:.3f}" + (f"  (sin contar: {', '.join(unknown)})" if unknown else ""))
        return "\n".join(lines)


# Un contador por proceso: cada paso (CLI o Lambda) cuenta lo suyo.
TRACKER = UsageTracker()


class TrackedClient:
    """Envuelve el cliente de OpenAI y apunta los tokens de `chat.completions.parse` y `embeddings.create`.

    Expone las mismas rutas que usa el código (`client.chat.completions.parse`, `client.embeddings.create`), así que se puede
    pasar donde se pasaba el cliente normal.
    """

    def __init__(self, client, tracker: UsageTracker = TRACKER):
        self._client, self.tracker = client, tracker
        self.chat = SimpleNamespace(completions=SimpleNamespace(parse=self._parse))
        self.embeddings = SimpleNamespace(create=self._embed)

    def _parse(self, **kwargs):
        completion = self._client.chat.completions.parse(**kwargs)
        usage = getattr(completion, "usage", None)
        if usage is not None:
            self.tracker.record(kwargs.get("model", "desconocido"), usage.prompt_tokens, usage.completion_tokens)
        return completion

    def _embed(self, **kwargs):
        response = self._client.embeddings.create(**kwargs)
        usage = getattr(response, "usage", None)
        if usage is not None:
            self.tracker.record(kwargs.get("model", "desconocido"), getattr(usage, "prompt_tokens", None) or usage.total_tokens)
        return response
