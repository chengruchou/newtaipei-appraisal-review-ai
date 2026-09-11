import { useId, useState } from "react";
import { useText } from "./Language";
import type { SourceCitation } from "@/api/client";
import { PdfEvidence, type SourceLoader } from "./PdfEvidence";

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

export function CitationItem({
  citation,
  loadSource,
}: {
  citation: SourceCitation;
  loadSource?: SourceLoader;
}) {
  const t = useText();
  const canLocate = locatable(citation);
  return (
    <li className="card" style={{ overflowWrap: "anywhere" }}>
      <dl className="kv">
        <dt>{t("Document", "文件")}</dt>
        <dd>
          {citation.document_id} <span className="muted">v{citation.version}</span>
        </dd>
        <dt>{t("Page", "頁碼")}</dt>
        <dd>{citation.page}</dd>
        <dt>{t("Region", "證據區域")}</dt>
        <dd>
          {canLocate ? (
            citation.region_id
          ) : (
            <span data-testid="unlocatable">
              {citation.region_id} — <strong>{t("position unavailable", "無法定位")}</strong>
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
      <details className="technical">
        <summary>{t("Source identity and coordinates", "來源識別與座標")}</summary>
        <dl className="kv">
          <dt>SHA-256</dt>
          <dd>
            <code>{citation.content_hash}</code>
          </dd>
          <dt>{t("PDF coordinates", "PDF 座標")}</dt>
          <dd>{citation.bbox.join(", ")}</dd>
        </dl>
      </details>
      {canLocate ? null : (
        <p className="notice" data-tone="warn" style={{ marginBottom: 0 }}>
          {t(
            `This citation names a page but carries no usable region, so no area is highlighted. Open page ${citation.page} of ${citation.document_id} and read it directly.`,
            `此引用只有頁碼，缺少可用的區域座標，因此不標示框線。請直接開啟 ${citation.document_id} 第 ${citation.page} 頁核對。`,
          )}
        </p>
      )}
      {loadSource ? <PdfEvidence citation={citation} loadSource={loadSource} /> : null}
    </li>
  );
}

export function EvidenceList({
  citations,
  loadSource,
  label,
}: {
  citations: readonly SourceCitation[];
  loadSource?: SourceLoader;
  label?: string;
}) {
  const t = useText();
  const remainingId = useId();
  const [expandedFor, setExpandedFor] = useState<string | null>(null);
  // Preserve every occurrence and raw field; expansion belongs to this exact list.
  const binding = JSON.stringify(citations);
  const expanded = expandedFor === binding;
  const visibleCount = 3;
  const first = citations.slice(0, visibleCount);
  const remaining = citations.slice(visibleCount);
  const items = (values: readonly SourceCitation[], offset: number) =>
    values.map((citation, index) => (
      <CitationItem
        key={`${citation.document_id}:${citation.version}:${citation.content_hash}:${citation.page}:${citation.region_id}:${index + offset}`}
        citation={citation}
        {...(loadSource ? { loadSource } : {})}
      />
    ));
  if (citations.length === 0) {
    return (
      <p className="notice" data-tone="warn">
        {t(
          "This task cites no evidence. Treat every value on it as unverified and read the source document before answering.",
          "此項未提供引用證據，不能將數值當作已驗證。請先取得來源再回覆。",
        )}
      </p>
    );
  }
  return (
    <div role="group" aria-label={label ?? t("Citation collection", "引用證據集合")}>
      <ul className="plain" aria-label={label ?? t("Evidence", "證據")}>
        {items(first, 0)}
      </ul>
      {remaining.length > 0 ? (
        <>
          <button
            type="button"
            aria-expanded={expanded}
            aria-controls={remainingId}
            style={{
              maxWidth: "100%",
              whiteSpace: "normal",
              textAlign: "start",
              overflowWrap: "anywhere",
            }}
            onClick={() => setExpandedFor(expanded ? null : binding)}
          >
            {expanded
              ? t(
                  `Show fewer citations (first ${visibleCount} of ${citations.length})`,
                  `收合引用（保留前 ${visibleCount} 筆，共 ${citations.length} 筆）`,
                )
              : t(
                  `Show all ${citations.length} citations (${remaining.length} more)`,
                  `顯示全部 ${citations.length} 筆引用（另有 ${remaining.length} 筆）`,
                )}
          </button>
          <ul
            id={remainingId}
            className="plain"
            hidden={!expanded}
            aria-label={t("Additional citations", "其餘引用證據")}
          >
            {items(remaining, visibleCount)}
          </ul>
        </>
      ) : null}
    </div>
  );
}
