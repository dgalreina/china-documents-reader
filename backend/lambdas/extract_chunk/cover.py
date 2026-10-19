"""La portada de un libro: su primera página, dibujada como una imagen pequeña (JPEG).

Se genera UNA vez, en el primer paso del flujo (que ya tiene el PDF abierto), y se guarda para que la web enseñe una miniatura en la lista de libros y en el
progreso sin tener que abrir ningún PDF. `pypdfium2` lleva dentro el motor de PDF de Chrome: dibuja la página tal cual se ve (con sus imágenes y tipografías),
cosa que pypdf, que solo extrae texto, no puede hacer. Una portada que falle NUNCA debe tumbar el análisis del libro: quien la llama la trata como opcional.
"""

import io
from pathlib import Path

COVER_WIDTH = 360  # px: sobra para una miniatura de lista y pantallas de alta densidad (se enseña a ~56-90 px)
COVER_QUALITY = 82  # JPEG: ~30 KB por portada
COVER_KEY_NAME = "cover.jpg"


def render_cover(pdf_path: str | Path, width: int = COVER_WIDTH, quality: int = COVER_QUALITY) -> bytes:
    """JPEG con la primera página del PDF, de `width` píxeles de ancho. Lanza una excepción si el PDF no se puede dibujar."""
    import pypdfium2 as pdfium  # importación tardía: solo la usa este paso, y así no ralentiza el arranque de las demás Lambdas

    document = pdfium.PdfDocument(str(pdf_path))
    try:
        page = document[0]
        bitmap = page.render(scale=width / page.get_width())
        image = bitmap.to_pil().convert("RGB")  # JPEG no admite transparencia
        buffer = io.BytesIO()
        image.save(buffer, "JPEG", quality=quality, optimize=True)
        return buffer.getvalue()
    finally:
        document.close()
