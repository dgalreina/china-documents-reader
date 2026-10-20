// La petición HTTP del agente, sin depender de AWS: comprueba el login, valida, y escribe la respuesta en flujo (NDJSON).
// `open(estado, cabeceras)` abre la respuesta y devuelve { write(texto), end() }: en Lambda es el stream de la función; en las pruebas, un falso.
import { answer, parseRequest } from './core.mjs';

const JSON_HEADERS = { 'content-type': 'application/json; charset=utf-8', 'cache-control': 'no-store' };
const STREAM_HEADERS = { 'content-type': 'application/x-ndjson; charset=utf-8', 'cache-control': 'no-store', 'x-accel-buffering': 'no' };
const GENERIC_ERROR = 'No se pudo obtener la respuesta. Inténtalo de nuevo.';

const header = (event, name) => {
  const headers = event.headers ?? {};
  const key = Object.keys(headers).find((candidate) => candidate.toLowerCase() === name);
  return key ? headers[key] : undefined;
};

function bodyOf(event) {
  if (typeof event.body !== 'string') return event.body ?? null;
  return event.isBase64Encoded ? Buffer.from(event.body, 'base64').toString('utf-8') : event.body;
}

/** Una respuesta de error completa (antes de empezar a responder): estado + JSON con el mensaje. */
function fail(open, status, message) {
  const out = open(status, JSON_HEADERS);
  out.write(JSON.stringify({ error: message }));
  out.end();
}

/**
 * `deps`: { verifyToken(token) -> rechaza si no vale, getDeps() -> { chunks, embed, streamChat, minScore }, log(texto) }.
 * Todo lo que pueda fallar antes de escribir el primer byte (login, formato) responde con un estado HTTP normal; lo que falla DESPUÉS
 * (Mongo, OpenAI, el corte de red) ya no puede cambiar el estado: va como un evento `error` dentro del flujo.
 */
export async function handleRequest(event, deps, open) {
  const method = event.requestContext?.http?.method ?? event.httpMethod ?? 'POST';
  if (method !== 'POST') return fail(open, 405, 'Método no permitido.');

  const authorization = header(event, 'authorization') ?? '';
  const token = authorization.replace(/^Bearer\s+/i, '').trim();
  if (!token) return fail(open, 401, 'Falta iniciar sesión.');
  try {
    await deps.verifyToken(token);
  } catch {
    return fail(open, 401, 'La sesión no es válida o ha caducado.');
  }

  const parsed = parseRequest(bodyOf(event));
  if (!parsed.ok) return fail(open, 400, parsed.error);

  const out = open(200, STREAM_HEADERS);
  const emit = (message) => out.write(`${JSON.stringify(message)}\n`);
  try {
    await answer(await deps.getDeps(), parsed.value, emit);
  } catch (error) {
    deps.log?.(`ask falló: ${error?.name ?? 'Error'}`); // solo el tipo: el mensaje podría llevar datos de conexión
    emit({ type: 'error', message: GENERIC_ERROR });
  }
  out.end();
}
