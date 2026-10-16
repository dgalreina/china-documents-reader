"""Paso 4a: pedirle al modelo los hitos históricos de un grupo de chunks (en español, inglés y chino).

Un hito es un hecho concreto con lugar y año (una batalla, la fundación de una ciudad...), que se pinta como
un punto en el mapa de china-history. Los campos base van en ESPAÑOL (idioma por defecto de la app) y las
versiones `_en` y `_zh` se generan en la misma llamada, viendo el texto original.
"""

import re

from openai import OpenAI
from pydantic import BaseModel, Field

# Un hito debe caer dentro de los años del grupo de chunks, con este margen (en años).
YEAR_MARGIN = 100
MIN_YEAR = -10000
MAX_YEAR = 3000

EVIDENCE_MIN_OVERLAP = 0.8  # fracción de palabras de la cita que deben estar en el texto

CJK = re.compile(r"[一-鿿]")


class ExtractedHito(BaseModel):
    evidencia: str = Field(
        description=(
            "Copia LITERAL, en el idioma original del texto, de la frase (o dos) que dice a la vez QUÉ ocurrió y DÓNDE. "
            "El nombre del lugar elegido tiene que aparecer en esa frase."
        )
    )
    sitio: str = Field(
        description=(
            "Nombre en pinyin, tal como aparece en el texto, del lugar CONCRETO donde ocurrió (ciudad, capital, "
            "yacimiento, campo de batalla). NUNCA un estado, reino, dinastía o región (Qin, Zhou, Western Zhou...)."
        )
    )
    sitio_moderno: str | None = Field(
        default=None,
        description=(
            "Nombre ACTUAL de ese lugar en pinyin solo si difiere del histórico (Chang'an -> Xi'an, Luoyi -> Luoyang); "
            "ayuda a ubicarlo cuando el nombre antiguo no existe en los mapas. null si coincide o no lo sabes."
        ),
    )
    provincia: str | None = Field(description="Provincia actual de China donde está ese lugar, en pinyin inglés (Henan, Shaanxi...). null si no estás seguro.")
    ano: int = Field(description="Año del evento; negativo si es a.C. Si solo se conoce la década o el siglo, el año central.")
    titulo: str = Field(description="Título corto en ESPAÑOL, 4-8 palabras.")
    descripcion: str = Field(description="1-2 frases en ESPAÑOL describiendo el hito.")
    lugar: str = Field(description="Lugar en ESPAÑOL con su provincia, p. ej. 'Luoyang, Henan'.")
    titulo_en: str = Field(description="El mismo título en inglés.")
    descripcion_en: str = Field(description="La misma descripción en inglés.")
    lugar_en: str = Field(description="El mismo lugar en inglés, p. ej. 'Luoyang, Henan'.")
    titulo_zh: str = Field(description="El mismo título en chino simplificado.")
    descripcion_zh: str = Field(description="La misma descripción en chino simplificado.")
    lugar_zh: str = Field(description="El mismo lugar en chino simplificado (caracteres, p. ej. '河南洛阳').")


class HitosBatch(BaseModel):
    hitos: list[ExtractedHito]


SYSTEM_PROMPT = (
    "Lees fragmentos de un libro de historia de China y extraes ÚNICAMENTE hitos históricos concretos y datables: "
    "eventos que ocurrieron en un lugar real y en un año (o década) identificables — batallas, fundaciones o caídas "
    "de ciudades o capitales, traslados de una corte, revueltas, tratados, construcciones significativas con fecha. "
    "NO extraigas descripciones genéricas de costumbres, economía o sociedad sin fecha y lugar concretos, ni "
    "generalidades sobre 'la vida en esta época'. Si los fragmentos no contienen ningún hito así, devuelve una lista "
    "vacía: es preferible pocos hitos sólidos que muchos dudosos.\n\n"
    "El lugar es donde OCURRIÓ el evento, no la capital ni la base del bando que lo protagoniza: una conquista ocurre donde está la ciudad conquistada o el campo de batalla; una muerte, donde murió, no donde se instaló luego la corte. Cita en `evidencia` la frase literal que da el hecho y el lugar; si no hay una frase que los una, no lo extraigas.\n\n"
    "El lugar debe ser un sitio concreto (ciudad, capital, yacimiento, campo de batalla). Si el evento se atribuye solo "
    "a un estado, reino o dinastía (Qin, Zhou, 'el imperio') sin un lugar concreto, NO lo extraigas: un estado no es "
    "un punto en el mapa.\n\n"
    "Si el libro es arqueológico o académico (citas de excavaciones, dataciones por radiocarbono, nombres de "
    "arqueólogos, años de publicación): eso NO son hitos. El año de un hito es cuándo ocurrió el evento HISTÓRICO "
    "(una fundación, una batalla, un entierro real, la caída de una capital), nunca el año en que un arqueólogo "
    "moderno lo excavó, lo dató o lo publicó. Sí son hitos válidos: la fundación o abandono fechado de una capital o "
    "asentamiento, un entierro real o de élite concreto y fechado, una batalla o conquista mencionada en los textos "
    "o inscripciones antiguos, el traslado de una corte.\n\n"
    "Años a.C. en negativo (221 a.C. = -221). Da cada hito en español (campos base), en inglés (_en) y en chino "
    "simplificado (_zh); usa los nombres de lugar chinos con sus caracteres correctos en _zh."
)


def year_in_range(year: int, year_from: int, year_to: int) -> bool:
    return MIN_YEAR <= year <= MAX_YEAR and year_from - YEAR_MARGIN <= year <= year_to + YEAR_MARGIN


def _tokens(text: str) -> list[str]:
    # Quita guiones de maquetación del PDF ("cul- tural") y signos; solo letras minúsculas.
    return re.sub(r"[^a-z\s]", " ", re.sub(r"-\s+", "", text.lower())).split()


def evidence_is_valid(evidence: str, place_names: list[str], source_text: str) -> bool:
    """La cita debe estar en el texto (tolerando el ruido del PDF) y nombrar el lugar elegido."""
    quote, source = _tokens(evidence), set(_tokens(source_text))
    if len(quote) < 4:
        return False
    found = sum(1 for word in quote if word in source)
    if found / len(quote) < EVIDENCE_MIN_OVERLAP:
        return False
    flat = "".join(quote)  # sin espacios: 'Hao' casa con 'Haojing', y 'Chang an' con 'Chang'an'
    return any("".join(_tokens(name)) and "".join(_tokens(name)) in flat for name in place_names if name)


def has_cjk(text: str | None) -> bool:
    return bool(text and CJK.search(text))


def extract_group(client: OpenAI, model: str, texts: list[str]) -> list[ExtractedHito]:
    """Hitos de un grupo de chunks (sin validar años ni geocodificar)."""
    body = "\n\n".join(f"--- Fragmento {i} ---\n{text}" for i, text in enumerate(texts, start=1))
    completion = client.chat.completions.parse(
        model=model,
        messages=[{"role": "system", "content": SYSTEM_PROMPT}, {"role": "user", "content": body}],
        response_format=HitosBatch,
    )
    parsed = completion.choices[0].message.parsed
    if parsed is None:
        raise ValueError("Respuesta sin el formato esperado")
    return parsed.hitos
