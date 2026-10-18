"""Dónde viven los ficheros entre pasos: S3 en AWS, una carpeta en tu ordenador para probar en local.

Los pasos NO se pasan los datos entre sí (Step Functions limita lo que viaja entre pasos a 256 KB y los chunks de un libro ocupan
varios MB): se dejan en S3 y se pasa solo dónde están (bucket + clave). Los handlers reciben el almacenamiento por parámetro, así
que los tests y las pruebas locales usan `LocalStorage` y AWS usa `S3Storage`, con el mismo código.
"""

import json
import shutil
from pathlib import Path
from typing import Any, Protocol


class Storage(Protocol):
    def download(self, bucket: str, key: str, path: Path) -> None: ...
    def put_json(self, bucket: str, key: str, data: Any) -> None: ...
    def get_json(self, bucket: str, key: str) -> Any: ...
    def try_get_json(self, bucket: str, key: str) -> Any | None: ...
    def copy(self, src_bucket: str, src_key: str, dst_bucket: str, dst_key: str) -> None: ...
    def delete_prefix(self, bucket: str, prefix: str) -> int: ...


class S3Storage:
    """S3 de verdad. boto3 ya viene en el runtime de Lambda, por eso no está en requirements.txt."""

    def __init__(self, client=None):
        self._client = client

    @property
    def client(self):
        if self._client is None:
            import boto3  # importación tardía: los tests y el modo local no lo necesitan

            self._client = boto3.client("s3")
        return self._client

    def download(self, bucket: str, key: str, path: Path) -> None:
        self.client.download_file(bucket, key, str(path))

    def put_json(self, bucket: str, key: str, data: Any) -> None:
        body = json.dumps(data, ensure_ascii=False).encode("utf-8")
        self.client.put_object(Bucket=bucket, Key=key, Body=body, ContentType="application/json")

    def get_json(self, bucket: str, key: str) -> Any:
        return json.loads(self.client.get_object(Bucket=bucket, Key=key)["Body"].read().decode("utf-8"))


    def try_get_json(self, bucket: str, key: str) -> Any | None:
        """Como get_json, pero devuelve None si la clave no existe (para cachés)."""
        try:
            return self.get_json(bucket, key)
        except self.client.exceptions.NoSuchKey:
            return None

    def copy(self, src_bucket: str, src_key: str, dst_bucket: str, dst_key: str) -> None:
        """Copia dentro de S3 (no baja el fichero: lo copia S3 por dentro). `copy` hace copia multiparte si pesa mucho."""
        self.client.copy({"Bucket": src_bucket, "Key": src_key}, dst_bucket, dst_key)

    def delete_prefix(self, bucket: str, prefix: str) -> int:
        """Borra todo lo que empieza por `prefix` y devuelve cuántos objetos eran. El prefijo no puede ser vacío (borraría el bucket entero)."""
        if not prefix:
            raise ValueError("prefijo vacío: se borraría todo el bucket")
        deleted = 0
        for page in self.client.get_paginator("list_objects_v2").paginate(Bucket=bucket, Prefix=prefix):
            keys = [{"Key": item["Key"]} for item in page.get("Contents", [])]
            if keys:
                self.client.delete_objects(Bucket=bucket, Delete={"Objects": keys, "Quiet": True})
                deleted += len(keys)
        return deleted


class LocalStorage:
    """Imita un bucket con una carpeta: <raíz>/<bucket>/<clave>. Para probar sin AWS."""

    def __init__(self, root: str | Path):
        self.root = Path(root)

    def _path(self, bucket: str, key: str) -> Path:
        path = (self.root / bucket / key).resolve()
        if self.root.resolve() not in path.parents:  # una clave con ".." no debe salirse de la carpeta
            raise ValueError(f"clave fuera de la carpeta local: {key!r}")
        return path

    def download(self, bucket: str, key: str, path: Path) -> None:
        shutil.copyfile(self._path(bucket, key), path)

    def put_json(self, bucket: str, key: str, data: Any) -> None:
        target = self._path(bucket, key)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")

    def get_json(self, bucket: str, key: str) -> Any:
        return json.loads(self._path(bucket, key).read_text(encoding="utf-8"))

    def try_get_json(self, bucket: str, key: str) -> Any | None:
        try:
            return self.get_json(bucket, key)
        except FileNotFoundError:
            return None

    def copy(self, src_bucket: str, src_key: str, dst_bucket: str, dst_key: str) -> None:
        target = self._path(dst_bucket, dst_key)
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(self._path(src_bucket, src_key), target)

    def delete_prefix(self, bucket: str, prefix: str) -> int:
        if not prefix:
            raise ValueError("prefijo vacío: se borraría todo el bucket")
        base = self._path(bucket, ".keep").parent  # carpeta del bucket
        count = 0
        for path in list(base.rglob("*")):
            if path.is_file() and path.relative_to(base).as_posix().startswith(prefix):
                path.unlink()
                count += 1
        return count
