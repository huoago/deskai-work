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
  current_version_id: string | null;
  parser_version: string | null;
  queue_status: string | null;
};

export type ScanSummary = {
  discovered: number;
  queued: number;
  unchanged: number;
  unsupported: number;
  skipped: number;
  deleted: number;
};

export type WatcherStatus = {
  running: boolean;
  watched_roots: number;
  workspace_watched_roots: number;
  workspace_watching: boolean;
  cycles: number;
  last_cycle_at: string | null;
  last_error: string | null;
  last_summary: ScanSummary;
};

export type IndexQueueSummary = {
  queued: number;
  processing: number;
  completed: number;
  failed: number;
  cancelled: number;
  total: number;
};

export type ParserStatus = {
  running: boolean;
  processed: number;
  failed: number;
  last_file_id: string | null;
  last_completed_at: string | null;
  last_error: string | null;
  parser_version: string;
};

export type KnowledgeStatus = {
  running: boolean;
  processed: number;
  failed: number;
  last_file_id: string | null;
  last_completed_at: string | null;
  last_error: string | null;
  embedding_provider: string;
  workspace_id: string | null;
  parsed_files: number;
  indexed_files: number;
  index_failed_files: number;
  active_chunks: number;
};

export type SearchHit = {
  chunk_id: string;
  file_id: string;
  filename: string;
  score: number;
  content: string;
  snippet: string;
  locator: Record<string, unknown>;
  citation_label: string;
  lexical_rank: number | null;
  vector_rank: number | null;
  embedding_provider: string;
};

export type ParsedPreview = {
  file_id: string;
  filename: string;
  sha256: string;
  parser: string;
  parser_version: string;
  file_type: string;
  title: string | null;
  metadata: Record<string, unknown>;
  text: string;
  text_truncated: boolean;
  units: Array<{
    kind: string;
    text: string;
    locator: Record<string, unknown>;
    metadata: Record<string, unknown>;
  }>;
  units_truncated: boolean;
};

export type Conversation = {
  id: string;
  workspace_id: string | null;
  title: string | null;
  created_at: string;
  updated_at: string;
};

export type MessageCitation = {
  source_index: number | null;
  chunk_id: string | null;
  file_id: string | null;
  label: string;
  locator: Record<string, unknown>;
};

export type ChatMessage = {
  id: string;
  conversation_id: string;
  role: "user" | "assistant" | "system";
  content: string;
  created_at: string;
  citations: MessageCitation[];
};

export type OpenAIProviderStatus = {
  configured: boolean;
  source: "environment" | "credential_manager" | null;
  credential_store_available: boolean;
  writable: boolean;
  error: string | null;
  model: string;
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
  return request<{ root: WorkspaceRoot; scan: ScanSummary | null }>(
    `/workspaces/${workspaceId}/roots`,
    {
      method: "POST",
      body: JSON.stringify({
        path,
        read_allowed: true,
        write_allowed: false,
        watch_enabled: true,
        scan_now: true,
      }),
    },
  );
}

export function listWorkspaceRoots(workspaceId: string): Promise<WorkspaceRoot[]> {
  return request<WorkspaceRoot[]>(`/workspaces/${workspaceId}/roots`);
}

export function updateWorkspaceRoot(
  workspaceId: string,
  rootId: string,
  changes: Partial<Pick<WorkspaceRoot, "read_allowed" | "write_allowed" | "watch_enabled">> & { scan_now?: boolean },
): Promise<{ root: WorkspaceRoot; scan: ScanSummary | null; revoked_files: number }> {
  return request(`/workspaces/${workspaceId}/roots/${rootId}`, {
    method: "PATCH",
    body: JSON.stringify(changes),
  });
}

export function revokeWorkspaceRoot(
  workspaceId: string,
  rootId: string,
): Promise<{ revoked: boolean; path: string; revoked_files: number }> {
  return request(`/workspaces/${workspaceId}/roots/${rootId}`, { method: "DELETE" });
}

export function scanWorkspace(workspaceId: string): Promise<{ roots: number; summary: ScanSummary }> {
  return request(`/workspaces/${workspaceId}/scan`, { method: "POST" });
}

export function getWorkspaceWatcherStatus(workspaceId: string): Promise<WatcherStatus> {
  return request<WatcherStatus>(`/workspaces/${workspaceId}/watcher`);
}

export function listFiles(workspaceId: string): Promise<IndexedFile[]> {
  return request<IndexedFile[]>(`/files?workspace_id=${encodeURIComponent(workspaceId)}`);
}

export function getIndexQueueSummary(workspaceId: string): Promise<IndexQueueSummary> {
  return request<IndexQueueSummary>(`/index-jobs/summary?workspace_id=${encodeURIComponent(workspaceId)}`);
}

export function getParserStatus(): Promise<ParserStatus> {
  return request<ParserStatus>("/parser/status");
}

export function processParserQueue(limit = 20): Promise<{ processed: number; status: ParserStatus }> {
  return request(`/parser/process?limit=${encodeURIComponent(String(limit))}`, { method: "POST" });
}

export function getParsedPreview(fileId: string): Promise<ParsedPreview> {
  return request<ParsedPreview>(`/files/${fileId}/parsed`);
}

export function getKnowledgeStatus(workspaceId?: string): Promise<KnowledgeStatus> {
  const suffix = workspaceId ? `?workspace_id=${encodeURIComponent(workspaceId)}` : "";
  return request<KnowledgeStatus>(`/knowledge/status${suffix}`);
}

export function processKnowledgeQueue(limit = 20): Promise<{ processed: number; status: KnowledgeStatus }> {
  return request(`/knowledge/process?limit=${encodeURIComponent(String(limit))}`, { method: "POST" });
}

export function searchKnowledge(
  workspaceId: string,
  query: string,
  limit = 8,
): Promise<{ workspace_id: string; query: string; count: number; results: SearchHit[] }> {
  return request("/search", {
    method: "POST",
    body: JSON.stringify({ workspace_id: workspaceId, query, limit }),
  });
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

export function getOpenAIProviderStatus(): Promise<OpenAIProviderStatus> {
  return request<OpenAIProviderStatus>("/providers/openai/status");
}

export function saveOpenAIApiKey(apiKey: string): Promise<OpenAIProviderStatus> {
  return request<OpenAIProviderStatus>("/providers/openai/api-key", {
    method: "PUT",
    body: JSON.stringify({ api_key: apiKey }),
  });
}

export function deleteOpenAIApiKey(): Promise<OpenAIProviderStatus & { deleted: boolean }> {
  return request("/providers/openai/api-key", { method: "DELETE" });
}

export function testOpenAIProvider(): Promise<{ ok: boolean; model: string }> {
  return request("/providers/openai/test", { method: "POST" });
}

export type ChatStreamCallbacks = {
  onMeta?: (payload: {
    conversation_id: string;
    user_message_id: string;
    transport: string;
    model?: string;
    privacy_mode?: string;
    source_count?: number;
  }) => void;
  onSources?: (sources: MessageCitation[]) => void;
  onDelta?: (text: string) => void;
  onDone?: (payload: {
    assistant_message_id: string;
    citations?: MessageCitation[];
    response_id?: string | null;
    model?: string;
    input_tokens?: number;
    output_tokens?: number;
  }) => void;
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
    if (event === "meta") {
      callbacks.onMeta?.(data as {
        conversation_id: string;
        user_message_id: string;
        transport: string;
        model?: string;
        privacy_mode?: string;
        source_count?: number;
      });
    }
    if (event === "sources") {
      const payload = data as { sources?: MessageCitation[] };
      callbacks.onSources?.(payload.sources ?? []);
    }
    if (event === "delta") callbacks.onDelta?.(String(data.text ?? ""));
    if (event === "done") {
      callbacks.onDone?.(
        data as {
          assistant_message_id: string;
          citations?: MessageCitation[];
          response_id?: string | null;
          model?: string;
          input_tokens?: number;
          output_tokens?: number;
        },
      );
    }
    if (event === "error") {
      throw new Error(String(data.message ?? "AI response failed"));
    }
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
