import type { SourceCitation } from "@/api/client";

/**
 * A citation is only useful if the reviewer can go and look. #25 forbids inventing a
 * highlight when the region cannot be located, so a citation whose geometry is degenerate
 * is shown as "page only" rather than drawn at a plausible-looking rectangle.
 *
 * The bbox is bottom-left origin in PDF points, as produced by the extraction adapters.
 */
export function locatable(citation: SourceCitation): boolean {
  const box = citation.bbox;
  if (!Array.isArray(box) || box.length !== 4) {
    return false;
  }
  const [x0, y0, x1, y1] = box;
  return [x0, y0, x1, y1].every(Number.isFinite) && x1 > x0 && y1 > y0;
}

export function CitationItem({ citation }: { citation: SourceCitation }) {
  const canLocate = locatable(citation);
  return (
    <li className="card">
      <dl className="kv">
        <dt>Document</dt>
        <dd>
          {citation.document_id} <span className="muted">v{citation.version}</span>
        </dd>
        <dt>Page</dt>
        <dd>{citation.page}</dd>
        <dt>Region</dt>
        <dd>
          {canLocate ? (
            citation.region_id
          ) : (
            <span data-testid="unlocatable">
              {citation.region_id} — <strong>position unavailable</strong>
            </span>
          )}
        </dd>
      </dl>
      {citation.excerpt === "" ? null : (
        <blockquote
          style={{ margin: "0.6rem 0 0", paddingLeft: "0.75rem", borderLeft: "3px solid" }}
        >
          {citation.excerpt}
        </blockquote>
      )}
      {canLocate ? null : (
        <p className="notice" data-tone="warn" style={{ marginBottom: 0 }}>
          This citation names a page but carries no usable region, so no area is highlighted. Open
          page {citation.page} of {citation.document_id} and read it directly.
        </p>
      )}
    </li>
  );
}

export function EvidenceList({ citations }: { citations: readonly SourceCitation[] }) {
  if (citations.length === 0) {
    return (
      <p className="notice" data-tone="warn">
        This task cites no evidence. Treat every value on it as unverified and read the source
        document before answering.
      </p>
    );
  }
  return (
    <ul className="plain" aria-label="Evidence">
      {citations.map((citation) => (
        <CitationItem
          key={`${citation.document_id}:${citation.page}:${citation.region_id}`}
          citation={citation}
        />
      ))}
    </ul>
  );
}
