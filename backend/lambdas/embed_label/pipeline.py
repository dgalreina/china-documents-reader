"""Paso 3: embeddings + etiquetado de años y ubicación. Lógica pura, sin AWS ni Mongo.

Entrada: los chunks de `extract_chunk`. Salida: chunks listos para guardar y
una lista aparte de pendientes (los que no se pudieron etiquetar). Escribir en
Mongo es un paso posterior: así este se prueba sin base de datos.

Años: obligatorios (sin ellos el chunk va a pendientes). Ubicación: opcional; solo se
añade cuando el sitio se resuelve con claridad, nunca aproximada.
"""

import os
from collections import Counter
from dataclasses import dataclass, field

from openai import OpenAI

from shared.geocoder import Geocoder

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
    # Resumen de la geocodificación, para medir cuántos chunks se localizan y qué falla.
    geo_sources: Counter = field(default_factory=Counter)  # "curated"/"chgis"/"mapbox" -> nº de chunks
    geo_unresolved: Counter = field(default_factory=Counter)  # (sitio, provincia) -> nº de chunks


def make_client() -> OpenAI:
    if not os.environ.get("OPENAI_API_KEY"):
        raise RuntimeError("Falta OPENAI_API_KEY (ponla en el .env)")
    return OpenAI(max_retries=5)


def embed_and_label(
    chunks: list[Chunk],
    client: OpenAI | None = None,
    label_model: str | None = None,
    geocoder: Geocoder | None = None,
) -> EmbedLabelResult:
    client = client or make_client()
    label_model = label_model or os.environ.get("LABEL_MODEL", DEFAULT_LABEL_MODEL)
    geocoder = geocoder or Geocoder.from_env()
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

    labels = label_chunks(client, label_model, result.document_period, [c.text for c in chunks])
    # Rangos válidos pero inútiles (≈ todo el libro) -> pendientes, con su motivo.
    reasons = {}
    for i, label in enumerate(labels):
        if label is not None and is_uninformative(label.years, result.document_period):
            labels[i] = None
            reasons[i] = "rango_igual_al_periodo_del_documento"

    # Solo se pagan embeddings de los chunks que van a guardarse.
    labeled = [(c, lab) for c, lab in zip(chunks, labels) if lab is not None]


    #Parte 2: embedding
    vectors = embed_texts(client, [c.text for c, _ in labeled])

    for (chunk, label), vector in zip(labeled, vectors):
        doc = {
            "chunkIndex": chunk.index,
            "text": chunk.text,
            "embedding": vector,
            "embeddingModel": EMBEDDING_MODEL,
            "añoInicio": label.years.ano_inicio,
            "añoFin": label.years.ano_fin,
            "pageStart": chunk.page_start,
            "pageEnd": chunk.page_end,
        }
        # Parte 3: ubicación (opcional). Sin sitio claro o sin resolver, el chunk se guarda igual, sin ella.
        if label.site:
            geo = geocoder.geocode(label.site, label.province, label.years.ano_inicio, label.years.ano_fin)
            if geo:
                doc["ubicacion"] = {"type": "Point", "coordinates": [geo.lng, geo.lat]}  # GeoJSON: [lng, lat]
                doc["lat"] = geo.lat
                doc["lng"] = geo.lng
                doc["ubicacionNombre"] = label.site
                doc["ubicacionFuente"] = geo.source
                doc["ubicacionCoincidencia"] = geo.matched_name  # cómo se llama el sitio en la fuente (auditoría)
                result.geo_sources[geo.source] += 1
            else:
                result.geo_unresolved[(label.site, label.province)] += 1
        result.ready.append(doc)

    for i, (chunk, label) in enumerate(zip(chunks, labels)):
        if label is None:
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
