import { useMemo, useState } from "react";
import {
  BrowserRouter,
  Link,
  Navigate,
  Route,
  Routes,
  useNavigate,
  useParams,
} from "react-router-dom";

import { buildClient, readToken, writeToken } from "./config";
import { JobPage } from "./features/JobPage";
import { TaskPage } from "./features/TaskPage";

function SignIn({ onSignedIn }: { onSignedIn: () => void }) {
  const [value, setValue] = useState("");
  return (
    <form
      onSubmit={(event) => {
        event.preventDefault();
        writeToken(value.trim() === "" ? null : value.trim());
        onSignedIn();
      }}
    >
      <h1>Sign in</h1>
      <p className="muted">
        The workbench holds your session token for this tab only and never inspects it. What you are
        allowed to do is decided by the service on every request.
      </p>
      <p>
        <label htmlFor="token" style={{ display: "block" }}>
          Session token
        </label>
        <input
          id="token"
          type="password"
          autoComplete="off"
          value={value}
          onChange={(event) => setValue(event.target.value)}
        />
      </p>
      <button type="submit" data-variant="primary" disabled={value.trim() === ""}>
        Continue
      </button>
    </form>
  );
}

function OpenJob() {
  const navigate = useNavigate();
  const [jobId, setJobId] = useState("");
  return (
    <form
      onSubmit={(event) => {
        event.preventDefault();
        void navigate(`/jobs/${jobId.trim()}`);
      }}
    >
      <h1>Open a review job</h1>
      <p>
        <label htmlFor="job" style={{ display: "block" }}>
          Job identifier
        </label>
        <input id="job" value={jobId} onChange={(event) => setJobId(event.target.value)} />
      </p>
      <button type="submit" data-variant="primary" disabled={jobId.trim() === ""}>
        Open
      </button>
    </form>
  );
}

function JobRoute({ client }: { client: ReturnType<typeof buildClient> }) {
  const { jobId } = useParams();
  return jobId === undefined ? (
    <Navigate to="/" replace />
  ) : (
    <JobPage jobId={jobId} client={client} />
  );
}

function TaskRoute({ client }: { client: ReturnType<typeof buildClient> }) {
  const { taskId } = useParams();
  return taskId === undefined ? (
    <Navigate to="/" replace />
  ) : (
    <TaskPage taskId={taskId} client={client} />
  );
}

export function App() {
  const [signedIn, setSignedIn] = useState(() => readToken() !== null);
  const client = useMemo(() => buildClient(), []);

  return (
    <BrowserRouter>
      <main>
        <nav aria-label="Workbench">
          <Link to="/">Jobs</Link>
          {signedIn ? (
            <>
              {" · "}
              <button
                onClick={() => {
                  writeToken(null);
                  setSignedIn(false);
                }}
              >
                Sign out
              </button>
            </>
          ) : null}
        </nav>
        {signedIn ? (
          <Routes>
            <Route path="/" element={<OpenJob />} />
            <Route path="/jobs/:jobId" element={<JobRoute client={client} />} />
            <Route path="/tasks/:taskId" element={<TaskRoute client={client} />} />
            <Route path="*" element={<Navigate to="/" replace />} />
          </Routes>
        ) : (
          <SignIn onSignedIn={() => setSignedIn(true)} />
        )}
      </main>
    </BrowserRouter>
  );
}
