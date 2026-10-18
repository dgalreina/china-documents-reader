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

    def _parse(self, model, messages, response_format, **kwargs):
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


# ---- personajes: solo lo que se puede comprobar en el texto -----------------------------------------

from lambdas.extract_hitos.extractor import ExtractedPersonaje, clean_personaje  # noqa: E402

HITO_TEXTS = {
    "es": "Batalla de Muye. El rey Wu dirigió el ataque final.",
    "en": "Battle of Muye. King Wu led the final attack.",
    "zh": "牧野之战。武王率领最后的进攻。",
}
SOURCE = "Next time, Fa gathered his allies at the Muye field and led them in the final attack."


def persona(**over):
    base = dict(
        en_el_texto="Fa", nombre="Rey Wu de Zhou", nombre_en="King Wu of Zhou", nombre_zh="周武王", alias=["Ji Fa", "武王"],
        rol="líder de la conquista", rol_en="leader of the conquest", rol_zh="征服的领导者",
        mencion_es="rey Wu", mencion_en="King Wu", mencion_zh="武王", nacimiento=None, muerte=-1043,
        estado="Zhou Occidental", estado_en="Western Zhou", estado_zh="西周",
        descripcion="Fundador de la dinastía Zhou.", descripcion_en="Founder of the Zhou dynasty.", descripcion_zh="周朝的创立者。",
    )
    return ExtractedPersonaje(**{**base, **over})


def test_personaje_valido_conserva_menciones_alias_y_chino():
    clean, reason = clean_personaje(persona(), HITO_TEXTS, SOURCE)
    assert reason is None and clean["menciones"] == {"es": "rey Wu", "en": "King Wu", "zh": "武王"}
    assert "Fa" in clean["alias"] and "Ji Fa" in clean["alias"] and clean["muerte"] == -1043


def test_personaje_que_no_esta_en_el_texto_original_se_descarta():
    # Anti-invención: "Duke of Zhou" no aparece en el texto de los fragmentos.
    clean, reason = clean_personaje(persona(en_el_texto="Duke of Zhou"), HITO_TEXTS, SOURCE)
    assert clean is None and reason == "personaje_no_esta_en_el_texto"


def test_la_mencion_se_busca_en_el_texto_con_los_alias_no_se_fia_del_modelo():
    # El modelo dijo "周武王" (que no está en el texto zh) pero el alias "武王" sí: se enlaza con el texto real.
    clean, _ = clean_personaje(persona(mencion_zh="周武王", mencion_en="Wu the king"), HITO_TEXTS, SOURCE)
    assert clean["menciones"] == {"es": "rey Wu", "en": "King Wu", "zh": "武王"}  # el sufijo "de Zhou" se quita


def test_el_nombre_tal_como_esta_en_el_texto_enlaza_aunque_el_hito_use_otro():
    # El hito dice "Fa" y el modelo lo llamó "King Wu": se enlaza "Fa", la forma que de verdad está escrita.
    texts = {"es": "Fa dirigió el ataque.", "en": "Fa led the attack.", "zh": "武王发起进攻。"}
    clean, _ = clean_personaje(persona(), texts, SOURCE)
    assert clean["menciones"]["en"] == "Fa" and clean["menciones"]["es"] == "Fa"


def test_un_alias_corto_no_casa_dentro_de_otro_nombre():
    texts = {"es": "El gobernador de Wuhan atacó.", "en": "The governor of Wuhan attacked.", "zh": ""}
    clean, reason = clean_personaje(persona(en_el_texto="Fa", alias=["Wu"], nombre="Rey Wu", nombre_en="King Wu", nombre_zh=None), texts, SOURCE)
    assert clean is None and reason == "personaje_sin_mencion_enlazable"


def test_sin_ninguna_mencion_enlazable_se_descarta():
    texts = {"es": "Batalla de Muye.", "en": "Battle of Muye.", "zh": "牧野之战。"}
    clean, reason = clean_personaje(persona(), texts, SOURCE)
    assert clean is None and reason == "personaje_sin_mencion_enlazable"


def test_fechas_incoherentes_y_chino_sin_caracteres_se_anulan():
    clean, _ = clean_personaje(persona(nacimiento=-1000, muerte=-1100, nombre_zh="King Wu", rol_zh="leader"), HITO_TEXTS, SOURCE)
    assert clean["nacimiento"] is None and clean["muerte"] is None  # nacimiento posterior a la muerte
    assert clean["nombre_zh"] is None and clean["rol_zh"] is None


def test_el_hito_trae_sus_personajes_validados_en__personajes():
    texts_hito = hito(
        titulo="Batalla de Muye", descripcion="El rey Wu dirigió el ataque final.", titulo_en="Battle of Muye",
        descripcion_en="King Wu led the final attack.", titulo_zh="牧野之战", descripcion_zh="武王率领最后的进攻。",
        evidencia="The court moved to Luoyang after the invasion.", personajes=[persona(en_el_texto="Luoyang")],
    )
    (h,) = run([texts_hito]).hitos
    assert [p["nombre_en"] for p in h["_personajes"]] == ["King Wu of Zhou"]
