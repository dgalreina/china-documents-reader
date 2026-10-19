import type { ReactNode } from 'react';
import type { PDFDocumentProxy } from 'pdfjs-dist';
import type { PdfState } from '../state/usePdfDocument';
import { CenteredSpinner } from './Spinner';

interface PdfGateProps {
  /** La URL temporal del PDF (aún no llegó si es undefined). */
  url?: string;
  /** Por qué no se pudo conseguir la URL. */
  urlError?: string;
  pdf: PdfState;
  children: (doc: PDFDocumentProxy) => ReactNode;
}

/** Los estados previos a tener el PDF (pidiendo la URL, error, cargando) son los mismos para el visor original y para el lector en español. */
export function PdfGate({ url, urlError, pdf, children }: PdfGateProps) {
  const error = urlError ?? pdf.error;
  if (error) return <div role="alert" className="flex h-full items-center justify-center p-6 text-center text-seal-dark">{error}</div>;
  if (!url || !pdf.doc) return <CenteredSpinner caption="Abriendo el libro…" />;
  return <>{children(pdf.doc)}</>;
}
