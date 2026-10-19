"""Da portada a los libros que se subieron ANTES de que el flujo las dibujara (o cuyo dibujo falló).

  python -m lambdas.store_book.backfill_covers --table <BooksTable> --bucket <BooksBucket>            (solo muestra qué haría)
  python -m lambdas.store_book.backfill_covers --table <BooksTable> --bucket <BooksBucket> --write    (lo hace)

Los nombres de la tabla y del bucket salen de las salidas del despliegue (BooksTableName y BooksBucketName). Usa tus credenciales de AWS (`aws login`).
Para cada libro del catálogo SIN portada: baja su PDF, dibuja la primera página, la guarda en `books/<slug>/cover.jpg` y marca `hasCover` en su registro.
Solo toca libros sin portada y solo añade esa marca: nunca borra ni cambia nada más. Se puede repetir sin riesgo.
"""

import argparse
import tempfile
from pathlib import Path

from ..extract_chunk.cover import COVER_KEY_NAME, render_cover


def backfill(table, s3, bucket: str, write: bool) -> dict:
    """Devuelve cuántos libros ya tenían portada, cuántos se generaron (o se generarían) y cuáles fallaron."""
    result = {"yaTenian": 0, "generadas": [], "fallidas": []}
    items, kwargs = [], {}
    while True:
        page = table.scan(**kwargs)
        items.extend(page.get("Items", []))
        if "LastEvaluatedKey" not in page:
            break
        kwargs["ExclusiveStartKey"] = page["LastEvaluatedKey"]

    for item in items:
        slug = item["slug"]
        if item.get("hasCover"):
            result["yaTenian"] += 1
            continue
        try:
            with tempfile.TemporaryDirectory() as tmp:
                pdf = Path(tmp) / "libro.pdf"
                s3.download_file(bucket, f"books/{slug}/original.pdf", str(pdf))
                cover = render_cover(pdf)
            if write:
                s3.put_object(Bucket=bucket, Key=f"books/{slug}/{COVER_KEY_NAME}", Body=cover, ContentType="image/jpeg")
                table.update_item(Key={"slug": slug}, UpdateExpression="SET hasCover = :c", ExpressionAttributeValues={":c": True})
            result["generadas"].append(slug)
        except Exception as error:  # noqa: BLE001 — un libro que falla no debe impedir los demás
            result["fallidas"].append(f"{slug}: {type(error).__name__}")
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description="Genera la portada de los libros que no la tienen.")
    parser.add_argument("--table", required=True, help="nombre de la tabla del catálogo (salida BooksTableName)")
    parser.add_argument("--bucket", required=True, help="nombre del bucket de libros (salida BooksBucketName)")
    parser.add_argument("--region", default="eu-west-1")
    parser.add_argument("--write", action="store_true", help="escribe de verdad (sin esto solo se muestra lo que se haría)")
    args = parser.parse_args()

    import boto3

    table = boto3.resource("dynamodb", region_name=args.region).Table(args.table)
    s3 = boto3.client("s3", region_name=args.region)
    result = backfill(table, s3, args.bucket, args.write)
    verb = "Generadas" if args.write else "Se generarían"
    print(f"Ya tenían portada: {result['yaTenian']}")
    print(f"{verb}: {len(result['generadas'])} {result['generadas']}")
    if result["fallidas"]:
        print(f"FALLARON: {result['fallidas']}")
    if not args.write and result["generadas"]:
        print("\n(Solo se ha mostrado lo que se haría. Añade --write para hacerlo.)")


if __name__ == "__main__":
    main()
