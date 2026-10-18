import type { ReactNode } from 'react';
import type { PDFDocumentProxy } from 'pdfjs-dist';
import type { PdfState } from '../state/usePdfDocument';

interface PdfGateProps {
  url?: string;
  pdf: PdfState;
  /** Sin PDF disponible, se ofrece abrir uno desde el ordenador. */
  onPickLocal: (file: File) => void;
  children: (doc: PDFDocumentProxy) => ReactNode;
}

/** Los estados previos a tener el PDF (sin PDF, error, cargando) son los mismos para el visor original y para el lector en español. */
export function PdfGate({ url, pdf, onPickLocal, children }: PdfGateProps) {
  if (!url) {
    return (
      <div className="flex h-full flex-col items-center justify-center gap-4 p-6 text-center">
        <p className="max-w-sm text-stone">Este libro no tiene PDF disponible en la maqueta.</p>
        <label className="cursor-pointer rounded bg-seal px-4 py-2 text-sm font-semibold text-white hover:bg-seal-dark">
          Abrir un PDF desde mi ordenador
          <input
            type="file"
            accept="application/pdf"
            className="hidden"
            onChange={(event) => {
              const file = event.target.files?.[0];
              if (file) onPickLocal(file);
            }}
          />
        </label>
      </div>
    );
  }
  if (pdf.error) return <div className="flex h-full items-center justify-center p-6 text-center text-seal-dark">{pdf.error}</div>;
  if (!pdf.doc) return <div className="flex h-full items-center justify-center text-stone">Cargando el PDF…</div>;
  return <>{children(pdf.doc)}</>;
}
