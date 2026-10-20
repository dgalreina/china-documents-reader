// Punto de entrada de AWS Lambda con streaming de la respuesta (Function URL en modo RESPONSE_STREAM). `awslambda` es un global del runtime de Node.js.
import { handleRequest } from './app.mjs';
import { getServices, makeVerifier } from './services.mjs';

let verifyToken;

export const handler = awslambda.streamifyResponse(async (event, responseStream) => {
  verifyToken ??= makeVerifier();
  await handleRequest(
    event,
    { verifyToken, getDeps: () => getServices(), log: (message) => console.log(message) },
    (statusCode, headers) => awslambda.HttpResponseStream.from(responseStream, { statusCode, headers }),
  );
});
