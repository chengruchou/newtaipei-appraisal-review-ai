import type { CaseContextView } from "@/api/client";
import { Icon } from "@/ui/Icon";
import { useText } from "@/ui/Language";
import {
  OFFICIAL_FORMS,
  officialFormGranularityText,
  officialFormLabel,
  type OfficialFormDefinition,
} from "./workbench-state";

/**
 * The section list is held as a list, not a single fixed file: Form 3 is per land value
 * section, and the published file count and naming are still an open question for the
 * organiser. Only sections the service actually publishes appear.
 */
function publishedSections(context: CaseContextView): string[] {
  const zone = context.identity?.zone?.trim();
  return zone ? [zone] : [];
}

function FormCard({ form, context }: { form: OfficialFormDefinition; context: CaseContextView }) {
  const t = useText();
  const sections = publishedSections(context);
  return (
    <section className="panel form-card" aria-label={officialFormLabel(form, t)}>
      <h3>{officialFormLabel(form, t)}</h3>
      <dl className="kv">
        <dt>{t("Visible worksheet", "可見工作表")}</dt>
        <dd>{form.visibleSheet}</dd>
        <dt>{t("Granularity", "產出單位")}</dt>
        <dd>{officialFormGranularityText(form, t)}</dd>
        <dt>{t("Rate convention", "比率格式慣例")}</dt>
        <dd>{t(...form.rateConvention)}</dd>
      </dl>
      {form.granularity === "section" ? (
        <div className="form-instances">
          <p className="small">
            {t("Sections published for this case", "本案已發布區段")}：
            {sections.join("、") || t("None supplied", "服務未提供")} ({sections.length}{" "}
            {t("instance(s) known", "份已知")})
          </p>
          <p className="small muted">
            {t(
              "If the subjects fall in more than one section, more than one file is required. How many files the organiser expects, and under what naming, is not settled.",
              "若各標的分屬不同區段，所需份數不只一份。主辦單位期望的份數與命名方式尚未確定。",
            )}
          </p>
        </div>
      ) : null}
      {form.landUseQualifier ? (
        <p className="small muted">
          {t(
            "Other land-use variants of this form exist. The land-use qualifier stays in the label so a district or use change cannot mislabel the sheet.",
            "此表另有其他用途別版本，標籤保留用途別，避免行政區或用途變更後誤標工作表。",
          )}
        </p>
      ) : null}
      <p className="notice" data-tone="warn" role="status">
        {t(
          "Not available in this deployment: no form download route is published by the service.",
          "本部署尚未提供：服務未發布此表的下載路由。",
        )}
      </p>
    </section>
  );
}

export function OfficialForms({ context }: { context: CaseContextView }) {
  const t = useText();
  return (
    <>
      <div className="notice" data-tone="warn" role="alert">
        <Icon name="file" />
        <div>
          <strong>{t("No official form has been produced.", "尚未產出任何官方表格。")}</strong>
          <p>
            {t(
              "This view names the three official forms and states what this deployment can do. It is not a draft, not a ready file, and no download is offered.",
              "此檢視僅標示三份官方表格並說明本部署現況；不是草稿，也不是已完成檔案，且未提供下載。",
            )}
          </p>
        </div>
      </div>
      <div className="form-grid">
        {OFFICIAL_FORMS.map((form) => (
          <FormCard key={form.id} form={form} context={context} />
        ))}
      </div>
      <p className="small muted">
        {t(
          "Every published cell must be computed before it is written: the visible worksheet of each workbook carries no formula. The frontend renders values and states the unit; it never converts between the two rate conventions and never recomputes a rate.",
          "每份表的可見工作表皆無公式，所有填入值都必須先行計算。前端僅呈現數值並標示單位，不在兩種比率慣例間換算，也不重新計算比率。",
        )}
      </p>
    </>
  );
}
