const API_BASE = "http://localhost:8000";

export interface LineItem {
  description: string;
  amount: number;
}

export interface ExtractedFields {
  vendor_name: string | null;
  invoice_number: string | null;
  amount: number | null;
  due_date: string | null;
  po_number: string | null;
  line_items: LineItem[];
}

export interface ExtractResponse {
  used_vision_fallback: boolean;
  raw_text: string;
  fields: ExtractedFields;
}

export async function extractInvoice(file: File): Promise<ExtractResponse> {
  const formData = new FormData();
  formData.append("file", file);

  const response = await fetch(`${API_BASE}/extract`, {
    method: "POST",
    body: formData,
  });

  if (!response.ok) {
    throw new Error(`Extraction failed: ${response.status} ${response.statusText}`);
  }

  return response.json();
}
