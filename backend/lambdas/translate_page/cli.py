"""Prueba local: python -m lambdas.translate_page.cli ruta/al/libro.pdf --pages 3-5 [--parallel 3] [--show]

Traduce unas páginas (usa la API de OpenAI: céntimos por página) y guarda la caché en una carpeta local, así que repetir la misma
página no vuelve a pagar. Imprime el tiempo y el coste de cada página, que es lo que decide si hace falta precargar o hacer streaming.
`--parallel N` simula la PRECARGA (traducir varias páginas a la vez). No imprime nunca la clave.
"""

import argparse
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from dotenv import find_dotenv, load_dotenv

from shared.storage import LocalStorage
from shared.usage import TRACKER

from ..embed_label.pipeline import make_client
from ..extract_chunk.pdf_reader import extract_pages
from .translator import translate_page


def main() -> None:
    parser = argparse.ArgumentParser(description="Traduce páginas de un PDF al español.")
    parser.add_argument("pdf", type=Path)
    parser.add_argument("--pages", required=True, help="páginas del PDF, p. ej. 3-5 o 4 (número de página del PDF, no el impreso)")
    parser.add_argument("--slug", default="prueba", help="identificador del libro en la caché")
    parser.add_argument("--cache", type=Path, default=Path(".translate-cache"), help="carpeta de la caché local")
    parser.add_argument("--parallel", type=int, default=1, help="páginas traducidas a la vez (simula la precarga)")
    parser.add_argument("--show", action="store_true", help="imprime el texto traducido")
    args = parser.parse_args()
    sys.stdout.reconfigure(encoding="utf-8")  # la consola de Windows (cp1252) no sabe imprimir caracteres chinos

    load_dotenv(find_dotenv(usecwd=True))

    first, _, last = args.pages.partition("-")
    wanted = range(int(first), int(last or first) + 1)
    pages = [p for p in extract_pages(args.pdf) if p.number in wanted]
    if not pages:
        raise SystemExit("Ninguna de esas páginas existe en el PDF.")

    client, storage = make_client(), LocalStorage(args.cache)

    def work(page):
        return translate_page(client, storage, "local", args.slug, page.number, page.text)

    started = time.perf_counter()
    with ThreadPoolExecutor(max_workers=max(1, args.parallel)) as pool:
        results = list(pool.map(work, pages))
    total = time.perf_counter() - started

    for r in results:
        origin = "caché" if r.cached else f"{r.seconds:.1f} s, {r.inputTokens}+{r.outputTokens} tokens"
        print(f"Pág. {r.page}: {len(r.text)} caracteres  ({origin})")
        if args.show:
            print(f"\n{r.text}\n{'-' * 60}")
    print(f"\nTotal: {total:.1f} s para {len(results)} página(s) con {max(1, args.parallel)} a la vez")
    print(TRACKER.report())


if __name__ == "__main__":
    main()
