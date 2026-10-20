import assert from 'node:assert/strict';
import { test } from 'node:test';
import { handleRequest } from '../src/app.mjs';

/** Una respuesta falsa: apunta el estado, las cabeceras y todo lo escrito. */
const fakeOpen = () => {
  const result = { status: null, headers: null, written: [], ended: false };
  return {
    result,
    open: (status, headers) => {
      result.status = status;
      result.headers = headers;
      return { write: (text) => result.written.push(text), end: () => (result.ended = true) };
    },
  };
};

const event = (extra = {}) => ({
  requestContext: { http: { method: 'POST' } },
  headers: { Authorization: 'Bearer token-bueno' },
  body: JSON.stringify({ question: '¿Quién fue el rey Wu?', slug: 'libro-a' }),
  ...extra,
});

const goodDeps = (extra = {}) => ({
  verifyToken: async (token) => {
    if (token !== 'token-bueno') throw new Error('token malo');
  },
  getDeps: async () => ({
    chunks: { aggregate: () => ({ toArray: async () => [{ text: 'King Wu won.', source: 'libro-a', pageStart: 4, pageEnd: 4, score: 0.8 }] }) },
    embed: async () => [0.1],
    streamChat: async function* () {
      yield 'Ganó ';
      yield 'él.';
    },
  }),
  log: () => undefined,
  ...extra,
});

const lines = (result) => result.written.join('').split('\n').filter(Boolean).map((line) => JSON.parse(line));

test('sin sesión: 401 y no se toca nada más', async () => {
  const { open, result } = fakeOpen();
  let touched = false;
  await handleRequest(event({ headers: {} }), goodDeps({ getDeps: async () => { touched = true; } }), open);
  assert.equal(result.status, 401);
  assert.equal(touched, false);
  assert.ok(result.ended);
});

test('token inválido o caducado: 401, sin detalles del motivo', async () => {
  const { open, result } = fakeOpen();
  await handleRequest(event({ headers: { authorization: 'Bearer falso' } }), goodDeps(), open);
  assert.equal(result.status, 401);
  assert.doesNotMatch(result.written.join(''), /token malo/);
});

test('las cabeceras se leen sin importar mayúsculas', async () => {
  const { open, result } = fakeOpen();
  await handleRequest(event({ headers: { AUTHORIZATION: 'bearer token-bueno' } }), goodDeps(), open);
  assert.equal(result.status, 200);
});

test('pregunta mal formada: 400 con el motivo', async () => {
  const { open, result } = fakeOpen();
  await handleRequest(event({ body: JSON.stringify({ question: '' }) }), goodDeps(), open);
  assert.equal(result.status, 400);
  assert.match(JSON.parse(result.written.join('')).error, /pregunta/i);
});

test('solo POST', async () => {
  const { open, result } = fakeOpen();
  await handleRequest(event({ requestContext: { http: { method: 'GET' } } }), goodDeps(), open);
  assert.equal(result.status, 405);
});

test('una petición buena: 200 en NDJSON, con citas, respuesta en trozos y fin', async () => {
  const { open, result } = fakeOpen();
  await handleRequest(event(), goodDeps(), open);
  assert.equal(result.status, 200);
  assert.match(result.headers['content-type'], /ndjson/);
  assert.deepEqual(lines(result).map((e) => e.type), ['fuentes', 'delta', 'delta', 'fin']);
  assert.ok(result.ended);
});

test('el cuerpo puede llegar en base64 (Function URL lo hace con algunos clientes)', async () => {
  const { open, result } = fakeOpen();
  const body = Buffer.from(JSON.stringify({ question: 'hola' })).toString('base64');
  await handleRequest(event({ body, isBase64Encoded: true }), goodDeps(), open);
  assert.equal(result.status, 200);
});

test('un fallo a mitad (Mongo, OpenAI) va como evento "error" y la respuesta se cierra, sin filtrar detalles', async () => {
  const { open, result } = fakeOpen();
  const logs = [];
  const deps = goodDeps({ getDeps: async () => { throw new Error('mongodb+srv://usuario:secreta@cluster inaccesible'); }, log: (m) => logs.push(m) });
  await handleRequest(event(), deps, open);
  const events = lines(result);
  assert.equal(events.at(-1).type, 'error');
  assert.doesNotMatch(JSON.stringify(events), /secreta|mongodb/);
  assert.doesNotMatch(logs.join(' '), /secreta|mongodb/); // el registro tampoco lleva la cadena de conexión
  assert.ok(result.ended);
});
