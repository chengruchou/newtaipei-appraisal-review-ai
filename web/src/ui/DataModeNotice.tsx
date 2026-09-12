import type { ReviewSessionView } from "@/api/client";
import { useText } from "./Language";

export function DataModeNotice({ mode }: { mode: ReviewSessionView["data_mode"] }) {
  const t = useText();
  return (
    <p className="notice small" data-tone={mode === "synthetic" ? "warn" : "neutral"}>
      {mode === "local_original"
        ? t(
            "Local original documents · local rules and human review. This source mode does not establish model execution, formal approval or real-case acceptance.",
            "真實本機原件・本機規則與人工協作。資料來源模式不代表已執行模型、正式核准或真實個案驗收通過。",
          )
        : mode === "synthetic"
          ? t(
              "Isolated synthetic data · no real-case acceptance. Example values must not be treated as real case results.",
              "隔離模擬資料・不構成真實個案驗收。範例數值不得視為真實案件成果。",
            )
          : t(
              "Data source mode is unspecified. Confirm document provenance with the local operator before relying on results.",
              "資料來源模式尚未指定。採用結果前，請向本機管理者核對文件來源。",
            )}
    </p>
  );
}
