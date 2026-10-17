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


class ExtractedPersonaje(BaseModel):
    """Una persona histórica con nombre propio que interviene en un hito."""

    en_el_texto: str = Field(
        description="El nombre TAL COMO está escrito en el texto original de los fragmentos (p. ej. 'Fa'), copiado literalmente."
    )
    nombre: str = Field(description="Nombre habitual en ESPAÑOL (p. ej. 'Rey Wu de Zhou').")
    nombre_en: str = Field(description="Nombre habitual en inglés (p. ej. 'King Wu of Zhou').")
    nombre_zh: str | None = Field(default=None, description="Nombre en caracteres chinos si lo conoces con seguridad (p. ej. '周武王'); si no, null.")
    alias: list[str] = Field(default_factory=list, description="Otros nombres con los que se le conoce, en cualquier idioma (Fa, Ji Fa, 武王, 姬发...).")
    rol: str = Field(description="Papel en ESTE hecho, 2-5 palabras en español (p. ej. 'líder de la conquista').")
    rol_en: str = Field(description="El mismo papel en inglés.")
    rol_zh: str | None = Field(default=None, description="El mismo papel en chino simplificado.")
    mencion_es: str | None = Field(
        default=None, description="Cómo aparece escrito en TU titulo o descripcion en español, copiado literalmente. null si no aparece."
    )
    mencion_en: str | None = Field(default=None, description="Cómo aparece en TU titulo_en o descripcion_en. null si no aparece.")
    mencion_zh: str | None = Field(default=None, description="Cómo aparece en TU titulo_zh o descripcion_zh. null si no aparece.")
    nacimiento: int | None = Field(default=None, description="Año de nacimiento SOLO si el texto lo dice (negativo si a.C.). Si no, null.")
    muerte: int | None = Field(default=None, description="Año de muerte SOLO si el texto lo dice (negativo si a.C.). Si no, null.")
    estado: str | None = Field(default=None, description="Estado o dinastía a la que pertenece, en español (p. ej. 'Zhou Occidental').")
    estado_en: str | None = Field(default=None, description="Lo mismo en inglés.")
    estado_zh: str | None = Field(default=None, description="Lo mismo en chino simplificado.")
    descripcion: str = Field(description="1-2 frases en ESPAÑOL sobre quién fue, solo con lo que dicen los fragmentos.")
    descripcion_en: str = Field(description="La misma descripción en inglés.")
    descripcion_zh: str | None = Field(default=None, description="La misma descripción en chino simplificado.")


class ExtractedHito(BaseModel):
    personajes: list[ExtractedPersonaje] = Field(
        default_factory=list,
        description=(
            "Personas históricas con nombre propio que intervienen en este hito (no 'el ejército'). Vacío si no hay ninguna. "
            "NUNCA autores modernos, arqueólogos o citas bibliográficas."
        ),
    )
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
    "Para cada hito indica también los personajes históricos con nombre propio que intervienen, con su papel en ese hecho. "
    "Cada personaje lleva su mención EXACTA tal como aparece escrita en tu titulo o descripcion de cada idioma, para poder "
    "enlazarla en el texto. Los años de nacimiento y muerte solo si el texto los dice.\n\n"
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


# "King Wu of Zhou" -> también "King Wu"; "Rey Wu de Zhou" -> "Rey Wu"
OF_SUFFIX = re.compile(r"\s+(of|de)\s+\w+$", re.IGNORECASE)
MIN_LATIN_FORM = 3  # un alias de 1-2 letras ("Wu", "Fa") casaría dentro de otros nombres; solo vale si viene literal del texto


def candidate_forms(p: "ExtractedPersonaje") -> list[str]:
    """Nombres con los que podría aparecer el personaje en un texto, del más largo al más corto."""
    names = [p.en_el_texto, p.nombre, p.nombre_en, p.nombre_zh, p.mencion_es, p.mencion_en, p.mencion_zh, *p.alias]
    forms: set[str] = set()
    for name in names:
        name = " ".join((name or "").split())
        if not name:
            continue
        for form in {name, OF_SUFFIX.sub("", name)}:
            if CJK.search(form) or len(form) >= MIN_LATIN_FORM or form == (p.en_el_texto or "").strip():
                forms.add(form)
    return sorted(forms, key=len, reverse=True)


def find_mention(text: str, forms: list[str]) -> str | None:
    """La forma más larga que aparece LITERALMENTE en el texto (tal como está escrita allí), o None."""
    for form in forms:
        if CJK.search(form):
            index = text.find(form)
            if index != -1:
                return text[index : index + len(form)]
            continue
        # Límites de palabra: "Wu" no debe casar dentro de "Wuhan".
        match = re.search(rf"(?<!\w){re.escape(form)}(?!\w)", text, re.IGNORECASE)
        if match:
            return match.group(0)
    return None


def clean_personaje(p: ExtractedPersonaje, hito_texts: dict[str, str], group_text: str) -> tuple[dict | None, str | None]:
    """(personaje validado, None) o (None, motivo). Solo se conserva lo que se puede comprobar en el texto."""
    if not p.nombre.strip() or not p.nombre_en.strip() or not p.rol.strip() or not p.descripcion.strip():
        return None, "personaje_campos_vacios"
    # Anti-invención: el nombre debe estar de verdad en el texto original de los fragmentos.
    flat_source = "".join(_tokens(group_text))
    wanted = "".join(_tokens(p.en_el_texto))
    if not wanted or wanted not in flat_source:
        return None, "personaje_no_esta_en_el_texto"
    # La mención se busca en código (no se fía de la del modelo, que no solía coincidir con el texto): la forma más
    # larga de sus nombres y alias que esté literalmente en el texto del hito en cada idioma. Sin ella, ese idioma no se enlaza.
    forms = candidate_forms(p)
    menciones = {lang: found for lang in ("es", "en", "zh") if (found := find_mention(hito_texts.get(lang, ""), forms))}
    if not menciones:
        return None, "personaje_sin_mencion_enlazable"
    birth = p.nacimiento if p.nacimiento is not None and MIN_YEAR <= p.nacimiento <= MAX_YEAR else None
    death = p.muerte if p.muerte is not None and MIN_YEAR <= p.muerte <= MAX_YEAR else None
    if birth is not None and death is not None and birth > death:
        birth = death = None  # incoherentes: mejor ninguno que uno equivocado
    zh = lambda value: value if has_cjk(value) else None  # noqa: E731 — el chino solo si trae caracteres chinos
    return {
        "nombre": p.nombre.strip(), "nombre_en": p.nombre_en.strip(), "nombre_zh": zh(p.nombre_zh),
        "alias": sorted({a.strip() for a in [p.en_el_texto, *p.alias] if a and a.strip()}),
        "rol": p.rol.strip(), "rol_en": p.rol_en.strip(), "rol_zh": zh(p.rol_zh),
        "menciones": menciones,
        "nacimiento": birth, "muerte": death,
        "estado": p.estado, "estado_en": p.estado_en, "estado_zh": zh(p.estado_zh),
        "descripcion": p.descripcion.strip(), "descripcion_en": p.descripcion_en.strip(), "descripcion_zh": zh(p.descripcion_zh),
    }, None


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
