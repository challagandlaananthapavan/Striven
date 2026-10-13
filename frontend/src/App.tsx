import { useCallback, useEffect, useRef, useState } from "react";
import type { FormEvent } from "react";
import {
  Activity,
  ArrowLeft,
  ArrowRight,
  ArrowUpRight,
  Bot,
  Check,
  ChevronDown,
  CircleHelp,
  Clock3,
  FileImage,
  FileSpreadsheet,
  FileText,
  FileUp,
  History,
  LayoutDashboard,
  LockKeyhole,
  LogOut,
  MessageSquareText,
  Plus,
  RefreshCw,
  Send,
  ShieldCheck,
  Sparkles,
  Trash2,
  Upload,
  Users,
  X,
} from "lucide-react";
import {
  api,
  ApiError,
  type ActivityEvent,
  type ChatSource,
  type Dashboard,
  type DocumentItem,
  type Employee,
  formatBytes,
  formatDate,
  prettyEvent,
  type User,
} from "./api";

type Workspace = "Workspace" | "Ask" | "Upload documents" | "Delete document" | "Employee details" | "Activity log";
type ChatMessage = { role: "user" | "assistant"; content: string; sources?: ChatSource[] };

const ADMIN_WORKSPACES: {
  label: Exclude<Workspace, "Workspace">;
  description: string;
  icon: typeof MessageSquareText;
  tint: string;
}[] = [
  { label: "Ask", description: "Find clear answers in approved files.", icon: MessageSquareText, tint: "purple" },
  { label: "Upload documents", description: "Add source files and refresh search.", icon: FileUp, tint: "blue" },
  { label: "Delete document", description: "Remove a source from the knowledge base.", icon: Trash2, tint: "red" },
  { label: "Employee details", description: "Create and manage employee access.", icon: Users, tint: "amber" },
  { label: "Activity log", description: "Review recent account and document activity.", icon: History, tint: "green" },
];

const DEFAULT_TOKEN = () => window.localStorage.getItem("research-desk-token");

function App() {
  const [token, setToken] = useState<string | null>(() => DEFAULT_TOKEN());
  const [user, setUser] = useState<User | null>(null);
  const [workspace, setWorkspace] = useState<Workspace>("Workspace");
  const [dashboard, setDashboard] = useState<Dashboard | null>(null);
  const [documents, setDocuments] = useState<DocumentItem[]>([]);
  const [employees, setEmployees] = useState<Employee[]>([]);
  const [events, setEvents] = useState<ActivityEvent[]>([]);
  const [activityRefreshing, setActivityRefreshing] = useState(false);
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [loading, setLoading] = useState(Boolean(token));
  const [busy, setBusy] = useState(false);
  const [notice, setNotice] = useState<{ kind: "success" | "error" | "info"; text: string; log?: string } | null>(null);
  const [passwordOpen, setPasswordOpen] = useState(false);
  const [mobileMenuOpen, setMobileMenuOpen] = useState(false);
  const [query, setQuery] = useState("");
  const [selectedFiles, setSelectedFiles] = useState<File[]>([]);
  const [employeeUsername, setEmployeeUsername] = useState("");
  const [employeePassword, setEmployeePassword] = useState("");
  const [revealNewUserPassword, setRevealNewUserPassword] = useState(false);
  const chatBottomRef = useRef<HTMLDivElement>(null);

  const notifyError = useCallback((error: unknown) => {
    if (error instanceof ApiError && error.status === 401) {
      window.localStorage.removeItem("research-desk-token");
      setToken(null);
      setUser(null);
      setWorkspace("Workspace");
    }
    const message = error instanceof Error ? error.message : "Something went wrong.";
    setNotice({
      kind: "error",
      text: message,
      log: error instanceof ApiError ? error.log : undefined,
    });
  }, []);

  const refreshCore = useCallback(async (activeToken: string) => {
    const [stats, fileList] = await Promise.all([
      api.dashboard(activeToken),
      api.documents(activeToken),
    ]);
    setDashboard(stats);
    setDocuments(fileList.documents);
  }, []);

  const refreshActivity = useCallback(async (activeToken: string) => {
    setActivityRefreshing(true);
    try {
      setEvents((await api.activity(activeToken)).events);
    } catch (error) {
      notifyError(error);
    } finally {
      setActivityRefreshing(false);
    }
  }, [notifyError]);

  const refreshCurrent = useCallback(async (activeToken: string, activeUser: User, page: Workspace) => {
    await refreshCore(activeToken);
    if (activeUser.role === "admin" && page === "Employee details") {
      setEmployees((await api.employees(activeToken)).employees);
    }
    if (activeUser.role === "admin" && page === "Activity log") {
      setEvents((await api.activity(activeToken)).events);
    }
  }, [refreshCore]);

  useEffect(() => {
    if (!token) {
      setLoading(false);
      return;
    }
    let cancelled = false;
    const validate = async () => {
      try {
        const signedInUser = await api.me(token);
        if (cancelled) return;
        setUser(signedInUser);
        if (signedInUser.role === "employee") setWorkspace("Ask");
        await refreshCurrent(token, signedInUser, signedInUser.role === "employee" ? "Ask" : "Workspace");
      } catch (error) {
        if (!cancelled) notifyError(error);
      } finally {
        if (!cancelled) setLoading(false);
      }
    };
    void validate();
    return () => { cancelled = true; };
  }, [token, refreshCurrent, notifyError]);

  useEffect(() => {
    chatBottomRef.current?.scrollIntoView({ behavior: "smooth", block: "end" });
  }, [messages]);

  useEffect(() => {
    if (!token || user?.role !== "admin" || workspace !== "Activity log") return;
    const interval = window.setInterval(() => {
      void refreshActivity(token);
    }, 15_000);
    return () => window.clearInterval(interval);
  }, [token, user?.role, workspace, refreshActivity]);

  const openWorkspace = async (page: Workspace) => {
    setWorkspace(page);
    setMobileMenuOpen(false);
    setNotice(null);
    if (token && user) {
      try {
        await refreshCurrent(token, user, page);
      } catch (error) {
        notifyError(error);
      }
    }
  };

  const signOut = async () => {
    if (token) {
      try {
        await api.logout(token);
      } catch {
        // Clear the local session even if the server is temporarily unreachable.
      }
    }
    window.localStorage.removeItem("research-desk-token");
    setToken(null);
    setUser(null);
    setWorkspace("Workspace");
    setDashboard(null);
    setDocuments([]);
    setMessages([]);
    setNotice(null);
  };

  const runAction = async (action: () => Promise<void>, successMessage?: string) => {
    setBusy(true);
    setNotice(null);
    try {
      await action();
      if (successMessage) setNotice({ kind: "success", text: successMessage });
    } catch (error) {
      notifyError(error);
    } finally {
      setBusy(false);
    }
  };

  const handleLogin = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    const form = new FormData(event.currentTarget);
    setBusy(true);
    setNotice(null);
    try {
      const result = await api.login(String(form.get("username") || "").trim(), String(form.get("password") || ""));
      window.localStorage.setItem("research-desk-token", result.token);
      setToken(result.token);
      setUser(result.user);
      setWorkspace(result.user.role === "admin" ? "Workspace" : "Ask");
      setLoading(false);
      await refreshCurrent(result.token, result.user, result.user.role === "admin" ? "Workspace" : "Ask");
    } catch (error) {
      notifyError(error);
    } finally {
      setBusy(false);
    }
  };

  const sendQuestion = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    const question = query.trim();
    if (!token || !question || busy) return;
    setQuery("");
    setMessages((current) => [...current, { role: "user", content: question }]);
    setBusy(true);
    setNotice(null);
    try {
      const response = await api.chat(token, question);
      setMessages((current) => [...current, { role: "assistant", content: response.answer, sources: response.sources }]);
    } catch (error) {
      notifyError(error);
      setMessages((current) => [...current, {
        role: "assistant",
        content: error instanceof Error ? error.message : "The answer could not be generated.",
      }]);
    } finally {
      setBusy(false);
    }
  };

  const doUpload = async () => {
    if (!token || selectedFiles.length === 0) return;
    await runAction(async () => {
      const result = await api.upload(token, selectedFiles);
      setSelectedFiles([]);
      await refreshCore(token);
      if (result.errors.length) {
        setNotice({
          kind: "info",
          text: `Uploaded ${result.uploaded.length} file(s), but ${result.errors.length} file(s) could not be processed.`,
          log: result.errors.map((entry) => `${entry.file || "File"}: ${entry.error || "Unknown processing error"}`).join("\n"),
        });
      } else {
        setNotice({ kind: "success", text: `Added ${result.uploaded.length} document(s). ${result.vector_count} passages are searchable.` });
      }
    });
  };

  const doDelete = async (file: DocumentItem) => {
    if (!token || !window.confirm(`Permanently delete "${file.name}" and rebuild the search index?`)) return;
    await runAction(async () => {
      const result = await api.deleteDocument(token, file.path);
      await refreshCore(token);
      setNotice({
        kind: result.errors.length ? "info" : "success",
        text: result.errors.length
          ? `Deleted ${file.name}; ${result.errors.length} item(s) need attention.`
          : `Deleted ${file.name}. ${result.vector_count} passages remain searchable.`,
        log: result.errors.map((entry) => `${entry.file || "File"}: ${entry.error || "Unknown processing error"}`).join("\n"),
      });
    });
  };

  const doRebuild = async () => {
    if (!token) return;
    await runAction(async () => {
      const result = await api.rebuild(token);
      await refreshCore(token);
      setNotice({
        kind: result.errors.length ? "info" : "success",
        text: result.errors.length
          ? `Index rebuild finished with ${result.errors.length} processing error(s).`
          : `Index rebuilt. ${result.vector_count} passages are searchable.`,
        log: result.errors.map((entry) => `${entry.file || "File"}: ${entry.error || "Unknown processing error"}`).join("\n"),
      });
    });
  };

  const createEmployee = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    if (!token) return;
    const username = employeeUsername.trim();
    await runAction(async () => {
      const result = await api.createEmployee(token, username, employeePassword);
      setEmployeeUsername("");
      setEmployeePassword("");
      setEmployees((current) => {
        const existing = current.find((employee) => employee.username.toLowerCase() === username.toLowerCase());
        if (existing) {
          return current.map((employee) => employee.username.toLowerCase() === username.toLowerCase()
            ? { ...employee, active: true }
            : employee);
        }
        return [
          ...current,
          { username, role: "employee", created_at: new Date().toISOString(), managed: true, active: true },
        ];
      });
      setNotice({
        kind: "success",
        text: result.status === "reactivated"
          ? `Employee login for ${username} has been reactivated with the new temporary password.`
          : `Employee login created for ${username}.`,
      });
    });
  };

  const disableEmployee = async (employee: Employee) => {
    if (!token || !window.confirm(`Disable the employee login for "${employee.username}"?`)) return;
    await runAction(async () => {
      await api.deactivateEmployee(token, employee.username);
      setEmployees((current) => current.map((item) => item.username === employee.username
        ? { ...item, active: false }
        : item));
    }, `Employee login for ${employee.username} has been disabled.`);
  };

  const prepareEmployeeRestore = (employee: Employee) => {
    setEmployeeUsername(employee.username);
    setEmployeePassword("");
    setNotice({
      kind: "info",
      text: `Set a new temporary password above and select Create employee login to reactivate ${employee.username}.`,
    });
    document.getElementById("employee-password")?.focus();
  };

  const doChangePassword = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    if (!token) return;
    const form = new FormData(event.currentTarget);
    const currentPassword = String(form.get("currentPassword") || "");
    const newPassword = String(form.get("newPassword") || "");
    const confirmPassword = String(form.get("confirmPassword") || "");
    if (newPassword !== confirmPassword) {
      setNotice({ kind: "error", text: "The new passwords do not match." });
      return;
    }
    await runAction(async () => {
      await api.changePassword(token, currentPassword, newPassword);
      setPasswordOpen(false);
    }, "Your password has been updated.");
  };

  if (loading) {
    return <div className="boot-screen"><span className="brand-mark">R</span><span className="loader" />Opening your secure workspace</div>;
  }
  if (!token || !user) {
    return <LoginScreen onSubmit={handleLogin} busy={busy} notice={notice} />;
  }

  const isLanding = user.role === "admin" && workspace === "Workspace";
  const currentNav = ADMIN_WORKSPACES.find((item) => item.label === workspace);
  const title = isLanding ? "Workspace" : currentNav?.label || "Ask";
  const subtitle = isLanding
    ? "Everything your team needs to explore and manage its knowledge base."
    : currentNav?.description || "Ask questions and get grounded answers from approved files.";

  return (
    <div className="app-shell">
      <aside className={`sidebar ${mobileMenuOpen ? "sidebar-open" : ""}`}>
        <a className="brand" href="#" onClick={(event) => { event.preventDefault(); void openWorkspace(user.role === "admin" ? "Workspace" : "Ask"); }}>
          <span className="brand-mark">R</span>
          <span className="brand-copy"><strong>Research Desk</strong><small>KNOWLEDGE WORKSPACE</small></span>
        </a>
        <div className="side-section-label">MENU</div>
        <nav className="side-nav" aria-label="Workspace navigation">
          <button
            className={`nav-item ${
              user.role === "admin" ? (isLanding ? "active" : "") : (workspace === "Ask" ? "active" : "")
            }`}
            onClick={() => void openWorkspace(user.role === "admin" ? "Workspace" : "Ask")}
          >
            <LayoutDashboard size={17} /><span>Workspace</span>
          </button>
          <button className="nav-item password-nav" onClick={() => setPasswordOpen(true)}>
            <LockKeyhole size={17} /><span>Change your password</span>
          </button>
        </nav>
        <div className="sidebar-spacer" />
        <div className="knowledge-card">
          <div className="knowledge-head"><span className="knowledge-icon"><ShieldCheck size={15} /></span><span>Knowledge base</span></div>
          <div className="knowledge-stats">
            <div><strong>{dashboard?.document_count ?? 0}</strong><span>documents</span></div>
            <div><strong>{dashboard?.passage_count ?? 0}</strong><span>passages</span></div>
          </div>
          <div className="index-state"><span className={`status-dot ${dashboard?.index_ready ? "online" : ""}`} />{dashboard?.index_ready ? "Index is ready" : "Index needs documents"}</div>
        </div>
        <div className="profile">
          <span className="avatar">{user.username.slice(0, 1).toUpperCase()}</span>
          <span className="profile-copy"><strong>{user.username}</strong><small>{user.role === "admin" ? "Administrator" : "Employee"}</small></span>
        </div>
        <button className="logout-button" onClick={() => void signOut()}><LogOut size={16} /> Sign out</button>
      </aside>

      {mobileMenuOpen && <button className="mobile-scrim" aria-label="Close menu" onClick={() => setMobileMenuOpen(false)} />}
      <main className="main-area">
        <header className="topbar">
          <button className="mobile-menu-button icon-button" onClick={() => setMobileMenuOpen((open) => !open)} aria-label="Toggle menu"><span>☰</span></button>
          <div className="breadcrumbs"><span>Research Desk</span><span className="breadcrumb-sep">/</span><strong>{title}</strong></div>
          <div className="topbar-right">
            <span className="secure-label"><span className="status-dot online" />Secure workspace</span>
            <span className="top-avatar" title={user.username}>{user.username.slice(0, 1).toUpperCase()}</span>
          </div>
        </header>

        <section className="page-content">
          {!isLanding && user.role === "admin" && (
            <button className="back-button" onClick={() => void openWorkspace("Workspace")}>
              <ArrowLeft size={16} /> Back to workspace
            </button>
          )}
          <div className="page-heading">
            <div>
              <div className="eyebrow">{isLanding ? "YOUR KNOWLEDGE HUB" : user.role === "admin" ? "ADMINISTRATION" : "EMPLOYEE WORKSPACE"}</div>
              <h1>{isLanding ? "Good to see you, " : ""}{isLanding && <span>{user.username}.</span>}{!isLanding && title}</h1>
              <p>{subtitle}</p>
            </div>
            {dashboard?.index_ready && <div className="heading-status"><span className="status-dot online" /> Knowledge index ready</div>}
          </div>

          {notice && <Notice notice={notice} onDismiss={() => setNotice(null)} />}

          {isLanding && (
            <WorkspaceLanding onOpen={(page) => void openWorkspace(page)} dashboard={dashboard} busy={busy} onRebuild={doRebuild} />
          )}
          {!isLanding && workspace === "Ask" && (
            <AskWorkspace
              dashboard={dashboard}
              documents={documents}
              messages={messages}
              query={query}
              busy={busy}
              setQuery={setQuery}
              onSubmit={sendQuestion}
              bottomRef={chatBottomRef}
            />
          )}
          {workspace === "Upload documents" && user.role === "admin" && (
            <UploadWorkspace
              files={selectedFiles}
              setFiles={setSelectedFiles}
              busy={busy}
              onUpload={doUpload}
              onRebuild={doRebuild}
              documents={documents}
            />
          )}
          {workspace === "Delete document" && user.role === "admin" && (
            <DeleteWorkspace documents={documents} busy={busy} onDelete={(file) => void doDelete(file)} />
          )}
          {workspace === "Employee details" && user.role === "admin" && (
            <EmployeeWorkspace
              employees={employees}
              username={employeeUsername}
              password={employeePassword}
              revealPassword={revealNewUserPassword}
              setUsername={setEmployeeUsername}
              setPassword={setEmployeePassword}
              setRevealPassword={setRevealNewUserPassword}
              onCreate={createEmployee}
              onDisable={(employee) => void disableEmployee(employee)}
              onRestore={prepareEmployeeRestore}
              busy={busy}
            />
          )}
          {workspace === "Activity log" && user.role === "admin" && (
            <ActivityWorkspace
              events={events}
              refreshing={activityRefreshing}
              onRefresh={() => void refreshActivity(token)}
            />
          )}
        </section>
        <footer className="page-footer"><span>Research Desk <span className="footer-dot">·</span> Your approved knowledge, in one place.</span><span><CircleHelp size={14} /> Need help? Contact your administrator.</span></footer>
      </main>

      {passwordOpen && <PasswordModal onClose={() => setPasswordOpen(false)} onSubmit={doChangePassword} busy={busy} />}
    </div>
  );
}

function LoginScreen({
  onSubmit,
  busy,
  notice,
}: {
  onSubmit: (event: FormEvent<HTMLFormElement>) => void;
  busy: boolean;
  notice: { kind: "success" | "error" | "info"; text: string; log?: string } | null;
}) {
  return (
    <main className="login-screen">
      <div className="login-backdrop" />
      <div className="login-brand-panel">
        <div className="login-logo"><img src="/Image.png" alt="Striven" /></div>
        <div className="login-pitch"><div className="eyebrow">RESEARCH DESK</div><h1>Knowledge, made<br /><span>easy to find.</span></h1><p>Ask better questions. Get answers rooted in your team’s approved documents.</p><div className="login-proof"><span><ShieldCheck size={16} /> Private by design</span><span><Sparkles size={16} /> Grounded answers</span></div></div>
        <div className="login-version">STRIVEN TECHNOLOGIES <span>·</span> KNOWLEDGE ASSISTANT</div>
      </div>
      <div className="login-form-wrap">
        <div className="login-card">
          <span className="login-mini-mark">R</span>
          <div className="eyebrow">WELCOME BACK</div>
          <h2>Sign in to your workspace</h2>
          <p className="login-subtitle">Use the account provided by your administrator.</p>
          {notice && <Notice notice={notice} onDismiss={() => undefined} />}
          <form onSubmit={onSubmit}>
            <label className="field-label" htmlFor="username">Username</label>
            <input id="username" name="username" type="text" autoComplete="username" placeholder="Enter your username" required />
            <label className="field-label" htmlFor="password">Password</label>
            <input id="password" name="password" type="password" autoComplete="current-password" placeholder="Enter your password" required />
            <button className="primary-button login-submit" type="submit" disabled={busy}>{busy ? <><span className="button-spinner" /> Signing in…</> : <>Sign in <ArrowRight size={17} /></>}</button>
          </form>
          <div className="login-assurance"><LockKeyhole size={14} /> Your workspace is protected with secure sign-in.</div>
        </div>
      </div>
    </main>
  );
}

function WorkspaceLanding({
  onOpen,
  dashboard,
  busy,
  onRebuild,
}: {
  onOpen: (page: Exclude<Workspace, "Workspace">) => void;
  dashboard: Dashboard | null;
  busy: boolean;
  onRebuild: () => void;
}) {
  const workspaces = ADMIN_WORKSPACES;
  return (
    <>
      <div className="overview-grid">
        <MetricCard icon={FileText} label="Source documents" value={dashboard?.document_count ?? 0} foot="Approved files in your library" tint="purple" />
        <MetricCard icon={Sparkles} label="Searchable passages" value={dashboard?.passage_count ?? 0} foot="Indexed for grounded answers" tint="blue" />
        <MetricCard icon={Activity} label="Index status" value={dashboard?.index_ready ? "Ready" : "Not ready"} foot={dashboard?.index_ready ? "Search is available" : "Upload and index documents"} tint={dashboard?.index_ready ? "green" : "amber"} />
      </div>
      <div className="section-heading"><div><h2>Choose an action</h2><p>Open a workspace to get started.</p></div><span className="section-hint">ADMIN WORKSPACE</span></div>
      <div className="workspace-grid">
        {workspaces.map(({ label, description, icon: Icon, tint }) => (
          <button className="workspace-card" key={label} onClick={() => onOpen(label)}>
            <span className={`workspace-icon tint-${tint}`}><Icon size={20} /></span>
            <span className="workspace-card-text"><strong>{label}</strong><small>{description}</small></span>
            <span className="workspace-open"><ArrowUpRight size={17} /></span>
          </button>
        ))}
      </div>
      <div className="quick-actions">
        <div className="quick-action-copy"><span className="quick-action-icon"><RefreshCw size={18} /></span><span><strong>Refresh your search index</strong><small>Re-process all approved source documents.</small></span></div>
        <button className="outline-button" onClick={onRebuild} disabled={busy}>{busy ? <span className="button-spinner" /> : <RefreshCw size={15} />} Rebuild index</button>
      </div>
    </>
  );
}

function MetricCard({ icon: Icon, label, value, foot, tint }: { icon: typeof FileText; label: string; value: number | string; foot: string; tint: string }) {
  return <article className="metric-card"><div className="metric-top"><span className={`metric-icon tint-${tint}`}><Icon size={17} /></span><span className="metric-label">{label}</span></div><strong className="metric-value">{value}</strong><span className="metric-foot">{foot}</span></article>;
}

function AskWorkspace({
  dashboard,
  documents,
  messages,
  query,
  busy,
  setQuery,
  onSubmit,
  bottomRef,
}: {
  dashboard: Dashboard | null;
  documents: DocumentItem[];
  messages: ChatMessage[];
  query: string;
  busy: boolean;
  setQuery: (value: string) => void;
  onSubmit: (event: FormEvent<HTMLFormElement>) => void;
  bottomRef: React.RefObject<HTMLDivElement>;
}) {
  return (
    <div className="ask-layout">
      <div className="chat-panel">
        {messages.length === 0 ? (
          <div className="ask-empty">
            <span className="ask-art"><Bot size={29} /></span>
            <div className="eyebrow">YOUR DOCUMENT ASSISTANT</div>
            <h2>What would you like to know?</h2>
            <p>Ask a question about your approved documents. Answers include source locations so you can verify the details.</p>
            <div className="suggestion-list">
              {["Summarize the key information in the documents", "What are the most important requirements?", "Compare the relevant policies"].map((prompt) => (
                <button key={prompt} onClick={() => setQuery(prompt)}>{prompt}<ArrowUpRight size={14} /></button>
              ))}
            </div>
            {!dashboard?.index_ready && <div className="inline-state warning"><CircleHelp size={16} /> The knowledge base has no searchable index yet. Ask an administrator to upload and index approved files.</div>}
          </div>
        ) : (
          <div className="message-list">
            {messages.map((message, index) => (
              <article className={`message-row ${message.role}`} key={`${message.role}-${index}`}>
                <span className={`message-avatar ${message.role}`}>{message.role === "assistant" ? <Bot size={17} /> : "Y"}</span>
                <div className="message-content"><span className="message-author">{message.role === "assistant" ? "Research Desk" : "You"}</span><p>{message.content}</p>
                  {message.sources?.length ? <div className="source-list"><div className="source-heading">SOURCES <span>{message.sources.length}</span></div>{message.sources.map((source, sourceIndex) => <details className="source-card" key={`${source.source}-${sourceIndex}`}><summary><FileText size={15} /><span><strong>{source.source}</strong><small>{source.location}{source.is_neighbor ? " · neighboring passage" : ""}</small></span><ChevronDown size={15} /></summary><div className="source-passage">{source.passage}</div></details>)}</div> : null}
                </div>
              </article>
            ))}
            {busy && <div className="thinking"><span className="thinking-dots"><i /><i /><i /></span>Searching approved documents…</div>}
            <div ref={bottomRef} />
          </div>
        )}
        <form className="chat-composer" onSubmit={onSubmit}>
          <textarea value={query} onChange={(event) => setQuery(event.target.value)} onKeyDown={(event) => { if (event.key === "Enter" && !event.shiftKey) { event.preventDefault(); event.currentTarget.form?.requestSubmit(); } }} placeholder={dashboard?.index_ready ? "Ask a question about your documents…" : "No searchable documents yet"} rows={1} disabled={!dashboard?.index_ready || busy} />
          <div className="composer-footer"><span>Answers are grounded in your approved files <span className="composer-dot">·</span> Enter to send</span><button className="send-button" type="submit" disabled={!query.trim() || busy || !dashboard?.index_ready} aria-label="Send question"><Send size={16} /></button></div>
        </form>
      </div>
      <aside className="ask-context">
        <div className="context-card"><div className="context-title"><span className="context-title-icon"><ShieldCheck size={15} /></span>About your search</div><p>Answers use your approved knowledge base and include source references for verification.</p><div className="context-divider" /><div className="context-stat"><span>Searchable files</span><strong>{documents.filter((item) => item.indexed).length}</strong></div><div className="context-stat"><span>Index status</span><strong className={dashboard?.index_ready ? "text-green" : "text-muted"}>{dashboard?.index_ready ? "Ready" : "Unavailable"}</strong></div><div className="context-pills"><span>Hybrid search</span><span>Source citations</span></div></div>
        <div className="context-note"><Sparkles size={16} /><span><strong>Tip</strong><small>Be specific about the topic or document you want to explore.</small></span></div>
      </aside>
    </div>
  );
}

function UploadWorkspace({
  files,
  setFiles,
  busy,
  onUpload,
  onRebuild,
  documents,
}: {
  files: File[];
  setFiles: (files: File[]) => void;
  busy: boolean;
  onUpload: () => void;
  onRebuild: () => void;
  documents: DocumentItem[];
}) {
  const fileRef = useRef<HTMLInputElement>(null);
  const [dragging, setDragging] = useState(false);
  const acceptFiles = (incoming: FileList | File[]) => {
    const valid = Array.from(incoming).filter((file) => /\.(pdf|png|jpe?g|webp|bmp|tiff?|xlsx?|xlsm|csv)$/i.test(file.name));
    if (valid.length !== incoming.length) window.alert("Some files were skipped. Supported formats: PDF, image, Excel, and CSV.");
    setFiles([...files, ...valid.filter((file) => !files.some((selected) => selected.name === file.name))]);
  };
  return (
    <div className="operation-layout">
      <section className="panel upload-panel"><div className="panel-heading"><div><h2>Add source documents</h2><p>Files are saved to the approved knowledge base and indexed for search.</p></div><span className="panel-heading-icon tint-blue"><Upload size={18} /></span></div>
        <div className={`dropzone ${dragging ? "dragging" : ""}`} onDragOver={(event) => { event.preventDefault(); setDragging(true); }} onDragLeave={() => setDragging(false)} onDrop={(event) => { event.preventDefault(); setDragging(false); acceptFiles(event.dataTransfer.files); }} onClick={() => fileRef.current?.click()} role="button" tabIndex={0} onKeyDown={(event) => { if (event.key === "Enter" || event.key === " ") fileRef.current?.click(); }}>
          <span className="dropzone-icon"><FileUp size={23} /></span><strong>Drop files here or <span>browse</span></strong><small>PDF, images, Excel, CSV <span>·</span> Up to 30 MB each</small>
          <input ref={fileRef} type="file" multiple accept=".pdf,.png,.jpg,.jpeg,.webp,.bmp,.tif,.tiff,.xlsx,.xlsm,.xls,.csv" onChange={(event) => { if (event.target.files) acceptFiles(event.target.files); event.target.value = ""; }} hidden />
        </div>
        {files.length > 0 && <div className="selected-files"><div className="selected-heading"><strong>Selected files</strong><span>{files.length} item{files.length === 1 ? "" : "s"}</span></div>{files.map((file) => <div className="file-row" key={`${file.name}-${file.size}`}><FileTypeIcon name={file.name} /><span className="file-row-main"><strong>{file.name}</strong><small>{formatBytes(file.size)}</small></span><button className="icon-button danger-hover" title={`Remove ${file.name}`} onClick={() => setFiles(files.filter((item) => item !== file))}><X size={16} /></button></div>)}</div>}
        <div className="panel-actions"><button className="primary-button" onClick={onUpload} disabled={busy || files.length === 0}>{busy ? <><span className="button-spinner" /> Uploading & indexing…</> : <><Upload size={16} /> Upload and index</>}</button><button className="outline-button" onClick={onRebuild} disabled={busy}><RefreshCw size={15} /> Rebuild all</button></div>
      </section>
      <aside className="side-panel"><div className="panel side-info"><span className="side-info-icon tint-green"><ShieldCheck size={18} /></span><h3>Approved knowledge</h3><p>New files become available to employees after successful indexing.</p><div className="side-info-stat"><span>Current documents</span><strong>{documents.length}</strong></div></div><div className="panel side-info"><span className="side-info-icon tint-blue"><CircleHelp size={18} /></span><h3>Supported files</h3><ul><li>PDF documents</li><li>Images with readable text</li><li>Excel workbooks and CSV files</li></ul></div></aside>
    </div>
  );
}

function DeleteWorkspace({ documents, busy, onDelete }: { documents: DocumentItem[]; busy: boolean; onDelete: (file: DocumentItem) => void }) {
  return <section className="panel table-panel"><div className="panel-heading"><div><h2>Approved source files</h2><p>Deleting a file also rebuilds the search index.</p></div><span className="table-count">{documents.length} document{documents.length === 1 ? "" : "s"}</span></div>
    {documents.length ? <div className="table-scroll"><table><thead><tr><th>DOCUMENT</th><th>TYPE</th><th>SIZE</th><th>INDEX</th><th /></tr></thead><tbody>{documents.map((file) => <tr key={file.path}><td><div className="table-file"><FileTypeIcon name={file.name} /><span><strong>{file.name}</strong><small>{file.path}</small></span></div></td><td><span className="type-badge">{file.type}</span></td><td>{formatBytes(file.size)}</td><td><span className={`index-badge ${file.indexed ? "indexed" : "not-indexed"}`}><span />{file.indexed ? "Indexed" : "Not indexed"}</span></td><td><button className="icon-button delete-action" disabled={busy} title={`Delete ${file.name}`} onClick={() => onDelete(file)}><Trash2 size={16} /></button></td></tr>)}</tbody></table></div> : <EmptyState icon={FileText} title="No documents to delete" copy="Upload approved files to see them listed here." />}
  </section>;
}

function EmployeeWorkspace({
  employees,
  username,
  password,
  revealPassword,
  setUsername,
  setPassword,
  setRevealPassword,
  onCreate,
  onDisable,
  onRestore,
  busy,
}: {
  employees: Employee[];
  username: string;
  password: string;
  revealPassword: boolean;
  setUsername: (value: string) => void;
  setPassword: (value: string) => void;
  setRevealPassword: (value: boolean) => void;
  onCreate: (event: FormEvent<HTMLFormElement>) => void;
  onDisable: (employee: Employee) => void;
  onRestore: (employee: Employee) => void;
  busy: boolean;
}) {
  const restoringAccount = employees.some(
    (employee) => !employee.active && employee.username.toLowerCase() === username.trim().toLowerCase(),
  );
  return <div className="employee-layout"><section className="panel create-employee"><div className="panel-heading"><div><h2>Create employee login</h2><p>Give each employee an individual account for approved document search.</p></div><span className="panel-heading-icon tint-purple"><Plus size={18} /></span></div>
    <form onSubmit={onCreate} className="stacked-form"><label className="field-label" htmlFor="employee-username">Username</label><input id="employee-username" value={username} onChange={(event) => setUsername(event.target.value)} minLength={3} maxLength={40} pattern="[A-Za-z0-9._-]+" placeholder="e.g. alex.morgan" required /><small className="form-hint">3–40 letters, numbers, dots, underscores, or hyphens. Usernames are case-insensitive.</small><label className="field-label" htmlFor="employee-password">Temporary password</label><div className="password-input-wrap"><input id="employee-password" type={revealPassword ? "text" : "password"} value={password} onChange={(event) => setPassword(event.target.value)} minLength={12} maxLength={256} placeholder="At least 12 characters" required /><button type="button" className="password-toggle" onClick={() => setRevealPassword(!revealPassword)}>{revealPassword ? "Hide" : "Show"}</button></div><small className="form-hint">{restoringAccount ? "This replaces the old password and restores employee access." : "Ask the employee to change their temporary password after sign-in."}</small><button className="primary-button" type="submit" disabled={busy || username.length < 3 || password.length < 12}>{busy ? <><span className="button-spinner" /> Saving…</> : restoringAccount ? <><RefreshCw size={16} /> Reactivate employee login</> : <><Plus size={16} /> Create employee login</>}</button></form>
  </section><section className="panel table-panel employee-table"><div className="panel-heading"><div><h2>Employee accounts</h2><p>Review, disable, or restore employee access.</p></div><span className="table-count">{employees.filter((employee) => employee.active).length} active · {employees.filter((employee) => !employee.active).length} inactive</span></div>
    {employees.length ? <div className="table-scroll"><table><thead><tr><th>EMPLOYEE</th><th>CREATED</th><th>ACCESS</th><th /></tr></thead><tbody>{employees.map((employee) => <tr key={employee.username}><td><div className="employee-cell"><span className="avatar small">{employee.username.slice(0, 1).toUpperCase()}</span><span><strong>{employee.username}</strong><small>Employee</small></span></div></td><td>{formatDate(employee.created_at)}</td><td><span className={`access-badge ${employee.active ? "" : "inactive"}`}><span /> {employee.active ? "Active" : "Inactive"}</span></td><td>{employee.managed ? employee.active ? <button className="icon-button delete-action" title={`Disable ${employee.username}`} disabled={busy} onClick={() => onDisable(employee)}><Trash2 size={16} /></button> : <button className="restore-button" disabled={busy} onClick={() => onRestore(employee)}><RefreshCw size={13} /> Restore</button> : <span className="protected-account"><ShieldCheck size={14} /> Default</span>}</td></tr>)}</tbody></table></div> : <EmptyState icon={Users} title="No employee accounts" copy="Create a separate login for each employee." />}
  </section></div>;
}

function ActivityWorkspace({ events, refreshing, onRefresh }: { events: ActivityEvent[]; refreshing: boolean; onRefresh: () => void }) {
  return <section className="panel activity-panel"><div className="panel-heading"><div><h2>Recent activity</h2><p>Security and knowledge-base events. Passwords and question text are never recorded.</p></div><div className="activity-controls"><span className="panel-heading-icon tint-green"><History size={18} /></span><button className="outline-button refresh-activity" onClick={onRefresh} disabled={refreshing}>{refreshing ? <span className="button-spinner" /> : <RefreshCw size={14} />} Refresh</button></div></div>
    {events.length ? <div className="activity-list">{events.map((event, index) => <article className="activity-row" key={`${event.timestamp}-${index}`}><span className="activity-icon"><Activity size={16} /></span><div className="activity-copy"><strong>{prettyEvent(event.event)}</strong><small>{activityDetails(event)}</small></div><time>{formatDate(event.timestamp)}</time></article>)}</div> : <EmptyState icon={Clock3} title="No activity recorded yet" copy="Sign-ins and workspace actions will appear here." />}
  </section>;
}

function activityDetails(event: ActivityEvent): string {
  const subject = event.username ? ` · ${event.username}` : "";
  if (event.files?.length) return `${event.files.join(", ")}${subject}`;
  if (event.file) return `${event.file}${subject}`;
  if (event.role) return `${event.role}${subject}`;
  return subject.trim() || "Workspace event";
}

function EmptyState({ icon: Icon, title, copy }: { icon: typeof FileText; title: string; copy: string }) {
  return <div className="empty-state"><span><Icon size={22} /></span><strong>{title}</strong><p>{copy}</p></div>;
}

function FileTypeIcon({ name }: { name: string }) {
  const extension = name.split(".").pop()?.toLowerCase();
  if (["png", "jpg", "jpeg", "webp", "bmp", "tif", "tiff"].includes(extension || "")) return <span className="file-type-icon image"><FileImage size={17} /></span>;
  if (["xlsx", "xls", "xlsm", "csv"].includes(extension || "")) return <span className="file-type-icon sheet"><FileSpreadsheet size={17} /></span>;
  return <span className="file-type-icon pdf"><FileText size={17} /></span>;
}

function Notice({ notice, onDismiss }: { notice: { kind: "success" | "error" | "info"; text: string; log?: string }; onDismiss: () => void }) {
  return <div className={`notice notice-${notice.kind}`}><span className="notice-mark">{notice.kind === "success" ? <Check size={16} /> : notice.kind === "error" ? <CircleHelp size={16} /> : <RefreshCw size={16} />}</span><div className="notice-content"><span>{notice.text}</span>{notice.log && <details><summary>View details</summary><pre>{notice.log}</pre></details>}</div><button className="icon-button" onClick={onDismiss} aria-label="Dismiss notification"><X size={15} /></button></div>;
}

function PasswordModal({ onClose, onSubmit, busy }: { onClose: () => void; onSubmit: (event: FormEvent<HTMLFormElement>) => void; busy: boolean }) {
  return <div className="modal-overlay" onMouseDown={(event) => { if (event.target === event.currentTarget) onClose(); }}><section className="modal-card"><div className="modal-header"><span className="modal-icon"><LockKeyhole size={18} /></span><button className="icon-button" onClick={onClose} aria-label="Close"><X size={17} /></button></div><h2>Change your password</h2><p>Choose a strong password with at least 12 characters.</p><form className="stacked-form" onSubmit={onSubmit}><label className="field-label" htmlFor="currentPassword">Current password</label><input id="currentPassword" name="currentPassword" type="password" autoComplete="current-password" required /><label className="field-label" htmlFor="newPassword">New password</label><input id="newPassword" name="newPassword" type="password" autoComplete="new-password" minLength={12} required /><label className="field-label" htmlFor="confirmPassword">Confirm new password</label><input id="confirmPassword" name="confirmPassword" type="password" autoComplete="new-password" minLength={12} required /><div className="modal-actions"><button className="outline-button" type="button" onClick={onClose}>Cancel</button><button className="primary-button" type="submit" disabled={busy}>{busy ? "Updating…" : "Update password"}</button></div></form></section></div>;
}

export default App;
