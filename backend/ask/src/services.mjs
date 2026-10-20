// Lo externo del agente: secretos, Mongo, OpenAI y la verificación del login de Cognito. Se crea una vez por contenedor "caliente" y se reutiliza.
import { CognitoJwtVerifier } from 'aws-jwt-verify';
import { MongoClient } from 'mongodb';
import OpenAI from 'openai';
import { DEFAULT_FULL_BOOK_CHARS, DEFAULT_MIN_SCORE, EMBEDDING_MODEL } from './core.mjs';

const secretCache = new Map();

const PLANNER_PROMPT = [
  'You prepare searches over history books about China (the books are in English). Given the user question (usually in Spanish) and the recent conversation, answer ONLY with JSON:',
  '{"general": boolean, "query": string}',
  '"general" is true ONLY if the user asks about the whole book or document: what it is about, a summary, its main argument or thesis, its structure, who wrote it, what it covers.',
  'Otherwise false. "query" is a standalone search query in English that captures what the user wants to find, resolving pronouns and references using the conversation.',
  'Keep Chinese names and terms as they are (pinyin or characters). No extra text.',
].join('\n');

/** Un secreto: de la variable de entorno (en local, el `.env`) o, en AWS, de SSM Parameter Store (`SSM_PREFIX/<NOMBRE>`). Nunca se imprime. */
export async function getSecret(name) {
  if (process.env[name]) return process.env[name];
  const prefix = process.env.SSM_PREFIX;
  if (!prefix) return undefined;
  const key = `${prefix.replace(/\/+$/, '')}/${name}`;
  if (!secretCache.has(key)) {
    const { SSMClient, GetParameterCommand } = await import('@aws-sdk/client-ssm'); // viene en el runtime de Lambda; en local, como dependencia de desarrollo
    const response = await new SSMClient({}).send(new GetParameterCommand({ Name: key, WithDecryption: true }));
    secretCache.set(key, response.Parameter.Value);
  }
  return secretCache.get(key);
}

/** Cognito: comprueba la firma, la caducidad y que el token sea de ESTE user pool y ESTA aplicación web. */
export function makeVerifier(env = process.env) {
  const verifier = CognitoJwtVerifier.create({ userPoolId: env.USER_POOL_ID, tokenUse: 'id', clientId: env.USER_POOL_CLIENT_ID });
  return (token) => verifier.verify(token);
}

let cached;

/** Mongo y OpenAI listos para usar. El destino (dev o prod) lo decide SOLO el despliegue (`MONGO_TARGET`), nunca la petición. */
export async function getServices(env = process.env) {
  if (cached) return cached;
  const target = env.MONGO_TARGET === 'prod' ? 'prod' : 'dev';
  const uri = await getSecret(target === 'prod' ? 'MONGODB_URI_PROD' : 'MONGODB_URI_DEV');
  const apiKey = await getSecret('OPENAI_API_KEY');
  if (!uri || !apiKey) throw new Error('Faltan secretos de configuración.');
  const dbName = target === 'prod' ? env.MONGODB_DB_PROD ?? 'china-history' : env.MONGODB_DB_DEV ?? 'china_documents_dev';

  const mongo = new MongoClient(uri, { serverSelectionTimeoutMS: 8000, maxPoolSize: 2 });
  await mongo.connect();
  const openai = new OpenAI({ apiKey });
  const model = env.ASK_MODEL ?? 'gpt-4o';

  cached = {
    chunks: mongo.db(dbName).collection(env.CHUNKS_COLLECTION ?? 'book_chunks'),
    minScore: Number(env.ASK_MIN_SCORE ?? DEFAULT_MIN_SCORE),
    fullBookChars: Number(env.ASK_FULL_BOOK_CHARS ?? DEFAULT_FULL_BOOK_CHARS),
    async embed(input) {
      const response = await openai.embeddings.create({ model: EMBEDDING_MODEL, input });
      return response.data[0].embedding;
    },
    // El paso previo: decide si la pregunta es GENERAL sobre el libro y la reescribe como una consulta de búsqueda autónoma en inglés (los libros están en inglés y
    // "¿y quién le sucedió?" solo se entiende con la conversación). Un modelo pequeño basta: es una tarea sencilla.
    async plan(question, history) {
      const recent = history.slice(-4).map((turn) => `${turn.role === 'user' ? 'Usuario' : 'Asistente'}: ${turn.text.slice(0, 300)}`).join('\n');
      const response = await openai.chat.completions.create({
        model: env.ASK_PLANNER_MODEL ?? 'gpt-4o-mini',
        temperature: 0,
        response_format: { type: 'json_object' },
        messages: [
          { role: 'system', content: PLANNER_PROMPT },
          { role: 'user', content: `${recent ? `Conversación reciente:\n${recent}\n\n` : ''}Pregunta: ${question}` },
        ],
      });
      return JSON.parse(response.choices[0].message.content);
    },
    async *streamChat(messages) {
      const stream = await openai.chat.completions.create({ model, messages, stream: true, temperature: 0 });
      for await (const part of stream) yield part.choices[0]?.delta?.content ?? '';
    },
    async close() {
      await mongo.close();
    },
  };
  return cached;
}
