"""Prueba local de los pasos 1 y 2: python -m lambdas.extract_chunk.cli ruta/al/libro.pdf"""

import argparse
from pathlib import Path

from .chunker import chunk_pages
from .pdf_reader import extract_pages


def main() -> None:
    parser = argparse.ArgumentParser(description="Extrae y trocea un PDF (sin embeddings todavía).")
    parser.add_argument("pdf", type=Path)
    parser.add_argument("--max-chars", type=int, default=1200)
    parser.add_argument("--overlap", type=int, default=200)
    args = parser.parse_args()

    pages = extract_pages(args.pdf)
    empty = sum(1 for p in pages if not p.text)
    chunks = chunk_pages(pages, args.max_chars, args.overlap)

    print(f"Páginas: {len(pages)} ({empty} sin texto)")
    if pages and empty / len(pages) > 0.5:
        print("AVISO: más de la mitad de las páginas no tienen texto. Probablemente es un PDF escaneado y necesita OCR.")
    print(f"Chunks: {len(chunks)}")
    if chunks:
        avg = sum(len(c.text) for c in chunks) // len(chunks)
        print(f"Longitud media: {avg} caracteres")
        for c in chunks[:2]:
            print(f"\n--- chunk {c.index} (págs. {c.page_start}-{c.page_end}) ---\n{c.text[:300]}")


if __name__ == "__main__":
    main()
