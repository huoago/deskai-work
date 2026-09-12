import { invoke, isTauri } from "@tauri-apps/api/core";

export type UpdateCheckResult = {
  current_version: string;
  latest_version: string | null;
  release_tag: string | null;
  update_available: boolean;
  trusted: boolean;
  release_page_url: string | null;
  published_at: string | null;
  installer_assets: string[];
  verification_issues: string[];
  manifest_sha256: string | null;
  checksums_sha256: string | null;
  source_commit: string | null;
  engine_version: string | null;
};

type EngineBootstrap = {
  endpoint: string;
  session_token: string;
};

async function resolveBootstrap(): Promise<EngineBootstrap> {
  if (isTauri()) {
    const bootstrap = await invoke<EngineBootstrap | null>("get_engine_bootstrap");
    if (!bootstrap) throw new Error("桌面后端尚未启动");
    return bootstrap;
  }
  return {
    endpoint: import.meta.env.VITE_DESKAI_ENGINE_URL ?? "http://127.0.0.1:8765",
    session_token: import.meta.env.VITE_DESKAI_ENGINE_TOKEN ?? "",
  };
}

export async function checkForUpdates(currentVersion: string): Promise<UpdateCheckResult> {
  const bootstrap = await resolveBootstrap();
  const response = await fetch(`${bootstrap.endpoint}/updates/check`, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      ...(bootstrap.session_token ? { "X-DeskAI-Token": bootstrap.session_token } : {}),
    },
    body: JSON.stringify({ current_version: currentVersion }),
  });
  if (!response.ok) {
    const body = await response.json().catch(() => null);
    throw new Error(body?.message ?? body?.detail ?? `HTTP ${response.status}`);
  }
  return (await response.json()) as UpdateCheckResult;
}
