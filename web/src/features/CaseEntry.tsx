import { useMemo, useState } from "react";
import { useLocation } from "react-router-dom";
import { LoginRefusedError } from "@/api/client";
import { ORIGINAL_PREVIEW_URL } from "../config";
import { useText } from "@/ui/Language";
import { Icon } from "@/ui/Icon";
import { buildAuthApi, looksLikeEmail } from "./intake";

/**
 * The signed-out entrance. Two doors, in this order:
 *
 * 1. Email sign-in (primary): request a one-time code, verify it, and continue with
 *    the bearer session the service answers with. The request-code step shows one
 *    neutral sentence for every address — never an account-existence oracle — and the
 *    code itself is never echoed or logged.
 * 2. An existing session credential (advanced, collapsed): the token-paste flow kept
 *    for demo fixtures and controlled deployments. It opens expanded when the visitor
 *    arrived on a deep link (an operator-supplied reference), collapsed on the plain
 *    front door.
 */
export function CaseEntry({
  connect,
  busy,
  error,
}: {
  connect: (token: string, pairing?: string | null) => Promise<void>;
  busy: boolean;
  error: string;
}) {
  const t = useText();
  const { pathname } = useLocation();
  const auth = useMemo(() => buildAuthApi(), []);
  const [email, setEmail] = useState("");
  const [code, setCode] = useState("");
  const [codeSent, setCodeSent] = useState(false);
  const [notice, setNotice] = useState("");
  const [localError, setLocalError] = useState("");
  const [localBusy, setLocalBusy] = useState(false);
  const [tokenValue, setTokenValue] = useState("");
  const [pairing, setPairing] = useState("");
  const [advancedOpen, setAdvancedOpen] = useState(pathname !== "/");
  const working = busy || localBusy;

  const neutralSentence = t(
    "If this mailbox is available, a verification code has been sent (valid for 10 minutes).",
    "若信箱可用，驗證碼已寄出（10 分鐘內有效）。",
  );

  function refusalText(cause: unknown): string {
    if (cause instanceof LoginRefusedError) {
      if (cause.kind === "rate_limited") return t("Please try again later.", "請稍後再試。");
      if (cause.kind === "code_rejected")
        return t("The verification code is invalid or expired.", "驗證碼無效或已過期。");
      return t(
        "The sign-in service is currently unavailable. Please try again later.",
        "登入服務目前無法使用，請稍後再試。",
      );
    }
    return t(
      "The sign-in service could not be reached. Check the connection and try again.",
      "目前無法連線登入服務，請確認網路後再試。",
    );
  }

  async function sendCode() {
    const address = email.trim();
    if (!looksLikeEmail(address)) {
      setLocalError(t("Enter a complete email address.", "請輸入完整的電子郵件地址。"));
      return;
    }
    setLocalBusy(true);
    setLocalError("");
    setNotice("");
    try {
      await auth.requestCode(address);
      setCodeSent(true);
      setNotice(neutralSentence);
    } catch (cause) {
      setLocalError(refusalText(cause));
    } finally {
      setLocalBusy(false);
    }
  }

  async function verifyAndSignIn() {
    const address = email.trim();
    const oneTimeCode = code.trim();
    if (!oneTimeCode) {
      setLocalError(t("Enter the verification code.", "請輸入驗證碼。"));
      return;
    }
    setLocalBusy(true);
    setLocalError("");
    try {
      const grant = await auth.verify(address, oneTimeCode);
      setCode("");
      setNotice("");
      await connect(grant.token);
    } catch (cause) {
      setLocalError(refusalText(cause));
    } finally {
      setLocalBusy(false);
    }
  }

  return (
    <section className="sign-in-layout">
      <div className="intro">
        <span className="eyebrow">{t("APPRAISAL REVIEW WORKBENCH", "查估審查工作台")}</span>
        <h1>{t("Every conclusion starts with evidence.", "讓每個審查結論，\n都有依據。")}</h1>
        <p>
          {t(
            "Read the source. Review the rule. Record an explicit decision.",
            "從文件與規則出發，核對差異，留下可追溯的決定。",
          )}
        </p>
        <div className="intro-notes">
          <Icon name="shield" />
          <span>
            {t(
              "The service checks identity before opening any protected page.",
              "服務驗證身分後才開啟受保護頁面；憑證僅保留於此分頁。",
            )}
          </span>
        </div>
      </div>
      <div>
        {error ? (
          <p role="alert" className="notice" data-tone="danger">
            {error}
          </p>
        ) : null}
        <form
          className="panel sign-in-panel"
          onSubmit={(event) => {
            event.preventDefault();
            if (working) return;
            if (codeSent) void verifyAndSignIn();
            else void sendCode();
          }}
        >
          <span className="eyebrow">{t("EMAIL SIGN-IN", "電子郵件登入")}</span>
          <h2>{t("Sign in to the workbench", "登入審查工作台")}</h2>
          <p className="muted">
            {t(
              "Enter your email to receive a one-time verification code.",
              "輸入電子郵件，服務會寄送一次性驗證碼。",
            )}
          </p>
          <label htmlFor="login-email">{t("Email address", "電子郵件")}</label>
          <input
            id="login-email"
            type="email"
            autoComplete="email"
            inputMode="email"
            value={email}
            onChange={(event) => setEmail(event.target.value)}
            disabled={working || codeSent}
          />
          {codeSent ? (
            <>
              {notice ? (
                <p role="status" className="notice" data-tone="neutral">
                  <Icon name="check" />
                  {notice}
                </p>
              ) : null}
              <label htmlFor="login-code">{t("Verification code", "驗證碼")}</label>
              <input
                id="login-code"
                type="text"
                inputMode="numeric"
                autoComplete="one-time-code"
                value={code}
                onChange={(event) => setCode(event.target.value)}
                disabled={working}
              />
            </>
          ) : null}
          {localError ? (
            <p role="alert" className="notice" data-tone="danger">
              {localError}
            </p>
          ) : null}
          {codeSent ? (
            <>
              <button type="submit" data-variant="primary" disabled={working || !code.trim()}>
                {working ? t("Verifying…", "驗證中…") : t("Verify and sign in", "驗證並登入")}
                <Icon name="arrow" />
              </button>
              <div className="input-action">
                <button type="button" disabled={working} onClick={() => void sendCode()}>
                  {t("Resend code", "重新寄送驗證碼")}
                </button>
                <button
                  type="button"
                  disabled={working}
                  onClick={() => {
                    setCodeSent(false);
                    setCode("");
                    setNotice("");
                    setLocalError("");
                  }}
                >
                  {t("Use a different email", "使用其他信箱")}
                </button>
              </div>
            </>
          ) : (
            <button type="submit" data-variant="primary" disabled={working || !email.trim()}>
              {working ? t("Sending…", "寄送中…") : t("Send verification code", "送出驗證碼")}
              <Icon name="arrow" />
            </button>
          )}
          <p className="small muted">
            {t(
              "First sign-in starts with no cases; create one after signing in.",
              "首次登入不會看到任何案件，登入後即可建立新案件並上傳資料。",
            )}
          </p>
        </form>
        <details className="technical" open={advancedOpen} style={{ marginTop: 16, maxWidth: 450 }}>
          <summary
            onClick={(event) => {
              event.preventDefault();
              setAdvancedOpen((open) => !open);
            }}
          >
            {t("Use a session credential (advanced)", "使用工作階段憑證（進階）")}
          </summary>
          <form
            className="panel"
            onSubmit={(event) => {
              event.preventDefault();
              if (!working && tokenValue.trim())
                void connect(tokenValue.trim(), pairing.trim() || null);
            }}
          >
            <p className="small muted">
              {t(
                "For an existing session credential issued for demo or controlled deployments.",
                "供示範或受控部署使用之既有工作階段憑證。",
              )}
            </p>
            <label htmlFor="token">{t("Session token", "本機工作階段憑證")}</label>
            <input
              id="token"
              type="password"
              autoComplete="off"
              value={tokenValue}
              onChange={(event) => setTokenValue(event.target.value)}
              disabled={working}
            />
            {ORIGINAL_PREVIEW_URL ? (
              <>
                <label htmlFor="original-pairing">
                  {t("Local original pairing code (optional)", "本機原件配對碼（選填）")}
                </label>
                <input
                  id="original-pairing"
                  type="password"
                  autoComplete="off"
                  value={pairing}
                  onChange={(event) => setPairing(event.target.value)}
                  disabled={working}
                />
                <p className="small muted">
                  {t(
                    "Original pages require this separate local pairing and current case permission.",
                    "檢視原件須同時通過獨立本機配對與目前案件授權；未配對仍可查看審查狀態。",
                  )}
                </p>
              </>
            ) : null}
            <div className="input-action">
              <button type="submit" data-variant="primary" disabled={working || !tokenValue.trim()}>
                {working ? t("Checking session…", "正在驗證…") : t("Continue", "驗證並繼續")}
                <Icon name="arrow" />
              </button>
            </div>
          </form>
        </details>
      </div>
    </section>
  );
}
