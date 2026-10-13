const API_BASE_URL = (import.meta.env.VITE_API_BASE_URL || "")
  .replace(/\/+$/, "");

export interface User {
  username: string;
  role: "admin" | "employee";
}

export interface Dashboard {
  role: User["role"];
  index_ready: boolean;
  document_count: number;
  indexed_document_count: number;
  passage_count: number;
}

export interface DocumentItem {
  path: string;
  name: string;
  type: string;
  size: number;
  indexed: boolean;
}

export interface Employee {
  username: string;
  role: "employee";
  created_at: string;
  managed: boolean;
  active: boolean;
}

export interface ActivityEvent {
  timestamp: string;
  event: string;
  username?: string;
  role?: string;
  files?: string[];
  file?: string;
  action?: string;
  error?: string;
}

export interface ChatSource {
  source: string;
  location: string;
  passage: string;
  is_neighbor?: boolean;
}

export interface ChatResult {
  answer: string;
  sources: ChatSource[];
}

export class ApiError extends Error {
  status: number;
  log?: string;

  constructor(message: string, status: number, log?: string) {
    super(message);
    this.name = "ApiError";
    this.status = status;
    this.log = log;
  }
}

async function request<T>(
  path: string,
  options: {
    token?: string;
    method?: string;
    body?: unknown;
    files?: File[];
  } = {},
): Promise<T> {
  if (!API_BASE_URL) {
    throw new ApiError(
      "The backend URL is not configured. Set VITE_API_BASE_URL in Netlify and redeploy the site.",
      0,
    );
  }
  if (import.meta.env.PROD && !API_BASE_URL.startsWith("https://")) {
    throw new ApiError(
      "The deployed backend URL must use HTTPS. Set VITE_API_BASE_URL to the public HTTPS API address and redeploy.",
      0,
    );
  }
  const headers = new Headers();
  if (options.token) headers.set("Authorization", `Bearer ${options.token}`);
  let body: BodyInit | undefined;
  if (options.files) {
    const formData = new FormData();
    options.files.forEach((file) => formData.append("files", file));
    body = formData;
  } else if (options.body !== undefined) {
    headers.set("Content-Type", "application/json");
    body = JSON.stringify(options.body);
  }

  let response: Response;
  try {
    response = await fetch(`${API_BASE_URL}${path}`, {
      method: options.method || "GET",
      headers,
      body,
    });
  } catch {
    throw new ApiError(
      `Could not connect to the API at ${API_BASE_URL}. Check that the API is running and the frontend API URL is correct.`,
      0,
    );
  }

  const payload = await response.json().catch(() => null) as
    | { detail?: unknown; message?: string; log?: string }
    | null;
  if (!response.ok) {
    const detail = payload?.detail;
    const message = typeof detail === "string"
      ? detail
      : typeof detail === "object" && detail !== null && "message" in detail
        ? String((detail as { message: unknown }).message)
        : payload?.message || `Request failed (${response.status}).`;
    const log = typeof detail === "object" && detail !== null && "log" in detail
      ? String((detail as { log: unknown }).log)
      : payload?.log;
    throw new ApiError(message, response.status, log);
  }
  return payload as T;
}

export const api = {
  login: (username: string, password: string) =>
    request<{ token: string; user: User }>("/api/auth/login", {
      method: "POST",
      body: { username, password },
    }),
  me: (token: string) => request<User>("/api/auth/me", { token }),
  logout: (token: string) =>
    request<{ ok: boolean }>("/api/auth/logout", { token, method: "POST" }),
  changePassword: (token: string, current_password: string, new_password: string) =>
    request<{ ok: boolean }>("/api/auth/password", {
      token,
      method: "POST",
      body: { current_password, new_password },
    }),
  dashboard: (token: string) => request<Dashboard>("/api/dashboard", { token }),
  documents: (token: string) =>
    request<{ documents: DocumentItem[] }>("/api/documents", { token }),
  upload: (token: string, files: File[]) =>
    request<{ uploaded: string[]; vector_count: number; errors: { file?: string; error?: string }[] }>(
      "/api/documents/upload",
      { token, method: "POST", files },
    ),
  deleteDocument: (token: string, path: string) =>
    request<{ deleted: string; vector_count: number; errors: { file?: string; error?: string }[] }>(
      `/api/documents?path=${encodeURIComponent(path)}`,
      { token, method: "DELETE" },
    ),
  rebuild: (token: string) =>
    request<{ vector_count: number; errors: { file?: string; error?: string }[] }>(
      "/api/index/rebuild",
      { token, method: "POST" },
    ),
  employees: (token: string) =>
    request<{ employees: Employee[] }>("/api/employees", { token }),
  createEmployee: (token: string, username: string, password: string) =>
    request<{ username: string; role: string; status: "created" | "reactivated" }>("/api/employees", {
      token,
      method: "POST",
      body: { username, password },
    }),
  deactivateEmployee: (token: string, username: string) =>
    request<{ ok: boolean }>(`/api/employees/${encodeURIComponent(username)}`, {
      token,
      method: "DELETE",
    }),
  activity: (token: string) =>
    request<{ events: ActivityEvent[] }>("/api/activity?limit=100", { token }),
  chat: (token: string, question: string) =>
    request<ChatResult>("/api/chat", {
      token,
      method: "POST",
      body: { question },
    }),
};

export function formatBytes(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}

export function formatDate(value: string): string {
  const date = new Date(value);
  return Number.isNaN(date.getTime())
    ? value
    : new Intl.DateTimeFormat(undefined, { dateStyle: "medium", timeStyle: "short" }).format(date);
}

export function prettyEvent(event: string): string {
  return event
    .split("_")
    .map((part) => part.charAt(0).toUpperCase() + part.slice(1))
    .join(" ");
}
