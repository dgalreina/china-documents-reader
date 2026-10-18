"""Parámetros comunes de TODAS las llamadas de chat a OpenAI (etiquetado, hitos, jueces de duplicados y de personajes).

`temperature=0` hace que el modelo elija siempre la opción más probable en lugar de muestrear con algo de azar: con el mismo texto y el mismo
prompt las respuestas son (casi siempre) iguales. No es una garantía absoluta de OpenAI, pero reduce mucho la variación que vimos entre
ejecuciones (el mismo chunk de Jiahu salió "sin año" y luego "-7000..-5000"). Aquí no se busca creatividad sino extraer y decidir.

Se puede cambiar sin tocar código con la variable de entorno OPENAI_TEMPERATURE. Déjala VACÍA (OPENAI_TEMPERATURE=) si usas un modelo que no admite
el parámetro (los de razonamiento, p. ej.): entonces no se envía.
"""

import os


def chat_params() -> dict:
    value = os.environ.get("OPENAI_TEMPERATURE", "0")
    return {} if value.strip() == "" else {"temperature": float(value)}
