// Genera los iconos de china-documents-reader (favicon.ico, apple-touch-icon,
// icon-192/512, icon-maskable-512, og-image) a partir del perfil vectorial
// del jue (data/jue-path.txt), relleno hueco sobre un fondo degradado en
// rojo — mismo patrón que generate-app-icons.mjs en china-history-web, pero
// con el jue en vez del ding y rojo (dinastía Zhou) en vez de verde.
// Uso: node scripts/generate-app-icons.mjs
import sharp from 'sharp';
import { writeFile, mkdir, readFile } from 'node:fs/promises';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const root = path.join(__dirname, '..');

// Perfil exterior del jue — calco EXACTO (potrace) de una foto de
// referencia del usuario, 2000px de ancho, threshold 200 (el que mejor
// conservaba la punta de las tres patas sin ruido de textura de la
// pátina). Un único path, sin huecos reales (el jue no tiene ninguno).
const JUE = (await readFile(path.join(root, 'data/jue-path.txt'), 'utf-8')).trim();

// Bbox real del calco (coordenadas de la imagen de 2000px de ancho usada
// para trazar) — x en [444.939, 1479.276], y en [155.352, 1344.235].
const JUE_CENTRO_X = 962.108;
const JUE_CENTRO_Y = 749.793;
const JUE_ALTURA = 1188.883;

// Carácter en escritura de bronce arcaica (dos glifos) que va como máscara
// de luz de fondo, detrás del jue — mismo tratamiento que el carácter del
// ding en china-history-web: calco EXACTO con potrace (la imagen de
// referencia ya era blanco/negro puro, sin necesidad de tantear threshold).
const GLIFO = (await readFile(path.join(root, 'data/glifo-path.txt'), 'utf-8')).trim();

// Bbox real del calco (imagen de 1800px de ancho usada para trazar).
const GLIFO_CENTRO_X = 925.271;
const GLIFO_CENTRO_Y = 1413.009;
const GLIFO_ALTURA = 2630.019;

// Fondo: mismo degradado diagonal de varias paradas que china-history-web,
// pero recorriendo tonos de laca/bronce rojo (vermellón → rojo sello de
// --seal del portfolio → rojo oscuro --seal-dark → casi negro-rojizo en
// la esquina) en vez de verdes. SEAL/SEAL_DARK son los únicos que vienen
// literalmente de la marca (portfolio/app/globals.css); el resto son
// variaciones alrededor de ese mismo rojo, no colores sueltos.
const PATINA = [
  [0, '#c96a4e'], // vermellón claro
  [25, '#8e1b1b'], // --seal (portfolio)
  [55, '#6c1212'], // --seal-dark (portfolio)
  [80, '#3d0a0a'], // rojo oscuro, casi granate
  [100, '#1f0606'], // casi negro-rojizo, rincón
];

// Grosor del trazo en pantalla, como fracción del lienzo: el mismo que el
// ding (stroke-width 16 sobre un path de 512 unidades, al 72% del lienzo
// en 1024px) — 16 * (1024*0.72/471.978) / 1024 ≈ 0.02441. El path del jue
// viene de un calco de 2000px, así que su stroke-width en unidades propias
// se recalcula cada vez a partir de `escala` para dar el mismo grosor
// visual, en vez de copiar el 16 literal (que aquí quedaría mucho más fino).
const GROSOR_TRAZO_FRACCION = 0.019;

function svgIcono({ escala, despl, deslX, deslY, ancho = 1024, alto = 1024 }) {
  const dx = deslX ?? despl;
  const dy = deslY ?? despl;
  const grosorTrazo = (GROSOR_TRAZO_FRACCION * Math.min(ancho, alto)) / escala;
  // El glifo va como máscara de luz de fondo, centrado en el lienzo entero
  // y ocupando casi toda su altura — mismo patrón que china-history-web.
  const glowEscala = (Math.min(ancho, alto) * 0.92) / GLIFO_ALTURA;
  const glowDespl = { x: ancho / 2 - GLIFO_CENTRO_X * glowEscala, y: alto / 2 - GLIFO_CENTRO_Y * glowEscala };
  const paradas = PATINA.map(([pos, color]) => `<stop offset="${pos}%" stop-color="${color}" />`).join('\n      ');
  return `<svg xmlns="http://www.w3.org/2000/svg" width="${ancho}" height="${alto}" viewBox="0 0 ${ancho} ${alto}">
  <defs>
    <linearGradient id="fondo" x1="5%" y1="0%" x2="100%" y2="100%">
      ${paradas}
    </linearGradient>
    <radialGradient id="brilloCalido" cx="26%" cy="22%" r="55%">
      <stop offset="0%" stop-color="#ffffff" stop-opacity="0.1" />
      <stop offset="50%" stop-color="#ffffff" stop-opacity="0.03" />
      <stop offset="100%" stop-color="#ffffff" stop-opacity="0" />
    </radialGradient>
    <radialGradient id="rinconOscuro" cx="88%" cy="90%" r="60%">
      <stop offset="0%" stop-color="#0a0202" stop-opacity="0.3" />
      <stop offset="55%" stop-color="#0a0202" stop-opacity="0.1" />
      <stop offset="100%" stop-color="#0a0202" stop-opacity="0" />
    </radialGradient>
    <linearGradient id="blancoGris" x1="10%" y1="0%" x2="90%" y2="100%">
      <stop offset="0%" stop-color="#ffffff" />
      <stop offset="30%" stop-color="#f5f5f5" />
      <stop offset="100%" stop-color="#6b6b6b" />
    </linearGradient>
    <!-- Bisel real (no simulado): mismo filtro que china-history-web. -->
    <filter id="bisel" x="-30%" y="-30%" width="160%" height="160%">
      <feGaussianBlur in="SourceAlpha" stdDeviation="2.2" result="blur" />
      <feSpecularLighting in="blur" surfaceScale="2.5" specularConstant="0.75" specularExponent="14" lighting-color="#ffffff" result="luz">
        <feDistantLight azimuth="235" elevation="55" />
      </feSpecularLighting>
      <feComposite in="luz" in2="SourceAlpha" operator="in" result="luzRecortada" />
      <feComposite in="SourceGraphic" in2="luzRecortada" operator="arithmetic" k1="0" k2="1" k3="1" k4="0" />
    </filter>
    <!-- Difuminado amplio para que el glifo-máscara lea como una zona más
         luminosa del propio degradado, no como una silueta recortada. -->
    <filter id="desenfoqueGlow" x="-40%" y="-40%" width="180%" height="180%">
      <feGaussianBlur stdDeviation="${Math.min(ancho, alto) * 0.022}" />
    </filter>
    <mask id="maskaraGlifo">
      <rect width="${ancho}" height="${alto}" fill="black" />
      <g transform="translate(${glowDespl.x}, ${glowDespl.y}) scale(${glowEscala})">
        <path d="${GLIFO}" fill="white" fill-rule="evenodd" />
      </g>
    </mask>
  </defs>
  <rect width="${ancho}" height="${alto}" fill="url(#fondo)" />
  <rect width="${ancho}" height="${alto}" fill="url(#brilloCalido)" />
  <rect width="${ancho}" height="${alto}" fill="url(#rinconOscuro)" />
  <rect width="${ancho}" height="${alto}" fill="#ffffff" opacity="0.22" mask="url(#maskaraGlifo)" filter="url(#desenfoqueGlow)" />
  <g transform="translate(${dx}, ${dy}) scale(${escala})" filter="url(#bisel)">
    <path d="${JUE}" fill="none" stroke="url(#blancoGris)" stroke-width="${grosorTrazo}" stroke-linejoin="round" stroke-linecap="round" />
  </g>
</svg>`;
}

// `escala` coloca la altura del jue (su lado más largo) a la fracción
// deseada de los 1024px del lienzo; `despl` recentra el bbox real en el
// centro del lienzo (512, 512) — mismo método que china-history-web.
function parametros(fraccion, ancho = 1024, alto = 1024) {
  const escala = (Math.min(ancho, alto) * fraccion) / JUE_ALTURA;
  const deslX = ancho / 2 - JUE_CENTRO_X * escala;
  const deslY = alto / 2 - JUE_CENTRO_Y * escala;
  return { escala, deslX, deslY, ancho, alto };
}

// Jue ocupando ~72% del lienzo (icono normal) vs ~56% (maskable, con
// margen de seguridad de sobra para el recorte circular de Android).
const ICONO_NORMAL = svgIcono(parametros(0.82));
const ICONO_MASKABLE = svgIcono(parametros(0.62));

// Imagen Open Graph — mismo lienzo que el icono pero 1200×630.
const OG_ANCHO = 1200;
const OG_ALTO = 630;
const ICONO_OG = svgIcono(parametros(0.86, OG_ANCHO, OG_ALTO));

async function escribir(svg, destino, width, height = width) {
  await mkdir(path.dirname(destino), { recursive: true });
  await sharp(Buffer.from(svg)).resize(width, height).png().toFile(destino);
  console.log(`✓ ${path.relative(root, destino)} (${width}x${height})`);
}

await escribir(ICONO_NORMAL, path.join(root, 'public/icon-192.png'), 192);
await escribir(ICONO_NORMAL, path.join(root, 'public/icon-512.png'), 512);
await escribir(ICONO_MASKABLE, path.join(root, 'public/icon-maskable-512.png'), 512);
await escribir(ICONO_NORMAL, path.join(root, 'public/apple-touch-icon.png'), 180);
await escribir(ICONO_OG, path.join(root, 'public/og-image.png'), OG_ANCHO, OG_ALTO);

// favicon.ico — sharp no lo escribe; se renderizan los tamaños habituales
// a PNG y se empaquetan con png-to-ico.
const pngToIco = (await import('png-to-ico')).default;
const tamañosFavicon = [16, 32, 48];
const pngsFavicon = await Promise.all(
  tamañosFavicon.map((s) => sharp(Buffer.from(ICONO_NORMAL)).resize(s, s).png().toBuffer()),
);
const ico = await pngToIco(pngsFavicon);
await writeFile(path.join(root, 'public/favicon.ico'), ico);
console.log(`✓ public/favicon.ico (${tamañosFavicon.join('/')} )`);

console.log('\nListo.');
