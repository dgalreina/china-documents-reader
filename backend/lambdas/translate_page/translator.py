"""Traducir UNA página al español, con caché. Es independiente del pipeline de chunks/hitos: solo sirve para leer mejor.

La traducción no toca Mongo ni `book_chunks` (que siguen en inglés por contrato con china-history): se guarda aparte, una página por
fichero, en `translations/<slug>/<VERSIÓN>/<página>.json`. La VERSIÓN forma parte de la clave: si cambias el prompt o el modelo,
sube `PROMPT_VERSION` y las páginas se vuelven a traducir en lugar de servir las viejas.
"""

import os
import re
import time
from dataclasses import asdict, dataclass
from typing import Any

from shared.llm import chat_params
from shared.storage import Storage

DEFAULT_TRANSLATE_MODEL = "gpt-4o"
PROMPT_VERSION = "v2"
# Una página normal son ~2.500-4.000 caracteres; si una trae mucho más (tabla enorme), se corta antes de pagarla.
MAX_PAGE_CHARS = 12000

SYSTEM_PROMPT = """Eres un traductor de historia de China del inglés al español. Traduces UNA página de un libro académico para que \
un lector hispanohablante la lea de corrido.

Reglas:
- Traduce todo el texto al español, con precisión y en un registro académico claro. No resumas, no añadas ni quites contenido, no \
comentes nada: devuelve SOLO la traducción.
- El texto viene de un PDF: las líneas están cortadas a mitad de frase y hay palabras partidas con guion al final de línea. Une las \
líneas y reconstruye los párrafos; separa los párrafos con UNA línea en blanco. Un título de sección va en su propia línea.
- Omite el encabezado corrido y el número de página que se repiten en lo alto de cada página (p. ej. "368 MARIA KHAYUTINA"). Conserva \
las citas entre paréntesis y las notas al pie si las hay.
- NÚMEROS DE NOTA: en el texto extraído vienen como dígitos pegados a la palabra o al punto (p. ej. «sacrifices.24» es la nota 24). \
Cópialos EXACTOS, cifra por cifra, como superíndice (sacrificios.²⁴). Nunca los cambies, renumeres ni inventes: en la traducción tienen que \
aparecer los mismos números de nota, en el mismo orden, que en el original; y lo mismo con los números de las notas al pie del final.
- Los CARACTERES CHINOS NO se traducen: se dejan tal cual. Detrás de cada término, nombre de persona, lugar, dinastía o título en \
caracteres chinos, añade entre paréntesis qué es, en español y con pinyin: «周武王 (Zhou Wuwang, el rey Wu de Zhou)», «殷 (Yin, nombre \
de la dinastía Shang)». Si ya viene una transcripción inmediatamente delante (p. ej. «Zhou 周»), no la dupliques: escribe «Zhou 周 \
(dinastía Zhou)» o, si no aporta nada, déjalo como está. Si un mismo término chino se repite en la página, la aclaración solo hace falta \
en la primera aparición.
- Los nombres propios transcritos (Zhou, Shang, Sima Qian, Luoyi…) se dejan como están en pinyin, salvo los que en español tienen forma \
tradicional (Confucio, río Amarillo). Los años «BCE/CE» pasan a «a. C./d. C.».
- Si el texto de la página está vacío o es solo un número, devuelve una cadena vacía."""


@dataclass
class PageTranslation:
    page: int
    text: str  # la traducción
    model: str
    promptVersion: str
    inputTokens: int = 0
    outputTokens: int = 0
    seconds: float = 0.0  # lo que tardó la llamada (0 si salió de la caché)
    cached: bool = False


def cache_key(slug: str, page: int, version: str = PROMPT_VERSION) -> str:
    return f"translations/{slug}/{version}/{page:04d}.json"


_SLUG = re.compile(r"^[a-z0-9][a-z0-9-]{1,59}$")


def is_valid_slug(slug: str) -> bool:
    return bool(_SLUG.match(slug))


def lookup_cached(storage: Storage, bucket: str, slug: str, page: int, model: str | None = None) -> PageTranslation | None:
    """La traducción ya guardada de una página (hecha con este modelo), o None. No llama a OpenAI ni abre el PDF."""
    model = model or os.environ.get("TRANSLATE_MODEL", DEFAULT_TRANSLATE_MODEL)
    hit = storage.try_get_json(bucket, cache_key(slug, page))
    if hit is not None and hit.get("model") == model:
        return PageTranslation(**{**hit, "seconds": 0.0, "cached": True})
    return None


def translate_page(
    client: Any,
    storage: Storage,
    bucket: str,
    slug: str,
    page: int,
    text: str,
    model: str | None = None,
) -> PageTranslation:
    """Devuelve la traducción de una página: de la caché si ya está, y si no la pide al modelo y la guarda."""
    if not is_valid_slug(slug):
        raise ValueError(f"slug no válido: {slug!r}")  # va dentro de una clave de S3: nada de '/' ni '..'
    if page < 1:
        raise ValueError(f"número de página no válido: {page}")
    model = model or os.environ.get("TRANSLATE_MODEL", DEFAULT_TRANSLATE_MODEL)

    cached = lookup_cached(storage, bucket, slug, page, model)
    if cached is not None:
        return cached
    key = cache_key(slug, page)

    text = text.strip()
    if not text:  # página sin texto (imagen, en blanco): no hay nada que pagar
        result = PageTranslation(page=page, text="", model=model, promptVersion=PROMPT_VERSION)
        storage.put_json(bucket, key, asdict(result))
        return result

    started = time.perf_counter()
    completion = client.chat.completions.create(
        model=model,
        messages=[
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": text[:MAX_PAGE_CHARS]},
        ],
        **chat_params(),
    )
    seconds = time.perf_counter() - started
    usage = getattr(completion, "usage", None)
    result = PageTranslation(
        page=page,
        text=(completion.choices[0].message.content or "").strip(),
        model=model,
        promptVersion=PROMPT_VERSION,
        inputTokens=getattr(usage, "prompt_tokens", 0) or 0,
        outputTokens=getattr(usage, "completion_tokens", 0) or 0,
        seconds=round(seconds, 2),
    )
    storage.put_json(bucket, key, asdict(result))
    return result
