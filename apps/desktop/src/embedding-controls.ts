import { invoke, isTauri } from "@tauri-apps/api/core";

type Bootstrap = { endpoint: string; session_token: string };
type Settings = {
  privacy_mode: "local" | "hybrid" | "cloud";
  embedding_provider: "local_hash" | "local_bge_m3" | "openai";
  embedding_model: "text-embedding-3-small" | "text-embedding-3-large";
};
type LocalModelStatus = {
  model: string;
  installed: boolean;
  download_required: boolean;
  model_sha256: string;
  tokenizer_sha256: string;
};
type Workspace = { id: string; name: string };

let bootstrapCache: Bootstrap | null = null;

async function bootstrap(): Promise<Bootstrap> {
  if (bootstrapCache) return bootstrapCache;
  if (isTauri()) {
    const value = await invoke<Bootstrap | null>("get_engine_bootstrap");
    if (!value) throw new Error("桌面 Engine 尚未启动");
    bootstrapCache = value;
    return value;
  }
  bootstrapCache = {
    endpoint: import.meta.env.VITE_DESKAI_ENGINE_URL ?? "http://127.0.0.1:8765",
    session_token: import.meta.env.VITE_DESKAI_ENGINE_TOKEN ?? "",
  };
  return bootstrapCache;
}

async function request<T>(path: string, init: RequestInit = {}): Promise<T> {
  const connection = await bootstrap();
  const headers = new Headers(init.headers);
  if (connection.session_token) headers.set("X-DeskAI-Token", connection.session_token);
  if (init.body) headers.set("Content-Type", "application/json");
  const response = await fetch(`${connection.endpoint}${path}`, { ...init, headers });
  if (!response.ok) {
    const body = await response.json().catch(() => null);
    throw new Error(body?.detail ?? body?.message ?? `HTTP ${response.status}`);
  }
  return response.json() as Promise<T>;
}

function el<K extends keyof HTMLElementTagNameMap>(tag: K, className?: string): HTMLElementTagNameMap[K] {
  const node = document.createElement(tag);
  if (className) node.className = className;
  return node;
}

function installStyles() {
  const style = el("style");
  style.textContent = `
    .embedding-launcher{position:fixed;right:22px;bottom:22px;z-index:10000;border:0;border-radius:999px;padding:11px 16px;background:#111827;color:#fff;font:600 13px system-ui;box-shadow:0 8px 28px #0003;cursor:pointer}
    .embedding-center{position:fixed;right:22px;bottom:72px;z-index:10000;width:min(420px,calc(100vw - 32px));max-height:78vh;overflow:auto;background:#fff;color:#111827;border:1px solid #d1d5db;border-radius:16px;box-shadow:0 18px 60px #0004;padding:18px;font:13px/1.45 system-ui;display:none}
    .embedding-center.open{display:block}.embedding-center h3{margin:0 0 4px;font-size:17px}.embedding-center p{margin:4px 0 12px;color:#6b7280}
    .embedding-center label{display:grid;gap:5px;margin:10px 0;font-weight:600}.embedding-center select,.embedding-center button{font:inherit}.embedding-center select{padding:8px;border:1px solid #d1d5db;border-radius:8px;background:#fff}
    .embedding-row{display:flex;gap:8px;flex-wrap:wrap;margin-top:10px}.embedding-row button{border:1px solid #d1d5db;background:#f9fafb;padding:8px 10px;border-radius:8px;cursor:pointer}.embedding-row button.primary{background:#111827;color:#fff;border-color:#111827}.embedding-row button:disabled{opacity:.55;cursor:not-allowed}
    .embedding-status{padding:9px 10px;border-radius:9px;background:#f3f4f6;margin:10px 0;word-break:break-word}.embedding-status.ok{background:#ecfdf5;color:#065f46}.embedding-status.bad{background:#fef2f2;color:#991b1b}.embedding-small{font-size:11px;color:#6b7280}.embedding-sep{height:1px;background:#e5e7eb;margin:14px 0}
  `;
  document.head.appendChild(style);
}

async function mount() {
  installStyles();
  const launcher = el("button", "embedding-launcher");
  launcher.textContent = "Embedding";
  launcher.type = "button";
  const panel = el("section", "embedding-center");
  panel.innerHTML = `<h3>Embedding Control Center</h3><p>Phase 27B · 本地语义模型不会自动下载或自动安装。</p>`;

  const providerLabel = el("label");
  providerLabel.textContent = "Embedding Provider";
  const provider = el("select");
  provider.innerHTML = `
    <option value="local_hash">Local Hash（兼容/测试 fallback）</option>
    <option value="local_bge_m3">Local BGE-M3（真正本地语义）</option>
    <option value="openai">OpenAI Embeddings</option>`;
  providerLabel.appendChild(provider);

  const modelLabel = el("label");
  modelLabel.textContent = "OpenAI Embedding Model";
  const model = el("select");
  model.innerHTML = `<option value="text-embedding-3-small">text-embedding-3-small</option><option value="text-embedding-3-large">text-embedding-3-large</option>`;
  modelLabel.appendChild(model);

  const modelStatus = el("div", "embedding-status");
  const modelActions = el("div", "embedding-row");
  const download = el("button", "primary");
  download.textContent = "下载并校验 BGE-M3";
  const remove = el("button");
  remove.textContent = "删除本地模型";
  modelActions.append(download, remove);

  const separator = el("div", "embedding-sep");
  const workspaceLabel = el("label");
  workspaceLabel.textContent = "重建向量的 Workspace";
  const workspace = el("select");
  workspaceLabel.appendChild(workspace);
  const rebuildRow = el("div", "embedding-row");
  const rebuild = el("button", "primary");
  rebuild.textContent = "按当前 Provider 重建向量";
  rebuildRow.appendChild(rebuild);
  const actionStatus = el("div", "embedding-status");
  actionStatus.textContent = "尚未执行操作。";

  panel.append(providerLabel, modelLabel, modelStatus, modelActions, separator, workspaceLabel, rebuildRow, actionStatus);
  document.body.append(panel, launcher);

  launcher.addEventListener("click", () => panel.classList.toggle("open"));

  async function refresh() {
    try {
      const [settings, local, workspaces] = await Promise.all([
        request<Settings>("/settings"),
        request<LocalModelStatus>("/knowledge/embeddings/local-model"),
        request<Workspace[]>("/workspaces"),
      ]);
      provider.value = settings.embedding_provider;
      model.value = settings.embedding_model;
      model.disabled = settings.embedding_provider !== "openai";
      modelStatus.className = `embedding-status ${local.installed ? "ok" : ""}`;
      modelStatus.innerHTML = local.installed
        ? `<strong>BGE-M3 已安装并通过 SHA-256 校验</strong><div class="embedding-small">${local.model}</div>`
        : `<strong>BGE-M3 尚未安装</strong><div class="embedding-small">只有点击“下载并校验”才会联网下载。</div>`;
      download.disabled = local.installed;
      remove.disabled = !local.installed;
      workspace.innerHTML = workspaces.map((item) => `<option value="${item.id}">${item.name}</option>`).join("");
      rebuild.disabled = workspaces.length === 0;
    } catch (error) {
      actionStatus.className = "embedding-status bad";
      actionStatus.textContent = error instanceof Error ? error.message : "无法读取 Embedding 状态";
    }
  }

  async function saveProvider() {
    model.disabled = provider.value !== "openai";
    actionStatus.className = "embedding-status";
    actionStatus.textContent = "保存 Provider…";
    try {
      await request<Settings>("/settings", {
        method: "PATCH",
        body: JSON.stringify({ embedding_provider: provider.value, embedding_model: model.value }),
      });
      actionStatus.className = "embedding-status ok";
      actionStatus.textContent = "Provider 已保存。切换 Provider 后请执行向量重建。";
    } catch (error) {
      actionStatus.className = "embedding-status bad";
      actionStatus.textContent = error instanceof Error ? error.message : "保存 Provider 失败";
    }
  }

  provider.addEventListener("change", () => void saveProvider());
  model.addEventListener("change", () => void saveProvider());

  download.addEventListener("click", async () => {
    if (!window.confirm("下载约 570 MB 的 BGE-M3 量化 ONNX 模型到 DeskAI 私有 models 目录？下载完成后会校验固定 SHA-256。")) return;
    download.disabled = true;
    actionStatus.className = "embedding-status";
    actionStatus.textContent = "正在下载并校验 BGE-M3，请保持 DeskAI 运行…";
    try {
      await request<LocalModelStatus>("/knowledge/embeddings/local-model/download", { method: "POST" });
      actionStatus.className = "embedding-status ok";
      actionStatus.textContent = "BGE-M3 下载与 SHA-256 校验完成。现在可选择 Local BGE-M3 并重建向量。";
      await refresh();
    } catch (error) {
      actionStatus.className = "embedding-status bad";
      actionStatus.textContent = error instanceof Error ? error.message : "BGE-M3 下载失败";
      download.disabled = false;
    }
  });

  remove.addEventListener("click", async () => {
    if (!window.confirm("删除 DeskAI 私有目录中的本地 BGE-M3 模型文件？现有向量索引不会被自动删除。")) return;
    try {
      await request<LocalModelStatus>("/knowledge/embeddings/local-model", { method: "DELETE" });
      actionStatus.className = "embedding-status ok";
      actionStatus.textContent = "本地 BGE-M3 模型已删除。";
      await refresh();
    } catch (error) {
      actionStatus.className = "embedding-status bad";
      actionStatus.textContent = error instanceof Error ? error.message : "删除本地模型失败";
    }
  });

  rebuild.addEventListener("click", async () => {
    if (!workspace.value) return;
    if (!window.confirm("按当前 Embedding Provider 为所选 Workspace 重建现有活动 chunks 的向量？源文件不会被重新解析或修改。")) return;
    rebuild.disabled = true;
    actionStatus.className = "embedding-status";
    actionStatus.textContent = "正在重建向量…";
    try {
      const result = await request<{ files_rebuilt: number; chunks_written: number; provider: { signature: string } }>(
        `/knowledge/embeddings/rebuild?workspace_id=${encodeURIComponent(workspace.value)}&file_limit=200`,
        { method: "POST" },
      );
      actionStatus.className = "embedding-status ok";
      actionStatus.textContent = `重建完成：${result.files_rebuilt} 个文件 / ${result.chunks_written} 个 chunks · ${result.provider.signature}`;
    } catch (error) {
      actionStatus.className = "embedding-status bad";
      actionStatus.textContent = error instanceof Error ? error.message : "向量重建失败";
    } finally {
      rebuild.disabled = false;
    }
  });

  await refresh();
}

window.addEventListener("DOMContentLoaded", () => void mount());
