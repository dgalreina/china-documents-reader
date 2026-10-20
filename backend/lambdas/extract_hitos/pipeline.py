"""Paso 4: de los chunks ya etiquetados a hitos listos para guardar en hitos_conocidos.

Entrada: los chunks `ready` de embed_label (con su rango de años). Salida: hitos geocodificados y una lista
de descartados con su motivo, para poder revisar qué se pierde. Lógica pura: no toca Mongo.
"""

import os
from collections import Counter
from dataclasses import dataclass, field, replace

from openai import OpenAI

from shared.geocoder import Geocoder, normalize

from ..embed_label.pipeline import make_client
from .extractor import ExtractedHito, clean_personaje, evidence_is_valid, extract_group, has_cjk, locate_page, year_in_range
from .ids import hito_id

DEFAULT_HITOS_MODEL = "gpt-4o"
GROUP_SIZE = 5  # chunks consecutivos por llamada (~6000 caracteres): contexto suficiente sin gastar de más
ORIGIN = "china-documents-reader"  # marca propia: permite distinguir (y no tocar) los hitos de china-history
MAX_EVIDENCE_CHARS = 500  # tope de la cita guardada


@dataclass
class HitosResult:
    hitos: list[dict] = field(default_factory=list)
    discarded: list[dict] = field(default_factory=list)  # {"reason", "titulo", "año", "sitio", "provincia"}
    stats: Counter = field(default_factory=Counter)


def _discard(result: HitosResult, reason: str, hito: ExtractedHito) -> None:
    result.stats[reason] += 1
    result.discarded.append(
        {
            "reason": reason,
            "titulo": hito.titulo,
            "año": hito.ano,
            "sitio": hito.sitio,
            "sitio_moderno": hito.sitio_moderno,
            "provincia": hito.provincia,
        }
    )


def extract_hitos(
    chunks: list[dict],
    source: str,
    client: OpenAI | None = None,
    model: str | None = None,
    geocoder: Geocoder | None = None,
) -> HitosResult:
    client = client or make_client()
    model = model or os.environ.get("HITOS_MODEL", DEFAULT_HITOS_MODEL)
    geocoder = geocoder or Geocoder.from_env()
    result = HitosResult()
    ordered = sorted(chunks, key=lambda c: c["chunkIndex"])
    seen_ids: set[str] = set()
    seen_events: set[tuple[str, int]] = set()  # (sitio normalizado, año): los chunks se solapan y repiten hitos

    for start in range(0, len(ordered), GROUP_SIZE):
        group = ordered[start : start + GROUP_SIZE]
        year_from = min(c["añoInicio"] for c in group)
        year_to = max(c["añoFin"] for c in group)
        try:
            extracted = extract_group(client, model, [c["text"] for c in group], (year_from, year_to))
        except Exception:  # noqa: BLE001 — un grupo malo no debe tumbar el libro
            result.stats["grupo_con_error"] += 1
            continue
        result.stats["extraidos"] += len(extracted)

        for hito in extracted:
            if not hito.sitio.strip() or not hito.titulo.strip() or not hito.descripcion.strip() or not hito.lugar.strip():
                _discard(result, "campos_vacios", hito)
                continue
            if not year_in_range(hito.ano, year_from, year_to):
                _discard(result, "año_fuera_del_rango_del_texto", hito)
                continue
            group_text = "\n".join(c["text"] for c in group)
            if not evidence_is_valid(hito.evidencia, [hito.sitio, hito.sitio_moderno or ""], group_text):
                _discard(result, "evidencia_no_respalda_el_lugar", hito)
                continue
            event_key = (normalize(hito.sitio), hito.ano)
            if event_key in seen_events:
                _discard(result, "duplicado", hito)
                continue
            geo = geocoder.geocode(hito.sitio, hito.provincia, hito.ano, hito.ano)
            if geo is None and hito.sitio_moderno and hito.sitio_moderno.strip().lower() != hito.sitio.strip().lower():
                # Como china-history (`lugarModerno`): segundo intento con el nombre actual. Las mismas reglas de seguridad.
                geo = geocoder.geocode(hito.sitio_moderno, hito.provincia, hito.ano, hito.ano)
                if geo is not None:
                    geo = replace(geo, matched_name=f"{geo.matched_name} [vía nombre actual de {hito.sitio}]")
                    result.stats["geo_via_nombre_moderno"] += 1
            if geo is None:
                _discard(result, "sin_coordenadas", hito)
                continue
            seen_events.add(event_key)

            doc = {
                "id": hito_id(hito.titulo, hito.ano, hito.lugar, geo.lat, geo.lng, seen_ids),
                "lugar": hito.lugar,
                "año": hito.ano,
                "titulo": hito.titulo,
                "descripcion": hito.descripcion,
                "fuente": source,
                "lugar_en": hito.lugar_en,
                "titulo_en": hito.titulo_en,
                "descripcion_en": hito.descripcion_en,
                "location": {"type": "Point", "coordinates": [geo.lng, geo.lat]},  # GeoJSON: [lng, lat]
                "origen": ORIGIN,
                "ubicacionFuente": geo.source,
                "ubicacionCoincidencia": geo.matched_name,
                "ubicacionPrecision": geo.precision,
                # La frase del libro que respalda el hecho: sirve para auditar el hito y, más adelante, para resaltarla en el visor.
                "evidencia": " ".join(hito.evidencia.split())[:MAX_EVIDENCE_CHARS],
            }
            # Página del libro (la del párrafo que contiene la cita). Opcional: sin ella el hito se guarda igual, solo que la web no puede saltar a su página.
            pagina = locate_page(hito.evidencia, group)
            if pagina is None:
                result.stats["sin_pagina"] += 1
            else:
                doc["pagina"] = pagina
            # El chino es opcional: si el modelo no devolvió caracteres chinos, mejor sin él que con texto equivocado.
            for key in ("lugar_zh", "titulo_zh", "descripcion_zh"):
                value = getattr(hito, key)
                if has_cjk(value):
                    doc[key] = value
                else:
                    result.stats["zh_descartado"] += 1
            # Personajes: solo los comprobables en el texto. Aún sin resolver (el paso siguiente los une con las fichas).
            texts = {
                "es": f"{hito.titulo} {hito.descripcion}",
                "en": f"{hito.titulo_en} {hito.descripcion_en}",
                "zh": f"{hito.titulo_zh} {hito.descripcion_zh}",
            }
            doc["_personajes"] = []
            for person in hito.personajes:
                clean, reason = clean_personaje(person, texts, group_text)
                if clean is None:
                    result.stats[reason] += 1
                else:
                    doc["_personajes"].append(clean)
                    result.stats["personajes"] += 1
            result.hitos.append(doc)
            result.stats[f"geo_{geo.source}"] += 1
    result.stats["guardables"] = len(result.hitos)
    return result
