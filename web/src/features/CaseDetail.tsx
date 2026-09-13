import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";

import type { CaseRecord, CaseReviewBasis, MaterialRecord, ReviewClient } from "@/api/client";
import { MATERIAL_SIZE_LIMIT_BYTES } from "@/api/client";
import { ServiceError } from "@/api/problems";
import { sha256Hex } from "@/api/exports";
import { useText } from "@/ui/Language";
import { Icon } from "@/ui/Icon";
import { CandidatePanel } from "./CandidatePanel";
import { buildCandidateClient } from "./candidate-api";
import { formatBytes, readFileBytes, shortDisplayId } from "./intake";

/** One intake case's own management page: record, materials, data-check candidates.
 *
 * No review job exists for an intake case yet, and this page says so plainly
 * instead of pretending: the review workflow starts only once the form-filling
 * and parsing integration lands. Everything shown here is durable server state.
 */
export function CaseDetail({ client }: { client: ReviewClient }) {
  const t = useText();
  const { caseId } = useParams();
  const [record, setRecord] = useState<CaseRecord | null>(null);
  const [materials, setMaterials] = useState<MaterialRecord[]>([]);
  const [failed, setFailed] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [uploadNote, setUploadNote] = useState<string | null>(null);
  const [uploading, setUploading] = useState(false);
  const fileInput = useRef<HTMLInputElement>(null);
  const candidateApi = useMemo(() => buildCandidateClient(), []);
  const navigate = useNavigate();
  const [basis, setBasis] = useState<CaseReviewBasis | null>(null);
  const [starting, setStarting] = useState(false);
  const [startNote, setStartNote] = useState<string | null>(null);

  async function startReview() {
    if (!basis || basis.state !== "ready" || starting) return;
    setStarting(true);
    setStartNote(null);
    try {
      const started = await client.startReview(basis);
      await navigate(`/jobs/${started.job_id}/progress`);
    } catch (error) {
      setStartNote(
        error instanceof ServiceError && error.code === "unauthorized"
          ? t(
              "You do not have permission to start a review on this case, or the session lapsed.",
              "沒有發起審查的權限，或工作階段已失效，請重新登入。",
            )
          : t(
              "The review could not be started. Nothing was created; try again.",
              "審查未能發起，未建立任何資料，請再試一次。",
            ),
      );
    } finally {
      setStarting(false);
    }
  }

  const refresh = useCallback(async () => {
    if (!caseId) return;
    try {
      const [one, list, reviewBasis] = await Promise.all([
        client.readCase(caseId),
        client.listCaseMaterials(caseId),
        // A basis read that fails must not fail the page: the case itself still loads
        // and the review section then simply offers no button.
        client.readCaseReviewBasis(caseId).catch(() => null),
      ]);
      setRecord(one);
      setMaterials([...list.materials]);
      setBasis(reviewBasis);
      setFailed(null);
    } catch (error) {
      if (error instanceof ServiceError && error.code === "not_found") {
        setFailed(t("This case does not exist or you have no access.", "查無此案件或無存取權限。"));
      } else if (error instanceof ServiceError && error.code === "unauthorized") {
        setFailed(t("Please sign in again to open this case.", "請重新登入後再開啟此案件。"));
      } else {
        setFailed(
          t(
            "The case could not be loaded; this is a load failure, not an empty case.",
            "案件載入失敗；這是載入問題，不代表案件不存在。",
          ),
        );
      }
    } finally {
      setLoading(false);
    }
  }, [caseId, client, t]);

  useEffect(() => {
    void refresh();
  }, [refresh]);

  async function upload() {
    const file = fileInput.current?.files?.[0];
    if (!file || !caseId) return;
    if (file.size > MATERIAL_SIZE_LIMIT_BYTES) {
      setUploadNote(
        t(
          "The service refuses this file: over the 64MB limit; nothing was saved.",
          "服務拒絕此檔案：太大（上限 64MB），未保存。",
        ),
      );
      return;
    }
    setUploading(true);
    setUploadNote(null);
    try {
      const bytes = await readFileBytes(file);
      const stored = await client.uploadCaseMaterial(caseId, {
        bytes,
        filename: file.name,
        contentType: file.type || "application/octet-stream",
        idempotencyKey: `case-detail-${caseId}-${file.name}-${file.size}`,
      });
      const localDigest = await sha256Hex(bytes);
      setUploadNote(
        stored.sha256 === localDigest
          ? t("Saved; SHA-256 verified.", "已保存，SHA-256 已核對。")
          : t(
              "Saved, but the server digest does not match this file - do not rely on it.",
              "已保存，但伺服器雜湊與本機檔案不符，請勿依賴此份材料。",
            ),
      );
      await refresh();
    } catch (error) {
      setUploadNote(
        error instanceof ServiceError && error.code === "unauthorized"
          ? t(
              "Not signed in or no access; sign in and retry.",
              "未登入或無存取權限，請重新登入後再試。",
            )
          : t(
              "The upload did not complete; the list shows what is saved.",
              "上傳未完成；清單顯示的才是已保存內容。",
            ),
      );
    } finally {
      setUploading(false);
    }
  }

  if (loading) {
    return <p className="muted">{t("Loading case…", "案件載入中…")}</p>;
  }
  if (failed || !record || !caseId) {
    return (
      <section className="panel">
        <p role="alert">{failed ?? t("This case could not be opened.", "無法開啟此案件。")}</p>
        <Link to="/">{t("Back to case list", "返回案件清單")}</Link>
      </section>
    );
  }
  return (
    <div className="stack">
      <section className="panel" aria-label={t("Case", "案件資訊")}>
        <h1>
          {record.title}
          <span className="muted">
            {" "}
            {t("Display id", "顯示編號")} {shortDisplayId(record.case_id)}
          </span>
        </h1>
        <dl className="kv">
          <dt>{t("District", "行政區")}</dt>
          <dd>{record.district}</dd>
          <dt>{t("Valuation date", "估價基準日")}</dt>
          <dd>{record.valuation_date ?? t("Not provided", "尚未提供")}</dd>
        </dl>
        <details className="technical">
          <summary>{t("Technical record", "技術紀錄")}</summary>
          <code>{record.case_id}</code>
        </details>
      </section>
      <section className="panel" aria-label={t("Review status", "審查狀態")}>
        <h2>
          <Icon name="clock" />
          {t("Review status", "審查狀態")}
        </h2>
        {basis?.state === "ready" ? (
          <>
            <p>
              {t(
                "This case has an admitted material revision, so a review can start. Starting twice reopens the same review rather than creating a second one.",
                "本案已有受控的材料版本，可以發起審查。重複發起會回到同一份審查，不會另建第二份。",
              )}
            </p>
            <button
              type="button"
              data-variant="primary"
              disabled={starting}
              onClick={() => {
                void startReview();
              }}
            >
              {starting ? t("Starting…", "發起中…") : t("Start review", "發起審查")}
              <Icon name="arrow" />
            </button>
          </>
        ) : (
          <p>
            {t(
              "Materials are saved durably, but this case has no admitted material revision yet, so no review can start.",
              "材料已妥善保存，但本案尚無受控的材料版本，因此還不能發起審查。",
            )}{" "}
            {basis?.reason ?? null}
          </p>
        )}
        {startNote ? (
          <p className="notice" data-tone="danger" role="alert">
            {startNote}
          </p>
        ) : null}
      </section>
      <section className="panel" aria-label={t("Case materials", "案件材料")}>
        <h2>
          <Icon name="book" />
          {t("Case materials", "案件材料")}
        </h2>
        <p>
          <label>
            {t("Upload material (64MB limit)", "上傳材料檔案（上限 64MB）")}
            <input ref={fileInput} type="file" disabled={uploading} />
          </label>
          <button onClick={() => void upload()} disabled={uploading}>
            {uploading ? t("Uploading…", "上傳中…") : t("Upload", "上傳")}
          </button>
        </p>
        {uploadNote ? <p role="status">{uploadNote}</p> : null}
        {materials.length === 0 ? (
          <p className="muted">{t("No materials yet.", "尚未上傳任何材料。")}</p>
        ) : (
          <ul>
            {materials.map((material) => (
              <li key={material.material_id}>
                {material.filename}
                <span className="muted">
                  {" "}
                  {formatBytes(material.size)} · SHA-256 {material.sha256.slice(0, 10)}…
                  {material.sha256.slice(-8)}
                </span>
              </li>
            ))}
          </ul>
        )}
      </section>
      <CandidatePanel api={candidateApi} caseId={caseId} />
    </div>
  );
}
