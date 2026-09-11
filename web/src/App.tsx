import { useCallback, useEffect, useRef, useState } from "react";
import {
  BrowserRouter,
  Link,
  Navigate,
  NavLink,
  Route,
  Routes,
  useLocation,
  useParams,
} from "react-router-dom";
import {
  buildClient,
  readToken,
  writeToken,
  readPairToken,
  writePairToken,
  ORIGINAL_PREVIEW_URL,
} from "./config";
import type { ReviewClient, ReviewSessionView } from "./api/client";
import { ServiceError } from "./api/problems";
import { TaskPage } from "./features/TaskPage";
import { PrivacyRoute } from "./features/PrivacyRoute";
import { CaseEntry } from "./features/CaseEntry";
import { WorkbenchJob } from "./features/WorkbenchJob";
import { Icon } from "./ui/Icon";
import { DataModeNotice } from "./ui/DataModeNotice";
import { LanguageProvider, useText, type Language } from "./ui/Language";
import { RouteTransition } from "./ui/RouteTransition";

type Connection = { client: ReviewClient; session: ReviewSessionView };

function SignIn({
  connect,
  busy,
  error,
}: {
  connect: (token: string, pairing?: string | null) => Promise<void>;
  busy: boolean;
  error: string;
}) {
  const [value, setValue] = useState("");
  const [pairing, setPairing] = useState("");
  const t = useText();
  return (
    <section className="sign-in-layout">
      <div className="intro">
        <span className="eyebrow">LOCAL REVIEW WORKSPACE</span>
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
              "Originals stay on your device. Local pairing is checked separately.",
              "原件留在本機；文件配對與案件授權分開驗證。",
            )}
          </span>
        </div>
      </div>
      <form
        className="panel sign-in-panel"
        onSubmit={(event) => {
          event.preventDefault();
          if (!busy) void connect(value.trim(), pairing.trim() || null);
        }}
      >
        <span className="eyebrow">{t("CONTROLLED LOCAL SESSION", "受控本機驗證")}</span>
        <h2>{t("Connect to the workbench", "開啟審查工作台")}</h2>
        <p className="muted">
          {t(
            "Use a session issued by this local service. This is not a production account registration or password login.",
            "請使用本機服務簽發的工作階段。這裡尚未提供正式帳號註冊或密碼登入。",
          )}
        </p>
        <label htmlFor="token">{t("Session token", "本機工作階段憑證")}</label>
        <input
          id="token"
          type="password"
          autoComplete="off"
          value={value}
          onChange={(event) => setValue(event.target.value)}
          disabled={busy}
        />
        {error ? (
          <p role="alert" className="notice" data-tone="danger">
            {error}
          </p>
        ) : null}
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
              disabled={busy}
            />
            <p className="small muted">
              {t(
                "Original pages require this separate local pairing and current case permission.",
                "檢視原件須同時通過獨立本機配對與目前案件授權。未配對仍可查看審查狀態。",
              )}
            </p>
          </>
        ) : null}
        <button type="submit" data-variant="primary" disabled={busy || !value.trim()}>
          {busy ? t("Checking session…", "正在向本機服務驗證…") : t("Continue", "驗證並繼續")}
          <Icon name="arrow" />
        </button>
        <p className="small muted">
          {t(
            "The service checks identity before opening any protected page. Your session stays in this tab only.",
            "服務驗證成功後才開啟受保護頁面，憑證僅保留於此分頁。",
          )}
        </p>
      </form>
    </section>
  );
}

function TaskRoute({ client }: { client: ReviewClient }) {
  const { taskId } = useParams();
  return taskId ? (
    <TaskPage key={taskId} taskId={taskId} client={client} />
  ) : (
    <Navigate to="/" replace />
  );
}

function Shell({
  connection,
  children,
  logout,
  language,
  setLanguage,
}: {
  connection: Connection | null;
  children: React.ReactNode;
  logout: () => void;
  language: Language;
  setLanguage: (language: Language) => void;
}) {
  const t = useText();
  const { pathname } = useLocation();
  const jobId = pathname.match(/^\/jobs\/([^/]+)/)?.[1];
  const sections = [
    ["progress", "clock", t("Progress", "工作進度")],
    ["results", "check", t("Results", "結果總覽")],
    ["evidence", "book", t("Evidence", "證據比對")],
    ["tasks", "person", t("Human review", "人工作業")],
  ] as const;
  return (
    <div className="app-shell">
      <a className="skip-link" href="#workspace-main">
        {t("Skip to content", "跳至主要內容")}
      </a>
      <aside className="sidebar">
        <Link className="brand" to="/">
          <svg viewBox="0 0 40 44" aria-hidden="true">
            <path d="m20 2 18 10v21L20 43 2 33V12Z" fill="currentColor" />
            <path
              d="m20 8 12 7v14l-12 7-12-7V15Zm0 0v14m-12-7 12 7 12-7m-12 7v14"
              fill="none"
              stroke="white"
              strokeWidth="1.5"
            />
          </svg>
          <span>{t("Appraisal Review", "查估審查平台")}</span>
        </Link>
        <nav aria-label={t("Workbench", "工作台導覽")}>
          <NavLink to="/" end>
            <Icon name="file" />
            {t("Cases", "案件入口")}
          </NavLink>
          {sections.map(([path, icon, label]) =>
            jobId && connection ? (
              <NavLink key={path} to={`/jobs/${jobId}/${path}`}>
                <Icon name={icon} />
                {label}
              </NavLink>
            ) : (
              <span
                key={path}
                className="nav-unavailable"
                title={t("Open a case first", "開啟案件後可用")}
              >
                <Icon name={icon} />
                {label}
              </span>
            ),
          )}
          <NavLink to="/privacy">
            <Icon name="shield" />
            {t("Local privacy review", "本機隱私檢查")}
          </NavLink>
        </nav>
        <div className="sidebar-note">
          <span className="short-rule" />
          <p>{t("Evidence first.\nDecisions you can trace.", "讓城市發展，\n更有依據。")}</p>
          <div className="city-lines" aria-hidden="true">
            <svg viewBox="0 0 220 150">
              <g fill="none" stroke="currentColor" strokeWidth="1">
                <path d="m0 120 100-60 120 70M0 145 110 80 220 143M0 90 110 150M55 60 220 150M32 116V60l28-16 28 16v57M60 44v57m28-19 38-22 32 18v44m-32-62v54m32-61V12l29-10 23 15v74m-23-89v74M39 67v8m10-14v8m-10 10v8m10-14v8m-10 10v8m10-14v8m115-65v9m10-14v9m-10 8v9m10-14v9m-10 8v9m10-14v9m-10 8v9m10-14v9" />
              </g>
            </svg>
          </div>
        </div>
        <div className="sidebar-foot">
          <Icon name="shield" />
          <span>{t("Local verification", "本機驗證")}</span>
        </div>
      </aside>
      <div className="workspace">
        <header className="topbar">
          <div className="breadcrumb">
            <Icon name="home" />
            <span>{t("Workspace", "工作台")}</span>
            <span>/</span>
            <span>
              {pathname === "/"
                ? t("Case entry", "案件入口")
                : pathname.startsWith("/privacy")
                  ? t("Local privacy", "本機隱私")
                  : t("Case review", "案件審查")}
            </span>
          </div>
          <div className="account">
            <button
              className="language-button"
              onClick={() => setLanguage(language === "zh" ? "en" : "zh")}
            >
              {language === "zh" ? "EN" : "中文"}
            </button>
            {connection ? (
              <>
                <span className="avatar">
                  <Icon name="person" />
                </span>
                <span className="account-label">
                  {t("Verified local session", "已驗證本機工作階段")}
                </span>
                <button onClick={logout}>{t("Sign out", "登出")}</button>
              </>
            ) : (
              <span className="muted">{t("Not connected", "尚未連線")}</span>
            )}
          </div>
        </header>
        <main id="workspace-main">
          {connection ? <DataModeNotice mode={connection.session.data_mode} /> : null}
          {children}
          <footer className="page-footer">
            <span>
              {t(
                "Confirmation, correction and approval remain separate decisions.",
                "確認、更正與核准，各自保留權限與紀錄。",
              )}
            </span>
            <span>KPI1 · LOCAL</span>
          </footer>
        </main>
      </div>
    </div>
  );
}

export function App() {
  const [connection, setConnection] = useState<Connection | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [language, setLanguageState] = useState<Language>(() => {
    try {
      return window.localStorage.getItem("workbench.language") === "en" ? "en" : "zh";
    } catch {
      return "zh";
    }
  });
  const initialToken = useRef(readToken());
  const languageRef = useRef(language);
  languageRef.current = language;
  const current = useRef<ReviewClient | null>(null);
  const currentActor = useRef<string | null>(null);
  const verifying = useRef<ReviewClient | null>(null);
  const recent = useRef(new Map<string, string>());
  const logout = useCallback(() => {
    current.current?.dispose();
    current.current = null;
    currentActor.current = null;
    writeToken(null);
    recent.current.clear();
    setConnection(null);
    setError("");
    setBusy(false);
  }, []);
  const recheck = useCallback(
    async function recheck(client: ReviewClient) {
      if (verifying.current === client || current.current !== client) return;
      verifying.current = client;
      try {
        const session = await client.readSession();
        if (current.current === client) {
          const identity = JSON.stringify(session.actor);
          if (currentActor.current !== identity) {
            logout();
            setError(
              languageRef.current === "zh"
                ? "工作階段身分已改變，舊資料已清除。請重新驗證。"
                : "The session identity changed. Previous data was cleared. Verify the session again.",
            );
          } else setConnection({ client, session });
        }
      } catch (cause) {
        if (
          current.current === client &&
          cause instanceof ServiceError &&
          cause.code === "unauthorized"
        ) {
          logout();
          setError(
            languageRef.current === "zh"
              ? "目前工作階段已失效或無存取權限，請重新驗證。"
              : "This session is no longer authorized. Verify a current local session.",
          );
        }
      } finally {
        if (verifying.current === client) verifying.current = null;
      }
    },
    [logout],
  );
  const connect = useCallback(
    async function connect(token: string, pairing: string | null = readPairToken()) {
      logout();
      setBusy(true);
      const client: ReviewClient = buildClient(
        token,
        () => {
          void recheck(client);
        },
        pairing,
      );
      current.current = client;
      verifying.current = client;
      try {
        const session = await client.readSession();
        if (current.current !== client) return;
        writeToken(token);
        writePairToken(pairing);
        currentActor.current = JSON.stringify(session.actor);
        setConnection({ client, session });
        setError("");
      } catch (cause) {
        if (current.current !== client) return;
        client.dispose();
        current.current = null;
        setError(
          cause instanceof ServiceError && cause.code === "unauthorized"
            ? languageRef.current === "zh"
              ? "憑證未獲本機服務授權，尚未登入。"
              : "The local service refused this session. You are not signed in."
            : languageRef.current === "zh"
              ? "本機服務目前無法完成驗證，請確認服務已啟動。"
              : "Local session verification is unavailable. Check the configured service.",
        );
      } finally {
        if (verifying.current === client) verifying.current = null;
        if (current.current === client || current.current === null) setBusy(false);
      }
    },
    [logout, recheck],
  );
  useEffect(() => {
    const token = initialToken.current;
    if (token) void connect(token);
    return () => {
      current.current?.dispose();
      current.current = null;
    };
  }, [connect]);
  useEffect(() => {
    const check = () => {
      if (current.current) void recheck(current.current);
    };
    const timer = setInterval(check, 15_000);
    window.addEventListener("focus", check);
    return () => {
      clearInterval(timer);
      window.removeEventListener("focus", check);
    };
  }, [recheck]);
  const setLanguage = (next: Language) => {
    setLanguageState(next);
    try {
      window.localStorage.setItem("workbench.language", next);
    } catch {
      /* Language preference is optional. */
    }
  };
  useEffect(() => {
    document.documentElement.lang = language === "zh" ? "zh-Hant" : "en";
  }, [language]);
  return (
    <LanguageProvider language={language}>
      <BrowserRouter>
        <Shell
          connection={connection}
          logout={logout}
          language={language}
          setLanguage={setLanguage}
        >
          <RouteTransition>
            {connection ? (
              <Routes>
                <Route
                  path="/"
                  element={
                    <CaseEntry
                      client={connection.client}
                      session={connection.session}
                      recent={recent.current}
                    />
                  }
                />
                <Route
                  path="/jobs/:jobId/*"
                  element={<WorkbenchJob client={connection.client} recent={recent.current} />}
                />
                <Route path="/tasks/:taskId" element={<TaskRoute client={connection.client} />} />
                <Route path="/privacy" element={<PrivacyRoute />} />
                <Route path="*" element={<Navigate to="/" replace />} />
              </Routes>
            ) : (
              <SignIn connect={connect} busy={busy} error={error} />
            )}
          </RouteTransition>
        </Shell>
      </BrowserRouter>
    </LanguageProvider>
  );
}
