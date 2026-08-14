// The agent's reasoning is free text, but it is written consistently: one
// opening sentence, a blank line, then numbered or labeled findings each
// separated by another blank line -- e.g. "1. Vendor resolved..." or
// "VENDOR: lookup_vendor returned...". Splitting on the blank lines the model
// already puts there turns that into a real summary-plus-list without asking
// the agent to produce a second, structured field just for display -- this is
// a presentation problem, not a change to what the agent is asked to do.
const LABEL_RE = /^([A-Z][A-Za-z0-9 ()'"–—-]{1,60}?)(:|\s+—)\s*(.*)$/s;

interface ParsedReasoning {
  headline: string;
  points: { label: string | null; text: string }[];
}

function parseReasoning(reasoning: string): ParsedReasoning {
  const paragraphs = reasoning
    .split(/\n{2,}/)
    .map((p) => p.trim())
    .filter(Boolean);

  const [headline = reasoning.trim(), ...rest] = paragraphs;
  const points: ParsedReasoning["points"] = [];

  for (const paragraph of rest) {
    // Some findings are only one newline apart from a header that introduces
    // them -- "FINDINGS:\n1. Vendor resolved..." -- so a paragraph that
    // survived the blank-line split above can still bundle a header with the
    // numbered item after it. Splitting again on a newline immediately before
    // "N. " separates the two.
    for (const piece of paragraph.split(/\n(?=\d+\.\s)/)) {
      // A leading "N. " duplicates the <ol>'s own numbering once rendered as
      // a list item, so it is dropped rather than shown twice.
      const withoutOrdinal = piece.trim().replace(/^\d+\.\s*/, "");
      const match = withoutOrdinal.match(LABEL_RE);
      const label = match ? match[1] : null;
      const text = match ? match[3].trim() : withoutOrdinal;

      if (!text) continue; // a bare header like "FINDINGS:" with nothing after it
      points.push({ label, text });
    }
  }

  return { headline, points };
}

interface ReasoningSummaryProps {
  reasoning: string | null;
}

function ReasoningSummary({ reasoning }: ReasoningSummaryProps) {
  if (!reasoning) {
    return <p>No reasoning recorded.</p>;
  }

  const { headline, points } = parseReasoning(reasoning);

  return (
    <div className="reasoning">
      <p className="reasoning-headline">{headline}</p>
      {points.length > 0 && (
        <ol className="reasoning-points">
          {points.map((point, index) => (
            <li key={index}>
              {point.label && <strong>{point.label}: </strong>}
              {point.text}
            </li>
          ))}
        </ol>
      )}
    </div>
  );
}

export default ReasoningSummary;
