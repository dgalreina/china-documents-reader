"""Prueba local del paso 4: python -m lambdas.extract_hitos.cli salida_de_embed_label.json --source mi-libro

Lee el JSON de `embed_label.cli --out ... --with-vectors` (solo usa texto y años) y extrae los hitos.
Usa la API de OpenAI (céntimos) y Wikidata/Mapbox. No escribe en ninguna base de datos.
"""

import argparse
import json
from pathlib import Path

from dotenv import find_dotenv, load_dotenv

from .pipeline import extract_hitos


def main() -> None:
    parser = argparse.ArgumentParser(description="Extrae hitos históricos (es/en/zh) de los chunks de un libro.")
    parser.add_argument("input", type=Path, help="JSON de embed_label.cli (campo ready)")
    parser.add_argument("--source", required=True, help="slug del libro, se guarda como `fuente`")
    parser.add_argument("--out", type=Path, default=None, help="guarda los hitos en JSON (para write_mongo --kind hitos)")
    parser.add_argument("--limit", type=int, default=None, help="solo los N primeros chunks (para gastar poco)")
    args = parser.parse_args()

    load_dotenv(find_dotenv(usecwd=True))
    chunks = json.loads(args.input.read_text(encoding="utf-8"))["ready"]
    if args.limit:
        chunks = chunks[: args.limit]
    print(f"Chunks de entrada: {len(chunks)}")

    result = extract_hitos(chunks, args.source)

    print(f"\nEstadísticas: {dict(result.stats)}")
    print(f"\nHitos guardables: {len(result.hitos)}")
    for h in sorted(result.hitos, key=lambda x: x["año"]):
        lng, lat = h["location"]["coordinates"]
        print(f"  {h['año']:>6} | {h['lugar']:<28} ({lat:.3f}, {lng:.3f}) [{h['ubicacionFuente']}] {h['titulo']}")
        print(f"           cita: {h['_evidencia'][:150]!r}")
        for person in h.get("_personajes", []):
            life = f" ({person['nacimiento']}..{person['muerte']})" if person["nacimiento"] or person["muerte"] else ""
            print(f"           personaje: {person['nombre_en']} / {person['nombre_zh']}{life} — {person['rol_en']} | menciones {person['menciones']} | alias {person['alias']}")
    if result.discarded:
        print(f"\nDescartados: {len(result.discarded)}")
        for d in result.discarded:
            print(f"  {d['reason']:<30} {d['año']:>6} | {d['sitio']} / hoy: {d['sitio_moderno']} ({d['provincia']}) | {d['titulo']}")

    if args.out:
        args.out.write_text(
            json.dumps({"hitos": result.hitos, "descartados": result.discarded}, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        print(f"\nGuardado en {args.out}")


if __name__ == "__main__":
    main()
