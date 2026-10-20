// Lógica pura del agente (sin AWS ni red): validar la petición, buscar fragmentos, armar el prompt y emitir los eventos de la respuesta.
// Todo lo externo (Mongo, OpenAI) entra por `deps`, así se prueba con falsos en `test/`.
//
// Protocolo de la respuesta: NDJSON (un JSON por línea), en este orden:
//   {"type":"fuentes","fuentes":[{"slug","pagina","texto"}]}   los fragmentos en que se apoya (o [] si no hay)
//   {"type":"delta","text":"..."}                              trozos de la respuesta, según los va escribiendo el modelo
//   {"type":"fin","origen":"libro"|"ninguno"}                  terminó ("ninguno": no se encontró nada relevante)
//   {"type":"error","message":"..."}                           algo falló a mitad (después de haber empezado a responder)

export const VECTOR_INDEX = 'book_chunks_vector_index';
export const ORIGIN = 'china-documents-reader'; // la marca de los fragmentos de esta app (nunca se leen los de otros)
export const EMBEDDING_MODEL = 'text-embedding-3-small'; // el mismo con el que se guardaron (cambiar de modelo obliga a reindexar)

export const MAX_QUESTION_CHARS = 600;
export const MAX_HISTORY_TURNS = 6;
export const MAX_TURN_CHARS = 1500;
export const RESULTS = 8; // fragmentos que se pasan al modelo
export const NUM_CANDIDATES = 150;
// Si el libro ENTERO cabe en este presupuesto de texto (caracteres), una pregunta general lo lee entero; si no, solo el comienzo y una muestra. 100.000 caracteres son unos
// 25.000 tokens (~0,06 USD por pregunta con gpt-4o): un artículo o un capítulo cabe; un libro de 800 páginas (~2,4 millones de caracteres) no, y ahí manda la muestra.
export const DEFAULT_FULL_BOOK_CHARS = 100_000;
export const OVERVIEW_OPENING = 3; // en una pregunta general: los primeros fragmentos del libro (título, resumen, introducción)...
export const OVERVIEW_SAMPLE = 7; // ...y tantos más repartidos a partes iguales por el resto, para tener una visión de conjunto
export const OVERVIEW_ALL_BOOKS = 2; // con varios libros: los primeros fragmentos de cada uno (hasta MAX_OVERVIEW_BOOKS libros)
export const MAX_OVERVIEW_BOOKS = 6;
export const MAX_OVERVIEW_SOURCES = 5; // citas que se enseñan en una respuesta general (los fragmentos son muchos y repartidos: no se listan todos)
// Atlas normaliza la similitud del coseno a 0,5-1 (0,5 = nada que ver). Medido con el Oxford Handbook: preguntas sin relación (un pastel, un mundial de fútbol) sacan 0,51-0,54;
// las que sí vienen al caso, 0,61-0,78 (en español contra un libro en inglés baja unas centésimas). Por debajo del umbral no se llama al modelo.
export const DEFAULT_MIN_SCORE = 0.58;
const SLUG = /^[a-z0-9][a-z0-9-]{0,58}[a-z0-9]$/;

export const NOT_FOUND_TEXT = 'No encuentro nada sobre eso en el libro. Prueba a preguntarlo de otra forma o con otros términos.';
export const NOT_FOUND_ALL_TEXT = 'No encuentro nada sobre eso en tus libros. Prueba a preguntarlo de otra forma o con otros términos.';

const text = (value) => (typeof value === 'string' ? value.trim() : '');

/** Valida el cuerpo de la petición. Devuelve {ok:true, value} o {ok:false, error} con un mensaje en español. */
export function parseRequest(raw) {
  let body;
  try {
    body = typeof raw === 'string' ? JSON.parse(raw) : raw;
  } catch {
    return { ok: false, error: 'La petición no es un JSON válido.' };
  }
  if (!body || typeof body !== 'object') return { ok: false, error: 'La petición está vacía.' };

  const question = text(body.question);
  if (!question) return { ok: false, error: 'Escribe una pregunta.' };
  if (question.length > MAX_QUESTION_CHARS) return { ok: false, error: `La pregunta es demasiado larga (máximo ${MAX_QUESTION_CHARS} caracteres).` };

  let slug = null;
  if (body.slug !== null && body.slug !== undefined) {
    if (typeof body.slug !== 'string' || !SLUG.test(body.slug)) return { ok: false, error: 'Identificador de libro no válido.' };
    slug = body.slug;
  }

  const history = [];
  if (body.history !== undefined) {
    if (!Array.isArray(body.history)) return { ok: false, error: 'El historial no es válido.' };
    for (const turn of body.history.slice(-MAX_HISTORY_TURNS)) {
      const content = text(turn?.text);
      if ((turn?.role !== 'user' && turn?.role !== 'assistant') || !content) continue; // un turno raro se ignora, no tumba la pregunta
      history.push({ role: turn.role, text: content.slice(0, MAX_TURN_CHARS) });
    }
  }
  return { ok: true, value: { question, slug, history } };
}

/** El texto con el que se busca: si hay conversación, la pregunta anterior ayuda a entender un "¿y quién le sucedió?" suelto. */
export function buildQueryText(question, history) {
  const previous = [...history].reverse().find((turn) => turn.role === 'user');
  return previous ? `${previous.text}\n${question}` : question;
}

/** Los `limit` fragmentos más parecidos a la pregunta (de un libro, o de todos si `slug` es null), con su puntuación (0 a 1). */
export async function retrieve({ chunks, embed }, { query, slug, minScore }) {
  const vector = await embed(query);
  const stages = [
    {
      $vectorSearch: {
        index: VECTOR_INDEX,
        path: 'embedding',
        queryVector: vector,
        numCandidates: NUM_CANDIDATES,
        limit: RESULTS,
        ...(slug ? { filter: { source: slug } } : {}),
      },
    },
    { $match: { origen: ORIGIN } }, // solo lo escrito por esta app
    { $project: { _id: 0, text: 1, source: 1, pageStart: 1, pageEnd: 1, añoInicio: 1, añoFin: 1, score: { $meta: 'vectorSearchScore' } } },
  ];
  const found = await chunks.aggregate(stages).toArray();
  return found.filter((chunk) => chunk.score >= minScore);
}

/** Qué buscar. Si el paso previo falla (o no está), se busca con el texto de la pregunta y la anterior, como antes: una pregunta nunca se pierde por esto. */
export async function makePlan(deps, request) {
  const fallback = { general: false, query: buildQueryText(request.question, request.history) };
  if (!deps.plan) return fallback;
  try {
    const plan = await deps.plan(request.question, request.history);
    const query = typeof plan?.query === 'string' ? plan.query.trim() : '';
    return { general: plan?.general === true, query: query || fallback.query };
  } catch {
    return fallback;
  }
}

/**
 * Los índices de los fragmentos que dan una visión de conjunto: los primeros `opening` (título, resumen, introducción) y `sample` más repartidos a partes iguales
 * por el resto del libro. Si el libro es corto, todos.
 */
export function pickIndexes(indexes, opening, sample) {
  if (indexes.length <= opening + sample) return [...indexes];
  const rest = indexes.slice(opening);
  const step = rest.length / sample;
  const spread = Array.from({ length: sample }, (_, i) => rest[Math.min(rest.length - 1, Math.floor(i * step + step / 2))]);
  return [...new Set([...indexes.slice(0, opening), ...spread])];
}

/** Para una pregunta GENERAL ("¿de qué trata?", "un resumen"): no hay nada que buscar por parecido, así que se toma el comienzo del libro y una muestra de todo él. */
export async function overviewChunks({ chunks, fullBookChars = DEFAULT_FULL_BOOK_CHARS }, slug) {
  const sources = slug ? [slug] : (await chunks.distinct('source', { origen: ORIGIN })).slice(0, MAX_OVERVIEW_BOOKS);
  const projection = { _id: 0, text: 1, source: 1, pageStart: 1, pageEnd: 1, chunkIndex: 1 };
  const found = [];
  for (const source of sources) {
    const base = { source, origen: ORIGIN };
    // Un libro (el que se pregunta) que cabe entero en el presupuesto: se lee entero, que es lo mejor para un resumen.
    if (slug) {
      const [size] = await chunks.aggregate([{ $match: base }, { $group: { _id: null, chars: { $sum: { $strLenCP: '$text' } } } }]).toArray();
      if (size && size.chars > 0 && size.chars <= fullBookChars) {
        found.push(...(await chunks.find(base, { projection }).sort({ chunkIndex: 1 }).toArray()).map((doc) => ({ ...doc, score: 1 })));
        continue;
      }
    }
    const indexes = (await chunks.find(base, { projection: { _id: 0, chunkIndex: 1 } }).sort({ chunkIndex: 1 }).toArray()).map((doc) => doc.chunkIndex);
    const wanted = slug ? pickIndexes(indexes, OVERVIEW_OPENING, OVERVIEW_SAMPLE) : pickIndexes(indexes, OVERVIEW_ALL_BOOKS, 0);
    if (wanted.length === 0) continue;
    const docs = await chunks
      .find({ ...base, chunkIndex: { $in: wanted } }, { projection })
      .sort({ chunkIndex: 1 })
      .toArray();
    found.push(...docs.map((doc) => ({ ...doc, score: 1 })));
  }
  return found;
}

/** Las citas que se enseñan: libro, página y un trozo del fragmento. */
export function toSources(chunks) {
  return chunks.map((chunk) => ({
    slug: chunk.source,
    pagina: Number.isInteger(chunk.pageStart) && chunk.pageStart >= 1 ? chunk.pageStart : 1,
    texto: chunk.text.replace(/\s+/g, ' ').trim().slice(0, 160),
  }));
}

const SYSTEM_PROMPT = [
  'Eres un asistente de lectura de libros de historia de China. Respondes en español.',
  'Respondes SOLO con la información de los fragmentos que se te dan (pueden estar en inglés u otro idioma: tradúcelos). No uses conocimiento propio ni inventes.',
  'Si los fragmentos no contienen la respuesta, dilo claramente y di qué sí aparece, sin rellenar con suposiciones.',
  'Si se te avisa de que es una pregunta GENERAL, los fragmentos son el libro entero, o su comienzo y una muestra repartida por él: da una visión de conjunto (de qué trata, qué sostiene, cómo se organiza) y no describas un fragmento suelto como si fuera todo el libro.',
  'Cita siempre la página de donde sale cada dato, con el formato (pág. N). Si hay varios libros, indica también el libro.',
  'Los nombres y términos chinos: deja los caracteres chinos tal como vengan y aclara su significado entre paréntesis si hace falta.',
  'Sé claro y directo; no repitas la pregunta. Nunca menciones "fragmentos" ni números de fragmento: solo la página (y el libro si hay varios).',
].join('\n');

/** Los mensajes para el modelo: instrucciones, la conversación previa y la pregunta con los fragmentos como contexto. */
export function buildMessages({ question, slug, history }, chunks, general = false) {
  const context = chunks
    .map((chunk) => {
      const pages = chunk.pageEnd && chunk.pageEnd !== chunk.pageStart ? `${chunk.pageStart}-${chunk.pageEnd}` : `${chunk.pageStart ?? '?'}`;
      const where = slug ? `pág. ${pages}` : `libro: ${chunk.source}, pág. ${pages}`;
      return `[${where}]\n${chunk.text}`; // sin numerar los fragmentos: el modelo acababa escribiendo "(Fragmento 3)" en la respuesta
    })
    .join('\n\n---\n\n');
  return [
    { role: 'system', content: SYSTEM_PROMPT },
    ...history.map((turn) => ({ role: turn.role, content: turn.text })),
    { role: 'user', content: `Fragmentos${general ? ' (pregunta GENERAL: el libro entero o, si es muy largo, su comienzo y una muestra repartida)' : ''}:\n\n${context}\n\nPregunta: ${question}` },
  ];
}

/**
 * Responde una pregunta: busca, emite las citas y va emitiendo la respuesta según llega del modelo.
 * `emit(evento)` escribe una línea; `deps`: { chunks, embed, streamChat, minScore }.
 */
export async function answer(deps, request, emit) {
  const plan = await makePlan(deps, request);
  const chunks = plan.general
    ? await overviewChunks(deps, request.slug)
    : await retrieve(deps, { query: plan.query, slug: request.slug, minScore: deps.minScore ?? DEFAULT_MIN_SCORE });
  if (chunks.length === 0) {
    // Nada relevante: no se llama al modelo (ni se paga) y se dice claramente.
    emit({ type: 'fuentes', fuentes: [] });
    emit({ type: 'delta', text: request.slug ? NOT_FOUND_TEXT : NOT_FOUND_ALL_TEXT });
    emit({ type: 'fin', origen: 'ninguno' });
    return { chunks: 0 };
  }
  emit({ type: 'fuentes', fuentes: toSources(plan.general ? chunks.slice(0, MAX_OVERVIEW_SOURCES) : chunks) });
  for await (const delta of deps.streamChat(buildMessages(request, chunks, plan.general))) {
    if (delta) emit({ type: 'delta', text: delta });
  }
  emit({ type: 'fin', origen: 'libro' });
  return { chunks: chunks.length };
}
