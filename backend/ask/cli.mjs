// Prueba el agente en tu ordenador con los servicios REALES (OpenAI y la base de dev): lee el `.env` de la raíz del repo.
// Uso:   node cli.mjs "¿Quién fue el rey Wu?" [slug-del-libro]
// Cuesta céntimos (un embedding y una respuesta). No imprime ningún secreto.
import { answer, parseRequest } from './src/core.mjs';
import { getServices } from './src/services.mjs';

try {
  process.loadEnvFile(new URL('../../.env', import.meta.url));
} catch {
  /* sin .env: se usan las variables del entorno */
}

const [question, slug] = process.argv.slice(2);
const parsed = parseRequest({ question, slug: slug ?? null });
if (!parsed.ok) {
  console.error(parsed.error);
  process.exit(1);
}
const services = await getServices();
const started = Date.now();
let firstDelta = null;
await answer(services, parsed.value, (event) => {
  if (event.type === 'fuentes') {
    console.log(`Fuentes (${event.fuentes.length}):`);
    for (const source of event.fuentes) console.log(`  - ${source.slug} pág. ${source.pagina}: ${source.texto.slice(0, 90)}`);
    console.log('');
  } else if (event.type === 'delta') {
    firstDelta ??= Date.now() - started;
    process.stdout.write(event.text);
  } else if (event.type === 'fin') {
    console.log(`\n\n[fin: ${event.origen} · primer trozo a los ${firstDelta ?? '-'} ms · total ${Date.now() - started} ms]`);
  }
});
await services.close();
