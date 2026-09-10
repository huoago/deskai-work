import { useEffect, useMemo, useState } from "react";
import { open } from "@tauri-apps/plugin-dialog";
import {
  addWorkspaceRoot,
  checkEngine,
  createWorkspace,
  getDesktopSettings,
  getIndexQueueSummary,
  getWorkspaceWatcherStatus,
  listConversations,
  listFiles,
  listMessages,
  listWorkspaceRoots,
  listWorkspaces,
  revokeWorkspaceRoot,
  scanWorkspace,
  streamChat,
  updateDesktopSettings,
  updateWorkspaceRoot,
  type ChatMessage,
  type Conversation,
  type DesktopSettings,
  type EngineConnection,
  type IndexedFile,
  type IndexQueueSummary,
  type WatcherStatus,
  type Workspace,
  type WorkspaceRoot,
} from "./lib/engine";

type EngineState =
  | { kind: "checking" }
  | { kind: "online"; connection: EngineConnection }
  | { kind: "offline"; message: string };

type Page = "chat" | "workspace" | "files" | "settings";

const nav: Array<{ id: Page | "tasks" | "memory" | "activity"; label: string; enabled: boolean; phase?: string }> = [
  { id: "chat", label: "对话", enabled: true },
  { id: "workspace", label: "工作区", enabled: true },
  { id: "files", label: "文件", enabled: true },
  { id: "tasks", label: "任务", enabled: false, phase: "Phase 7" },
  { id: "memory", label: "记忆", enabled: false, phase: "Phase 6" },
  { id: "activity", label: "活动", enabled: false, phase: "Phase 7" },
];

const defaultSettings: DesktopSettings = {
  privacy_mode: "hybrid",
  default_model: "gpt-5.6-sol",
  reasoning_level: "medium",
  auto_index: true,
};

const emptyQueue: IndexQueueSummary = {
  queued: 0,
  processing: 0,
  completed: 0,
  failed: 0,
  cancelled: 0,
  total: 0,
};

export default function App() {
  const [engine, setEngine] = useState<EngineState>({ kind: "checking" });
  const [page, setPage] = useState<Page>("workspace");
  const [workspaces, setWorkspaces] = useState<Workspace[]>([]);
  const [activeWorkspaceId, setActiveWorkspaceId] = useState("");
  const [roots, setRoots] = useState<WorkspaceRoot[]>([]);
  const [files, setFiles] = useState<IndexedFile[]>([]);
  const [watcher, setWatcher] = useState<WatcherStatus | null>(null);
  const [queue, setQueue] = useState<IndexQueueSummary>(emptyQueue);
  const [newWorkspaceName, setNewWorkspaceName] = useState("");
  const [busy, setBusy] = useState(false);
  const [notice, setNotice] = useState("");

  const [conversations, setConversations] = useState<Conversation[]>([]);
  const [activeConversationId, setActiveConversationId] = useState("");
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [chatInput, setChatInput] = useState("");
  const [pendingUser, setPendingUser] = useState("");
  const [streamingText, setStreamingText] = useState("");
  const [chatStreaming, setChatStreaming] = useState(false);

  const [desktopSettings, setDesktopSettings] = useState<DesktopSettings>(defaultSettings);
  const [settingsDirty, setSettingsDirty] = useState(false);

  useEffect(() => {
    const controller = new AbortController();
    const timer = window.setTimeout(() => controller.abort(), 5000);
    checkEngine(controller.signal)
      .then(async (connection) => {
        setEngine({ kind: "online", connection });
        const [items, settings] = await Promise.all([listWorkspaces(), getDesktopSettings()]);
        setWorkspaces(items);
        setDesktopSettings(settings);
        if (items[0]) {
          setActiveWorkspaceId(items[0].id);
          setPage("chat");
        }
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
      setConversations([]);
      setWatcher(null);
      setQueue(emptyQueue);
      setActiveConversationId("");
      setMessages([]);
      return;
    }
    refreshWorkspaceData(activeWorkspaceId).catch((error: unknown) =>
      setNotice(error instanceof Error ? error.message : "加载工作区失败"),
    );
  }, [activeWorkspaceId]);

  useEffect(() => {
    if (!activeConversationId) {
      setMessages([]);
      return;
    }
    listMessages(activeConversationId)
      .then(setMessages)
      .catch((error: unknown) => setNotice(error instanceof Error ? error.message : "加载会话失败"));
  }, [activeConversationId]);

  useEffect(() => {
    if (!activeWorkspaceId || engine.kind !== "online" || !["workspace", "files"].includes(page)) return;
    const timer = window.setInterval(() => {
      Promise.all([
        listFiles(activeWorkspaceId),
        getWorkspaceWatcherStatus(activeWorkspaceId),
        getIndexQueueSummary(activeWorkspaceId),
      ])
        .then(([nextFiles, nextWatcher, nextQueue]) => {
          setFiles(nextFiles);
          setWatcher(nextWatcher);
          setQueue(nextQueue);
        })
        .catch(() => undefined);
    }, 3000);
    return () => window.clearInterval(timer);
  }, [activeWorkspaceId, engine.kind, page]);

  const activeWorkspace = workspaces.find((item) => item.id === activeWorkspaceId) ?? null;
  const counts = useMemo(() => {
    const result = { pending: 0, indexed: 0, unsupported: 0, deleted: 0, failed: 0, revoked: 0 };
    for (const file of files) {
      if (file.status in result) result[file.status as keyof typeof result] += 1;
    }
    return result;
  }, [files]);

  async function refreshWorkspaceData(workspaceId = activeWorkspaceId) {
    if (!workspaceId) return;
    const [nextRoots, nextFiles, nextConversations, nextWatcher, nextQueue] = await Promise.all([
      listWorkspaceRoots(workspaceId),
      listFiles(workspaceId),
      listConversations(workspaceId),
      getWorkspaceWatcherStatus(workspaceId),
      getIndexQueueSummary(workspaceId),
    ]);
    setRoots(nextRoots);
    setFiles(nextFiles);
    setConversations(nextConversations);
    setWatcher(nextWatcher);
    setQueue(nextQueue);
    setActiveConversationId((current) => {
      if (current && nextConversations.some((item) => item.id === current)) return current;
      return nextConversations[0]?.id ?? "";
    });
  }

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
      setPage("workspace");
      setNotice("工作区已创建。现在可以授权资料文件夹。");
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
      await refreshWorkspaceData();
      const queued = result.scan?.queued ?? 0;
      setNotice(`目录已授权并完成 SHA256 扫描，进入索引队列：${queued} 个文件。`);
    } catch (error) {
      setNotice(error instanceof Error ? error.message : "授权文件夹失败");
    } finally {
      setBusy(false);
    }
  }

  async function onScanWorkspace() {
    if (!activeWorkspaceId) return;
    setBusy(true);
    setNotice("");
    try {
      const result = await scanWorkspace(activeWorkspaceId);
      await refreshWorkspaceData();
      setNotice(
        `扫描完成：发现 ${result.summary.discovered}，入队 ${result.summary.queued}，未变化 ${result.summary.unchanged}，删除 ${result.summary.deleted}。`,
      );
    } catch (error) {
      setNotice(error instanceof Error ? error.message : "扫描失败");
    } finally {
      setBusy(false);
    }
  }

  async function onToggleWatch(root: WorkspaceRoot) {
    if (!activeWorkspaceId) return;
    setBusy(true);
    try {
      await updateWorkspaceRoot(activeWorkspaceId, root.id, { watch_enabled: !root.watch_enabled });
      await refreshWorkspaceData();
      setNotice(root.watch_enabled ? "已暂停该目录自动监控。" : "已启用该目录自动监控。");
    } catch (error) {
      setNotice(error instanceof Error ? error.message : "更新监控状态失败");
    } finally {
      setBusy(false);
    }
  }

  async function onRevokeRoot(root: WorkspaceRoot) {
    if (!activeWorkspaceId) return;
    if (!window.confirm("撤销后 DeskAI 将不再读取此目录；不会删除电脑上的任何文件。继续吗？")) return;
    setBusy(true);
    try {
      const result = await revokeWorkspaceRoot(activeWorkspaceId, root.id);
      await refreshWorkspaceData();
      setNotice(`已撤销目录授权，${result.revoked_files} 个文件记录已停止索引访问。`);
    } catch (error) {
      setNotice(error instanceof Error ? error.message : "撤销授权失败");
    } finally {
      setBusy(false);
    }
  }

  function onNewConversation() {
    setActiveConversationId("");
    setMessages([]);
    setPendingUser("");
    setStreamingText("");
    setChatInput("");
  }

  async function onSendMessage() {
    const text = chatInput.trim();
    if (!text || chatStreaming || !activeWorkspaceId) return;
    setChatInput("");
    setPendingUser(text);
    setStreamingText("");
    setChatStreaming(true);
    setNotice("");
    let resolvedConversationId = activeConversationId;
    try {
      await streamChat(
        {
          workspace_id: activeWorkspaceId,
          conversation_id: activeConversationId || undefined,
          message: text,
        },
        {
          onMeta: ({ conversation_id }) => {
            resolvedConversationId = conversation_id;
            setActiveConversationId(conversation_id);
          },
          onDelta: (delta) => setStreamingText((current) => current + delta),
        },
      );
      if (resolvedConversationId) setMessages(await listMessages(resolvedConversationId));
      setConversations(await listConversations(activeWorkspaceId));
      setPendingUser("");
      setStreamingText("");
    } catch (error) {
      setNotice(error instanceof Error ? error.message : "发送失败");
    } finally {
      setChatStreaming(false);
    }
  }

  async function onSaveSettings() {
    setBusy(true);
    setNotice("");
    try {
      const saved = await updateDesktopSettings(desktopSettings);
      setDesktopSettings(saved);
      setSettingsDirty(false);
      if (activeWorkspaceId) await refreshWorkspaceData();
      setNotice("设置已保存到本地数据库。");
    } catch (error) {
      setNotice(error instanceof Error ? error.message : "保存设置失败");
    } finally {
      setBusy(false);
    }
  }

  const online = engine.kind === "online";
  const title = pageTitle(page);

  return (
    <div className="app-shell">
      <aside className="sidebar">
        <div className="brand">
          <div className="brand-mark">D</div>
          <div><strong>DeskAI Work</strong><span>Windows V1</span></div>
        </div>
        <nav>
          {nav.map((item) => (
            <button
              className={item.enabled && page === item.id ? "nav-item active" : "nav-item"}
              key={item.id}
              disabled={!item.enabled}
              title={!item.enabled ? `${item.phase} 开放` : undefined}
              onClick={() => item.enabled && setPage(item.id as Page)}
            >
              <span className="nav-dot" />
              <span>{item.label}</span>
              {!item.enabled && <small>{item.phase}</small>}
            </button>
          ))}
        </nav>
        <button className={page === "settings" ? "settings active-settings" : "settings"} onClick={() => setPage("settings")}>设置</button>
      </aside>

      <main className="main">
        <header className="topbar">
          <div>
            <p className="eyebrow">本地优先 AI 工作系统</p>
            <h1>{title}</h1>
          </div>
          <div className="topbar-actions">
            {!!workspaces.length && (
              <select className="workspace-select" value={activeWorkspaceId} onChange={(e) => setActiveWorkspaceId(e.target.value)}>
                {workspaces.map((item) => <option key={item.id} value={item.id}>{item.name}</option>)}
              </select>
            )}
            <div className={`status-pill ${online ? "ok" : engine.kind === "checking" ? "pending" : "bad"}`}>
              <span />{engine.kind === "checking" ? "检查 Engine" : online ? "Engine 在线" : "Engine 离线"}
            </div>
          </div>
        </header>

        {!workspaces.length && page !== "settings" ? (
          <Onboarding
            name={newWorkspaceName}
            setName={setNewWorkspaceName}
            onCreate={onCreateWorkspace}
            disabled={!online || busy}
          />
        ) : page === "chat" ? (
          <ChatPage
            workspace={activeWorkspace}
            conversations={conversations}
            activeConversationId={activeConversationId}
            setActiveConversationId={setActiveConversationId}
            messages={messages}
            pendingUser={pendingUser}
            streamingText={streamingText}
            input={chatInput}
            setInput={setChatInput}
            streaming={chatStreaming}
            onSend={onSendMessage}
            onNew={onNewConversation}
          />
        ) : page === "workspace" ? (
          <WorkspacePage
            workspace={activeWorkspace}
            roots={roots}
            files={files}
            counts={counts}
            watcher={watcher}
            queue={queue}
            onAddFolder={onAddFolder}
            onScan={onScanWorkspace}
            onToggleWatch={onToggleWatch}
            onRevoke={onRevokeRoot}
            disabled={!online || busy}
          />
        ) : page === "files" ? (
          <FilesPage
            files={files}
            counts={counts}
            queue={queue}
            watcher={watcher}
            onScan={onScanWorkspace}
            disabled={!online || busy}
          />
        ) : (
          <SettingsPage
            values={desktopSettings}
            onChange={(next) => {
              setDesktopSettings(next);
              setSettingsDirty(true);
            }}
            dirty={settingsDirty}
            busy={busy}
            onSave={onSaveSettings}
          />
        )}

        {notice && <div className="notice">{notice}</div>}
        {engine.kind === "offline" && <div className="error-box">{engine.message}</div>}
      </main>
    </div>
  );
}

function Onboarding({ name, setName, onCreate, disabled }: { name: string; setName: (value: string) => void; onCreate: () => void; disabled: boolean }) {
  return (
    <section className="hero-card onboarding">
      <p className="eyebrow">第一步</p>
      <h2>创建第一个 Workspace</h2>
      <p className="muted">每个项目独立管理资料、对话与后续记忆，避免不同工作内容串库。</p>
      <div className="create-row">
        <input value={name} onChange={(e) => setName(e.target.value)} placeholder="例如：利马管网项目" onKeyDown={(e) => e.key === "Enter" && onCreate()} />
        <button className="primary" onClick={onCreate} disabled={disabled || !name.trim()}>创建工作区</button>
      </div>
    </section>
  );
}

function ChatPage({ workspace, conversations, activeConversationId, setActiveConversationId, messages, pendingUser, streamingText, input, setInput, streaming, onSend, onNew }: {
  workspace: Workspace | null;
  conversations: Conversation[];
  activeConversationId: string;
  setActiveConversationId: (id: string) => void;
  messages: ChatMessage[];
  pendingUser: string;
  streamingText: string;
  input: string;
  setInput: (value: string) => void;
  streaming: boolean;
  onSend: () => void;
  onNew: () => void;
}) {
  return (
    <section className="chat-layout">
      <aside className="conversation-panel">
        <button className="primary full" onClick={onNew}>+ 新对话</button>
        <div className="conversation-list">
          {conversations.map((item) => (
            <button key={item.id} className={item.id === activeConversationId ? "conversation active" : "conversation"} onClick={() => setActiveConversationId(item.id)}>
              <strong>{item.title || "新对话"}</strong>
              <span>{formatDate(item.updated_at)}</span>
            </button>
          ))}
          {!conversations.length && <p className="muted small">还没有历史会话。</p>}
        </div>
      </aside>
      <div className="chat-panel">
        <div className="chat-context">
          <div><span className="eyebrow">当前工作区</span><strong>{workspace?.name ?? "未选择"}</strong></div>
          <span className="phase-chip">Phase 2 · 文件系统在线</span>
        </div>
        <div className="messages">
          {!messages.length && !pendingUser && (
            <div className="empty-chat">
              <div className="brand-mark large">D</div>
              <h2>开始一个工作对话</h2>
              <p>当前版本已完成本地会话和文件监控。文档内容解析从 Phase 3 开始，知识库检索与云模型仍按后续阶段接入。</p>
            </div>
          )}
          {messages.map((message) => <MessageBubble key={message.id} role={message.role} content={message.content} />)}
          {pendingUser && <MessageBubble role="user" content={pendingUser} pending />}
          {(streaming || streamingText) && <MessageBubble role="assistant" content={streamingText || "正在建立流式响应…"} pending />}
        </div>
        <div className="composer">
          <textarea
            value={input}
            onChange={(e) => setInput(e.target.value)}
            placeholder="输入任务或问题…"
            rows={3}
            disabled={streaming}
            onKeyDown={(e) => {
              if (e.key === "Enter" && !e.shiftKey) {
                e.preventDefault();
                onSend();
              }
            }}
          />
          <div className="composer-foot"><span>Enter 发送 · Shift+Enter 换行</span><button className="primary" onClick={onSend} disabled={!input.trim() || streaming}>{streaming ? "响应中…" : "发送"}</button></div>
        </div>
      </div>
    </section>
  );
}

function MessageBubble({ role, content, pending = false }: { role: string; content: string; pending?: boolean }) {
  return (
    <article className={`message ${role === "user" ? "user" : "assistant"} ${pending ? "pending-message" : ""}`}>
      <div className="message-avatar">{role === "user" ? "你" : "D"}</div>
      <div><strong>{role === "user" ? "你" : "DeskAI"}</strong><p>{content}</p></div>
    </article>
  );
}

function WorkspacePage({ workspace, roots, files, counts, watcher, queue, onAddFolder, onScan, onToggleWatch, onRevoke, disabled }: {
  workspace: Workspace | null;
  roots: WorkspaceRoot[];
  files: IndexedFile[];
  counts: Record<string, number>;
  watcher: WatcherStatus | null;
  queue: IndexQueueSummary;
  onAddFolder: () => void;
  onScan: () => void;
  onToggleWatch: (root: WorkspaceRoot) => void;
  onRevoke: (root: WorkspaceRoot) => void;
  disabled: boolean;
}) {
  return (
    <>
      <section className="workspace-bar">
        <div><span className="eyebrow">当前 Workspace</span><strong>{workspace?.name}</strong></div>
        <div className="button-row">
          <button className="secondary" onClick={onScan} disabled={disabled}>重新扫描</button>
          <button className="primary" onClick={onAddFolder} disabled={disabled}>+ 授权资料文件夹</button>
        </div>
      </section>
      <section className="metric-grid top-metrics">
        <Metric label="授权目录" value={String(roots.length)} />
        <Metric label="发现文件" value={String(files.length)} />
        <Metric label="索引队列" value={String(queue.queued)} />
        <Metric label="自动监控" value={watcher?.workspace_watching ? "运行中" : "未运行"} />
      </section>
      <section className="grid workspace-grid">
        <article className="panel">
          <div className="panel-head"><h3>授权目录</h3><span>Phase 2 安全边界</span></div>
          <div className="list-stack">
            {roots.length ? roots.map((root) => (
              <div className="root-row" key={root.id}>
                <div className="root-path"><strong>{root.path}</strong><span>只读：{root.read_allowed ? "是" : "否"} · Watcher：{root.watch_enabled ? "开启" : "暂停"}</span></div>
                <div className="row-actions">
                  <button className="text-button" onClick={() => onToggleWatch(root)} disabled={disabled}>{root.watch_enabled ? "暂停监控" : "开启监控"}</button>
                  <button className="text-button danger" onClick={() => onRevoke(root)} disabled={disabled}>撤销授权</button>
                </div>
              </div>
            )) : <p className="muted">尚未授权资料目录。</p>}
          </div>
        </article>
        <article className="panel">
          <div className="panel-head"><h3>文件系统状态</h3><span>{watcher?.cycles ?? 0} 次监控周期</span></div>
          <div className="security-list phase2-status">
            <p><b>✓</b> SHA256 Hash 已启用</p>
            <p><b>✓</b> Scanner 只读取授权目录</p>
            <p><b>{watcher?.workspace_watching ? "✓" : "·"}</b> Watcher {watcher?.workspace_watching ? "正在监控目录变化" : "当前没有启用的监控目录"}</p>
            <p><b>✓</b> Index Queue 当前待处理 {queue.queued} 个</p>
            {watcher?.last_error && <p className="watcher-error"><b>!</b> {watcher.last_error}</p>}
          </div>
          <div className="mini-summary">待索引 {counts.pending ?? 0} · 不支持 {counts.unsupported ?? 0} · 已删除 {counts.deleted ?? 0} · 已撤权 {counts.revoked ?? 0}</div>
        </article>
      </section>
    </>
  );
}

function FilesPage({ files, counts, queue, watcher, onScan, disabled }: {
  files: IndexedFile[];
  counts: Record<string, number>;
  queue: IndexQueueSummary;
  watcher: WatcherStatus | null;
  onScan: () => void;
  disabled: boolean;
}) {
  return (
    <section className="panel files-page">
      <div className="panel-head files-head">
        <div><h3>工作区文件</h3><p className="muted small">发现 {files.length} · 待索引 {counts.pending ?? 0} · 队列 {queue.queued} · Watcher {watcher?.workspace_watching ? "运行" : "暂停"}</p></div>
        <button className="secondary" onClick={onScan} disabled={disabled}>立即扫描</button>
      </div>
      <div className="file-table-head phase2-table"><span>文件</span><span>类型</span><span>大小</span><span>SHA256</span><span>队列/状态</span></div>
      <div className="list-stack">
        {files.map((file) => <FileRow key={file.id} file={file} table />)}
        {!files.length && <p className="muted">当前工作区暂无文件。先在“工作区”授权一个资料文件夹。</p>}
      </div>
    </section>
  );
}

function FileRow({ file, table = false }: { file: IndexedFile; table?: boolean }) {
  if (table) {
    return (
      <div className="file-table-row phase2-table">
        <strong title={file.path}>{file.filename}</strong>
        <span>{file.extension || "—"}</span>
        <span>{formatBytes(file.size)}</span>
        <code title={file.sha256 ?? ""}>{file.sha256 ? file.sha256.slice(0, 10) : "—"}</code>
        <b className={`file-status ${file.status}`}>{file.queue_status ? `${queueLabel(file.queue_status)} · ` : ""}{statusLabel(file.status)}</b>
      </div>
    );
  }
  return (
    <div className="file-row">
      <div><strong>{file.filename}</strong><span>{formatBytes(file.size)} · {file.extension || "无扩展名"}</span></div>
      <b className={`file-status ${file.status}`}>{statusLabel(file.status)}</b>
    </div>
  );
}

function SettingsPage({ values, onChange, dirty, busy, onSave }: { values: DesktopSettings; onChange: (values: DesktopSettings) => void; dirty: boolean; busy: boolean; onSave: () => void }) {
  return (
    <section className="settings-grid">
      <article className="panel settings-card">
        <div className="panel-head"><div><h3>AI 与隐私</h3><p className="muted small">这些设置已真实保存到本地 SQLite；模型 Provider 将在 Phase 5 接入。</p></div></div>
        <label>隐私模式<select value={values.privacy_mode} onChange={(e) => onChange({ ...values, privacy_mode: e.target.value as DesktopSettings["privacy_mode"] })}><option value="local">Local Only</option><option value="hybrid">Hybrid</option><option value="cloud">Cloud</option></select></label>
        <label>默认模型<input value={values.default_model} onChange={(e) => onChange({ ...values, default_model: e.target.value })} /></label>
        <label>推理级别<select value={values.reasoning_level} onChange={(e) => onChange({ ...values, reasoning_level: e.target.value as DesktopSettings["reasoning_level"] })}><option value="low">Low</option><option value="medium">Medium</option><option value="high">High</option></select></label>
        <label className="toggle-row"><input type="checkbox" checked={values.auto_index} onChange={(e) => onChange({ ...values, auto_index: e.target.checked })} /><span><strong>自动索引入队</strong><small>Watcher 发现目录变化时自动进入 Index Queue；关闭后仍可手动扫描入队</small></span></label>
        <button className="primary save-settings" disabled={!dirty || busy || !values.default_model.trim()} onClick={onSave}>{busy ? "保存中…" : dirty ? "保存设置" : "已保存"}</button>
      </article>
      <article className="panel settings-card">
        <div className="panel-head"><h3>安全状态</h3><span>Phase 2</span></div>
        <div className="security-list"><p><b>✓</b> Engine 仅监听 127.0.0.1</p><p><b>✓</b> Tauri 与 Engine 使用临时 Session Token</p><p><b>✓</b> Scanner/Watcher 仅访问授权目录</p><p><b>✓</b> 撤销授权不会删除本地文件</p><p><b>✓</b> Parser 尚未启用，文档内容不会在本阶段解析</p></div>
      </article>
    </section>
  );
}

function Metric({ label, value }: { label: string; value: string }) {
  return <div className="metric"><span>{label}</span><strong>{value}</strong></div>;
}

function pageTitle(page: Page) {
  if (page === "chat") return "工作对话";
  if (page === "workspace") return "工作区与资料授权";
  if (page === "files") return "文件与索引队列";
  return "设置";
}

function statusLabel(status: string) {
  const labels: Record<string, string> = {
    pending: "待索引",
    indexed: "已索引",
    unsupported: "暂不支持",
    deleted: "已删除",
    revoked: "已撤权",
    failed: "失败",
  };
  return labels[status] ?? status;
}

function queueLabel(status: string) {
  const labels: Record<string, string> = { queued: "已入队", processing: "处理中", completed: "完成", failed: "失败", cancelled: "已取消" };
  return labels[status] ?? status;
}

function formatBytes(bytes: number) {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / 1024 / 1024).toFixed(1)} MB`;
}

function formatDate(value: string) {
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? "" : date.toLocaleString(undefined, { month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit" });
}
