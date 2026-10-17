"""Tests de secretos (env o SSM) y de la descarga de datos geográficos desde S3 (clientes falsos, sin AWS)."""

import os

import pytest

from shared import geodata, secrets


class NotFound(Exception):
    pass


class FakeSSM:
    class exceptions:  # igual que boto3: el cliente expone sus excepciones
        ParameterNotFound = NotFound

    def __init__(self, params):
        self.params, self.calls = params, []

    def get_parameter(self, Name, WithDecryption):
        self.calls.append((Name, WithDecryption))
        if Name not in self.params:
            raise NotFound(Name)
        return {"Parameter": {"Value": self.params[Name]}}


@pytest.fixture(autouse=True)
def limpio(monkeypatch):
    secrets.clear_cache()
    geodata.reset()
    for name in ("OPENAI_API_KEY", "SSM_PREFIX", "GEODATA_BUCKET", "GEODATA_PREFIX", "CHGIS_DIR", "CHINA_HISTORY_DATA_DIR"):
        monkeypatch.delenv(name, raising=False)


def test_la_variable_de_entorno_tiene_prioridad_y_no_toca_ssm(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "desde-env")
    monkeypatch.setenv("SSM_PREFIX", "/china-docs")
    ssm = FakeSSM({"/china-docs/OPENAI_API_KEY": "desde-ssm"})
    assert secrets.get_secret("OPENAI_API_KEY", ssm) == "desde-env" and ssm.calls == []


def test_sin_variable_ni_prefijo_no_hay_secreto():
    assert secrets.get_secret("OPENAI_API_KEY", FakeSSM({})) is None


def test_con_prefijo_se_lee_de_ssm_descifrado_y_se_cachea(monkeypatch):
    monkeypatch.setenv("SSM_PREFIX", "/china-docs/")  # una barra final de más no importa
    ssm = FakeSSM({"/china-docs/OPENAI_API_KEY": "desde-ssm"})
    assert secrets.get_secret("OPENAI_API_KEY", ssm) == "desde-ssm"
    assert secrets.get_secret("OPENAI_API_KEY", ssm) == "desde-ssm"
    assert ssm.calls == [("/china-docs/OPENAI_API_KEY", True)]  # con WithDecryption y una sola vez


def test_un_parametro_que_no_existe_devuelve_none_sin_romper(monkeypatch):
    monkeypatch.setenv("SSM_PREFIX", "/china-docs")
    assert secrets.get_secret("MAPBOX_TOKEN", FakeSSM({})) is None


class FakeStorage:
    def __init__(self, existing):
        self.existing, self.calls = set(existing), []

    def download(self, bucket, key, path):
        self.calls.append((bucket, key))
        if key not in self.existing:
            raise FileNotFoundError(key)
        path.write_text("[]", encoding="utf-8")


def test_sin_bucket_configurado_no_hace_nada(tmp_path):
    storage = FakeStorage([])
    assert geodata.ensure_geodata(storage, tmp_path) == [] and storage.calls == [] and "CHGIS_DIR" not in os.environ


def test_con_bucket_descarga_los_datos_y_apunta_las_variables(monkeypatch, tmp_path):
    monkeypatch.setenv("GEODATA_BUCKET", "datos")
    storage = FakeStorage({f"geodata/{key}" for key, _ in geodata.GEODATA_FILES})
    assert geodata.ensure_geodata(storage, tmp_path) == []
    assert os.environ["CHGIS_DIR"] == str(tmp_path / "chgis") and os.environ["CHINA_HISTORY_DATA_DIR"] == str(tmp_path)
    assert (tmp_path / "chgis" / "county-points.json").exists() and (tmp_path / "yacimientos" / "yacimientos.json").exists()
    monkeypatch.delenv("CHGIS_DIR"), monkeypatch.delenv("CHINA_HISTORY_DATA_DIR")  # lo escribió el código, no el test


def test_un_fichero_ausente_se_avisa_pero_no_tumba_el_paso(monkeypatch, tmp_path):
    monkeypatch.setenv("GEODATA_BUCKET", "datos")
    storage = FakeStorage({"geodata/yacimientos/yacimientos.json"})
    missing = geodata.ensure_geodata(storage, tmp_path)
    assert "chgis/county-points.json" in missing and "yacimientos/yacimientos.json" not in missing
    monkeypatch.delenv("CHGIS_DIR"), monkeypatch.delenv("CHINA_HISTORY_DATA_DIR")


def test_solo_descarga_una_vez_por_proceso(monkeypatch, tmp_path):
    monkeypatch.setenv("GEODATA_BUCKET", "datos")
    storage = FakeStorage({f"geodata/{key}" for key, _ in geodata.GEODATA_FILES})
    geodata.ensure_geodata(storage, tmp_path)
    first = len(storage.calls)
    geodata.ensure_geodata(storage, tmp_path)
    assert len(storage.calls) == first
    monkeypatch.delenv("CHGIS_DIR"), monkeypatch.delenv("CHINA_HISTORY_DATA_DIR")
