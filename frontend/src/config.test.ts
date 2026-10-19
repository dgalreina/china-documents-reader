import { describe, expect, it } from 'vitest';
import { friendlyAuthError } from './auth/errors';
import { s3ErrorMessage } from './api/s3Upload';
import { loadConfig, parseConfig } from './config';

const valid = { region: 'eu-west-1', apiUrl: 'https://abc.execute-api.eu-west-1.amazonaws.com/', userPoolId: 'eu-west-1_X', userPoolClientId: 'cliente' };

describe('parseConfig', () => {
  it('acepta una configuración completa y quita la barra final de la dirección', () => {
    expect(parseConfig(valid)).toEqual({ ...valid, apiUrl: 'https://abc.execute-api.eu-west-1.amazonaws.com' });
  });

  it('dice qué campos faltan', () => {
    expect(() => parseConfig({ ...valid, userPoolId: '', userPoolClientId: undefined })).toThrow(/userPoolId, userPoolClientId/);
    expect(() => parseConfig(null)).toThrow(/Falta en config.json/);
  });

  it('la dirección de la API tiene que ser una URL', () => {
    expect(() => parseConfig({ ...valid, apiUrl: 'abc.example' })).toThrow(/https:\/\//);
  });
});

describe('loadConfig', () => {
  const answer = (body: string, status = 200) => (async () => new Response(body, { status })) as unknown as typeof fetch;

  it('lee /config.json', async () => {
    expect((await loadConfig(answer(JSON.stringify(valid)))).userPoolId).toBe('eu-west-1_X');
  });

  it('si no es JSON (al desarrollar, Vite contesta con index.html) explica cómo crearlo', async () => {
    await expect(loadConfig(answer('<!doctype html><html></html>'))).rejects.toThrow(/write-front-config/);
    await expect(loadConfig(answer('no existe', 404))).rejects.toThrow(/No se encontró config.json/);
  });

  it('sin conexión, lo dice', async () => {
    await expect(loadConfig((async () => Promise.reject(new TypeError('x'))) as unknown as typeof fetch)).rejects.toThrow(/Comprueba tu conexión/);
  });
});

describe('friendlyAuthError', () => {
  it('correo o contraseña mal: el mismo mensaje en los dos casos (no se revela si el correo existe)', () => {
    expect(friendlyAuthError({ code: 'NotAuthorizedException', message: 'Incorrect username or password.' })).toBe('Correo o contraseña incorrectos.');
    expect(friendlyAuthError({ code: 'UserNotFoundException' })).toBe('Correo o contraseña incorrectos.');
  });

  it('demasiados intentos y sin conexión tienen su mensaje; lo desconocido no filtra el texto original', () => {
    expect(friendlyAuthError({ code: 'TooManyRequestsException' })).toMatch(/Demasiados intentos/);
    expect(friendlyAuthError({ message: 'Network error' })).toMatch(/No hay conexión/);
    const unknown = friendlyAuthError({ code: 'InternalErrorException', message: 'detalle interno de AWS' });
    expect(unknown).toMatch(/No se pudo iniciar sesión/);
    expect(unknown).not.toMatch(/AWS/);
  });
});

describe('s3ErrorMessage', () => {
  it('traduce lo habitual y, si no, da el código', () => {
    expect(s3ErrorMessage(400, '<Error><Code>EntityTooLarge</Code></Error>')).toMatch(/tamaño máximo/);
    expect(s3ErrorMessage(403, '<Error><Code>AccessDenied</Code><Message>Request has expired</Message></Error>')).toMatch(/caducó/);
    expect(s3ErrorMessage(500, '')).toBe('S3 rechazó la subida (HTTP 500).');
  });
});
