import { invoke, isTauri } from "@tauri-apps/api/core";

export type EngineHealth = {
  status: "ok" | "degraded";
  app: string;
  version: string;
  database: "ok" | "error";
  database_path: string;
};

export type Workspace = {
  id: string;
  name: string;
  description: string | null;
  archived: boolean;
};

export type WorkspaceRoot = {
  id: string;
  workspace_id: string;
  path: string;
  read_allowed: boolean;
  write_allowed: boolean;
  watch_enabled: boolean;
};

export type IndexedFile = {
  id: string;
  workspace_id: string;
  path: string;
  filename: string;
  extension: string | null;
  mime_type: string | null;
  size: number;
  sha256: string | null;
  status: string;
  modified_at: string | null;
};

export type Conversation = {
  id: string;
  workspace_id: string | null;
  title: string | null;
  created_at: string;
  updated_at: string;
};

export type ChatMessage = {
  id: string;
  conversation_id: string;
  role: "user" | "assistant" | "system";
  content: string;
  created_at: string;
};

export type DesktopSettings = {
  privacy_mode: "local" | "hybrid" | "cloud";
  default_model: string;
  reasoning_level: "low" | "medium" | "high";
  auto_index: boolean;
};

type EngineBootstrap = {
  endpoint: string;
  session_token: string;
};

export type EngineConnection = EngineBootstrap & {
  health: EngineHealth;
};

let bootstrapCache: EngineBootstrap | null = null;

async function resolveBootstrap(): Promise<EngineBootstrap> {
  if (bootstrapCache) return bootstrapCache;
  if (isTauri()) {
    const bootstrap = await invoke<EngineBootstrap | null>("get_engine_bootstrap");
    if (!bootstrap) throw new Error("桌面后端尚未启动");
    bootstrapCache = bootstrap;
    return bootstrap;
  }
  bootstrapCache = {
    endpoint: import.meta.env.VITE_DESKAI_ENGINE_URL ?? "http://127.0.0.1:8765",
    session_token: import.meta.env.VITE_DESKAI_ENGINE_TOKEN ?? "",
  };
  return bootstrapCache;
}

async function request<T>(path: string, init: RequestInit = {}): Promise<T> {
  const bootstrap = await resolveBootstrap();
  const headers = new Headers(init.headers);
  if (bootstrap.session_token) headers.set("X-DeskAI-Token", bootstrap.session_token);
  if (init.body && !headers.has("Content-Type")) headers.set("Content-Type", "application/json");
  const response = await fetch(`${bootstrap.endpoint}${path}`, { ...init, headers });
  if (!response.ok) {
    const body = await response.json().catch(() => null);
    throw new Error(body?.message ?? body?.detail ?? `HTTP ${response.status}`);
  }
  return (await response.json()) as T;
}

export async function checkEngine(signal?: AbortSignal): Promise<EngineConnection> {
  const bootstrap = await resolveBootstrap();
  const health = await request<EngineHealth>("/health", { signal });
  return { ...bootstrap, health };
}

export function listWorkspaces(): Promise<Workspace[]> {
  return request<Workspace[]>("/workspaces");
}

export function createWorkspace(name: string): Promise<Workspace> {
  return request<Workspace>("/workspaces", {
    method: "POST",
    body: JSON.stringify({ name }),
  });
}

export async function addWorkspaceRoot(workspaceId: string, path: string) {
  return request<{ root: WorkspaceRoot; scan: Record<string, number> | null }>(
    `/workspaces/${workspaceId}/roots`,
    {
      method: "POST",
      body: JSON.stringify({ path, read_allowed: true, write_allowed: false, watch_enabled: true, scan_now: true }),
    },
  );
}

export function listWorkspaceRoots(workspaceId: string): Promise<WorkspaceRoot[]> {
  return request<WorkspaceRoot[]>(`/workspaces/${workspaceId}/roots`);
}

export function listFiles(workspaceId: string): Promise<IndexedFile[]> {
  return request<IndexedFile[]>(`/files?workspace_id=${encodeURIComponent(workspaceId)}`);
}

export function listConversations(workspaceId?: string): Promise<Conversation[]> {
  const suffix = workspaceId ? `?workspace_id=${encodeURIComponent(workspaceId)}` : "";
  return request<Conversation[]>(`/conversations${suffix}`);
}

export function createConversation(workspaceId?: string): Promise<Conversation> {
  return request<Conversation>("/conversations", {
    method: "POST",
    body: JSON.stringify({ workspace_id: workspaceId || null, title: "新对话" }),
  });
}

export function listMessages(conversationId: string): Promise<ChatMessage[]> {
  return request<ChatMessage[]>(`/conversations/${conversationId}/messages`);
}

export function getDesktopSettings(): Promise<DesktopSettings> {
  return request<DesktopSettings>("/settings");
}

export function updateDesktopSettings(values: Partial<DesktopSettings>): Promise<DesktopSettings> {
  return request<DesktopSettings>("/settings", {
    method: "PATCH",
    body: JSON.stringify(values),
  });
}

export type ChatStreamCallbacks = {
  onMeta?: (payload: { conversation_id: string; user_message_id: string; transport: string }) => void;
  onDelta?: (text: string) => void;
  onDone?: (payload: { assistant_message_id: string }) => void;
};

export async function streamChat(
  payload: { workspace_id?: string; conversation_id?: string; message: string },
  callbacks: ChatStreamCallbacks,
  signal?: AbortSignal,
): Promise<void> {
  const bootstrap = await resolveBootstrap();
  const headers = new Headers({ "Content-Type": "application/json", Accept: "text/event-stream" });
  if (bootstrap.session_token) headers.set("X-DeskAI-Token", bootstrap.session_token);

  const response = await fetch(`${bootstrap.endpoint}/chat/stream`, {
    method: "POST",
    headers,
    body: JSON.stringify({
      workspace_id: payload.workspace_id || null,
      conversation_id: payload.conversation_id || null,
      message: payload.message,
    }),
    signal,
  });
  if (!response.ok) {
    const body = await response.json().catch(() => null);
    throw new Error(body?.message ?? body?.detail ?? `HTTP ${response.status}`);
  }
  if (!response.body) throw new Error("Engine 未返回流式响应");

  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";

  const consumeBlock = (block: string) => {
    const lines = block.replaceAll("\r", "").split("\n");
    const event = lines.find((line) => line.startsWith("event:"))?.slice(6).trim() ?? "message";
    const dataText = lines
      .filter((line) => line.startsWith("data:"))
      .map((line) => line.slice(5).trimStart())
      .join("\n");
    if (!dataText) return;
    const data = JSON.parse(dataText) as Record<string, unknown>;
    if (event === "meta") callbacks.onMeta?.(data as { conversation_id: string; user_message_id: string; transport: string });
    if (event === "delta") callbacks.onDelta?.(String(data.text ?? ""));
    if (event === "done") callbacks.onDone?.(data as { assistant_message_id: string });
  };

  while (true) {
    const { done, value } = await reader.read();
    buffer += decoder.decode(value, { stream: !done });
    let boundary = buffer.indexOf("\n\n");
    while (boundary >= 0) {
      consumeBlock(buffer.slice(0, boundary));
      buffer = buffer.slice(boundary + 2);
      boundary = buffer.indexOf("\n\n");
    }
    if (done) break;
  }
  if (buffer.trim()) consumeBlock(buffer);
}
