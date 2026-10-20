/**
 * Lee una respuesta en flujo de líneas JSON (NDJSON): cada línea es un evento. Los trozos llegan como lleguen (una línea puede venir partida entre dos trozos, o varias
 * juntas), así que se acumula hasta tener la línea completa. Una línea que no es JSON se ignora: un fallo de formato no debe tumbar la respuesta entera.
 */
export async function* readNdjson(body: ReadableStream<Uint8Array>): AsyncGenerator<unknown> {
  const reader = body.getReader();
  const decoder = new TextDecoder();
  let pending = '';
  const parse = (line: string): unknown[] => {
    if (!line.trim()) return [];
    try {
      return [JSON.parse(line)];
    } catch {
      return [];
    }
  };
  try {
    for (;;) {
      const { done, value } = await reader.read();
      if (done) break;
      pending += decoder.decode(value, { stream: true });
      const lines = pending.split('\n');
      pending = lines.pop() ?? ''; // lo último puede ser media línea: se guarda para el siguiente trozo
      for (const line of lines) yield* parse(line);
    }
    pending += decoder.decode();
    yield* parse(pending);
  } finally {
    reader.releaseLock();
  }
}
