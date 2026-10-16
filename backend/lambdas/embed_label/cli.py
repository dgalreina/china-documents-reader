"""Prueba local de los pasos 1-3: python -m lambdas.embed_label.cli ruta/al/libro.pdf --limit 20
  (o --pages 14-45 para un cacho de un libro grande; los números son los de página del PDF, no los impresos)

Encadena extract_chunk y embed_label. Usa la API de OpenAI (cuesta céntimos):
empieza con --limit pequeño. No imprime nunca los vectores ni la clave.
"""

import argparse
import json
from pathlib import Path

from dotenv import find_dotenv, load_dotenv

from ..extract_chunk.chunker import chunk_pages
from ..extract_chunk.pdf_reader import extract_pages
from .pipeline import embed_and_label


def main() -> None:
    parser = argparse.ArgumentParser(description="Embeddings y años de los chunks de un PDF.")
    parser.add_argument("pdf", type=Path)
    parser.add_argument("--pages", default=None, help="rango de páginas del PDF a procesar, p. ej. 14-45 (para pruebas)")
    parser.add_argument("--limit", type=int, default=None, help="solo los N primeros chunks (para gastar poco)")
    parser.add_argument("--out", type=Path, default=None, help="guarda el resultado en JSON (sin vectores salvo --with-vectors)")
    parser.add_argument("--with-vectors", action="store_true", help="incluye los vectores en --out (necesario para write_mongo)")
    args = parser.parse_args()

    # Busca .env subiendo desde la carpeta actual (backend/ o la raíz del repo).
    load_dotenv(find_dotenv(usecwd=True))

    pages = extract_pages(args.pdf)
    if args.pages:
        first, _, last = args.pages.partition("-")
        pages = [p for p in pages if int(first) <= p.number <= int(last or first)]
    chunks = chunk_pages(pages)
    if args.limit:
        chunks = chunks[: args.limit]
    print(f"Chunks a procesar: {len(chunks)}")

    result = embed_and_label(chunks)

    if result.document_period:
        p = result.document_period
        print(f"Periodo del documento: {p.resumen} ({p.ano_inicio} a {p.ano_fin})")
    print(f"Listos para guardar: {len(result.ready)} | Pendientes: {len(result.pending)}")
    if result.ready:
        print(f"Dimensiones del embedding: {len(result.ready[0]['embedding'])}")
    located = sum(1 for d in result.ready if "ubicacion" in d)
    print(f"Con ubicación: {located} de {len(result.ready)} | por fuente: {dict(result.geo_sources)}")
    if result.geo_unresolved:
        print("Sitios que el modelo nombró y no se pudieron resolver:")
        for (site, province), n in result.geo_unresolved.most_common(15):
            print(f"  {n}x {site} ({province})")
    for doc in result.ready[:5]:
        print(f"  chunk {doc['chunkIndex']} (pág. {doc['pageStart']}): {doc['añoInicio']} a {doc['añoFin']} | {doc['text'][:70]!r}")
    for doc in result.pending[:5]:
        print(f"  PENDIENTE chunk {doc['chunkIndex']} (pág. {doc['pageStart']}): {doc['text'][:70]!r}")

    if args.out:
        slim = result.ready if args.with_vectors else [{k: v for k, v in d.items() if k != "embedding"} for d in result.ready]
        args.out.write_text(
            json.dumps({"ready": slim, "pending": result.pending}, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        print(f"Guardado en {args.out} ({'con' if args.with_vectors else 'sin'} vectores)")


if __name__ == "__main__":
    main()
