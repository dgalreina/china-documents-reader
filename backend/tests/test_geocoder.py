"""Tests del geocodificador con datos de prueba y red falsa (sin Mapbox real)."""

import json

from shared.geocoder import Geocoder, normalize


def write_chgis(tmp_path, items):
    (tmp_path / "county-points.json").write_text(json.dumps(items), encoding="utf-8")
    (tmp_path / "prefecture-points.json").write_text("[]", encoding="utf-8")


def chgis_item(name, lat, lng, beg, end):
    return {"namePy": name, "begYear": beg, "endYear": end, "lat": lat, "lng": lng}


def test_normalize_quita_acentos_y_signos():
    assert normalize("Chāng'ān") == "chang an"
    assert normalize("Chang'an Xian") == "chang an xian"


def test_chgis_exige_un_unico_candidato_vigente(tmp_path):
    write_chgis(tmp_path, [chgis_item("Yulin Xian", 22.6, 110.1, 100, 900), chgis_item("Yulin Xian", 38.3, 109.7, 100, 900)])
    assert Geocoder(chgis_dir=tmp_path).geocode("Yulin", None, 200, 300) is None  # ambiguo: dos Yulin


def test_chgis_resuelve_si_no_hay_ambiguedad_y_respeta_el_rango(tmp_path):
    write_chgis(tmp_path, [chgis_item("Qiantang Xian", 30.25, 120.17, -221, 600)])
    geo = Geocoder(chgis_dir=tmp_path)
    assert geo.geocode("Qiantang", None, 100, 200).source == "chgis"
    assert geo.geocode("Qiantang", None, 1200, 1300) is None  # ya no vigente


def test_lugares_genericos_no_se_geocodifican(tmp_path):
    write_chgis(tmp_path, [chgis_item("China Xian", 30, 110, -221, 1900)])
    geo = Geocoder(chgis_dir=tmp_path, mapbox_token="x", http_get=lambda url: (_ for _ in ()).throw(AssertionError("no debe llamar")))
    assert geo.geocode("China", "Henan") is None
    assert geo.geocode("Empire of the Tang", "Shaanxi") is None


def mapbox_response(region, lat=34.0, lng=113.0, name="Puyang", place_type="place", relevance=1.0):
    return {
        "features": [
            {
                "text": name,
                "place_name": f"{name}, {region}, China",
                "center": [lng, lat],
                "place_type": [place_type],
                "relevance": relevance,
                "context": [{"id": "region.1", "text": region}],
            }
        ]
    }


def test_mapbox_solo_se_acepta_si_la_provincia_coincide():
    geo = Geocoder(mapbox_token="x", http_get=lambda url: mapbox_response("Henan Province", name="Puyang"))
    assert geo.geocode("Puyang", "Henan").source == "mapbox"
    wrong = Geocoder(mapbox_token="x", http_get=lambda url: mapbox_response("Guangxi", name="Yulin"))
    assert wrong.geocode("Yulin", "Shaanxi") is None  # Yulin equivocado: se rechaza


def test_mapbox_sin_provincia_no_se_usa():
    geo = Geocoder(mapbox_token="x", http_get=lambda url: mapbox_response("Henan", name="Puyang"))
    assert geo.geocode("Puyang", None) is None


def test_shaanxi_y_shanxi_no_se_confunden():
    geo = Geocoder(mapbox_token="x", http_get=lambda url: mapbox_response("Shanxi", name="Yulin"))
    assert geo.geocode("Yulin", "Shaanxi") is None


def test_resultado_fuera_de_china_se_descarta():
    geo = Geocoder(mapbox_token="x", http_get=lambda url: mapbox_response("Henan", lat=48.8, lng=2.3, name="Puyang"))  # París
    assert geo.geocode("Puyang", "Henan") is None


def test_red_caida_es_no_resuelto_no_error():
    def boom(url):
        raise OSError("sin red")

    assert Geocoder(mapbox_token="x", http_get=boom).geocode("Puyang", "Henan") is None


def test_capital_con_contorno_usa_el_centro_del_contorno(tmp_path):
    (tmp_path / "capitales").mkdir()
    square = [[100.0, 30.0], [102.0, 30.0], [102.0, 32.0], [100.0, 32.0], [100.0, 30.0]]
    (tmp_path / "capitales" / "capitales.json").write_text(
        json.dumps([{"nombre": "Ciudad X", "nombre_en": "City X", "geometry": {"type": "Polygon", "coordinates": [square]}}]),
        encoding="utf-8",
    )
    geo = Geocoder(curated_dir=tmp_path).geocode("City X")
    assert geo.source == "curated" and 30 < geo.lat < 32 and 100 < geo.lng < 102


def test_mapbox_rechaza_una_provincia_como_resultado():
    # El fallo real: buscar "Erlitou, Henan" devolvió la propia provincia (su centro = coordenada genérica).
    geo = Geocoder(mapbox_token="x", http_get=lambda url: mapbox_response("Henan", name="Henan", place_type="region"))
    assert geo.geocode("Erlitou", "Henan") is None


def test_mapbox_rechaza_otro_lugar_de_la_misma_provincia():
    geo = Geocoder(mapbox_token="x", http_get=lambda url: mapbox_response("Henan", name="Luoyang"))
    assert geo.geocode("Erlitou", "Henan") is None


def test_mapbox_rechaza_baja_relevancia():
    geo = Geocoder(mapbox_token="x", http_get=lambda url: mapbox_response("Henan", name="Puyang", relevance=0.5))
    assert geo.geocode("Puyang", "Henan") is None


def test_capital_no_se_encuentra_por_el_nombre_de_su_ciudad(tmp_path):
    # Fallo real: un yacimiento neolítico "en Shenyang" casaba con el Palacio Imperial (1625) por la ciudad.
    (tmp_path / "capitales").mkdir()
    (tmp_path / "capitales" / "capitales.json").write_text(
        json.dumps([{"nombre": "Palacio", "nombre_en": "Imperial Palace", "lugar_en": "Shenyang", "geometry": {"type": "Point", "coordinates": [123.45, 41.79]}}]),
        encoding="utf-8",
    )
    geo = Geocoder(curated_dir=tmp_path)
    assert geo.geocode("Shenyang") is None
    assert geo.geocode("Imperial Palace").source == "curated"


# ---- Wikidata ---------------------------------------------------------------


def wikidata_http(entities, mapbox_region=None):
    """Red falsa: entities = lista de (id, label, description, lat, lng). Reparte por tipo de URL."""

    def fake(url):
        if "wbsearchentities" in url:
            return {"search": [{"id": i, "label": lb, "description": d, "match": {"text": lb}} for i, lb, d, _, _ in entities]}
        if "wbgetclaims" in url:
            _, _, _, lat, lng = next(e for e in entities if f"entity={e[0]}" in url)
            return {"claims": {"P625": [{"mainsnak": {"datavalue": {"value": {"latitude": lat, "longitude": lng}}}}]}}
        if "types=region" in url:
            return {"features": [{"text": mapbox_region}] if mapbox_region else []}
        return {"features": []}

    return fake


def test_wikidata_resuelve_con_provincia_en_la_descripcion():
    http = wikidata_http([("Q1", "Erlitou", "archaeological site in Henan, China", 34.69, 112.68)])
    geo = Geocoder(mapbox_token="x", http_get=http).geocode("Erlitou site", "Henan")
    assert geo.source == "wikidata" and (geo.lat, geo.lng) == (34.69, 112.68)


def test_wikidata_confirma_la_provincia_con_geocodificacion_inversa():
    ok = wikidata_http([("Q1", "Banpo", "Neolithic village", 34.27, 109.05)], mapbox_region="Shaanxi")
    assert Geocoder(mapbox_token="x", http_get=ok).geocode("Banpo", "Shaanxi").source == "wikidata"
    wrong = wikidata_http([("Q1", "Banpo", "Neolithic village", 34.27, 109.05)], mapbox_region="Shanxi")
    assert Geocoder(mapbox_token="x", http_get=wrong).geocode("Banpo", "Shaanxi") is None


def test_wikidata_sin_provincia_no_se_usa():
    http = wikidata_http([("Q1", "Erlitou", "archaeological site in Henan", 34.69, 112.68)])
    assert Geocoder(mapbox_token="x", http_get=http).geocode("Erlitou", None) is None


def test_wikidata_dos_candidatos_en_sitios_distintos_es_ambiguo():
    http = wikidata_http(
        [("Q1", "Yulin", "city in Guangxi, China", 22.6, 110.1), ("Q2", "Yulin", "city in Guangxi, China", 38.3, 109.7)]
    )
    assert Geocoder(mapbox_token="x", http_get=http).geocode("Yulin", "Guangxi") is None


def test_wikidata_nombre_distinto_se_ignora():
    http = wikidata_http([("Q1", "Luoyang", "city in Henan, China", 34.6, 112.4)])
    assert Geocoder(mapbox_token="x", http_get=http).geocode("Erlitou", "Henan") is None


def test_wikidata_coordenadas_fuera_de_china_se_descartan():
    http = wikidata_http([("Q1", "Erlitou", "site in Henan, China", 48.8, 2.3)])
    assert Geocoder(mapbox_token="x", http_get=http).geocode("Erlitou", "Henan") is None


def test_yin_xu_y_yinxu_se_consideran_el_mismo_nombre():
    http = wikidata_http([("Q1", "Yin Xu", "archaeological site in Henan, China", 36.13, 114.31)])
    assert Geocoder(mapbox_token="x", http_get=http).geocode("Yinxu", "Henan").source == "wikidata"


def test_wikidata_varias_entidades_del_mismo_lugar_no_son_ambiguas_y_se_elige_el_yacimiento():
    # Fallo real: el yacimiento, la cultura y el museo de Erlitou tienen coordenadas a 1-2 km y se rechazaban.
    http = wikidata_http(
        [
            ("Q3", "Erlitou Site Museum", "museum at Erlitou Site, Henan", 34.6832, 112.6887),
            ("Q2", "Erlitou culture", "Bronze age culture in Henan", 34.70175, 112.69707),
            ("Q1", "Erlitou", "archaeological site in Yanshi, Henan, China", 34.6925, 112.69),
        ]
    )
    geo = Geocoder(mapbox_token="x", http_get=http).geocode("Erlitou", "Henan")
    assert geo.source == "wikidata" and (geo.lat, geo.lng) == (34.6925, 112.69)


def test_wikidata_una_estacion_lejana_con_el_mismo_nombre_no_invalida_al_yacimiento():
    # Fallo real: "Liangzhu Station" (metro de Ningbo, misma provincia, 170 km) hacía ambiguo al yacimiento.
    http = wikidata_http(
        [
            ("Q2", "Liangzhu Station", "ningbo Metro station", 29.897, 121.472),
            ("Q1", "Liangzhu site", "archaeological site", 30.3967, 119.9853),
        ],
        mapbox_region="Zhejiang",
    )
    geo = Geocoder(mapbox_token="x", http_get=http).geocode("Liangzhu", "Zhejiang")
    assert (geo.lat, geo.lng) == (30.3967, 119.9853)
