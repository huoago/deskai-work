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
