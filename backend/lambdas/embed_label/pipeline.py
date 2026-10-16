"""Paso 3: embeddings + etiquetado de años. Lógica pura, sin AWS ni Mongo.

Entrada: los chunks de `extract_chunk`. Salida: chunks listos para guardar y
una lista aparte de pendientes (los que no se pudieron etiquetar). Escribir en
Mongo es un paso posterior: así este se prueba sin base de datos.
"""

import os
from dataclasses import dataclass, field

from openai import OpenAI

from ..extract_chunk.chunker import Chunk
from .embedder import EMBEDDING_MODEL, embed_texts
from .labeler import DocumentPeriod, infer_document_period, is_uninformative, label_chunks

# Modelo para etiquetar años. gpt-4o-mini se equivocaba con seguridad en culturas poco
# conocidas (Laoguantai -3000..-2000 en vez de ~-6000..-5000); gpt-4o acierta mucho más
# (ver prueba con el Oxford Handbook). Configurable por entorno (en la Lambda, variable de entorno).
DEFAULT_LABEL_MODEL = "gpt-4o"


@dataclass
class EmbedLabelResult:
    document_period: DocumentPeriod | None
    ready: list[dict] = field(default_factory=list)  # cumplen el contrato
    pending: list[dict] = field(default_factory=list)  # fuera de book_chunks


def make_client() -> OpenAI:
    if not os.environ.get("OPENAI_API_KEY"):
        raise RuntimeError("Falta OPENAI_API_KEY (ponla en el .env)")
    return OpenAI(max_retries=5)


def embed_and_label(
    chunks: list[Chunk], client: OpenAI | None = None, label_model: str | None = None
) -> EmbedLabelResult:
    client = client or make_client()
    label_model = label_model or os.environ.get("LABEL_MODEL", DEFAULT_LABEL_MODEL)
    result = EmbedLabelResult(document_period=None)
    if not chunks:
        return result

    # Contexto del documento: el arranque del libro (primeros chunks).
    head = "\n\n".join(c.text for c in chunks[:4])

    #Parte1: El labeler:
    try:
        result.document_period = infer_document_period(client, label_model, head)
    except Exception:  # noqa: BLE001 — sin contexto se etiqueta igual, solo peor
        result.document_period = None

    ranges = label_chunks(client, label_model, result.document_period, [c.text for c in chunks])
    # Rangos válidos pero inútiles (≈ todo el libro) -> pendientes, con su motivo.
    reasons = {}
    for i, rng in enumerate(ranges):
        if rng is not None and is_uninformative(rng, result.document_period):
            ranges[i] = None
            reasons[i] = "rango_igual_al_periodo_del_documento"

    # Solo se pagan embeddings de los chunks que van a guardarse.
    labeled = [(c, r) for c, r in zip(chunks, ranges) if r is not None]


    #Parte 2: embedding
    vectors = embed_texts(client, [c.text for c, _ in labeled])

    for (chunk, rng), vector in zip(labeled, vectors):
        result.ready.append(
            {
                "chunkIndex": chunk.index,
                "text": chunk.text,
                "embedding": vector,
                "embeddingModel": EMBEDDING_MODEL,
                "añoInicio": rng.ano_inicio,
                "añoFin": rng.ano_fin,
                "pageStart": chunk.page_start,
                "pageEnd": chunk.page_end,
            }
        )
    for i, (chunk, rng) in enumerate(zip(chunks, ranges)):
        if rng is None:
            result.pending.append(
                {
                    "chunkIndex": chunk.index,
                    "text": chunk.text,
                    "pageStart": chunk.page_start,
                    "pageEnd": chunk.page_end,
                    "reason": reasons.get(i, "sin_ano_fiable"),
                }
            )
    return result
