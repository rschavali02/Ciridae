import { invoiceFileUrl } from "../api";

interface PdfPreviewProps {
  invoiceId: string;
}

function PdfPreview({ invoiceId }: PdfPreviewProps) {
  // <embed> over <iframe>: the browser's native PDF viewer, with no chrome of
  // ours to keep in sync with a document we do not control the contents of.
  return (
    <embed
      src={invoiceFileUrl(invoiceId)}
      type="application/pdf"
      className="pdf-preview"
      title="Invoice preview"
    />
  );
}

export default PdfPreview;
