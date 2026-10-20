"""Tests de la resolución de identidad de personajes (juez y base falsos: sin red, sin coste)."""

from lambdas.write_mongo.personajes import (
    ORIGIN, SamePerson, build_aliases, era_compatible, name_forms, norm_name, personaje_id, resolve_personajes,
)


def persona(nombre_en="King Wu of Zhou", nombre="Rey Wu de Zhou", alias=("Fa", "Ji Fa"), estado_en="Western Zhou", **over):
    base = {
        "nombre": nombre, "nombre_en": nombre_en, "nombre_zh": "周武王", "alias": list(alias),
        "rol": "líder", "rol_en": "leader", "rol_zh": "领袖", "menciones": {"en": "King Wu"},
        "nacimiento": None, "muerte": None, "estado": "Zhou Occidental", "estado_en": estado_en, "estado_zh": "西周",
        "descripcion": "Fundador.", "descripcion_en": "Founder.", "descripcion_zh": "创立者。",
    }
    return {**base, **over}


def hito(id_="h1", año=-1046, personajes=()):
    return {"id": id_, "año": año, "titulo": "t", "_personajes": list(personajes)}


class Base:
    """Una 'base' de fichas con la misma consulta por alias que usa Mongo."""

    def __init__(self, docs=()):
        self.docs = list(docs)

    def find(self, forms):
        return [d for d in self.docs if forms & set(d.get("aliasNorm", []))]


class Juez:
    def __init__(self, veredicto=False):
        self.veredicto, self.llamadas = veredicto, 0

    def __call__(self, new, old):
        self.llamadas += 1
        return SamePerson(misma_persona=self.veredicto, motivo="x")


def resolver(hitos, base=None, juez=None, source="libro"):
    return resolve_personajes(hitos, source, (base or Base()).find, client=None, judge_fn=juez or Juez())


def ficha(id_="king-wu-of-zhou-aaaaaa", origen=ORIGIN, **over):
    p = persona()
    doc = {
        "id": id_, "nombre": p["nombre"], "nombre_en": p["nombre_en"], "nombre_zh": "周武王",
        "aliases": build_aliases(p), "aliasNorm": sorted(name_forms(p)), "actividadInicio": -1050, "actividadFin": -1043,
        "estado_en": "Western Zhou", "fuentes": ["otro"], "origen": origen,
    }
    return {**doc, **over}


def test_norm_name_conserva_el_chino_y_quita_acentos_y_signos():
    assert norm_name("Chāng'ān") == "changan" and norm_name("周 武 王") == "周武王" and norm_name("King Wu") == "kingwu"


def test_formas_no_incluyen_alias_cortos_pero_si_el_nombre_sin_sufijo():
    forms = name_forms(persona(alias=("Fa", "Ji Fa")))
    assert "kingwu" in forms and "周武王" in forms and "jifa" in forms and "fa" not in forms


def test_persona_nueva_crea_ficha_con_id_estable_y_enlace_en_el_hito():
    r = resolver([hito(personajes=[persona()])])
    assert r.stats["personajes_nuevos"] == 1 and len(r.docs) == 1
    doc = r.docs[0]
    assert doc["id"].startswith("king-wu-of-zhou-") and doc["origen"] == ORIGIN and doc["fuentes"] == ["libro"]
    assert doc["actividadInicio"] == doc["actividadFin"] == -1046
    (link,) = r.hitos[0]["personajes"]
    assert link["id"] == doc["id"] and link["menciones"] == {"en": "King Wu"} and "_personajes" not in r.hitos[0]


def test_la_misma_persona_en_dos_hitos_es_una_sola_ficha_sin_gastar_el_juez():
    juez = Juez()
    r = resolver([hito("h1", -1046, [persona()]), hito("h2", -1044, [persona(alias=("Fa",))])], juez=juez)
    assert r.stats["personajes_nuevos"] == 1 and r.stats["personajes_unidos"] == 1 and juez.llamadas == 0
    assert r.hitos[0]["personajes"][0]["id"] == r.hitos[1]["personajes"][0]["id"]
    assert (r.docs[0]["actividadInicio"], r.docs[0]["actividadFin"]) == (-1046, -1044)


def test_dos_menciones_de_la_misma_persona_en_un_hito_son_un_solo_enlace():
    r = resolver([hito(personajes=[persona(), persona(alias=("Fa",))])])
    assert len(r.hitos[0]["personajes"]) == 1


def test_homonimo_de_otra_epoca_es_otra_persona_sin_preguntar_al_juez():
    # El mismo nombre ocho siglos después (-140) no es compatible con una persona activa hacia -1046: ficha separada.
    juez = Juez()
    r = resolver([hito("h", -140, [persona(estado_en="Han")])], base=Base([ficha()]), juez=juez)
    assert r.stats["personajes_nuevos"] == 1 and r.stats["personajes_unidos"] == 0 and juez.llamadas == 0


def test_mismo_nombre_epoca_y_estado_compatibles_se_une_sin_gastar_el_juez():
    juez = Juez()
    r = resolver([hito("h", -1046, [persona()])], base=Base([ficha()]), juez=juez)
    assert r.stats["personajes_unidos"] == 1 and r.stats["personajes_nuevos"] == 0 and juez.llamadas == 0


def test_mismo_nombre_pero_otro_estado_pasa_por_el_juez_y_ante_la_duda_son_distintas():
    juez = Juez(veredicto=False)
    otra = persona(estado_en="Chu")  # mismo nombre canónico, estado distinto: no vale el atajo
    r = resolver([hito(personajes=[otra])], base=Base([ficha()]), juez=juez)
    assert juez.llamadas == 1 and r.stats["personajes_nuevos"] == 1 and r.stats["personajes_unidos"] == 0


def test_juez_que_confirma_une_con_la_ficha_existente():
    juez = Juez(veredicto=True)
    r = resolver([hito(personajes=[persona(nombre_en="Ji Fa", nombre="Ji Fa", alias=("King Wu", "武王"))])], base=Base([ficha()]), juez=juez)
    assert juez.llamadas == 1 and r.stats["personajes_unidos"] == 1 and r.stats["personajes_nuevos"] == 0
    assert r.hitos[0]["personajes"][0]["id"] == "king-wu-of-zhou-aaaaaa"


def test_el_veredicto_del_juez_se_reutiliza_para_la_misma_pareja():
    juez = Juez(veredicto=True)
    nuevos = [hito("h1", -1046, [persona(nombre_en="Ji Fa", nombre="Ji Fa", alias=("King Wu",))]),
              hito("h2", -1045, [persona(nombre_en="Ji Fa", nombre="Ji Fa", alias=("King Wu",))])]
    resolver(nuevos, base=Base([ficha()]), juez=juez)
    assert juez.llamadas == 1


def test_una_ficha_de_otro_origen_se_enlaza_pero_no_se_modifica_ni_se_escribe():
    ajena = ficha(origen="china-history")
    r = resolver([hito(personajes=[persona(alias=("Fa", "Ji Fa", "otro alias"))])], base=Base([ajena]), juez=Juez(veredicto=True))  # un alias nuevo: lo confirma el juez
    assert r.hitos[0]["personajes"][0]["id"] == ajena["id"] and r.docs == []
    assert "otro alias" not in str(ajena)


def test_la_fusion_anade_alias_rellena_huecos_y_no_pisa_lo_que_ya_habia():
    existente = ficha(nacimiento=-1087)
    r = resolver([hito(personajes=[persona(alias=("Fa", "alias nuevo"), nacimiento=-1100, muerte=-1043, estado="otro estado")])], base=Base([existente]), juez=Juez(veredicto=True))  # un alias nuevo: lo confirma el juez
    (doc,) = r.docs
    assert "alias nuevo" in doc["aliases"]["en"] and doc["nacimiento"] == -1087 and doc["muerte"] == -1043  # no pisa el nacimiento
    assert doc["fuentes"] == ["otro", "libro"] and doc["estado"] == "otro estado"  # rellena el hueco


def test_era_sin_datos_no_descarta_y_con_datos_si():
    assert era_compatible({}, -1000) and era_compatible({"actividadInicio": -1050, "actividadFin": -1040}, -1000)
    assert not era_compatible({"actividadInicio": -1050, "actividadFin": -1040}, -140)


def test_personaje_id_con_colision_usa_otro_hash():
    seen = set()
    assert personaje_id("King Wu", -1046, seen) != personaje_id("King Wu", -1046, seen)


def test_cada_lista_de_alias_lleva_su_nombre_y_no_el_del_otro_idioma():
    aliases = build_aliases(persona())
    assert "Rey Wu de Zhou" in aliases["es"] and "King Wu of Zhou" not in aliases["es"]
    assert "King Wu of Zhou" in aliases["en"] and "Rey Wu de Zhou" not in aliases["en"]
    assert "Fa" in aliases["es"] and "Fa" in aliases["en"] and aliases["zh"] == ["周武王"]


def test_tras_una_fusion_los_nombres_canonicos_siguen_sin_cruzarse():
    r = resolver([hito(personajes=[persona(alias=("Fa", "alias nuevo"))])], base=Base([ficha()]))
    (doc,) = r.docs
    assert "King Wu of Zhou" not in doc["aliases"]["es"] and "Rey Wu de Zhou" not in doc["aliases"]["en"]
    assert "alias nuevo" in doc["aliases"]["es"] and "alias nuevo" in doc["aliases"]["en"]


def test_dos_homonimos_de_la_misma_epoca_y_estado_con_otra_grafia_pasan_por_el_juez_y_no_se_unen_solos():
    # Han Xin, el general, y Han Xin, el rey de Han: mismo nombre canónico, misma época y estado; el libro los escribe "Han Hsin" y "Hann Hsin".
    general = persona(nombre_en="Han Xin", nombre="Han Xin", alias=("Han Hsin", "Marquis of Huaiyin"), estado_en="Han", rol_en="general")
    rey = persona(nombre_en="Han Xin", nombre="Han Xin", alias=("Hann Hsin",), estado_en="Han", rol_en="king of Han")
    juez = Juez(veredicto=False)  # "ante la duda, distintas"
    resultado = resolver([hito("h1", -204, [general]), hito("h2", -205, [rey])], juez=juez)
    ids = {hito_["personajes"][0]["id"] for hito_ in resultado.hitos}
    assert len(ids) == 2 and juez.llamadas == 1  # dos fichas, y el juez fue quien lo decidió
    assert resultado.stats["personajes_nuevos"] == 2


def test_el_juez_ve_el_hecho_y_la_frase_del_libro_de_la_mencion_nueva():
    vistos = []

    def juez(new, old):
        vistos.append(new.get("_contexto", ""))
        return SamePerson(misma_persona=False, motivo="x")

    h2 = {**hito("h2", -205, [persona(nombre_en="Han Xin", nombre="Han Xin", alias=("Hann Hsin",))]), "titulo": "Conquista de Yangcheng", "evidencia": "Hann Hsin invaded Hann"}
    resolver([hito("h1", -204, [persona(nombre_en="Han Xin", nombre="Han Xin", alias=("Han Hsin",))]), h2], juez=juez)
    assert vistos and "Conquista de Yangcheng" in vistos[0] and "Hann Hsin invaded Hann" in vistos[0]


def test_la_misma_persona_con_las_mismas_formas_sigue_uniendose_sin_juez_aunque_salga_otras_veces():
    juez = Juez()
    g = persona(nombre_en="Han Xin", nombre="Han Xin", alias=("Han Hsin",), estado_en="Han")
    resultado = resolver([hito("h1", -204, [g]), hito("h2", -201, [g]), hito("h3", -196, [g])], juez=juez)
    assert juez.llamadas == 0 and len({h_["personajes"][0]["id"] for h_ in resultado.hitos}) == 1


def test_una_mencion_con_un_nombre_nuevo_que_el_juez_confirma_se_une_y_aprende_la_forma():
    juez = Juez(veredicto=True)
    primero = persona(nombre_en="Han Xin", nombre="Han Xin", alias=("Han Hsin",), estado_en="Han")
    despues = persona(nombre_en="Han Xin", nombre="Han Xin", alias=("Marqués de Huaiyin",), estado_en="Han")
    resultado = resolver([hito("h1", -204, [primero]), hito("h2", -201, [despues])], juez=juez)
    assert juez.llamadas == 1 and len({h_["personajes"][0]["id"] for h_ in resultado.hitos}) == 1
    assert any("marquesdehuaiyin" in doc["aliasNorm"] for doc in resultado.docs)

