import { useMemo, useState } from "react";
import { useLocation } from "react-router-dom";
import { LoginRefusedError } from "@/api/client";
import { ORIGINAL_PREVIEW_URL } from "../config";
import { useText } from "@/ui/Language";
import { Icon } from "@/ui/Icon";
import { buildAuthApi, looksLikeEmail, passwordSetupIssue } from "./intake";

/**
 * The signed-out entrance: a registration-and-login card plus one advanced fold.
 *
 * 1. Password sign-in (primary): email + password against POST /v1/auth/login. The
 *    refusal is one deliberately generic sentence — identical for an unknown email
 *    and a wrong password — so the entrance is never an account-existence oracle.
 * 2. Registration (「註冊新帳號」) and password reset (「忘記密碼」): the same staged
 *    flow on the same card — request a verification mail (neutral sentence for every
 *    address), then verification code + set/confirm password in one step against
 *    POST /v1/auth/verify with new_password. Only the wording differs between the
 *    two. Passwords are validated locally (8-128 characters, both entries equal)
 *    before any request; the code and both password fields are cleared on failure
 *    and never echoed into any message.
 * 3. An existing session credential (advanced, collapsed): the token-paste flow kept
 *    for demo fixtures and controlled deployments. It opens expanded when the visitor
 *    arrived on a deep link (an operator-supplied reference), collapsed on the plain
 *    front door.
 */
type EntryMode = "login" | "register" | "reset";

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
  const [mode, setMode] = useState<EntryMode>("login");
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [code, setCode] = useState("");
  const [newPassword, setNewPassword] = useState("");
  const [confirmPassword, setConfirmPassword] = useState("");
  const [codeSent, setCodeSent] = useState(false);
  const [notice, setNotice] = useState("");
  const [localError, setLocalError] = useState("");
  const [localBusy, setLocalBusy] = useState(false);
  const [tokenValue, setTokenValue] = useState("");
  const [pairing, setPairing] = useState("");
  const [advancedOpen, setAdvancedOpen] = useState(pathname !== "/");
  const working = busy || localBusy;

  const neutralSentence = t(
    "A verification mail has been sent (if this mailbox is available); complete the verification within 10 minutes.",
    "驗證信已寄出（若信箱可用），請於 10 分鐘內完成驗證。",
  );

  function refusalText(cause: unknown): string {
    if (cause instanceof LoginRefusedError) {
      if (cause.kind === "rate_limited") return t("Please try again later.", "請稍後再試。");
      if (cause.kind === "credentials_rejected")
        return t(
          "The email or password is incorrect, or the account is not registered.",
          "帳號或密碼錯誤，或帳號尚未註冊。",
        );
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

  /** Mode switches always land on a clean first stage; only the email survives. */
  function switchMode(next: EntryMode) {
    setMode(next);
    setCodeSent(false);
    setPassword("");
    setCode("");
    setNewPassword("");
    setConfirmPassword("");
    setNotice("");
    setLocalError("");
  }

  async function signInWithPassword() {
    const address = email.trim();
    if (!looksLikeEmail(address)) {
      setLocalError(t("Enter a complete email address.", "請輸入完整的電子郵件地址。"));
      return;
    }
    if (!password) {
      setLocalError(t("Enter the password.", "請輸入密碼。"));
      return;
    }
    setLocalBusy(true);
    setLocalError("");
    try {
      const grant = await auth.login(address, password);
      setPassword("");
      await connect(grant.token);
    } catch (cause) {
      setLocalError(refusalText(cause));
    } finally {
      setLocalBusy(false);
    }
  }

  async function sendVerificationMail() {
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

  async function completeVerification() {
    const address = email.trim();
    const oneTimeCode = code.trim();
    if (!oneTimeCode) {
      setLocalError(t("Enter the verification code.", "請輸入驗證碼。"));
      return;
    }
    // Local password gate first: an unacceptable pair never reaches the service.
    const issue = passwordSetupIssue(newPassword, confirmPassword);
    if (issue) {
      setLocalError(t(issue.en, issue.zh));
      return;
    }
    setLocalBusy(true);
    setLocalError("");
    try {
      const grant = await auth.verify(address, oneTimeCode, newPassword);
      setCode("");
      setNewPassword("");
      setConfirmPassword("");
      setNotice("");
      await connect(grant.token);
    } catch (cause) {
      // The one-time code and both password entries are cleared on every failure.
      setCode("");
      setNewPassword("");
      setConfirmPassword("");
      setLocalError(refusalText(cause));
    } finally {
      setLocalBusy(false);
    }
  }

  const staged = mode !== "login";
  const heading =
    mode === "login"
      ? t("Sign in to the workbench", "登入審查工作台")
      : mode === "register"
        ? t("Create a new account", "註冊新帳號")
        : t("Reset the password", "重設密碼");

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
            if (mode === "login") void signInWithPassword();
            else if (codeSent) void completeVerification();
            else void sendVerificationMail();
          }}
        >
          <span className="eyebrow">
            {mode === "login"
              ? t("EMAIL SIGN-IN", "電子郵件登入")
              : t("EMAIL VERIFICATION", "電子郵件驗證")}
          </span>
          <h2>{heading}</h2>
          <p className="muted">
            {mode === "login"
              ? t(
                  "Sign in with your email and password.",
                  "輸入電子郵件與密碼登入工作台。",
                )
              : mode === "register"
                ? t(
                    "Enter your email; the service sends a verification mail to create the account.",
                    "輸入電子郵件，服務會寄送驗證信以建立帳號。",
                  )
                : t(
                    "Enter your email; the service sends a verification mail to reset the password.",
                    "輸入電子郵件，服務會寄送驗證信以重設密碼。",
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
            disabled={working || (staged && codeSent)}
          />
          {mode === "login" ? (
            <>
              <label htmlFor="login-password">{t("Password", "密碼")}</label>
              <input
                id="login-password"
                type="password"
                autoComplete="current-password"
                value={password}
                onChange={(event) => setPassword(event.target.value)}
                disabled={working}
              />
            </>
          ) : null}
          {staged && codeSent ? (
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
              <label htmlFor="new-password">{t("Set a password", "設定密碼")}</label>
              <input
                id="new-password"
                type="password"
                autoComplete="new-password"
                value={newPassword}
                onChange={(event) => setNewPassword(event.target.value)}
                disabled={working}
              />
              <label htmlFor="confirm-password">{t("Confirm the password", "確認密碼")}</label>
              <input
                id="confirm-password"
                type="password"
                autoComplete="new-password"
                value={confirmPassword}
                onChange={(event) => setConfirmPassword(event.target.value)}
                disabled={working}
              />
              <p className="small muted">
                {t("Passwords are 8 to 128 characters.", "密碼長度須為 8 至 128 個字元。")}
              </p>
            </>
          ) : null}
          {localError ? (
            <p role="alert" className="notice" data-tone="danger">
              {localError}
            </p>
          ) : null}
          {mode === "login" ? (
            <>
              <button
                type="submit"
                data-variant="primary"
                disabled={working || !email.trim() || !password}
              >
                {working ? t("Signing in…", "登入中…") : t("Sign in", "登入")}
                <Icon name="arrow" />
              </button>
              <div className="input-action" style={{ flexWrap: "wrap" }}>
                <button type="button" disabled={working} onClick={() => switchMode("register")}>
                  {t("Create a new account", "註冊新帳號")}
                </button>
                <button type="button" disabled={working} onClick={() => switchMode("reset")}>
                  {t("Forgot the password", "忘記密碼")}
                </button>
              </div>
            </>
          ) : codeSent ? (
            <>
              <button type="submit" data-variant="primary" disabled={working || !code.trim()}>
                {working
                  ? t("Verifying…", "驗證中…")
                  : mode === "register"
                    ? t("Complete registration and sign in", "完成註冊並登入")
                    : t("Reset the password and sign in", "重設密碼並登入")}
                <Icon name="arrow" />
              </button>
              <div className="input-action" style={{ flexWrap: "wrap" }}>
                <button type="button" disabled={working} onClick={() => void sendVerificationMail()}>
                  {t("Resend the verification mail", "重新寄送驗證信")}
                </button>
                <button
                  type="button"
                  disabled={working}
                  onClick={() => {
                    setCodeSent(false);
                    setCode("");
                    setNewPassword("");
                    setConfirmPassword("");
                    setNotice("");
                    setLocalError("");
                  }}
                >
                  {t("Use a different email", "使用其他信箱")}
                </button>
                <button type="button" disabled={working} onClick={() => switchMode("login")}>
                  {t("Back to sign-in", "返回登入")}
                </button>
              </div>
            </>
          ) : (
            <>
              <button type="submit" data-variant="primary" disabled={working || !email.trim()}>
                {working ? t("Sending…", "寄送中…") : t("Send verification mail", "寄送驗證信")}
                <Icon name="arrow" />
              </button>
              <div className="input-action" style={{ flexWrap: "wrap" }}>
                <button type="button" disabled={working} onClick={() => switchMode("login")}>
                  {t("Back to sign-in", "返回登入")}
                </button>
              </div>
            </>
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
