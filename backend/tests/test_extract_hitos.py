"""Tests de extract_hitos con un cliente de OpenAI falso y un geocodificador falso (sin red, sin coste)."""

from types import SimpleNamespace

from lambdas.extract_hitos.extractor import ExtractedHito, HitosBatch, year_in_range
from lambdas.extract_hitos.ids import hito_id, js_number
from lambdas.extract_hitos.pipeline import ORIGIN, extract_hitos
from shared.geocoder import GeoResult


def hito(**over):
    base = dict(
        evidencia="The court moved to Luoyang after the invasion.",
        sitio="Luoyang", provincia="Henan", ano=-1042, titulo="Fundación de Chengzhou", descripcion="Se funda la ciudad.",
        lugar="Luoyang, Henan", titulo_en="Founding of Chengzhou", descripcion_en="The city is founded.", lugar_en="Luoyang, Henan",
        titulo_zh="成周的建立", descripcion_zh="建立了这座城市。", lugar_zh="河南洛阳",
    )
    return ExtractedHito(**{**base, **over})


class FakeClient:
    def __init__(self, hitos):
        self.hitos = hitos
        self.calls = 0
        self.chat = SimpleNamespace(completions=SimpleNamespace(parse=self._parse))

    def _parse(self, model, messages, response_format):
        self.calls += 1
        assert response_format is HitosBatch
        return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(parsed=HitosBatch(hitos=self.hitos)))])


class FakeGeocoder:
    """Resuelve solo los sitios del diccionario, como el geocodificador real devuelve None para el resto."""

    def __init__(self, known):
        self.known = known

    def geocode(self, site, province=None, year_from=0, year_to=0):
        return self.known.get(site)


LUOYANG = GeoResult(34.62, 112.45, "wikidata", "Luoyang (Q1)")


def chunks(n=2, year_from=-1100, year_to=-900):
    return [{"chunkIndex": i, "text": f"The court moved to Luoyang after the invasion in the west {i}", "añoInicio": year_from, "añoFin": year_to} for i in range(n)]


def run(hitos, known=None, **kw):
    return extract_hitos(chunks(**kw), "mi-libro", client=FakeClient(hitos), model="fake", geocoder=FakeGeocoder({"Luoyang": LUOYANG} if known is None else known))


def test_hito_valido_sale_completo_y_con_la_marca_de_origen():
    (h,) = run([hito()]).hitos
    assert h["lugar"] == "Luoyang, Henan" and h["año"] == -1042 and h["fuente"] == "mi-libro"
    assert h["titulo_en"] and h["titulo_zh"] == "成周的建立"
    assert h["location"] == {"type": "Point", "coordinates": [112.45, 34.62]}  # GeoJSON [lng, lat]
    assert h["origen"] == ORIGIN and len(h["id"]) == 10


def test_sin_coordenadas_se_descarta_y_queda_registrado():
    result = run([hito()], known={})  # la evidencia es válida pero el lugar no se resuelve
    assert result.hitos == [] and result.discarded[0]["reason"] == "sin_coordenadas"


def test_anio_fuera_del_rango_del_texto_se_descarta():
    # Un año de excavación (1957) en un texto sobre -1100..-900 no es un hito.
    result = run([hito(ano=1957)])
    assert result.hitos == [] and result.discarded[0]["reason"] == "año_fuera_del_rango_del_texto"


def test_margen_de_100_anios_alrededor_del_texto():
    assert year_in_range(-1000, -1100, -900) and year_in_range(-1190, -1100, -900)
    assert not year_in_range(-1300, -1100, -900)


def test_duplicados_por_solape_de_chunks():
    result = run([hito(), hito(titulo="Fundación de la ciudad de Chengzhou")])  # mismo sitio y año
    assert len(result.hitos) == 1 and result.discarded[0]["reason"] == "duplicado"


def test_chino_sin_caracteres_chinos_se_omite():
    (h,) = run([hito(titulo_zh="Founding of Chengzhou")]).hitos
    assert "titulo_zh" not in h and "descripcion_zh" in h


def test_campos_vacios_se_descartan():
    assert run([hito(descripcion="  ")]).discarded[0]["reason"] == "campos_vacios"


def test_se_agrupan_5_chunks_por_llamada():
    client = FakeClient([])
    extract_hitos(chunks(11), "x", client=client, model="fake", geocoder=FakeGeocoder({}))
    assert client.calls == 3  # 5 + 5 + 1

# Hitos reales de china-history-api/data/hitos/hitos.json: (titulo, año, lugar, lat, lng, id esperado).
REALES = [
    ('Unificación de China por el rey de Qin', -221, 'Reino de Qin', 28.143461, 120.28485, '6e9d225ce7'),
    ('Primeros cultivos de arroz inundado documentados', -5000, 'Zhejiang', 28.725902, 120.071318, '2c30f6e896'),
]


# ---- id: mismo algoritmo que extract-hitos.mjs de china-history -------------------------------------


def test_js_number_escribe_como_javascript():
    assert js_number(34.0) == "34" and js_number(-221) == "-221" and js_number(34.6925) == "34.6925"


def test_id_coincide_con_hitos_reales_de_china_history():
    # Verificado contra los 1259 hitos de china-history (1249 coinciden; los otros 10 se editaron a mano tras calcular su id).
    for titulo, ano, lugar, lat, lng, esperado in REALES:
        assert hito_id(titulo, ano, lugar, lat, lng, set()) == esperado


def test_id_con_colision_usa_salt():
    seen = set()
    a = hito_id("T", 1, "L", 1.5, 2.5, seen)
    b = hito_id("T", 1, "L", 1.5, 2.5, seen)
    assert a != b and len(a) == len(b) == 10


def test_segundo_intento_con_el_nombre_actual_como_china_history():
    # "Luoyi" no se resuelve, pero su nombre actual "Luoyang" sí.
    result = run([hito(sitio="Luoyi", sitio_moderno="Luoyang")], known={"Luoyang": LUOYANG})
    (h,) = result.hitos
    assert h["location"]["coordinates"] == [112.45, 34.62]
    assert "nombre actual de Luoyi" in h["ubicacionCoincidencia"] and result.stats["geo_via_nombre_moderno"] == 1


def test_sin_nombre_actual_y_sin_coordenadas_se_descarta_igual():
    result = run([hito(sitio_moderno=None)], known={})
    assert result.hitos == [] and result.discarded[0]["reason"] == "sin_coordenadas"


# ---- evidencia: la cita debe estar en el texto y nombrar el lugar ----------------------------------


def test_evidencia_que_no_nombra_el_lugar_se_descarta():
    # Fallo real: "Muerte del Rey You" atribuida a Luoyi, cuando la frase habla de otro sitio.
    result = run([hito(sitio="Luoyi", sitio_moderno=None, evidencia="The court moved to Luoyang after the invasion.")], known={"Luoyi": LUOYANG})
    assert result.hitos == []  # la cita nombra "Luoyang", no "Luoyi"... pero 'luoyi' no es subcadena de 'luoyang'
    assert result.discarded[0]["reason"] == "evidencia_no_respalda_el_lugar"


def test_evidencia_inventada_que_no_esta_en_el_texto_se_descarta():
    result = run([hito(evidencia="Armies of the king crossed the river at dawn near Luoyang and won.")])
    assert result.discarded[0]["reason"] == "evidencia_no_respalda_el_lugar"


def test_evidencia_tolera_guiones_de_maquetacion_del_pdf():
    from lambdas.extract_hitos.extractor import evidence_is_valid

    assert evidence_is_valid("The cul- tural centre was in Luoyang", ["Luoyang"], "The cultural centre was in Luoyang today")
    assert evidence_is_valid("moved the capital to Haojing city", ["Hao"], "they moved the capital to Haojing city then")
