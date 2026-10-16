"""Paso 3a: convertir cada chunk en un vector (embedding).

Un embedding es una lista de 1536 números que representa el *significado* del
texto: dos fragmentos que hablan de lo mismo quedan "cerca" en ese espacio, y
eso es lo que usa Atlas Vector Search. Por eso el modelo es parte del contrato
con china-history: un vector de otro modelo no se puede comparar con los demás.
"""

from openai import OpenAI

# NO CAMBIAR sin reindexar todo book_chunks (contrato con china-history).
EMBEDDING_MODEL = "text-embedding-3-small"
EMBEDDING_DIMENSIONS = 1536

# La API admite muchos textos por llamada; 100 es un buen equilibrio entre
# pocas peticiones y no pasarse del límite de tokens por petición.
BATCH_SIZE = 100


def embed_texts(client: OpenAI, texts: list[str]) -> list[list[float]]:
    """Devuelve un embedding por texto, en el mismo orden.

    Los reintentos ante límites de peticiones (429) o fallos de red los hace el
    propio cliente de OpenAI (`max_retries`, con espera exponencial).
    """
    vectors: list[list[float]] = []
    for start in range(0, len(texts), BATCH_SIZE):
        batch = texts[start : start + BATCH_SIZE]
        response = client.embeddings.create(model=EMBEDDING_MODEL, input=batch)
        # La API devuelve `index` por elemento: ordenamos por si acaso.
        ordered = sorted(response.data, key=lambda item: item.index)
        for item in ordered:
            if len(item.embedding) != EMBEDDING_DIMENSIONS:
                raise ValueError(
                    f"Embedding de {len(item.embedding)} dimensiones, "
                    f"se esperaban {EMBEDDING_DIMENSIONS}"
                )
            vectors.append(item.embedding)
    return vectors
