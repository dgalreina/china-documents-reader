"""Limpia en Mongo los datos de un libro que NO está en el catálogo (restos de pruebas, p. ej. `oxford-test`). La web no puede borrarlos porque solo lista los del catálogo.

  python -m lambdas.delete_book.cli oxford-test                 (solo CUENTA lo que borraría)
  python -m lambdas.delete_book.cli oxford-test --write         (lo borra)

Solo toca la base de DEV (MONGO_TARGET, por defecto dev) y solo documentos con la marca de origen de esta app: nunca los de china-history. Es la misma lógica que
usa el botón de borrar de la web (`purge_mongo`); aquí no se tocan archivos de S3 ni el catálogo.
"""

import argparse
import os

from dotenv import find_dotenv, load_dotenv

from ..presign.signer import SOURCE_PATTERN
from ..write_mongo.store import ORIGIN, cached_database
from .purge import CHUNKS, HITOS, PERSONAJES, purge_mongo


def main() -> None:
    parser = argparse.ArgumentParser(description="Borra de Mongo (dev) los datos de un libro.")
    parser.add_argument("slug")
    parser.add_argument("--write", action="store_true", help="borra de verdad (sin esto solo cuenta)")
    args = parser.parse_args()
    if not SOURCE_PATTERN.match(args.slug):
        raise SystemExit("Identificador de libro no válido (2 a 60 caracteres: minúsculas, números y guiones).")

    load_dotenv(find_dotenv(usecwd=True))
    target = os.environ.get("MONGO_TARGET", "dev")
    db = cached_database(target)
    print(f"Base de datos: {db.name} ({target})")

    if not args.write:
        print(f"book_chunks de {args.slug}: {db[CHUNKS].count_documents({'source': args.slug, 'origen': ORIGIN})}")
        print(f"hitos_conocidos de {args.slug}: {db[HITOS].count_documents({'fuente': args.slug, 'origen': ORIGIN})}")
        print(f"personajes que lo citan: {db[PERSONAJES].count_documents({'fuentes': args.slug, 'origen': ORIGIN})}")
        print("\n(Solo se ha contado. Añade --write para borrar; los personajes que otro libro usa se conservan.)")
        return
    print(f"Borrado: {purge_mongo(db, args.slug)}")


if __name__ == "__main__":
    main()
