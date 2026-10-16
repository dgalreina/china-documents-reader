"""Escribe en MongoDB el resultado de embed_label.

  python -m lambdas.embed_label.cli libro.pdf --limit 20 --out salida.json --with-vectors
  python -m lambdas.write_mongo.cli salida.json --source mi-libro --target dev            (dry-run)
  python -m lambdas.write_mongo.cli salida.json --source mi-libro --target dev --write --ensure-index

Sin --write no se escribe nada. Con --target prod --write se pide escribir el nombre de la base.
"""

import argparse
import json
from pathlib import Path

from dotenv import find_dotenv, load_dotenv

from .store import ContractError, resolve_target, write_chunks, write_hitos


def main() -> None:
    parser = argparse.ArgumentParser(description="Guarda los chunks en book_chunks (upsert por source+chunkIndex).")
    parser.add_argument("input", type=Path, help="JSON de embed_label.cli (--with-vectors) o de extract_hitos.cli (--kind hitos)")
    parser.add_argument("--kind", choices=["chunks", "hitos"], default="chunks", help="qué se escribe: book_chunks o hitos_conocidos")
    parser.add_argument("--source", required=True, help="slug del documento, p. ej. gernet-mundo-chino")
    parser.add_argument("--target", required=True, choices=["dev", "prod"])
    parser.add_argument("--write", action="store_true", help="escribe de verdad (sin esto es dry-run)")
    parser.add_argument("--replace-source", action="store_true", help="borra los chunks de ese source que ya no están en esta tanda (solo ese source)")
    parser.add_argument("--ensure-index", action="store_true", help="solo dev: crea los índices en la base de pruebas")
    args = parser.parse_args()

    load_dotenv(find_dotenv(usecwd=True))
    try:
        target = resolve_target(args.target)
        docs = json.loads(args.input.read_text(encoding="utf-8"))["hitos" if args.kind == "hitos" else "ready"]
        if args.write and target.name == "prod":
            typed = input(f"Vas a ESCRIBIR en producción (base '{target.db_name}'). Escribe el nombre de la base para confirmar: ")
            if typed.strip() != target.db_name:
                print("No coincide. Cancelado, no se ha escrito nada.")
                return
        writer = write_hitos if args.kind == "hitos" else write_chunks
        report = writer(target, args.source, docs, write=args.write, ensure_index=args.ensure_index, replace_source=args.replace_source)
    except ContractError as err:
        print(f"ERROR de contrato, no se ha escrito nada: {err}")
        raise SystemExit(1)

    modo = "DRY-RUN (no se ha escrito nada)" if report.dry_run else "ESCRITO"
    print(f"{modo} -> {report.target} / base '{report.db_name}' / source '{report.source}'")
    print(f"{'Hitos' if args.kind == 'hitos' else 'Chunks'}: {report.total} | ya existían (se reemplazarían): {report.existing} | nuevos: {report.total - report.existing}")
    if args.replace_source:
        accion = "borrados" if not report.dry_run else "se borrarían"
        print(f"Huérfanos del source '{report.source}' ({accion}): {report.orphans}")
    if not report.dry_run:
        print(f"Operaciones aplicadas: {report.written}")


if __name__ == "__main__":
    main()
