"""Personajes: unir las menciones de una misma persona en una sola ficha y enlazarla con sus hitos.

El rey Wu aparece como *Fa*, *King Wu*, *Ji Fa*, *武王*... en libros y capítulos distintos. Hay que unirlos, pero con cuidado:
mezclar dos personas distintas (dos "Wu" de épocas diferentes) es peor que partir una en dos, porque mezcla sus historias.
Por eso la regla es CONSERVADORA: se unen solo si comparten un nombre o alias, la época es compatible y, salvo coincidencia
exacta del nombre canónico, un modelo confirma que es la misma persona. Ante la duda, ficha separada.
"""

import hashlib
import re
import unicodedata
from collections import Counter
from dataclasses import dataclass, field
from typing import Callable

from openai import OpenAI
from pydantic import BaseModel, Field

from shared.llm import chat_params

ERA_MARGIN = 60  # años de holgura alrededor de la actividad conocida (una generación)
MIN_LATIN_FORM = 3  # un alias de 1-2 letras ("Wu") casaría con otras personas
OF_SUFFIX = re.compile(r"\s+(of|de)\s+\w+$", re.IGNORECASE)
CJK = re.compile(r"[一-鿿]")
DEFAULT_JUDGE_MODEL = "gpt-4o"
ORIGIN = "china-documents-reader"

FindByAliases = Callable[[set[str]], list[dict]]  # alias normalizados -> fichas ya guardadas que comparten alguno


class SamePerson(BaseModel):
    misma_persona: bool = Field(description="true solo si ambas fichas son SIN DUDA la misma persona histórica.")
    motivo: str = Field(description="Una frase explicando la decisión.")


JUDGE_PROMPT = (
    "Decides si dos fichas de personajes históricos de China corresponden a la MISMA persona. Fíjate en los nombres y alias, "
    "la época, el estado o dinastía y el papel. Hay muchos homónimos (varios 'rey Wu', 'duque Wen', 'emperador Wu', y dos 'Han Xin'): si la época, "
    "el estado o el papel no encajan, son personas DISTINTAS. Un título distinto (p. ej. 'marqués de Huaiyin' frente a 'rey de Han') o una grafía distinta del mismo nombre en el libro "
    "('Han Hsin' frente a 'Hann Hsin') son indicios de personas DISTINTAS. Ante cualquier duda razonable, responde que son distintas."
)


def norm_name(name: str) -> str:
    """Minúsculas, sin acentos ni signos ni espacios; conserva los caracteres chinos."""
    text = unicodedata.normalize("NFKD", name)
    text = "".join(ch for ch in text if not unicodedata.combining(ch))
    return re.sub(r"[\W_]+", "", text.lower())


def name_forms(p: dict) -> set[str]:
    """Todas las formas normalizadas por las que se puede reconocer a una persona (nombres, alias y sus variantes)."""
    raw = [p.get("nombre"), p.get("nombre_en"), p.get("nombre_zh"), *(p.get("alias") or [])]
    forms: set[str] = set()
    for name in raw:
        name = " ".join((name or "").split())
        if not name:
            continue
        for variant in {name, OF_SUFFIX.sub("", name)}:
            key = norm_name(variant)
            if key and (CJK.search(variant) or len(key) >= MIN_LATIN_FORM):
                forms.add(key)
    return forms


def build_aliases(p: dict) -> dict[str, list[str]]:
    """aliases {es, en, zh}: cada lista lleva su nombre canónico; los alias en caracteres latinos (pinyin) valen para es y en."""
    names = [" ".join(n.split()) for n in [p.get("nombre"), p.get("nombre_en"), p.get("nombre_zh"), *(p.get("alias") or [])] if n]
    chinese = sorted({n for n in names if CJK.search(n)})
    canonical = {" ".join((p.get("nombre") or "").split()), " ".join((p.get("nombre_en") or "").split())}
    extra = {n for n in names if not CJK.search(n)} - canonical  # sin esto, el nombre español acabaría en la lista inglesa
    return {
        "es": sorted(extra | ({" ".join(p["nombre"].split())} if p.get("nombre") else set())),
        "en": sorted(extra | ({" ".join(p["nombre_en"].split())} if p.get("nombre_en") else set())),
        "zh": chinese,
    }


def personaje_id(nombre_en: str, year: int, seen: set[str]) -> str:
    """Id legible y estable: slug del nombre + 6 hex. Se asigna al crear la ficha y no se recalcula."""
    ascii_name = unicodedata.normalize("NFKD", nombre_en).encode("ascii", "ignore").decode()
    slug = re.sub(r"[^a-z0-9]+", "-", ascii_name.lower()).strip("-")[:40] or "persona"
    salt = 0
    while True:
        digest = hashlib.sha1(f"{nombre_en}|{year}|{salt}".encode("utf-8")).hexdigest()[:6]
        candidate = f"{slug}-{digest}"
        if candidate not in seen:
            seen.add(candidate)
            return candidate
        salt += 1


def era_compatible(known: dict, year: int) -> bool:
    start, end = known.get("actividadInicio"), known.get("actividadFin")
    if start is None or end is None:
        return True  # sin época conocida no se puede descartar por época; decidirá el juez
    return start - ERA_MARGIN <= year <= end + ERA_MARGIN


def _brief(p: dict) -> str:
    life = f"{p.get('nacimiento')}..{p.get('muerte')}" if p.get("nacimiento") or p.get("muerte") else "años de vida desconocidos"
    activity = f"activo {p.get('actividadInicio')}..{p.get('actividadFin')}" if p.get("actividadInicio") is not None else ""
    return (
        f"Nombre: {p.get('nombre_en')} / {p.get('nombre')} / {p.get('nombre_zh')}\nAlias: {', '.join(p.get('alias') or [])}\n"
        f"Estado o dinastía: {p.get('estado_en')}\nPapel: {p.get('rol_en')}\nVida: {life} {activity}\nDescripción: {p.get('descripcion_en')}"
        + (f"\nHecho en el que sale ahora (con la frase del libro): {p['_contexto']}" if p.get("_contexto") else "")
    )


def judge(client: OpenAI, model: str, new: dict, existing: dict) -> SamePerson:
    completion = client.chat.completions.parse(
        model=model,
        messages=[
            {"role": "system", "content": JUDGE_PROMPT},
            {"role": "user", "content": f"FICHA NUEVA:\n{_brief(new)}\n\nFICHA EXISTENTE:\n{_brief(existing)}"},
        ],
        response_format=SamePerson,
        **chat_params(),
    )
    parsed = completion.choices[0].message.parsed
    if parsed is None:
        raise ValueError("El juez no devolvió el formato esperado")
    return parsed


def _state_compatible(a: dict, b: dict) -> bool:
    ea, eb = norm_name(a.get("estado_en") or ""), norm_name(b.get("estado_en") or "")
    return not ea or not eb or ea == eb


def _new_doc(p: dict, year: int, source: str, ids: set[str]) -> dict:
    activity_start = min([year, *(y for y in (p.get("nacimiento"), p.get("muerte")) if y is not None)])
    activity_end = max([year, *(y for y in (p.get("nacimiento"), p.get("muerte")) if y is not None)])
    doc = {
        "id": personaje_id(p["nombre_en"], year, ids),
        "nombre": p["nombre"], "nombre_en": p["nombre_en"], "nombre_zh": p.get("nombre_zh"),
        "aliases": build_aliases(p), "aliasNorm": sorted(name_forms(p)),
        "nacimiento": p.get("nacimiento"), "muerte": p.get("muerte"),
        "actividadInicio": activity_start, "actividadFin": activity_end,
        "rol": p["rol"], "rol_en": p["rol_en"], "rol_zh": p.get("rol_zh"),
        "estado": p.get("estado"), "estado_en": p.get("estado_en"), "estado_zh": p.get("estado_zh"),
        "descripcion": p["descripcion"], "descripcion_en": p["descripcion_en"], "descripcion_zh": p.get("descripcion_zh"),
        "fuente": source, "fuentes": [source], "origen": ORIGIN,
    }
    return {k: v for k, v in doc.items() if v is not None}


def _merge(doc: dict, p: dict, year: int, source: str) -> bool:
    """Añade a la ficha lo nuevo de otra mención (alias, huecos, época, libro). True si cambió algo."""
    before = repr(doc)
    known_aliases = [*doc.get("aliases", {}).get("es", []), *doc.get("aliases", {}).get("en", []), *doc.get("aliases", {}).get("zh", [])]
    new_names = [p.get("nombre"), p.get("nombre_en"), p.get("nombre_zh"), *(p.get("alias") or [])]
    doc["aliases"] = build_aliases(
        {"nombre": doc["nombre"], "nombre_en": doc["nombre_en"], "nombre_zh": doc.get("nombre_zh"), "alias": [*known_aliases, *new_names]}
    )
    doc["aliasNorm"] = sorted(set(doc.get("aliasNorm", [])) | name_forms(p))
    for key in ("nombre_zh", "nacimiento", "muerte", "estado", "estado_en", "estado_zh", "rol_zh", "descripcion_zh"):
        if doc.get(key) is None and p.get(key) is not None:
            doc[key] = p[key]
    points = [year, *(y for y in (p.get("nacimiento"), p.get("muerte")) if y is not None)]
    doc["actividadInicio"] = min([doc.get("actividadInicio", year), *points])
    doc["actividadFin"] = max([doc.get("actividadFin", year), *points])
    if source not in doc.get("fuentes", []):
        doc["fuentes"] = [*doc.get("fuentes", []), source]
    return repr(doc) != before


@dataclass
class Resolution:
    hitos: list[dict]  # los hitos con `personajes` [{id, rol, rol_en, rol_zh?, menciones}] ya enlazados
    docs: list[dict]  # fichas nuevas o modificadas (solo las de esta app) que hay que escribir
    stats: Counter = field(default_factory=Counter)
    log: list[str] = field(default_factory=list)


def resolve_personajes(
    hitos: list[dict],
    source: str,
    find_by_aliases: FindByAliases,
    client: OpenAI | None = None,
    judge_model: str = DEFAULT_JUDGE_MODEL,
    judge_fn: Callable[[dict, dict], SamePerson] | None = None,
) -> Resolution:
    judge_fn = judge_fn or (lambda new, old: judge(client, judge_model, new, old))
    known: dict[str, dict] = {}  # fichas en memoria por id: las de la base que han salido y las creadas en esta tanda
    ids: set[str] = set()
    dirty: set[str] = set()
    cache: dict[tuple[str, str], bool] = {}
    result = Resolution(hitos=[], docs=[])

    def candidates(p: dict, year: int) -> list[dict]:
        forms = name_forms(p)
        for doc in find_by_aliases(forms):
            known.setdefault(doc["id"], doc)
            ids.add(doc["id"])
        return [d for d in known.values() if forms & set(d.get("aliasNorm", [])) and era_compatible(d, year)]

    for hito in hitos:
        linked: dict[str, dict] = {}
        for p in hito.get("_personajes", []):
            year = hito["año"]
            match = None
            for cand in candidates(p, year):
                same_name = norm_name(p["nombre_en"]) == norm_name(cand.get("nombre_en", ""))
                key = (norm_name(p["nombre_en"]), cand["id"])
                # Unir SIN preguntar al juez solo si el nombre, la época y el estado encajan Y la mención no trae ninguna forma de nombre que la ficha no conozca ya. Dos homónimos
                # de la misma época y estado (p. ej. Han Xin, el general, y Han Xin, el rey de Han) pasaban esa regla antes y acababan en una sola ficha; si el libro los escribe
                # distinto ("Han Hsin" / "Hann Hsin"), esa forma nueva es la señal para preguntar. Ante la duda el juez dice "distintas" (mezclar es peor que partir).
                new_forms = name_forms(p) - set(cand.get("aliasNorm", []))
                if same_name and _state_compatible(p, cand) and not new_forms:
                    match, why = cand, "nombre canónico igual, época y estado compatibles, sin nombres nuevos"
                else:
                    p.setdefault("_contexto", f"{hito.get('titulo', '')} ({year}): {hito.get('evidencia') or ''}".strip())
                    if key not in cache:
                        verdict = judge_fn(p, cand)
                        cache[key] = verdict.misma_persona
                        result.stats["juez_llamadas"] += 1
                        if verdict.misma_persona:
                            result.log.append(f"juez: {p['nombre_en']} = {cand['nombre_en']} ({verdict.motivo})")
                    if cache[key]:
                        match, why = cand, "confirmado por el juez"
                if match:
                    break
            if match is None:
                match = _new_doc(p, year, source, ids)
                known[match["id"]] = match
                dirty.add(match["id"])
                result.stats["personajes_nuevos"] += 1
                result.log.append(f"nuevo: {match['nombre_en']} ({match['id']})")
            else:
                result.stats["personajes_unidos"] += 1
                # Solo se modifican las fichas de esta app: de otro origen se enlaza pero no se toca.
                if match.get("origen") == ORIGIN and _merge(match, p, year, source):
                    dirty.add(match["id"])
                result.log.append(f"unido: {p['nombre_en']} -> {match['nombre_en']} ({why})")
            link = {"id": match["id"], "rol": p["rol"], "rol_en": p["rol_en"], "menciones": p["menciones"]}
            if p.get("rol_zh"):
                link["rol_zh"] = p["rol_zh"]
            linked.setdefault(match["id"], link)  # dos menciones de la misma persona en un hito = un enlace
        enriched = {k: v for k, v in hito.items() if k != "_personajes"}
        enriched["personajes"] = list(linked.values())
        result.hitos.append(enriched)

    result.docs = [known[i] for i in sorted(dirty) if known[i].get("origen") == ORIGIN]
    return result
