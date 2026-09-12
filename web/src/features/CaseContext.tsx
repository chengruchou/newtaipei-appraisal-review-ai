import type { CaseContextView } from "@/api/client";
import { useText } from "@/ui/Language";
import { documentPurposeText, statusText } from "./workbench-state";

export function CaseContext({ context }: { context: CaseContextView }) {
  const t = useText();
  const identity = context.identity;
  const bundle = context.rule_bundle;
  const blocked =
    !identity ||
    !context.rules.length ||
    !context.selections.length ||
    context.selections.some((selection) => selection.status !== "unique") ||
    context.rules.some((rule) => rule.declared_status !== "approved") ||
    (bundle &&
      (!bundle.conditions_confirmed ||
        bundle.sources.some(
          (source) => source.review_status !== "reviewed" || source.unresolved?.length,
        )));
  const roleText = (role: "general_rules" | "district_basis") =>
    role === "general_rules"
      ? t("General calculation rules", "通用計算規則")
      : t("District valuation basis", "地區地價基準");
  return (
    <section className="case-context">
      <div className="context-strip">
        <div>
          <span>{t("Administrative district", "行政區")}</span>
          <strong>{identity?.district || t("Unknown", "尚未提供")}</strong>
        </div>
        <div>
          <span>{t("Land use", "用途")}</span>
          <strong>{identity?.land_use_category || t("Unknown", "尚未提供")}</strong>
        </div>
        <div>
          <span>{t("Effective date", "查估基準日")}</span>
          <strong>{identity?.effective_date ?? t("Unknown", "尚未提供")}</strong>
        </div>
        <div>
          <span>{t("Pinned rule versions", "固定規則版本")}</span>
          <strong>
            {[...new Set(context.rules.map((rule) => rule.reference.version))].join(" / ") ||
              t("No rules", "尚無規則")}
          </strong>
        </div>
      </div>
      {bundle ? (
        <div
          className="bundle-summary"
          aria-label={t("Selected rule sources", "本次選取的規則來源")}
        >
          {bundle.sources.map((source) => (
            <div className="rule-row" key={source.document_id}>
              <strong>{roleText(source.role)}</strong>
              <p>
                {source.document_id} · {t("Document version", "文件版本")} {source.version}
              </p>
              <span
                className="status-pill"
                data-status={source.review_status === "candidate" ? "needs_review" : "received"}
              >
                {source.review_status === "reviewed"
                  ? t("Catalog metadata reviewed", "目錄資料已核對")
                  : t("Candidate metadata; review required", "候選資料，尚待核對")}
              </span>
            </div>
          ))}
          <p className="small">
            {bundle.conditions_confirmed
              ? t(
                  "Case conditions confirmed in this bundle. Rule execution and publication still require separate authorization.",
                  "此組規則來源的案件條件已確認；規則執行與發布仍須另行核准。",
                )
              : t(
                  "Case conditions are not confirmed for this bundle. Source selection is not approval.",
                  "此組規則來源的案件條件尚未確認。選取來源不代表已核准。",
                )}
          </p>
        </div>
      ) : null}
      {blocked ? (
        <p className="notice" data-tone="warn" role="status">
          {t(
            "Applicable rules or case conditions are missing, ambiguous or awaiting review or approval. No district fallback is applied.",
            "適用規則或案件條件尚缺、存在衝突，或仍待核對／核准；不會默認套用其他行政區規則。",
          )}
        </p>
      ) : null}
      <details className="rule-details">
        <summary>{t("Pinned rules, sources and revision", "查看固定規則、來源與修訂版本")}</summary>
        <p className="muted">
          {t(
            "These are pinned source and material records. Selection and catalog review do not grant authority to execute or publish.",
            "此處呈現本輪固定的來源與材料紀錄。選取來源、核對目錄資料，都不會授予執行或發布權限。",
          )}
        </p>
        <h3>{t("Pinned input roles", "固定輸入角色與版本")}</h3>
        {bundle ? (
          <>
            <dl className="kv">
              <dt>{t("Catalog version", "目錄版本")}</dt>
              <dd>{bundle.catalog_version}</dd>
              <dt>{t("Catalog digest", "目錄內容雜湊")}</dt>
              <dd>
                <code>{bundle.catalog_digest}</code>
              </dd>
              <dt>{t("Primary district factor source", "主要地區因素來源")}</dt>
              <dd>{bundle.primary_criteria_document_id}</dd>
            </dl>
            {context.rule_bundle_id ? (
              <dl className="kv">
                <dt>{t("Rule bundle identifier", "規則組合識別碼")}</dt>
                <dd>
                  <code>{context.rule_bundle_id}</code>
                </dd>
              </dl>
            ) : (
              <p className="small muted">
                {t(
                  "A separate bundle identifier is not supplied. The catalog digest is not a bundle identifier.",
                  "此檢視未提供獨立規則組合識別碼。目錄雜湊不等同組合識別碼。",
                )}
              </p>
            )}
            {(bundle.condition_candidates ?? []).length > 0 ? (
              <details className="condition-candidates">
                <summary>
                  {t("Case condition candidates and original evidence", "案件條件候選與原文依據")}
                </summary>
                <p className="notice" data-tone="warn">
                  {t(
                    "These are proposed interpretations, not confirmed applicability. Current use, regulatory zoning, and the rule category remain distinct.",
                    "以下為候選解讀，尚不代表適用性已確認。現況用途、法定使用分區與規則分類分開核對。",
                  )}
                </p>
                {bundle.condition_candidates!.map((candidate, index) => (
                  <section className="rule-row" key={index}>
                    <strong>
                      {
                        {
                          case_id: t("Case", "案件"),
                          district: t("District", "行政區"),
                          zone: t("Section", "區段"),
                          land_use_category: t("Rule use category", "規則用途分類"),
                          effective_date: t("Applicable date", "適用日期"),
                          current_use: t("Current use", "現況用途"),
                          regulatory_zone: t("Regulatory zoning", "法定使用分區"),
                          target_id: t("Target", "比準地"),
                          comparable_id: t("Comparable", "比較標的"),
                        }[candidate.field]
                      }
                      ：{candidate.value}
                    </strong>
                    <p>
                      {candidate.method === "native_proposed"
                        ? t("Native parsed candidate", "原生解析候選")
                        : t("Manual interpretation candidate", "人工解讀候選")}{" "}
                      · {candidate.interpretation}
                    </p>
                    {candidate.evidence.map((citation, n) => (
                      <blockquote key={n}>
                        {citation.document_id} · {t("page", "頁碼")} {citation.page} ·{" "}
                        {citation.region_id}：{citation.excerpt}
                      </blockquote>
                    ))}
                  </section>
                ))}
              </details>
            ) : null}
            {bundle.sources.map((source) => (
              <section
                className="rule-row"
                key={source.document_id}
                aria-label={`${roleText(source.role)} ${source.document_id}`}
              >
                <h3>
                  {roleText(source.role)} · {source.document_id}
                </h3>
                <dl className="kv">
                  <dt>{t("Document version", "文件版本")}</dt>
                  <dd>{source.version}</dd>
                  <dt>{t("Catalog entry", "目錄項目與版本")}</dt>
                  <dd>
                    {source.entry_id} · {source.entry_version}
                  </dd>
                  <dt>{t("Use", "來源用途")}</dt>
                  <dd>
                    {source.use === "factor_rules"
                      ? t("Factor rules", "因素規則")
                      : t("Procedure", "作業程序")}
                  </dd>
                  <dt>{t("Applicability", "適用條件")}</dt>
                  <dd>
                    {source.district} / {source.zone} / {source.land_use_category}
                  </dd>
                  <dt>{t("Scopes", "比較範圍")}</dt>
                  <dd>
                    {source.scopes
                      .map((scope) =>
                        scope === "regional"
                          ? t("Regional", "區域因素")
                          : t("Individual", "個別因素"),
                      )
                      .join(" / ")}
                  </dd>
                  <dt>{t("Effective period", "適用期間")}</dt>
                  <dd>
                    {source.effective_from ?? t("Start unknown", "起日未提供")} ~{" "}
                    {source.effective_to ?? t("End unknown", "迄日未提供")}
                  </dd>
                  <dt>{t("Source pages", "來源頁碼")}</dt>
                  <dd>{source.pages.join(", ")}</dd>
                  <dt>SHA-256</dt>
                  <dd>
                    <code>{source.content_hash}</code>
                  </dd>
                </dl>
                {!!source.unresolved?.length && (
                  <div className="notice" data-tone="warn">
                    <strong>{t("Unresolved source metadata", "尚未解決的來源資料問題")}</strong>
                    <ul>
                      {source.unresolved.map((reason, index) => (
                        <li key={index}>{reason}</li>
                      ))}
                    </ul>
                  </div>
                )}
                <details>
                  <summary>{t("Catalog evidence excerpts", "查看目錄依據原文")}</summary>
                  {source.evidence.map((citation, index) => (
                    <blockquote key={index}>
                      {t("Page", "頁碼")} {citation.page} · {citation.region_id}：{citation.excerpt}
                    </blockquote>
                  ))}
                </details>
              </section>
            ))}
          </>
        ) : (
          <p className="notice" data-tone="warn">
            {t(
              "No rule bundle was supplied for this revision. Document purpose alone cannot identify general rules or district bases; source roles and independent versions remain unverified.",
              "此修訂未提供規則組合。僅憑文件用途，無法辨識通用規則或地區基準；來源角色與獨立版本尚無法核對。",
            )}
          </p>
        )}
        {(context.documents ?? []).map((document) => (
          <div className="rule-row" key={document.document_id}>
            <strong>{documentPurposeText(document.purpose, t)}</strong>
            <p>
              {document.document_id} · v{document.version}
            </p>
            <code>{document.content_hash}</code>
          </div>
        ))}
        {!(context.documents ?? []).some((document) => document.purpose === "template") ? (
          <p className="notice" data-tone="warn">
            {t(
              "No separately versioned report template is recorded here. Reviewing an existing form does not establish support for generating a new case report.",
              "此材料未記錄獨立版本的報表模板。審查既有書表，不代表已支援依新個案產製報表。",
            )}
          </p>
        ) : null}
        {context.rules.map((rule, index) => (
          <div className="rule-row" key={`${rule.reference.content_hash}:${index}`}>
            <strong>
              {rule.reference.rule_set_id} · v{rule.reference.version}
            </strong>
            <p>
              {rule.applicability.jurisdiction} / {rule.zone} /{" "}
              {rule.applicability.land_use_category} ·{" "}
              {rule.applicability.effective_from ?? t("Start not specified", "起日未指定")} ~{" "}
              {rule.applicability.effective_to ?? t("End not specified", "迄日未指定")}
            </p>
            <p>
              {t("Stored declaration", "儲存的規則標記")}：{statusText(rule.declared_status, t)} ·{" "}
              {t("Source", "來源")}：
              {rule.evidence
                .map(
                  (citation) =>
                    `${citation.document_id} v${citation.version} · ${t("page", "第")} ${citation.page} ${t("", "頁")}`,
                )
                .join("; ") || t("No citation", "缺少引用")}
            </p>
            <code>{rule.reference.content_hash}</code>
          </div>
        ))}
        {context.selections.map((selection, index) => (
          <p key={index}>
            {selection.context.target_id} × {selection.context.comparable_id}：
            {selection.status === "unique"
              ? t(
                  "One applicability match; execution still checks authority",
                  "唯一適用版本；執行時仍須核驗權限",
                )
              : selection.status === "missing"
                ? t("No compatible rules", "沒有相容規則")
                : t("Conflicting matching versions", "多份適用版本衝突")}
          </p>
        ))}
        <dl className="kv">
          <dt>{t("Case", "案件")}</dt>
          <dd>{context.job.case_id}</dd>
          <dt>{t("Revision", "修訂")}</dt>
          <dd>{context.revision.revision_id}</dd>
          <dt>{t("Material digest", "材料摘要")}</dt>
          <dd>
            <code>{context.revision.material_digest}</code>
          </dd>
        </dl>
      </details>
    </section>
  );
}
