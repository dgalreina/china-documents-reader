"""Geocodificación de lugares históricos de China: nombre -> (lat, lng).

Se prueban varias fuentes, de más a menos fiable. Si ninguna da un resultado CLARO, se devuelve
None: nunca una coordenada aproximada o inventada (contrato con china-history).

1. Listas curadas de china-history (yacimientos.json y capitales.json): pocas entradas pero verificadas.
2. CHGIS: miles de condados y prefecturas, solo de 221 a.C. a 1911 d.C. Cruce exacto y sin ambigüedad.
3. Wikidata (la base estructurada de Wikipedia): coordenadas de la mayoría de yacimientos conocidos. Un único candidato,
   nombre parecido y provincia confirmada (por su descripción o por geocodificación inversa de Mapbox).
4. Mapbox: casi cualquier sitio actual, pero se equivoca con nombres históricos. Solo se acepta si
   el resultado cae en la provincia que se esperaba (evita "Yulin en Guangxi frente a Yulin en Shaanxi").

Los datos de CHGIS NO se redistribuyen: se leen desde fuera del repo (ver "Licencias" en CLAUDE.md).
"""

import json
import math
import os
import re
import unicodedata
import urllib.parse
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

# Caja que contiene China y su entorno: fuera de ella un resultado se descarta por absurdo.
LAT_RANGE = (15.0, 55.0)
LNG_RANGE = (70.0, 140.0)

# Sufijos administrativos de los nombres pinyin de CHGIS ("Qiantang Xian" -> "qiantang").
ADMIN_SUFFIXES = {"xian", "zhou", "fu", "jun", "ting", "wei", "dao", "lu", "sheng", "xiang", "zhen", "shi", "qu"}
# Palabras que el modelo añade al nombre de un sitio ("Erlitou site") y que estorban al comparar.
GENERIC_WORDS = {"site", "sites", "ruins", "ruin", "city", "culture", "archaeological", "settlement", "the"}
# "Lugares" que no son un sitio concreto: un punto sería arbitrario.
GENERIC_PLACE = re.compile(r"^(china|empire|dynasty|kingdom|state of|reino|estado|dinast)", re.IGNORECASE)
# Un estado, reino o dinastía no es un punto en el mapa, aunque su nombre coincida con un pueblo (Qin -> Qin Xian).
POLITY_WORDS = re.compile(
    r"\b(state|kingdom|dynasty|empire|principality|royal domain|reino|estado|dinast\w*|principado|imperio)\b", re.IGNORECASE
)
# Descripciones de Wikidata que empiezan por una entidad que NO es un lugar concreto (provincia, estado, región...).
NOT_A_SITE_DESCRIPTION = re.compile(
    r"^(former |ancient |historical |chinese )*(country|state|kingdom|empire|dynasty|province|autonomous region|region|"
    r"principality|period|municipality|prefecture)\b",
    re.IGNORECASE,
)

# Tipos de resultado de Mapbox aceptables (un lugar concreto). Se excluyen "region" y "country".
MAPBOX_PLACE_TYPES = {"place", "locality", "district", "neighborhood", "poi"}
MAPBOX_MIN_RELEVANCE = 0.8

HttpGet = Callable[[str], dict]


@dataclass(frozen=True)
class GeoResult:
    lat: float
    lng: float
    source: str  # "curated" | "chgis" | "wikidata" | "mapbox"
    matched_name: str


def normalize(name: str) -> str:
    """Minúsculas, sin acentos ni signos, para que variantes del mismo nombre coincidan."""
    text = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode()
    return re.sub(r"\s+", " ", re.sub(r"[^a-z\s]", " ", text.lower())).strip()


def _strip_admin(key: str) -> str:
    parts = key.split()
    if len(parts) > 1 and parts[-1] in ADMIN_SUFFIXES:
        return " ".join(parts[:-1])
    return key


def _site_key(name: str) -> str:
    words = [w for w in normalize(name).split() if w not in GENERIC_WORDS]
    return " ".join(words)


def _similar(a: str, b: str) -> bool:
    """Nombres parecidos: uno contiene al otro, ignorando espacios (Yin Xu ~ Yinxu)."""
    a, b = _site_key(a).replace(" ", ""), _site_key(b).replace(" ", "")
    return bool(a and b and (a in b or b in a))


def _distance_km(a: tuple[float, float], b: tuple[float, float]) -> float:
    """Distancia aproximada entre dos (lat, lng) en km (haversine)."""
    lat1, lng1, lat2, lng2 = map(math.radians, (*a, *b))
    h = math.sin((lat2 - lat1) / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin((lng2 - lng1) / 2) ** 2
    return 2 * 6371 * math.asin(math.sqrt(h))


# Candidatos de Wikidata a menos de esta distancia entre sí se consideran el mismo lugar
# (el yacimiento, su museo, la estación de metro, el pueblo...); más lejos es ambiguo.
SAME_PLACE_KM = 25
SITE_WORDS = ("archaeolog", "archeolog", "site", "ruins", "ancient", "neolithic", "bronze age", "capital")
# Cosas que llevan el nombre del lugar pero no son el lugar (el museo del yacimiento, una estación de metro...).
NOT_THE_PLACE_WORDS = ("museum", "station", "railway", "subdistrict", "township")


def _site_priority(candidate: dict) -> int:
    """0 = parece el yacimiento en sí; 1 = una cultura; 2 = otra cosa (pueblo, estación, museo)."""
    text = f"{candidate.get('label', '')} {candidate.get('description', '')}".lower()
    if any(word in text for word in NOT_THE_PLACE_WORDS):
        return 2
    if "culture" in candidate.get("label", "").lower():
        return 1
    return 0 if any(word in text for word in SITE_WORDS) else 2


def _same_name(site: str, found: str) -> bool:
    """Como _similar, pero un nombre corto (Qi, Wei, Jin) debe coincidir exacto: contenerlo no basta."""
    a, b = _site_key(site).replace(" ", ""), _site_key(found).replace(" ", "")
    if not a or not b:
        return False
    return a == b if min(len(a), len(b)) <= 3 else (a in b or b in a)


def _in_china(lat: float, lng: float) -> bool:
    return LAT_RANGE[0] <= lat <= LAT_RANGE[1] and LNG_RANGE[0] <= lng <= LNG_RANGE[1]


def _province_key(province: str) -> str:
    key = normalize(province)
    return re.sub(r"\b(province|autonomous region|municipality|sheng|zhixiashi)\b", "", key).strip()


def _representative_point(geometry: dict) -> tuple[float, float] | None:
    """(lat, lng) de una geometría GeoJSON: el propio punto, o el centro del contorno principal."""
    kind, coords = geometry.get("type"), geometry.get("coordinates")
    if kind == "Point":
        return coords[1], coords[0]
    if kind == "Polygon":
        rings = [coords[0]]
    elif kind == "MultiPolygon":
        rings = [polygon[0] for polygon in coords]
    else:
        return None

    def bbox_area(ring):
        lngs, lats = [p[0] for p in ring], [p[1] for p in ring]
        return (max(lngs) - min(lngs)) * (max(lats) - min(lats))

    main = max(rings, key=bbox_area)  # en un multipolígono, la parte más grande
    return sum(p[1] for p in main) / len(main), sum(p[0] for p in main) / len(main)


# Wikimedia exige identificar la aplicación en el User-Agent (sin datos personales).
USER_AGENT = "china-documents-reader/0.1 (https://github.com/dgalreina/china-documents-reader; proyecto personal sin fines comerciales)"
WIKIDATA_API = "https://www.wikidata.org/w/api.php"


def _default_http_get(url: str) -> dict:
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(request, timeout=15) as response:  # noqa: S310 — URLs fijas de Mapbox y Wikidata
        return json.loads(response.read().decode("utf-8"))


class Geocoder:
    def __init__(
        self,
        curated_dir: str | Path | None = None,
        chgis_dir: str | Path | None = None,
        mapbox_token: str | None = None,
        http_get: HttpGet = _default_http_get,
    ):
        self.mapbox_token = mapbox_token
        self.http_get = http_get
        self._curated: dict[str, list[tuple[float, float, str]]] = {}
        self._chgis: dict[str, list[dict]] = {}
        self._cache: dict[tuple, GeoResult | None] = {}
        if curated_dir:
            self._load_curated(Path(curated_dir))
        if chgis_dir:
            self._load_chgis(Path(chgis_dir))

    @classmethod
    def from_env(cls) -> "Geocoder":
        """Usa lo que haya configurado en el entorno; las fuentes sin configurar se saltan."""
        return cls(
            curated_dir=os.environ.get("CHINA_HISTORY_DATA_DIR"),
            chgis_dir=os.environ.get("CHGIS_DIR"),
            mapbox_token=os.environ.get("MAPBOX_TOKEN"),
        )

    # ---- carga de datos -------------------------------------------------

    def _load_curated(self, data_dir: Path) -> None:
        # En yacimientos.json "lugar" es el nombre del sitio (Liulihe): sirve como alias. En capitales.json
        # "lugar" es solo la ciudad (Shenyang): un palacio no es "el sitio de la ciudad", así que no se usa.
        for relative, use_place_alias in (("yacimientos/yacimientos.json", True), ("capitales/capitales.json", False)):
            path = data_dir / relative
            if not path.exists():
                continue
            for entry in json.loads(path.read_text(encoding="utf-8")):
                point = _representative_point(entry.get("geometry", {}))
                if point is None:
                    continue
                lat, lng = point
                names = {entry.get("nombre_en", ""), entry.get("nombre", "")}
                if use_place_alias:
                    names.add((entry.get("lugar_en") or "").split(",")[0])
                for name in names:
                    key = _site_key(name)
                    if key:
                        self._curated.setdefault(key, []).append((lat, lng, entry.get("nombre_en") or entry.get("nombre", "")))

    def _load_chgis(self, chgis_dir: Path) -> None:
        for filename in ("county-points.json", "prefecture-points.json"):
            path = chgis_dir / filename
            if not path.exists():
                continue
            for item in json.loads(path.read_text(encoding="utf-8")):
                if not isinstance(item.get("lng"), (int, float)) or not isinstance(item.get("lat"), (int, float)):
                    continue
                self._chgis.setdefault(_strip_admin(normalize(item["namePy"])), []).append(item)

    # ---- fuentes ---------------------------------------------------------

    def _from_curated(self, site: str) -> GeoResult | None:
        candidates = self._curated.get(_site_key(site), [])
        # Las mismas coordenadas repetidas bajo otro alias no son ambigüedad.
        distinct = {(lat, lng) for lat, lng, _ in candidates}
        if len(distinct) != 1:
            return None
        lat, lng, name = candidates[0]
        return GeoResult(lat, lng, "curated", name)

    def _from_chgis(self, site: str, year_from: int, year_to: int, province: str | None) -> GeoResult | None:
        # CHGIS no trae provincia: sin ella (o sin poder verificarla) 'Qin' acaba en un condado Qin de otra provincia.
        if not province:
            return None
        key = _strip_admin(normalize(site))
        # Vigente en algún momento del rango del chunk, y un único asentamiento.
        valid = [s for s in self._chgis.get(key, []) if s["begYear"] <= year_to and s["endYear"] >= year_from]
        distinct = {(s["lat"], s["lng"]) for s in valid}
        if len(distinct) != 1:
            return None
        s = valid[0]
        # El punto debe caer en la provincia esperada (geocodificación inversa); si no se puede comprobar, se descarta.
        if _province_key(self._province_of(s["lat"], s["lng"]) or "") != _province_key(province):
            return None
        return GeoResult(s["lat"], s["lng"], "chgis", s["namePy"])

    def _from_mapbox(self, site: str, province: str | None) -> GeoResult | None:
        # Sin provincia esperada no hay forma de comprobar el resultado: no se acepta.
        if not self.mapbox_token or not province:
            return None
        query = urllib.parse.quote(f"{site}, {province}")
        url = (
            f"https://api.mapbox.com/geocoding/v5/mapbox.places/{query}.json"
            f"?country=cn&language=en&limit=5&access_token={self.mapbox_token}"
        )
        try:
            features = self.http_get(url).get("features", [])
        except Exception:  # noqa: BLE001 — red caída o límite: se trata como "no resuelto"
            return None
        expected = _province_key(province)
        for feature in features:
            # Nunca una provincia o un país: su centro sería una coordenada genérica, no el sitio.
            if not set(feature.get("place_type", [])) & MAPBOX_PLACE_TYPES:
                continue
            if feature.get("relevance", 0) < MAPBOX_MIN_RELEVANCE:
                continue
            region = next(
                (c.get("text", "") for c in feature.get("context", []) if str(c.get("id", "")).startswith("region.")), ""
            )
            if _province_key(region) != expected:
                continue
            # El nombre devuelto debe parecerse al buscado: si no, es otro lugar de la misma provincia.
            if not _similar(site, feature.get("text", "")):
                continue
            lng, lat = feature["center"]
            return GeoResult(lat, lng, "mapbox", feature.get("place_name", site))
        return None

    def _province_of(self, lat: float, lng: float) -> str | None:
        """Provincia de unas coordenadas, por geocodificación inversa de Mapbox (None si no se puede saber)."""
        if not self.mapbox_token:
            return None
        url = (
            f"https://api.mapbox.com/geocoding/v5/mapbox.places/{lng},{lat}.json"
            f"?types=region&language=en&limit=1&access_token={self.mapbox_token}"
        )
        try:
            features = self.http_get(url).get("features", [])
        except Exception:  # noqa: BLE001
            return None
        return features[0].get("text") if features else None

    def _from_wikidata(self, site: str, province: str | None) -> GeoResult | None:
        # Igual que con Mapbox: sin provincia esperada no hay forma de comprobar el resultado.
        if not province:
            return None
        expected = _province_key(province)
        try:
            query = urllib.parse.urlencode(
                {"action": "wbsearchentities", "search": site, "language": "en", "type": "item", "limit": 8, "format": "json"}
            )
            found = self.http_get(f"{WIKIDATA_API}?{query}").get("search", [])
        except Exception:  # noqa: BLE001 — red caída o límite: no resuelto
            return None
        # Candidatos con nombre parecido, de los que además hay que confirmar que están en la provincia.
        # Solo la etiqueta, no los alias: "Jin" es alias de la provincia de Shanxi y "Qi" de la ciudad de Jinan.
        candidates = [
            c
            for c in found
            if _same_name(site, c.get("label", "")) and not NOT_A_SITE_DESCRIPTION.match(c.get("description", ""))
        ]
        confirmed = []
        for candidate in candidates:
            coords = self._wikidata_coordinates(candidate["id"])
            if coords is None or not _in_china(*coords):
                continue
            in_description = bool(expected) and expected in normalize(candidate.get("description", ""))
            if in_description or _province_key(self._province_of(*coords) or "") == expected:
                confirmed.append((coords, candidate))
        if not confirmed:
            return None
        # Se decide solo entre los candidatos del mejor grupo (yacimientos antes que culturas, y estas antes que
        # museos o estaciones): una estación del mismo nombre a 170 km no debe invalidar al yacimiento.
        best = min(_site_priority(candidate) for _, candidate in confirmed)
        group = [(coords, candidate) for coords, candidate in confirmed if _site_priority(candidate) == best]
        # Si los del grupo están lejos unos de otros (dos "Yulin"), es ambiguo: mejor ninguno.
        points = [coords for coords, _ in group]
        if any(_distance_km(a, b) > SAME_PLACE_KM for a in points for b in points):
            return None
        (lat, lng), candidate = group[0]  # el primero de la búsqueda (el más relevante)
        matched = f"{candidate.get('label', site)} ({candidate['id']}): {candidate.get('description', '')}"
        return GeoResult(lat, lng, "wikidata", matched)

    def _wikidata_coordinates(self, entity_id: str) -> tuple[float, float] | None:
        try:
            query = urllib.parse.urlencode({"action": "wbgetclaims", "entity": entity_id, "property": "P625", "format": "json"})
            claims = self.http_get(f"{WIKIDATA_API}?{query}").get("claims", {}).get("P625", [])
            value = claims[0]["mainsnak"]["datavalue"]["value"]
            return float(value["latitude"]), float(value["longitude"])
        except Exception:  # noqa: BLE001 — sin coordenadas o red caída
            return None

    # ---- API pública -----------------------------------------------------

    def geocode(
        self, site: str, province: str | None = None, year_from: int = -221, year_to: int = 1911
    ) -> GeoResult | None:
        site = site.strip()
        if not site or GENERIC_PLACE.match(site) or POLITY_WORDS.search(site):
            return None
        cache_key = (site.lower(), (province or "").lower(), year_from, year_to)
        if cache_key in self._cache:
            return self._cache[cache_key]
        result = (
            self._from_curated(site)
            or self._from_chgis(site, year_from, year_to, province)
            or self._from_wikidata(site, province)
            or self._from_mapbox(site, province)
        )
        if result and not _in_china(result.lat, result.lng):
            result = None
        self._cache[cache_key] = result
        return result
