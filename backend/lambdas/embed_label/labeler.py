"""Paso 3b: etiquetar cada chunk con el rango de años que trata (añoInicio/añoFin).

Estrategia en dos niveles:
1. UNA llamada lee el arranque del documento (título, índice, introducción) y
   dice de qué época trata en general. Es barata y da contexto.
2. Los chunks se etiquetan en lotes, pasando ese periodo general como contexto.
   Así un fragmento ambiguo ("en aquel año...") se interpreta dentro de su libro.

El modelo puede responder null ("no sé"). Eso es correcto: un chunk sin
etiqueta fiable NO se escribe en book_chunks (contrato con china-history), así
que preferimos un pendiente a un año inventado.
"""

from dataclasses import dataclass

from openai import OpenAI
from pydantic import BaseModel, Field

# Rango que acepta el filtro de búsqueda de china-history. Años a.C. en negativo.
MIN_YEAR = -10000
MAX_YEAR = 3000

BATCH_SIZE = 10
# Con 600 caracteres por chunk el modelo ya reconoce la época (mismo criterio
# que tag-oxford-chunks-year.mjs en china-history).
CHUNK_PREVIEW_CHARS = 600
DOC_HEAD_CHARS = 4000


class DocumentPeriod(BaseModel):
    """Periodo general del documento, usado como contexto del etiquetado."""

    resumen: str = Field(description="Una frase: de qué trata el documento y qué época abarca.")
    ano_inicio: int | None = Field(description="Año de inicio del periodo que abarca. Negativo si es a.C. null si no se sabe.")
    ano_fin: int | None = Field(description="Año de fin del periodo que abarca. Negativo si es a.C. null si no se sabe.")


class ChunkYears(BaseModel):
    indice: int = Field(description="Número del fragmento dentro del lote, tal como se dio (1, 2, 3...).")
    ano_inicio: int | None = Field(description="Año en que empieza lo que narra el fragmento. Negativo si es a.C. null si no se puede determinar.")
    ano_fin: int | None = Field(description="Año en que termina lo que narra el fragmento. Negativo si es a.C. null si no se puede determinar.")


class BatchLabels(BaseModel):
    etiquetas: list[ChunkYears]


@dataclass(frozen=True)
class YearRange:
    ano_inicio: int
    ano_fin: int


DOC_SYSTEM_PROMPT = (
    "Lees el arranque de un documento sobre historia de China y dices, en general, de qué trata y qué rango "
    "de años abarca. Los años anteriores a la era común van en negativo (221 a.C. = -221). Si el documento "
    "no es histórico o no se puede saber, devuelve null en los años."
)

CHUNK_SYSTEM_PROMPT = (
    "Etiquetas fragmentos de un documento sobre historia de China con el rango de años que TRATA su contenido "
    "(no la fecha de publicación del libro). Reglas:\n"
    "- Años a.C. en negativo (221 a.C. = -221). ano_inicio <= ano_fin.\n"
    "- Usa dinastías, reinados, personajes y hechos para decidir (p. ej. Tang = 618 a 907).\n"
    "- Un fragmento sobre un solo año pone el mismo año en inicio y fin.\n"
    "- Si es texto editorial, índice, bibliografía, agradecimientos o no se puede determinar con "
    "confianza razonable, devuelve null en ambos años. No inventes.\n"
    "Tienes una descripción general del documento solo como orientación: NO es la respuesta. El rango de "
    "cada fragmento debe salir de lo que dice ese fragmento (culturas, dinastías, personajes, fechas). "
    "Da el rango más estrecho que el texto justifique; si solo puedes decir 'toda la época del libro', "
    "devuelve null."
)


def valid_range(ano_inicio: int | None, ano_fin: int | None) -> YearRange | None:
    """Devuelve el rango si cumple el contrato, o None si hay que descartarlo."""
    if ano_inicio is None or ano_fin is None:
        return None
    if ano_inicio > ano_fin:
        return None
    if ano_inicio < MIN_YEAR or ano_fin > MAX_YEAR:
        return None
    return YearRange(ano_inicio, ano_fin)


# Un rango que cubre casi todo el periodo del documento no informa de nada: solapa con
# casi cualquier consulta y metería ruido en la búsqueda. Solo aplica si el periodo
# del documento es largo (en un libro sobre una sola dinastía, todo cae dentro).
WIDE_SPAN_MIN_YEARS = 500
WIDE_COVERAGE = 0.9


def is_uninformative(rng: YearRange, period: DocumentPeriod | None) -> bool:
    if period is None or period.ano_inicio is None or period.ano_fin is None:
        return False
    doc_span = period.ano_fin - period.ano_inicio
    if doc_span < WIDE_SPAN_MIN_YEARS:
        return False
    overlap = min(rng.ano_fin, period.ano_fin) - max(rng.ano_inicio, period.ano_inicio)
    return overlap >= WIDE_COVERAGE * doc_span


def infer_document_period(client: OpenAI, model: str, head_text: str) -> DocumentPeriod:
    completion = client.chat.completions.parse(
        model=model,
        messages=[
            {"role": "system", "content": DOC_SYSTEM_PROMPT},
            {"role": "user", "content": head_text[:DOC_HEAD_CHARS]},
        ],
        response_format=DocumentPeriod,
    )
    parsed = completion.choices[0].message.parsed
    if parsed is None:
        raise ValueError("El modelo no devolvió el periodo del documento en el formato esperado")
    return parsed


def _label_batch(
    client: OpenAI, model: str, period: DocumentPeriod | None, texts: list[str]
) -> dict[int, YearRange | None]:
    """Etiqueta un lote. Clave: posición 0-based dentro del lote."""
    context = ""
    if period is not None:
        context = f"Periodo general del documento: {period.resumen} ({period.ano_inicio} a {period.ano_fin}).\n\n"
    body = "\n\n".join(
        f"--- Fragmento {i} ---\n{text[:CHUNK_PREVIEW_CHARS]}" for i, text in enumerate(texts, start=1)
    )
    completion = client.chat.completions.parse(
        model=model,
        messages=[
            {"role": "system", "content": CHUNK_SYSTEM_PROMPT},
            {"role": "user", "content": context + body},
        ],
        response_format=BatchLabels,
    )
    parsed = completion.choices[0].message.parsed
    if parsed is None:
        raise ValueError("Respuesta sin el formato esperado")

    result: dict[int, YearRange | None] = {}
    for label in parsed.etiquetas:
        position = label.indice - 1
        if 0 <= position < len(texts):
            result[position] = valid_range(label.ano_inicio, label.ano_fin)
    return result


def label_chunks(
    client: OpenAI, model: str, period: DocumentPeriod | None, texts: list[str]
) -> list[YearRange | None]:
    """Un rango válido (o None = pendiente) por texto, en el mismo orden.

    Si un lote falla o el modelo se salta algún fragmento, se reintenta una vez
    ese lote; lo que siga sin etiqueta válida queda en None. Nunca se rellena
    con el año de un chunk vecino.
    """
    labels: list[YearRange | None] = [None] * len(texts)
    for start in range(0, len(texts), BATCH_SIZE):
        batch = texts[start : start + BATCH_SIZE]
        pending = set(range(len(batch)))
        for _attempt in range(2):
            if not pending:
                break
            try:
                got = _label_batch(client, model, period, [batch[i] for i in sorted(pending)])
            except Exception:  # noqa: BLE001 — un lote malo no debe tumbar el documento
                continue
            order = sorted(pending)
            for pos, rng in got.items():
                labels[start + order[pos]] = rng
                pending.discard(order[pos])
    return labels
