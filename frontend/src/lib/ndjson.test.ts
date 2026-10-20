import { describe, expect, it } from 'vitest';
import { readNdjson } from './ndjson';

const streamOf = (...chunks: string[]) => {
  const encoder = new TextEncoder();
  return new ReadableStream<Uint8Array>({
    start(controller) {
      for (const chunk of chunks) controller.enqueue(encoder.encode(chunk));
      controller.close();
    },
  });
};
const collect = async (stream: ReadableStream<Uint8Array>) => {
  const out: unknown[] = [];
  for await (const item of readNdjson(stream)) out.push(item);
  return out;
};

describe('readNdjson', () => {
  it('una línea por evento', async () => {
    expect(await collect(streamOf('{"a":1}\n{"a":2}\n'))).toEqual([{ a: 1 }, { a: 2 }]);
  });
  it('una línea partida entre dos trozos se junta', async () => {
    expect(await collect(streamOf('{"type":"del', 'ta","text":"hola"}\n'))).toEqual([{ type: 'delta', text: 'hola' }]);
  });
  it('varias líneas en un solo trozo, y la última sin salto de línea', async () => {
    expect(await collect(streamOf('{"a":1}\n{"a":2}\n{"a":3}'))).toEqual([{ a: 1 }, { a: 2 }, { a: 3 }]);
  });
  it('un carácter con tilde partido por la mitad entre trozos no se rompe', async () => {
    const bytes = new TextEncoder().encode('{"t":"é"}\n');
    const stream = new ReadableStream<Uint8Array>({
      start(controller) {
        controller.enqueue(bytes.slice(0, 7)); // corta dentro de la "é" (2 bytes)
        controller.enqueue(bytes.slice(7));
        controller.close();
      },
    });
    expect(await collect(stream)).toEqual([{ t: 'é' }]);
  });
  it('las líneas vacías o rotas se ignoran', async () => {
    expect(await collect(streamOf('\n{no es json}\n{"ok":true}\n\n'))).toEqual([{ ok: true }]);
  });
});
