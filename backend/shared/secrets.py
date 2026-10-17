"""Secretos (claves de OpenAI, Mongo, Mapbox): del `.env` en tu ordenador, de SSM Parameter Store en AWS.

Un solo punto de lectura: `get_secret("OPENAI_API_KEY")` mira primero la variable de entorno (el `.env` en local) y, si no está y
hay un prefijo configurado (`SSM_PREFIX`, p. ej. "/china-docs", que pondrá el CDK), la lee de SSM como `<prefijo>/<NOMBRE>`.

Por qué SSM y no variables de entorno de la Lambda: las variables de entorno quedan en claro en la consola y en la plantilla de
CloudFormation. Un parámetro SecureString va cifrado, se puede cambiar sin redesplegar y no cuesta nada. Los parámetros se crean
UNA vez a mano (CloudFormation no puede crear SecureString):

    aws ssm put-parameter --name /china-docs/OPENAI_API_KEY --type SecureString --value "..."
"""

import os

_cache: dict[str, str] = {}  # el valor de SSM no cambia durante la vida de una Lambda "caliente": se lee una vez


def get_secret(name: str, ssm_client=None) -> str | None:
    value = os.environ.get(name)
    if value:
        return value
    prefix = os.environ.get("SSM_PREFIX")
    if not prefix:
        return None
    key = f"{prefix.rstrip('/')}/{name}"
    if key not in _cache:
        if ssm_client is None:
            import boto3  # importación tardía: en local con .env no hace falta

            ssm_client = boto3.client("ssm")
        try:
            _cache[key] = ssm_client.get_parameter(Name=key, WithDecryption=True)["Parameter"]["Value"]
        except ssm_client.exceptions.ParameterNotFound:
            return None
    return _cache[key]


def clear_cache() -> None:
    _cache.clear()
