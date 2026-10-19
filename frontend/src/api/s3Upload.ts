/** Lo que devuelve `POST /uploads`: la dirección de S3 y los campos firmados que hay que mandar junto con el fichero. */
export interface PresignedPost {
  url: string;
  fields: Record<string, string>;
}

/** Sube el PDF y avisa del avance (0 a 1). Se inyecta en `HttpApi` para poder probarlo sin red. */
export type UploadFn = (upload: PresignedPost, file: File, onProgress: (fraction: number) => void) => Promise<void>;

/** S3 contesta con un XML; de ahí se saca un mensaje en español sin enseñar el XML entero. */
export function s3ErrorMessage(status: number, body: string): string {
  if (/EntityTooLarge/.test(body)) return 'El PDF pasa del tamaño máximo permitido.';
  if (/EntityTooSmall/.test(body)) return 'El PDF está vacío.';
  if (/AccessDenied|ExpiredToken|Request has expired|RequestTimeTooSkewed/.test(body)) return 'El permiso de subida caducó. Vuelve a intentarlo.';
  return `S3 rechazó la subida (HTTP ${status}).`;
}

/**
 * Subida real: POST multipart directo a S3 (el PDF no pasa por nuestra API). Se usa XMLHttpRequest y no fetch porque fetch no informa del
 * avance de la subida. El fichero tiene que ir el ÚLTIMO en el formulario: S3 ignora todo lo que venga después.
 */
export const uploadToS3: UploadFn = (upload, file, onProgress) =>
  new Promise<void>((resolve, reject) => {
    const form = new FormData();
    for (const [name, value] of Object.entries(upload.fields)) form.append(name, value);
    form.append('file', file);

    const request = new XMLHttpRequest();
    request.open('POST', upload.url);
    request.upload.onprogress = (event) => {
      if (event.lengthComputable && event.total > 0) onProgress(Math.min(1, event.loaded / event.total));
    };
    request.onload = () => {
      if (request.status === 204 || request.status === 200 || request.status === 201) {
        onProgress(1);
        resolve();
      } else {
        reject(new Error(s3ErrorMessage(request.status, request.responseText)));
      }
    };
    request.onerror = () => reject(new Error('Se cortó la conexión mientras se subía el PDF.'));
    request.onabort = () => reject(new Error('Se canceló la subida del PDF.'));
    request.send(form);
  });
