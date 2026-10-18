"""Guardar solo lo nuevo: detectar hitos que ya existen aunque estén dichos con otras palabras.

Los libros repiten los mismos hechos con distinto texto, así que el `id` (hash del título) no basta. Se compara
contra la base A LA QUE SE ESCRIBE (dev hoy, producción cuando se promueva), en tres pasos:

1. Candidatos: hitos con año cercano y a pocos kilómetros. Si no hay ninguno, el hito es nuevo.
2. Parecido por significado (embeddings del texto en inglés): muy alto = duplicado, muy bajo = nuevo.
3. Zona gris: un modelo decide si los dos textos describen el mismo hecho.

Umbrales calibrados con hitos reales (Oxford Handbook y Gernet frente a nuestras extracciones): el mismo hecho sale
entre 0,62 y 0,95 y hechos distintos entre 0,31 y 0,60, así que hay solapamiento y por eso hace falta el juez.
"""

import math
from dataclasses import dataclass
from typing import Callable

from openai import OpenAI
from pydantic import BaseModel, Field

from shared.geocoder import _distance_km
from shared.llm import chat_params

YEAR_WINDOW = 15  # las fechas antiguas son discutidas (la conquista de Shang: -1050, -1046, -1045 según la fuente)
RADIUS_KM = 25
DUPLICATE_ABOVE = 0.85
NEW_BELOW = 0.5
DEFAULT_JUDGE_MODEL = "gpt-4o"
EMBEDDING_MODEL = "text-embedding-3-small"

NeighborsFn = Callable[[dict], list[dict]]  # hito -> hitos cercanos ya existentes en la base


class SameEvent(BaseModel):
    mismo_hecho: bool = Field(description="true si los dos textos describen el MISMO hecho histórico, aunque con otras palabras.")
    motivo: str = Field(description="Una frase explicando la decisión.")


JUDGE_PROMPT = (
    "Comparas dos descripciones de hitos históricos de China que ocurrieron en fechas y lugares cercanos. Decide si "
    "describen el MISMO hecho (aunque estén redactados distinto o con distinto nivel de detalle) o dos hechos "
    "DISTINTOS (p. ej. la fundación de una ciudad y una batalla posterior en ella, o un asedio y la caída de la misma "
    "ciudad). En caso de duda razonable, responde que son hechos distintos."
)


@dataclass(frozen=True)
class Decision:
    duplicate_of: dict | None  # el hito existente, o None si es nuevo
    score: float | None
    via: str  # "sin_vecinos" | "parecido_alto" | "parecido_bajo" | "juez"
    reason: str = ""


def hito_text(hito: dict) -> str:
    return f"{hito.get('titulo_en') or hito['titulo']}. {hito.get('descripcion_en') or hito['descripcion']}"


def cosine(a: list[float], b: list[float]) -> float:
    return sum(x * y for x, y in zip(a, b)) / (math.sqrt(sum(x * x for x in a)) * math.sqrt(sum(y * y for y in b)))


def embed(client: OpenAI, texts: list[str]) -> list[list[float]]:
    response = client.embeddings.create(model=EMBEDDING_MODEL, input=texts)
    return [item.embedding for item in sorted(response.data, key=lambda item: item.index)]


def judge(client: OpenAI, model: str, new_text: str, existing_text: str) -> SameEvent:
    completion = client.chat.completions.parse(
        model=model,
        messages=[
            {"role": "system", "content": JUDGE_PROMPT},
            {"role": "user", "content": f"HITO NUEVO:\n{new_text}\n\nHITO YA EXISTENTE:\n{existing_text}"},
        ],
        response_format=SameEvent,
        **chat_params(),
    )
    parsed = completion.choices[0].message.parsed
    if parsed is None:
        raise ValueError("El juez no devolvió el formato esperado")
    return parsed


def is_close(a: dict, b: dict) -> bool:
    """¿Año y lugar cercanos? Se usa para comparar con hitos de la misma tanda, que aún no están en la base."""
    if abs(a["año"] - b["año"]) > YEAR_WINDOW:
        return False
    (lng1, lat1), (lng2, lat2) = a["location"]["coordinates"], b["location"]["coordinates"]
    return _distance_km((lat1, lng1), (lat2, lng2)) <= RADIUS_KM


def classify(
    hito: dict,
    neighbors: list[dict],
    client: OpenAI,
    judge_model: str = DEFAULT_JUDGE_MODEL,
    embed_fn: Callable[[list[str]], list[list[float]]] | None = None,
    judge_fn: Callable[[str, str], SameEvent] | None = None,
) -> Decision:
    if not neighbors:
        return Decision(None, None, "sin_vecinos")
    embed_fn = embed_fn or (lambda texts: embed(client, texts))
    judge_fn = judge_fn or (lambda new, old: judge(client, judge_model, new, old))
    texts = [hito_text(hito)] + [hito_text(n) for n in neighbors]
    vectors = embed_fn(texts)
    scored = sorted(((cosine(vectors[0], v), n) for v, n in zip(vectors[1:], neighbors)), key=lambda item: -item[0])

    best_score, best = scored[0]
    if best_score >= DUPLICATE_ABOVE:
        return Decision(best, best_score, "parecido_alto")
    # Zona gris: se pregunta por cada vecino plausible, del más parecido al menos; basta uno que sea el mismo hecho.
    for score, neighbor in scored:
        if score < NEW_BELOW:
            break
        verdict = judge_fn(texts[0], hito_text(neighbor))
        if verdict.mismo_hecho:
            return Decision(neighbor, score, "juez", verdict.motivo)
    return Decision(None, best_score, "parecido_bajo" if best_score < NEW_BELOW else "juez")


def filter_new(
    hitos: list[dict],
    neighbors_fn: NeighborsFn,
    client: OpenAI,
    judge_model: str = DEFAULT_JUDGE_MODEL,
    **fns,
) -> tuple[list[dict], list[dict]]:
    """(nuevos, duplicados). Cada hito se compara con la base y con los ya aceptados de esta misma tanda."""
    kept: list[dict] = []
    duplicates: list[dict] = []
    for hito in hitos:
        neighbors = neighbors_fn(hito) + [k for k in kept if is_close(hito, k)]
        decision = classify(hito, neighbors, client, judge_model, **fns)
        if decision.duplicate_of is None:
            kept.append(hito)
            continue
        existing = decision.duplicate_of
        duplicates.append(
            {
                "id": hito["id"],
                "titulo": hito.get("titulo_en") or hito["titulo"],
                "año": hito["año"],
                "duplicado_de": existing.get("id"),
                "titulo_existente": existing.get("titulo_en") or existing.get("titulo"),
                "fuente_existente": existing.get("fuente"),
                "parecido": round(decision.score, 3) if decision.score is not None else None,
                "via": decision.via,
                "motivo": decision.reason,
            }
        )
    return kept, duplicates
