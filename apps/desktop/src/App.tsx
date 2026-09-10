import { useEffect, useMemo, useState } from "react";
import { open } from "@tauri-apps/plugin-dialog";
import {
  addWorkspaceRoot,
  checkEngine,
  createWorkspace,
  listFiles,
  listWorkspaceRoots,
  listWorkspaces,
  type EngineConnection,
  type IndexedFile,
  type Workspace,
  type WorkspaceRoot,
} from "./lib/engine";

type EngineState =
  | { kind: "checking" }
  | { kind: "online"; connection: EngineConnection }
  | { kind: "offline"; message: string };

const nav = ["对话", "工作区", "文件", "任务", "记忆", "活动"];

export default function App() {
  const [engine, setEngine] = useState<EngineState>({ kind: "checking" });
  const [workspaces, setWorkspaces] = useState<Workspace[]>([]);
  const [activeWorkspaceId, setActiveWorkspaceId] = useState<string>("");
  const [roots, setRoots] = useState<WorkspaceRoot[]>([]);
  const [files, setFiles] = useState<IndexedFile[]>([]);
  const [newWorkspaceName, setNewWorkspaceName] = useState("");
  const [busy, setBusy] = useState(false);
  const [notice, setNotice] = useState<string>("");

  useEffect(() => {
    const controller = new AbortController();
    const timer = window.setTimeout(() => controller.abort(), 5000);
    checkEngine(controller.signal)
      .then(async (connection) => {
        setEngine({ kind: "online", connection });
        const items = await listWorkspaces();
        setWorkspaces(items);
        if (items[0]) setActiveWorkspaceId(items[0].id);
      })
      .catch((error: unknown) =>
        setEngine({ kind: "offline", message: error instanceof Error ? error.message : "无法连接本地 AI Engine" }),
      )
      .finally(() => window.clearTimeout(timer));
    return () => {
      controller.abort();
      window.clearTimeout(timer);
    };
  }, []);

  useEffect(() => {
    if (!activeWorkspaceId) {
      setRoots([]);
      setFiles([]);
      return;
    }
    Promise.all([listWorkspaceRoots(activeWorkspaceId), listFiles(activeWorkspaceId)])
      .then(([nextRoots, nextFiles]) => {
        setRoots(nextRoots);
        setFiles(nextFiles);
      })
      .catch((error: unknown) => setNotice(error instanceof Error ? error.message : "加载工作区失败"));
  }, [activeWorkspaceId]);

  const activeWorkspace = workspaces.find((item) => item.id === activeWorkspaceId) ?? null;
  const counts = useMemo(() => {
    const result = { pending: 0, indexed: 0, unsupported: 0, deleted: 0 };
    for (const file of files) {
      if (file.status in result) result[file.status as keyof typeof result] += 1;
    }
    return result;
  }, [files]);

  async function onCreateWorkspace() {
    const name = newWorkspaceName.trim();
    if (!name) return;
    setBusy(true);
    setNotice("");
    try {
      const workspace = await createWorkspace(name);
      setWorkspaces((current) => [...current, workspace]);
      setActiveWorkspaceId(workspace.id);
      setNewWorkspaceName("");
      setNotice("工作区已创建。下一步可授权资料文件夹。");
    } catch (error) {
      setNotice(error instanceof Error ? error.message : "创建工作区失败");
    } finally {
      setBusy(false);
    }
  }

  async function onAddFolder() {
    if (!activeWorkspaceId) return;
    setBusy(true);
    setNotice("");
    try {
      const selected = await open({ directory: true, multiple: false, title: "选择 DeskAI 可读取的资料文件夹" });
      if (!selected || Array.isArray(selected)) return;
      const result = await addWorkspaceRoot(activeWorkspaceId, selected);
      setRoots(await listWorkspaceRoots(activeWorkspaceId));
      setFiles(await listFiles(activeWorkspaceId));
      const queued = result.scan?.queued ?? 0;
      setNotice(`目录已授权并完成首轮扫描，需要索引的文件：${queued} 个。`);
    } catch (error) {
      setNotice(error instanceof Error ? error.message : "授权文件夹失败");
    } finally {
      setBusy(false);
    }
  }

  const online = engine.kind === "online";

  return (
    <div className="app-shell">
      <aside className="sidebar">
        <div className="brand">
          <div className="brand-mark">D</div>
          <div><strong>DeskAI Work</strong><span>Windows V1</span></div>
        </div>
        <nav>
          {nav.map((item, index) => (
            <button className={index === 1 ? "nav-item active" : "nav-item"} key={item}>
              <span className="nav-dot" />{item}
            </button>
          ))}
        </nav>
        <button className="settings">设置</button>
      </aside>

      <main className="main">
        <header className="topbar">
          <div>
            <p className="eyebrow">本地优先 AI 工作系统</p>
            <h1>工作区与资料授权</h1>
          </div>
          <div className={`status-pill ${online ? "ok" : engine.kind === "checking" ? "pending" : "bad"}`}>
            <span />{engine.kind === "checking" ? "正在检查 Engine" : online ? "Engine 在线" : "Engine 离线"}
          </div>
        </header>

        {!workspaces.length ? (
          <section className="hero-card onboarding">
            <p className="eyebrow">第一步</p>
            <h2>创建第一个 Workspace</h2>
            <p className="muted">不同项目资料必须隔离检索。创建工作区后，再明确授权 DeskAI 可以读取的本地文件夹。</p>
            <div className="create-row">
              <input value={newWorkspaceName} onChange={(e) => setNewWorkspaceName(e.target.value)} placeholder="例如：利马管网项目" />
              <button className="primary" onClick={onCreateWorkspace} disabled={!online || busy || !newWorkspaceName.trim()}>创建工作区</button>
            </div>
          </section>
        ) : (
          <>
            <section className="workspace-bar">
              <div>
                <span className="eyebrow">当前 Workspace</span>
                <select value={activeWorkspaceId} onChange={(e) => setActiveWorkspaceId(e.target.value)}>
                  {workspaces.map((item) => <option key={item.id} value={item.id}>{item.name}</option>)}
                </select>
              </div>
              <button className="primary" onClick={onAddFolder} disabled={!online || busy}>+ 授权资料文件夹</button>
            </section>

            <section className="metric-grid top-metrics">
              <Metric label="授权目录" value={String(roots.length)} />
              <Metric label="发现文件" value={String(files.length)} />
              <Metric label="待索引" value={String(counts.pending)} />
              <Metric label="不支持/过大" value={String(counts.unsupported)} />
            </section>

            <section className="grid workspace-grid">
              <article className="panel">
                <div className="panel-head"><h3>授权目录</h3><span>{activeWorkspace?.name}</span></div>
                <div className="list-stack">
                  {roots.length ? roots.map((root) => (
                    <div className="file-row" key={root.id}>
                      <div><strong>{root.path}</strong><span>只读：{root.read_allowed ? "是" : "否"} · 自动监控：{root.watch_enabled ? "是" : "否"}</span></div>
                      <b>授权</b>
                    </div>
                  )) : <p className="muted">尚未授权资料目录。</p>}
                </div>
              </article>
              <article className="panel">
                <div className="panel-head"><h3>文件扫描</h3><span>真实 SQLite</span></div>
                <div className="list-stack files-list">
                  {files.slice(0, 8).map((file) => (
                    <div className="file-row" key={file.id}>
                      <div><strong>{file.filename}</strong><span>{formatBytes(file.size)} · {file.extension || "无扩展名"}</span></div>
                      <b className={`file-status ${file.status}`}>{statusLabel(file.status)}</b>
                    </div>
                  ))}
                  {!files.length && <p className="muted">授权目录后，Scanner 会计算 SHA256 并将支持的文件写入索引队列。</p>}
                </div>
              </article>
            </section>
          </>
        )}

        {notice && <div className="notice">{notice}</div>}
        {engine.kind === "offline" && <div className="error-box">{engine.message}</div>}
      </main>
    </div>
  );
}

function Metric({ label, value }: { label: string; value: string }) {
  return <div className="metric"><span>{label}</span><strong>{value}</strong></div>;
}

function statusLabel(status: string) {
  const labels: Record<string, string> = { pending: "待索引", indexed: "已索引", unsupported: "暂不支持", deleted: "已删除", failed: "失败" };
  return labels[status] ?? status;
}

function formatBytes(bytes: number) {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / 1024 / 1024).toFixed(1)} MB`;
}
