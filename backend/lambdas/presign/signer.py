"""Paso 0: dar al navegador permiso temporal para subir UN PDF directamente a S3.

La idea: el navegador no sube el archivo a nuestra Lambda (pasar un PDF grande por API Gateway tiene límites y cuesta), sino
directo a S3, con una "URL prefirmada": un permiso firmado por nosotros que S3 acepta durante unos minutos y SOLO para lo que
dijimos (esa clave, solo PDF, hasta un tamaño máximo). La Lambda solo firma; no ve el archivo.
"""

import os
import re
import unicodedata
import uuid

# Tamaño máximo de un PDF. El del Oxford Handbook completo (825 páginas) pesa 10 MB.
DEFAULT_MAX_MB = 100
URL_TTL_SECONDS = 300  # la URL firmada deja de valer a los 5 minutos
MAX_FILENAME_LENGTH = 100
CONTENT_TYPE = "application/pdf"


class InvalidUpload(ValueError):
    """La petición de subida no es válida (se responde 400 con el mensaje)."""


def max_bytes() -> int:
    return int(os.environ.get("MAX_PDF_MB", DEFAULT_MAX_MB)) * 1024 * 1024


def safe_filename(name: object) -> str:
    """Nombre de fichero seguro para usar en una clave de S3: sin rutas, sin acentos, sin caracteres raros, y acabado en .pdf."""
    if not isinstance(name, str) or not name.strip():
        raise InvalidUpload("Falta el nombre del archivo (filename).")
    base = re.split(r"[\\/]", name.strip())[-1]  # sin carpetas: 'C:\\x\\libro.pdf' y '../../libro.pdf' -> 'libro.pdf'
    if not base.lower().endswith(".pdf"):
        raise InvalidUpload("Solo se aceptan archivos PDF (el nombre debe acabar en .pdf).")
    stem = base[:-4]
    ascii_stem = unicodedata.normalize("NFKD", stem).encode("ascii", "ignore").decode()
    clean = re.sub(r"[^A-Za-z0-9._-]+", "-", ascii_stem).strip("-.")
    clean = clean[: MAX_FILENAME_LENGTH - 4] or "documento"
    return f"{clean}.pdf"


def create_upload(s3_client, bucket: str, filename: object) -> dict:
    """Devuelve lo que necesita el navegador para subir el PDF: {jobId, key, maxBytes, upload: {url, fields}}.

    Se usa POST prefirmado y no PUT porque permite exigir condiciones: tipo PDF y rango de tamaño. S3 las comprueba por su cuenta.
    """
    safe = safe_filename(filename)
    job_id = uuid.uuid4().hex
    key = f"uploads/{job_id}/{safe}"
    limit = max_bytes()
    post = s3_client.generate_presigned_post(
        Bucket=bucket,
        Key=key,
        Fields={"Content-Type": CONTENT_TYPE},
        Conditions=[{"Content-Type": CONTENT_TYPE}, ["content-length-range", 1, limit]],
        ExpiresIn=URL_TTL_SECONDS,
    )
    return {"jobId": job_id, "key": key, "maxBytes": limit, "expiresInSeconds": URL_TTL_SECONDS, "upload": post}


SOURCE_PATTERN = re.compile(r"^[a-z0-9][a-z0-9-]{1,59}$")


def slugify(text: str) -> str:
    """'Historia de China (2ª ed.)' -> 'historia-de-china-2a-ed': minúsculas, sin acentos, solo letras, números y guiones."""
    ascii_text = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode().lower()
    return re.sub(r"[^a-z0-9]+", "-", ascii_text).strip("-")[:60].strip("-")


def resolve_source(source: object, filename: object) -> str:
    """El slug del libro: el que pida el usuario (validado) o, si no, uno derivado del nombre del archivo."""
    if source is not None:
        if not isinstance(source, str) or not SOURCE_PATTERN.match(source):
            raise InvalidUpload("source debe ser un slug: minúsculas, números y guiones (2 a 60 caracteres).")
        return source
    safe_filename(filename)  # valida que sea un nombre .pdf
    # Del nombre ORIGINAL, no del saneado: un nombre sin letras latinas queda como 'documento', un slug genérico donde dos libros
    # distintos chocarían (y un reemplazo borraría el otro). Mejor pedir el slug a mano.
    base = re.split(r"[\\/]", filename.strip())[-1][:-4]
    derived = slugify(base)
    if not SOURCE_PATTERN.match(derived):
        raise InvalidUpload("No se pudo derivar un slug del nombre del archivo: indica 'source' a mano.")
    return derived


MAX_TITLE_LENGTH = 120


def resolve_title(title: object, filename: object) -> str:
    """El título que se enseña en la lista de libros: el que escribe el usuario (validado) o, si no, uno legible sacado del nombre del archivo."""
    if title is not None:
        if not isinstance(title, str) or not title.strip():
            raise InvalidUpload("title debe ser un texto no vacío.")
        clean = " ".join(title.split())  # sin saltos de línea ni espacios repetidos
        if len(clean) > MAX_TITLE_LENGTH or any(ord(char) < 32 for char in clean):
            raise InvalidUpload(f"title no puede pasar de {MAX_TITLE_LENGTH} caracteres.")
        return clean
    safe_filename(filename)  # valida que sea un .pdf
    base = re.split(r"[\\/]", str(filename).strip())[-1][:-4]
    readable = " ".join(re.sub(r"[_]+", " ", base).split())[:MAX_TITLE_LENGTH]
    return (readable[:1].upper() + readable[1:]) or "Documento"
