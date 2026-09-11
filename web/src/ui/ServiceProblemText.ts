import { EXPLANATIONS, type ServiceErrorCode } from "@/api/problems";

const CHINESE: Record<ServiceErrorCode, { title: string; guidance: string }> = {
  unauthorized: {
    title: "目前沒有此操作的權限",
    guidance: "請確認目前工作階段與案件權限，或交由具備所需權限的審查者處理。",
  },
  not_found: {
    title: "目前無法取得此任務",
    guidance: "此任務可能不在您的可見範圍內，或已不存在。請返回案件查看目前可用的任務。",
  },
  version_conflict: {
    title: "任務或案件版本已更新",
    guidance: "服務未接受這次提交。請重新讀取目前狀態，核對新版本後再決定回覆。",
  },
  invalid_request: {
    title: "服務未接受這份回覆",
    guidance: "提交內容不符合服務要求。請重新讀取任務，確認允許的動作、值與單位後再填寫。",
  },
  capability_unavailable: {
    title: "目前服務尚未提供此功能",
    guidance: "請確認本機服務配置。此狀態不表示審查完成，也不代表報表已就緒。",
  },
  execution_failed: {
    title: "服務無法完成這次操作",
    guidance: "請確認本機服務狀態後重新讀取任務，再決定下一步。",
  },
};

/** Presentation only; canonical refusal and unknown-outcome handling stay in the client. */
export function serviceProblemText(
  code: ServiceErrorCode,
  t: (english: string, chinese: string) => string,
) {
  return {
    title: t(EXPLANATIONS[code].title, CHINESE[code].title),
    guidance: t(EXPLANATIONS[code].guidance, CHINESE[code].guidance),
  };
}
