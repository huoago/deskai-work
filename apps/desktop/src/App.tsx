import { useEffect, useMemo, useState } from "react";
import { open } from "@tauri-apps/plugin-dialog";
import {
  addWorkspaceRoot,
  checkEngine,
  confirmSourceFileEdit,
  confirmSourceFileEditBatch,
  confirmFileOrganization,
  confirmFileOrganizationBatch,
  confirmRecycleProposal,
  confirmRecycleBatch,
  confirmRecoveryReconciliation,
  exportRecoveryEvidencePackage,
  createMemory,
  createTask,
  draftWorkPlan,
  createWorkspace,
  deactivateMemory,
  deleteOpenAIApiKey,
  getDesktopSettings,
  getIndexQueueSummary,
  getAgentStatus,
  getWorkPlanWorkerStatus,
  getKnowledgeStatus,
  getMemoryStatus,
  getOpenAIProviderStatus,
  getParsedPreview,
  getParserStatus,
  getRecoverySnapshot,
  getTask,
  getWorkspaceWatcherStatus,
  listActivity,
  listRecovery,
  listConversations,
  listFiles,
  listMemories,
  listMessages,
  listTasks,
  listWorkspaceRoots,
  listWorkspaces,
  processAgentQueue,
  processKnowledgeQueue,
  processMemoryQueue,
  processParserQueue,
  proposeRecoveryReconciliation,
  rejectRecoveryReconciliation,
  rejectSourceFileEdit,
  rejectSourceFileEditBatch,
  rejectFileOrganization,
  rejectFileOrganizationBatch,
  rejectRecycleProposal,
  rejectRecycleBatch,
  retryMemoryQueue,
  retryTask,
  retryWorkPlan,
  resumeWorkPlan,
  rollbackSourceFileEdit,
  rollbackSourceFileEditBatch,
  rollbackFileOrganization,
  rollbackFileOrganizationBatch,
  startWorkPlan,
  restoreRecycledFile,
  restoreRecycleBatch,
  revokeWorkspaceRoot,
  saveOpenAIApiKey,
  scanWorkspace,
  searchKnowledge,
  streamChat,
  testOpenAIProvider,
  updateDesktopSettings,
  updateMemory,
  updateWorkspaceRoot,
  cancelWorkPlan,
  type ActivityRecord,
  type AgentStatus,
  type ChatMessage,
  type Conversation,
  type DesktopSettings,
  type EngineConnection,
  type IndexedFile,
  type IndexQueueSummary,
  type KnowledgeStatus,
  type MemoryRecord,
  type MemoryStatus,
  type MessageCitation,
  type OpenAIProviderStatus,
  type ParsedPreview,
  type ParserStatus,
  type RecoveryEntry,
  type RecoveryEvidencePackage,
  type RecoveryReconciliationProposal,
  type RecoverySnapshot,
  type SearchHit,
  type TaskDetail,
  type TaskRecord,
  type WatcherStatus,
  type Workspace,
  type WorkspaceRoot,
  type WorkPlanRecord,
  type WorkPlanWorkerStatus,
} from "./lib/engine";

type EngineState =
  | { kind: "checking" }
  | { kind: "online"; connection: EngineConnection }
  | { kind: "offline"; message: string };

type Page = "chat" | "workspace" | "files" | "search" | "memory" | "tasks" | "recovery" | "activity" | "settings";

const nav: Array<{ id: Page; label: string; enabled: boolean; phase?: string }> = [
  { id: "chat", label: "对话", enabled: true },
  { id: "workspace", label: "工作区", enabled: true },
  { id: "files", label: "文件", enabled: true },
  { id: "search", label: "资料检索", enabled: true },
  { id: "memory", label: "记忆", enabled: true },
  { id: "tasks", label: "任务", enabled: true },
  { id: "recovery", label: "恢复中心", enabled: true },
  { id: "activity", label: "活动", enabled: true },
];

const defaultSettings: DesktopSettings = {
  privacy_mode: "hybrid",
  default_model: "gpt-5.6-sol",
  reasoning_level: "medium",
  auto_index: true,
  memory_auto_learn: true,
  memory_min_confidence: 0.78,
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
  const [parserStatus, setParserStatus] = useState<ParserStatus | null>(null);
  const [knowledgeStatus, setKnowledgeStatus] = useState<KnowledgeStatus | null>(null);
  const [memories, setMemories] = useState<MemoryRecord[]>([]);
  const [memoryStatus, setMemoryStatus] = useState<MemoryStatus | null>(null);
  const [tasks, setTasks] = useState<TaskRecord[]>([]);
  const [agentStatus, setAgentStatus] = useState<AgentStatus | null>(null);
  const [workPlanWorkerStatus, setWorkPlanWorkerStatus] = useState<WorkPlanWorkerStatus | null>(null);
  const [activity, setActivity] = useState<ActivityRecord[]>([]);
  const [recoveryEntries, setRecoveryEntries] = useState<RecoveryEntry[]>([]);
  const [taskRequest, setTaskRequest] = useState("");
  const [taskDetail, setTaskDetail] = useState<TaskDetail | null>(null);
  const [searchQuery, setSearchQuery] = useState("");
  const [searchResults, setSearchResults] = useState<SearchHit[]>([]);
  const [searchLoading, setSearchLoading] = useState(false);
  const [preview, setPreview] = useState<ParsedPreview | null>(null);
  const [previewLoading, setPreviewLoading] = useState(false);
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
  const [providerStatus, setProviderStatus] = useState<OpenAIProviderStatus | null>(null);
  const [apiKeyDraft, setApiKeyDraft] = useState("");
  const [providerAction, setProviderAction] = useState<"save" | "delete" | "test" | null>(null);

  useEffect(() => {
    const controller = new AbortController();
    const timer = window.setTimeout(() => controller.abort(), 5000);
    checkEngine(controller.signal)
      .then(async (connection) => {
        setEngine({ kind: "online", connection });
        const [items, settings, openAIStatus] = await Promise.all([
          listWorkspaces(),
          getDesktopSettings(),
          getOpenAIProviderStatus(),
        ]);
        setWorkspaces(items);
        setDesktopSettings(settings);
        setProviderStatus(openAIStatus);
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
      setParserStatus(null);
      setKnowledgeStatus(null);
      setMemories([]);
      setMemoryStatus(null);
      setTasks([]);
      setAgentStatus(null);
      setWorkPlanWorkerStatus(null);
      setActivity([]);
      setRecoveryEntries([]);
      setTaskDetail(null);
      setSearchResults([]);
      setPreview(null);
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
    if (page !== "tasks" || engine.kind !== "online" || !taskDetail?.id) return;
    const taskId = taskDetail.id;
    const timer = window.setInterval(() => {
      getTask(taskId)
        .then((next) => setTaskDetail(next))
        .catch(() => undefined);
    }, 1500);
    return () => window.clearInterval(timer);
  }, [engine.kind, page, taskDetail?.id]);

  useEffect(() => {
    if (!activeWorkspaceId || engine.kind !== "online" || !["workspace", "files", "search", "memory", "tasks", "recovery", "activity"].includes(page)) return;
    const timer = window.setInterval(() => {
      Promise.all([
        listFiles(activeWorkspaceId),
        getWorkspaceWatcherStatus(activeWorkspaceId),
        getIndexQueueSummary(activeWorkspaceId),
        getParserStatus(),
        getKnowledgeStatus(activeWorkspaceId),
        listMemories(activeWorkspaceId, true, true),
        getMemoryStatus(activeWorkspaceId),
        listTasks(activeWorkspaceId),
        getAgentStatus(activeWorkspaceId),
        getWorkPlanWorkerStatus(),
        listActivity(activeWorkspaceId),
        listRecovery(activeWorkspaceId),
      ])
        .then(([nextFiles, nextWatcher, nextQueue, nextParserStatus, nextKnowledgeStatus, nextMemories, nextMemoryStatus, nextTasks, nextAgentStatus, nextWorkPlanWorkerStatus, nextActivity, nextRecovery]) => {
          setFiles(nextFiles);
          setWatcher(nextWatcher);
          setQueue(nextQueue);
          setParserStatus(nextParserStatus);
          setKnowledgeStatus(nextKnowledgeStatus);
          setMemories(nextMemories);
          setMemoryStatus(nextMemoryStatus);
          setTasks(nextTasks);
          setAgentStatus(nextAgentStatus);
          setWorkPlanWorkerStatus(nextWorkPlanWorkerStatus);
          setActivity(nextActivity);
          setRecoveryEntries(nextRecovery);
        })
        .catch(() => undefined);
    }, 3000);
    return () => window.clearInterval(timer);
  }, [activeWorkspaceId, engine.kind, page]);

  const activeWorkspace = workspaces.find((item) => item.id === activeWorkspaceId) ?? null;
  const counts = useMemo(() => {
    const result = { pending: 0, parsed: 0, indexed: 0, unsupported: 0, deleted: 0, recycled: 0, failed: 0, revoked: 0 };
    for (const file of files) {
      if (file.status in result) result[file.status as keyof typeof result] += 1;
    }
    return result;
  }, [files]);

  async function refreshWorkspaceData(workspaceId = activeWorkspaceId) {
    if (!workspaceId) return;
    const [nextRoots, nextFiles, nextConversations, nextWatcher, nextQueue, nextParserStatus, nextKnowledgeStatus, nextMemories, nextMemoryStatus, nextTasks, nextAgentStatus, nextWorkPlanWorkerStatus, nextActivity, nextRecovery] = await Promise.all([
      listWorkspaceRoots(workspaceId),
      listFiles(workspaceId),
      listConversations(workspaceId),
      getWorkspaceWatcherStatus(workspaceId),
      getIndexQueueSummary(workspaceId),
      getParserStatus(),
      getKnowledgeStatus(workspaceId),
      listMemories(workspaceId, true, true),
      getMemoryStatus(workspaceId),
      listTasks(workspaceId),
      getAgentStatus(workspaceId),
      getWorkPlanWorkerStatus(),
      listActivity(workspaceId),
      listRecovery(workspaceId),
    ]);
    setRoots(nextRoots);
    setFiles(nextFiles);
    setConversations(nextConversations);
    setWatcher(nextWatcher);
    setQueue(nextQueue);
    setParserStatus(nextParserStatus);
    setKnowledgeStatus(nextKnowledgeStatus);
    setMemories(nextMemories);
    setMemoryStatus(nextMemoryStatus);
    setTasks(nextTasks);
    setAgentStatus(nextAgentStatus);
    setWorkPlanWorkerStatus(nextWorkPlanWorkerStatus);
    setActivity(nextActivity);
    setRecoveryEntries(nextRecovery);
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

  async function onToggleWrite(root: WorkspaceRoot) {
    if (!activeWorkspaceId) return;
    if (!root.write_allowed) {
      const approved = window.confirm(
        "开启后，DeskAI 可为此目录内受支持文件生成源文件编辑提案。任何实际覆盖仍必须在任务详情中逐次人工确认，并会先自动备份原文件。确定开启写入授权吗？",
      );
      if (!approved) return;
    }
    setBusy(true);
    setNotice("");
    try {
      await updateWorkspaceRoot(activeWorkspaceId, root.id, { write_allowed: !root.write_allowed });
      await refreshWorkspaceData();
      setNotice(root.write_allowed ? "已关闭该目录源文件写入授权。" : "已开启该目录写入授权；实际编辑仍需逐次确认。");
    } catch (error) {
      setNotice(error instanceof Error ? error.message : "更新写入授权失败");
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

  async function onProcessParserQueue() {
    setBusy(true);
    setNotice("");
    try {
      const result = await processParserQueue(50);
      setParserStatus(result.status);
      await refreshWorkspaceData();
      setNotice(`解析处理完成：本次处理 ${result.processed} 个文件。`);
    } catch (error) {
      setNotice(error instanceof Error ? error.message : "解析队列处理失败");
    } finally {
      setBusy(false);
    }
  }

  async function onPreviewFile(file: IndexedFile) {
    if (!["parsed", "indexed"].includes(file.status)) return;
    setPreviewLoading(true);
    setNotice("");
    try {
      setPreview(await getParsedPreview(file.id));
    } catch (error) {
      setNotice(error instanceof Error ? error.message : "读取解析预览失败");
    } finally {
      setPreviewLoading(false);
    }
  }

  async function onProcessKnowledgeQueue() {
    setBusy(true);
    setNotice("");
    try {
      const result = await processKnowledgeQueue(100);
      if (activeWorkspaceId) {
        await refreshWorkspaceData();
        setKnowledgeStatus(await getKnowledgeStatus(activeWorkspaceId));
      }
      setNotice(`知识库更新完成：本次处理 ${result.processed} 个已解析文件。`);
    } catch (error) {
      setNotice(error instanceof Error ? error.message : "知识库更新失败");
    } finally {
      setBusy(false);
    }
  }

  async function onSearchKnowledge() {
    const query = searchQuery.trim();
    if (!activeWorkspaceId || !query || searchLoading) return;
    setSearchLoading(true);
    setNotice("");
    try {
      const response = await searchKnowledge(activeWorkspaceId, query, 12);
      setSearchResults(response.results);
    } catch (error) {
      setSearchResults([]);
      setNotice(error instanceof Error ? error.message : "资料检索失败");
    } finally {
      setSearchLoading(false);
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
      setPendingUser("");
      setStreamingText("");
      if (resolvedConversationId) {
        listMessages(resolvedConversationId).then(setMessages).catch(() => undefined);
      }
      setNotice(error instanceof Error ? error.message : "发送失败");
    } finally {
      setChatStreaming(false);
    }
  }

  async function onProcessMemoryQueue() {
    setBusy(true);
    setNotice("");
    try {
      const result = await processMemoryQueue(50);
      await refreshWorkspaceData();
      setNotice(`记忆学习处理完成：本次处理 ${result.processed} 个任务。`);
    } catch (error) {
      setNotice(error instanceof Error ? error.message : "记忆学习处理失败");
    } finally {
      setBusy(false);
    }
  }

  async function onRetryMemoryQueue() {
    setBusy(true);
    setNotice("");
    try {
      const result = await retryMemoryQueue();
      await refreshWorkspaceData();
      setNotice(`已重新入队 ${result.queued} 个失败/阻塞的记忆学习任务。`);
    } catch (error) {
      setNotice(error instanceof Error ? error.message : "记忆任务重试失败");
    } finally {
      setBusy(false);
    }
  }

  async function onCreateMemory(payload: {
    workspace_id: string | null;
    type: string;
    subject: string;
    predicate: string;
    value: string;
    importance: number;
  }) {
    setBusy(true);
    setNotice("");
    try {
      await createMemory(payload);
      await refreshWorkspaceData();
      setNotice("记忆已保存。");
    } catch (error) {
      setNotice(error instanceof Error ? error.message : "新增记忆失败");
      throw error;
    } finally {
      setBusy(false);
    }
  }

  async function onUpdateMemory(memoryId: string, value: string) {
    setBusy(true);
    setNotice("");
    try {
      await updateMemory(memoryId, { value, reason: "desktop manual edit" });
      await refreshWorkspaceData();
      setNotice("记忆已更新，旧值已进入版本历史。");
    } catch (error) {
      setNotice(error instanceof Error ? error.message : "更新记忆失败");
      throw error;
    } finally {
      setBusy(false);
    }
  }

  async function onDeactivateMemory(memoryId: string) {
    if (!window.confirm("停用后这条记忆将不再用于后续对话，但历史版本会保留。继续吗？")) return;
    setBusy(true);
    setNotice("");
    try {
      await deactivateMemory(memoryId);
      await refreshWorkspaceData();
      setNotice("记忆已停用。");
    } catch (error) {
      setNotice(error instanceof Error ? error.message : "停用记忆失败");
    } finally {
      setBusy(false);
    }
  }

  async function onReactivateMemory(memoryId: string) {
    setBusy(true);
    setNotice("");
    try {
      await updateMemory(memoryId, { status: "active", reason: "desktop reactivate" });
      await refreshWorkspaceData();
      setNotice("记忆已恢复为活动状态。");
    } catch (error) {
      setNotice(error instanceof Error ? error.message : "恢复记忆失败");
    } finally {
      setBusy(false);
    }
  }

  async function onCreateTask() {
    if (!activeWorkspaceId || !taskRequest.trim()) return;
    setBusy(true);
    setNotice("");
    try {
      const task = await createTask(activeWorkspaceId, taskRequest.trim());
      setTaskRequest("");
      setTasks((current) => [task, ...current]);
      setTaskDetail(await getTask(task.id));
      setNotice("Agent 任务已进入持久化队列。");
    } catch (error) {
      setNotice(error instanceof Error ? error.message : "创建 Agent 任务失败");
    } finally {
      setBusy(false);
    }
  }

  async function onCreatePlannedTask() {
    if (!activeWorkspaceId || !taskRequest.trim()) return;
    setBusy(true);
    setNotice("");
    try {
      const task = await createTask(activeWorkspaceId, taskRequest.trim(), undefined, "plan");
      await draftWorkPlan(task.id);
      setTaskRequest("");
      await refreshWorkspaceData();
      setTaskDetail(await getTask(task.id));
      setNotice("多步骤工作计划已生成。请先审阅步骤，再点击“开始执行计划”。");
    } catch (error) {
      setNotice(error instanceof Error ? error.message : "生成多步骤工作计划失败");
      await refreshWorkspaceData().catch(() => undefined);
    } finally {
      setBusy(false);
    }
  }
  async function onSelectTask(taskId: string) {
    try {
      setTaskDetail(await getTask(taskId));
    } catch (error) {
      setNotice(error instanceof Error ? error.message : "加载任务详情失败");
    }
  }

  async function onRetryTask(taskId: string) {
    setBusy(true);
    setNotice("");
    try {
      await retryTask(taskId);
      await refreshWorkspaceData();
      setTaskDetail(await getTask(taskId));
      setNotice("任务已重新进入 Agent 队列。");
    } catch (error) {
      setNotice(error instanceof Error ? error.message : "重试任务失败");
    } finally {
      setBusy(false);
    }
  }

  async function onProcessAgentQueue() {
    setBusy(true);
    setNotice("");
    try {
      const result = await processAgentQueue(10);
      await refreshWorkspaceData();
      if (taskDetail) setTaskDetail(await getTask(taskDetail.id));
      setNotice(`Agent 队列处理完成：本次处理 ${result.processed} 个任务。`);
    } catch (error) {
      setNotice(error instanceof Error ? error.message : "Agent 队列处理失败");
    } finally {
      setBusy(false);
    }
  }

  async function onConfirmSourceEdit(editId: string, taskId: string) {
    if (!window.confirm("确认将这个编辑提案写入原文件吗？DeskAI 会先备份原文件，并在写入前再次校验 SHA-256。")) return;
    setBusy(true);
    setNotice("");
    try {
      await confirmSourceFileEdit(editId);
      await refreshWorkspaceData();
      setTaskDetail(await getTask(taskId));
      setNotice("源文件编辑已应用，原文件备份已保留，并已重新进入索引流程。");
    } catch (error) {
      setNotice(error instanceof Error ? error.message : "应用源文件编辑失败");
    } finally {
      setBusy(false);
    }
  }

  async function onRejectSourceEdit(editId: string, taskId: string) {
    setBusy(true);
    setNotice("");
    try {
      await rejectSourceFileEdit(editId);
      setTaskDetail(await getTask(taskId));
      setNotice("已拒绝该源文件编辑提案，原文件未发生变化。");
    } catch (error) {
      setNotice(error instanceof Error ? error.message : "拒绝源文件编辑失败");
    } finally {
      setBusy(false);
    }
  }

  async function onRollbackSourceEdit(editId: string, taskId: string) {
    if (!window.confirm("确认回滚到修改前的备份版本吗？只有当当前文件仍保持 DeskAI 刚刚应用的版本时才允许自动回滚。")) return;
    setBusy(true);
    setNotice("");
    try {
      await rollbackSourceFileEdit(editId);
      await refreshWorkspaceData();
      setTaskDetail(await getTask(taskId));
      setNotice("源文件已回滚到修改前版本，并重新进入索引流程。");
    } catch (error) {
      setNotice(error instanceof Error ? error.message : "回滚源文件编辑失败");
    } finally {
      setBusy(false);
    }
  }

  async function onConfirmSourceEditBatch(batchId: string, taskId: string) {
    if (!window.confirm("确认一次性应用这一组文件修改吗？DeskAI 会先预检全部文件、创建全部备份，再按事务执行；任何一项失败都会自动恢复已写入文件。")) return;
    setBusy(true);
    setNotice("");
    try {
      await confirmSourceFileEditBatch(batchId);
      await refreshWorkspaceData();
      setTaskDetail(await getTask(taskId));
      setNotice("批量源文件事务已全部应用，所有原文件备份均已保留并重新进入索引流程。");
    } catch (error) {
      setNotice(error instanceof Error ? error.message : "应用批量源文件事务失败");
    } finally {
      setBusy(false);
    }
  }

  async function onRejectSourceEditBatch(batchId: string, taskId: string) {
    setBusy(true);
    setNotice("");
    try {
      await rejectSourceFileEditBatch(batchId);
      setTaskDetail(await getTask(taskId));
      setNotice("已拒绝整个批量编辑事务，所有源文件均未发生变化。");
    } catch (error) {
      setNotice(error instanceof Error ? error.message : "拒绝批量源文件事务失败");
    } finally {
      setBusy(false);
    }
  }

  async function onRollbackSourceEditBatch(batchId: string, taskId: string) {
    if (!window.confirm("确认整体回滚这一组文件吗？DeskAI 会先确认所有文件仍是刚刚应用的版本，再将整组文件恢复到修改前状态。")) return;
    setBusy(true);
    setNotice("");
    try {
      await rollbackSourceFileEditBatch(batchId);
      await refreshWorkspaceData();
      setTaskDetail(await getTask(taskId));
      setNotice("批量源文件事务已整体回滚，所有成员已恢复到修改前版本。");
    } catch (error) {
      setNotice(error instanceof Error ? error.message : "回滚批量源文件事务失败");
    } finally {
      setBusy(false);
    }
  }

  async function onConfirmFileOrganization(proposalId: string, taskId: string) {
    if (!window.confirm("确认执行这个文件路径变更吗？DeskAI 会再次校验文件 SHA-256、目标不存在、目录可写，并且不会覆盖现有文件。")) return;
    setBusy(true);
    setNotice("");
    try {
      await confirmFileOrganization(proposalId);
      await refreshWorkspaceData();
      setTaskDetail(await getTask(taskId));
      setNotice("文件路径变更已应用，File ID 与版本关系保持不变。");
    } catch (error) {
      setNotice(error instanceof Error ? error.message : "应用文件路径变更失败");
    } finally {
      setBusy(false);
    }
  }

  async function onRejectFileOrganization(proposalId: string, taskId: string) {
    setBusy(true);
    setNotice("");
    try {
      await rejectFileOrganization(proposalId);
      setTaskDetail(await getTask(taskId));
      setNotice("已拒绝文件整理提案，文件路径未发生变化。");
    } catch (error) {
      setNotice(error instanceof Error ? error.message : "拒绝文件整理提案失败");
    } finally {
      setBusy(false);
    }
  }

  async function onRollbackFileOrganization(proposalId: string, taskId: string) {
    if (!window.confirm("确认恢复到原文件路径吗？只有文件内容仍保持 DeskAI 执行时的 SHA-256，且原路径没有被占用时才允许自动恢复。")) return;
    setBusy(true);
    setNotice("");
    try {
      await rollbackFileOrganization(proposalId);
      await refreshWorkspaceData();
      setTaskDetail(await getTask(taskId));
      setNotice("文件已恢复到原路径，File ID 与版本关系保持不变。");
    } catch (error) {
      setNotice(error instanceof Error ? error.message : "恢复原文件路径失败");
    } finally {
      setBusy(false);
    }
  }

  async function onConfirmFileOrganizationBatch(batchId: string, taskId: string) {
    if (!window.confirm("确认一次性执行这一组文件路径变更吗？DeskAI 会先预检全部成员；任何一项失败都会恢复此前已经移动的文件。")) return;
    setBusy(true);
    setNotice("");
    try {
      await confirmFileOrganizationBatch(batchId);
      await refreshWorkspaceData();
      setTaskDetail(await getTask(taskId));
      setNotice("批量文件整理事务已全部应用，所有 File ID 与内容 SHA 保持不变。");
    } catch (error) {
      setNotice(error instanceof Error ? error.message : "应用批量文件整理事务失败");
    } finally {
      setBusy(false);
    }
  }

  async function onRejectFileOrganizationBatch(batchId: string, taskId: string) {
    setBusy(true);
    setNotice("");
    try {
      await rejectFileOrganizationBatch(batchId);
      setTaskDetail(await getTask(taskId));
      setNotice("已拒绝整个批量文件整理事务，所有文件路径均未变化。");
    } catch (error) {
      setNotice(error instanceof Error ? error.message : "拒绝批量文件整理事务失败");
    } finally {
      setBusy(false);
    }
  }

  async function onRollbackFileOrganizationBatch(batchId: string, taskId: string) {
    if (!window.confirm("确认整体恢复这一组文件的原路径吗？DeskAI 会先确认全部成员内容未变化且所有原路径仍为空闲。")) return;
    setBusy(true);
    setNotice("");
    try {
      await rollbackFileOrganizationBatch(batchId);
      await refreshWorkspaceData();
      setTaskDetail(await getTask(taskId));
      setNotice("批量文件整理事务已整体回滚，全部成员恢复到原路径。");
    } catch (error) {
      setNotice(error instanceof Error ? error.message : "回滚批量文件整理事务失败");
    } finally {
      setBusy(false);
    }
  }

  async function onConfirmRecycle(proposalId: string, taskId: string) {
    if (!window.confirm("确认将这个文件移入 DeskAI 回收隔离区吗？DeskAI 会先复制并校验 SHA-256，验证成功后才移除工作区原件；隔离副本会保留，可恢复。本阶段不支持永久删除。")) return;
    setBusy(true);
    setNotice("");
    try {
      await confirmRecycleProposal(proposalId);
      await refreshWorkspaceData();
      setTaskDetail(await getTask(taskId));
      setNotice("文件已安全移入 DeskAI 回收隔离区，隔离副本已保留，可随时恢复。");
    } catch (error) {
      setNotice(error instanceof Error ? error.message : "回收文件失败");
    } finally {
      setBusy(false);
    }
  }

  async function onRejectRecycle(proposalId: string, taskId: string) {
    setBusy(true);
    setNotice("");
    try {
      await rejectRecycleProposal(proposalId);
      setTaskDetail(await getTask(taskId));
      setNotice("已拒绝回收提案，原文件未发生变化。");
    } catch (error) {
      setNotice(error instanceof Error ? error.message : "拒绝回收提案失败");
    } finally {
      setBusy(false);
    }
  }

  async function onRestoreRecycledFile(proposalId: string, taskId: string) {
    if (!window.confirm("确认恢复到原路径吗？DeskAI 会校验隔离副本 SHA-256，并且只有原路径仍为空时才恢复；隔离副本仍会保留。")) return;
    setBusy(true);
    setNotice("");
    try {
      await restoreRecycledFile(proposalId);
      await refreshWorkspaceData();
      setTaskDetail(await getTask(taskId));
      setNotice("文件已从 DeskAI 回收隔离区恢复到原路径，隔离副本仍保留。");
    } catch (error) {
      setNotice(error instanceof Error ? error.message : "恢复回收文件失败");
    } finally {
      setBusy(false);
    }
  }

  async function onConfirmRecycleBatch(batchId: string, taskId: string) {
    if (!window.confirm("确认一次性回收这一组文件吗？DeskAI 会先为全部成员生成并校验隔离副本，并再次校验全部源文件；只有全部通过后才开始移除 Workspace 原件。任何中途失败都会恢复已移除成员。")) return;
    setBusy(true);
    setNotice("");
    try {
      await confirmRecycleBatch(batchId);
      await refreshWorkspaceData();
      setTaskDetail(await getTask(taskId));
      setNotice("批量回收事务已完整应用。所有隔离副本均保留，可整批恢复。");
    } catch (error) {
      setNotice(error instanceof Error ? error.message : "应用批量回收事务失败");
    } finally {
      setBusy(false);
    }
  }

  async function onRejectRecycleBatch(batchId: string, taskId: string) {
    setBusy(true);
    setNotice("");
    try {
      await rejectRecycleBatch(batchId);
      setTaskDetail(await getTask(taskId));
      setNotice("已拒绝整个批量回收事务，所有 Workspace 原件均未被移除。");
    } catch (error) {
      setNotice(error instanceof Error ? error.message : "拒绝批量回收事务失败");
    } finally {
      setBusy(false);
    }
  }

  async function onRestoreRecycleBatch(batchId: string, taskId: string) {
    if (!window.confirm("确认整批恢复到原路径吗？DeskAI 会先验证全部隔离副本、写权限和原路径空闲状态；任何成员失败都会把已经恢复的成员撤回到回收态。")) return;
    setBusy(true);
    setNotice("");
    try {
      await restoreRecycleBatch(batchId);
      await refreshWorkspaceData();
      setTaskDetail(await getTask(taskId));
      setNotice("批量回收事务已整体恢复，所有隔离副本仍保留。");
    } catch (error) {
      setNotice(error instanceof Error ? error.message : "整批恢复失败");
    } finally {
      setBusy(false);
    }
  }

  async function onRecoverEntry(entry: RecoveryEntry) {
    if (!entry.action || busy) return;
    const verb = entry.action === "restore" ? "恢复" : "回滚";
    const scope = entry.scope === "batch" ? `这一组 ${entry.item_count} 个文件` : entry.filenames[0] ?? "这个文件";
    if (!window.confirm(`确认${verb}${scope}吗？DeskAI 将继续使用原事务的 SHA、no-overwrite、备份/隔离副本和事务一致性检查；不会绕过任何安全门禁。`)) return;

    setBusy(true);
    setNotice("");
    try {
      if (entry.entity_type === "source_edit") {
        await rollbackSourceFileEdit(entry.id);
      } else if (entry.entity_type === "source_edit_batch") {
        await rollbackSourceFileEditBatch(entry.id);
      } else if (entry.entity_type === "file_organization") {
        await rollbackFileOrganization(entry.id);
      } else if (entry.entity_type === "file_organization_batch") {
        await rollbackFileOrganizationBatch(entry.id);
      } else if (entry.entity_type === "file_recycle") {
        await restoreRecycledFile(entry.id);
      } else if (entry.entity_type === "file_recycle_batch") {
        await restoreRecycleBatch(entry.id);
      } else {
        throw new Error("未知恢复事务类型");
      }
      await refreshWorkspaceData();
      if (entry.task_id && taskDetail?.id === entry.task_id) {
        setTaskDetail(await getTask(entry.task_id));
      }
      setNotice(`${verb}操作已通过原安全事务完成。`);
    } catch (error) {
      setNotice(error instanceof Error ? error.message : `${verb}操作失败`);
    } finally {
      setBusy(false);
    }
  }

  async function onCaptureRecoverySnapshot(
    entry: RecoveryEntry,
  ): Promise<RecoverySnapshot> {
    return getRecoverySnapshot(entry.entity_type, entry.id);
  }

  async function onProposeRecoveryReconciliation(
    entry: RecoveryEntry,
  ): Promise<RecoveryReconciliationProposal> {
    return proposeRecoveryReconciliation(entry.entity_type, entry.id);
  }

  async function onConfirmRecoveryReconciliation(
    proposal: RecoveryReconciliationProposal,
  ): Promise<RecoveryReconciliationProposal> {
    setBusy(true);
    setNotice("");
    try {
      const confirmed = await confirmRecoveryReconciliation(proposal.id);
      await refreshWorkspaceData();
      if (proposal.task_id && taskDetail?.id === proposal.task_id) {
        setTaskDetail(await getTask(proposal.task_id));
      }
      setNotice(
        `状态核对已确认：事务元数据已恢复为 ${confirmed.target_status}；未修改任何用户文件。现在可重新使用原 ${confirmed.historical_action === "rollback" ? "rollback" : "restore"} 安全流程。`,
      );
      return confirmed;
    } catch (error) {
      setNotice(error instanceof Error ? error.message : "状态核对确认失败");
      throw error;
    } finally {
      setBusy(false);
    }
  }

  async function onRejectRecoveryReconciliation(
    proposal: RecoveryReconciliationProposal,
  ): Promise<RecoveryReconciliationProposal> {
    setBusy(true);
    setNotice("");
    try {
      const rejected = await rejectRecoveryReconciliation(proposal.id);
      setNotice("状态核对提案已拒绝；原 recovery_required 冻结保持不变。");
      return rejected;
    } catch (error) {
      setNotice(error instanceof Error ? error.message : "拒绝状态核对提案失败");
      throw error;
    } finally {
      setBusy(false);
    }
  }

  async function onExportRecoveryEvidence(
    entry: RecoveryEntry,
  ): Promise<RecoveryEvidencePackage> {
    setBusy(true);
    setNotice("");
    try {
      const evidence = await exportRecoveryEvidencePackage(entry.entity_type, entry.id);
      setNotice(
        `恢复证据包已生成：${evidence.artifact.filename} · SHA-256 ${evidence.artifact.sha256.slice(0, 16)}…；未复制或修改 Workspace 文件内容。`,
      );
      return evidence;
    } catch (error) {
      setNotice(error instanceof Error ? error.message : "恢复证据包生成失败");
      throw error;
    } finally {
      setBusy(false);
    }
  }

  async function onStartWorkPlan(plan: WorkPlanRecord) {
    if (!window.confirm("开始执行这个多步骤计划吗？只读/分析/新建成果步骤会按依赖自动执行；任何现有文件变更只会生成原安全提案并暂停等待你的确认。")) return;
    setBusy(true);
    setNotice("");
    try {
      const result = await startWorkPlan(plan.id);
      await refreshWorkspaceData();
      setTaskDetail(await getTask(plan.task_id));
      setNotice(result.status === "awaiting_confirmation" ? "计划已执行到人工确认门禁。请在下方审核对应文件提案，确认后再点击“继续计划”。" : result.status === "completed" ? "多步骤工作计划已完成。" : `计划状态：${result.status}`);
    } catch (error) {
      setNotice(error instanceof Error ? error.message : "启动工作计划失败");
    } finally {
      setBusy(false);
    }
  }

  async function onResumeWorkPlan(plan: WorkPlanRecord) {
    setBusy(true);
    setNotice("");
    try {
      const result = await resumeWorkPlan(plan.id);
      await refreshWorkspaceData();
      setTaskDetail(await getTask(plan.task_id));
      setNotice(result.status === "completed" ? "人工确认已被计划状态机识别，后续步骤执行完成。" : result.status === "blocked" ? "计划未绕过被拒绝或异常的文件动作，已安全阻断。" : `计划已继续，当前状态：${result.status}`);
    } catch (error) {
      setNotice(error instanceof Error ? error.message : "继续工作计划失败");
    } finally {
      setBusy(false);
    }
  }

  async function onRetryWorkPlan(plan: WorkPlanRecord) {
    if (!window.confirm("重试失败/中断的计划步骤吗？DeskAI 不会自动重试已经创建过文件提案的步骤。")) return;
    setBusy(true);
    setNotice("");
    try {
      const result = await retryWorkPlan(plan.id);
      await refreshWorkspaceData();
      setTaskDetail(await getTask(plan.task_id));
      setNotice(result.status === "completed" ? "重试后计划已完成。" : `重试后状态：${result.status}`);
    } catch (error) {
      setNotice(error instanceof Error ? error.message : "重试工作计划失败");
    } finally {
      setBusy(false);
    }
  }

  async function onCancelWorkPlan(plan: WorkPlanRecord) {
    if (!window.confirm("取消这个工作计划吗？尚未执行的步骤会停止；已经生成的文件提案不会被自动拒绝或删除，仍由你单独处理。")) return;
    setBusy(true);
    setNotice("");
    try {
      await cancelWorkPlan(plan.id);
      await refreshWorkspaceData();
      setTaskDetail(await getTask(plan.task_id));
      setNotice("工作计划已取消；已有提案保持原状态，没有执行额外文件操作。");
    } catch (error) {
      setNotice(error instanceof Error ? error.message : "取消工作计划失败");
    } finally {
      setBusy(false);
    }
  }
  async function onSaveApiKey() {
    const key = apiKeyDraft.trim();
    if (!key || providerAction) return;
    setProviderAction("save");
    setNotice("");
    try {
      const status = await saveOpenAIApiKey(key);
      setProviderStatus(status);
      setApiKeyDraft("");
      setNotice("OpenAI API Key 已保存到 Windows 凭据存储，不会写入 DeskAI 数据库。");
    } catch (error) {
      setNotice(error instanceof Error ? error.message : "保存 API Key 失败");
    } finally {
      setProviderAction(null);
    }
  }

  async function onDeleteApiKey() {
    if (providerAction) return;
    if (!window.confirm("确定从 Windows 凭据存储中删除 OpenAI API Key 吗？")) return;
    setProviderAction("delete");
    setNotice("");
    try {
      const status = await deleteOpenAIApiKey();
      setProviderStatus(status);
      setApiKeyDraft("");
      setNotice(status.deleted ? "OpenAI API Key 已从系统凭据存储删除。" : "系统凭据存储中没有可删除的 OpenAI API Key。");
    } catch (error) {
      setNotice(error instanceof Error ? error.message : "删除 API Key 失败");
    } finally {
      setProviderAction(null);
    }
  }

  async function onTestProvider() {
    if (providerAction) return;
    setProviderAction("test");
    setNotice("");
    try {
      const result = await testOpenAIProvider();
      setProviderStatus(await getOpenAIProviderStatus());
      setNotice(`OpenAI 连接成功，当前模型：${result.model}。`);
    } catch (error) {
      setNotice(error instanceof Error ? error.message : "OpenAI 连接测试失败");
    } finally {
      setProviderAction(null);
    }
  }

  async function onSaveSettings() {
    setBusy(true);
    setNotice("");
    try {
      const saved = await updateDesktopSettings(desktopSettings);
      setDesktopSettings(saved);
      setSettingsDirty(false);
      setProviderStatus(await getOpenAIProviderStatus());
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
            providerConfigured={providerStatus?.configured ?? false}
          />
        ) : page === "workspace" ? (
          <WorkspacePage
            workspace={activeWorkspace}
            roots={roots}
            files={files}
            counts={counts}
            watcher={watcher}
            queue={queue}
            parserStatus={parserStatus}
            onAddFolder={onAddFolder}
            onScan={onScanWorkspace}
            onToggleWatch={onToggleWatch}
            onToggleWrite={onToggleWrite}
            onRevoke={onRevokeRoot}
            disabled={!online || busy}
          />
        ) : page === "files" ? (
          <FilesPage
            files={files}
            counts={counts}
            queue={queue}
            watcher={watcher}
            parserStatus={parserStatus}
            preview={preview}
            previewLoading={previewLoading}
            onScan={onScanWorkspace}
            onProcessQueue={onProcessParserQueue}
            onPreview={onPreviewFile}
            onClosePreview={() => setPreview(null)}
            disabled={!online || busy}
          />
        ) : page === "search" ? (
          <SearchPage
            workspace={activeWorkspace}
            query={searchQuery}
            setQuery={setSearchQuery}
            results={searchResults}
            loading={searchLoading}
            knowledge={knowledgeStatus}
            onSearch={onSearchKnowledge}
            onProcess={onProcessKnowledgeQueue}
            disabled={!online || busy}
          />
        ) : page === "memory" ? (
          <MemoryPage
            workspace={activeWorkspace}
            memories={memories}
            status={memoryStatus}
            disabled={!online || busy}
            onProcess={onProcessMemoryQueue}
            onRetry={onRetryMemoryQueue}
            onCreate={onCreateMemory}
            onUpdate={onUpdateMemory}
            onDeactivate={onDeactivateMemory}
            onReactivate={onReactivateMemory}
          />
        ) : page === "tasks" ? (
          <TasksPage
            workspace={activeWorkspace}
            tasks={tasks}
            status={agentStatus}
            detail={taskDetail}
            request={taskRequest}
            setRequest={setTaskRequest}
            disabled={!online || busy}
            onCreate={onCreateTask}
            onCreatePlan={onCreatePlannedTask}
            onSelect={onSelectTask}
            onRetry={onRetryTask}
            onProcess={onProcessAgentQueue}
            onConfirmEdit={onConfirmSourceEdit}
            onRejectEdit={onRejectSourceEdit}
            onRollbackEdit={onRollbackSourceEdit}
            onConfirmBatch={onConfirmSourceEditBatch}
            onRejectBatch={onRejectSourceEditBatch}
            onRollbackBatch={onRollbackSourceEditBatch}
            onConfirmOrganization={onConfirmFileOrganization}
            onRejectOrganization={onRejectFileOrganization}
            onRollbackOrganization={onRollbackFileOrganization}
            onConfirmOrganizationBatch={onConfirmFileOrganizationBatch}
            onRejectOrganizationBatch={onRejectFileOrganizationBatch}
            onRollbackOrganizationBatch={onRollbackFileOrganizationBatch}
            onConfirmRecycle={onConfirmRecycle}
            onRejectRecycle={onRejectRecycle}
            onRestoreRecycle={onRestoreRecycledFile}
            onConfirmRecycleBatch={onConfirmRecycleBatch}
            onRejectRecycleBatch={onRejectRecycleBatch}
            onRestoreRecycleBatch={onRestoreRecycleBatch}
            onStartWorkPlan={onStartWorkPlan}
            onResumeWorkPlan={onResumeWorkPlan}
            onRetryWorkPlan={onRetryWorkPlan}
            onCancelWorkPlan={onCancelWorkPlan}
          />
        ) : page === "recovery" ? (
          <RecoveryPage
            entries={recoveryEntries}
            disabled={!online || busy}
            onRecover={onRecoverEntry}
            onSnapshot={onCaptureRecoverySnapshot}
            onProposeReconciliation={onProposeRecoveryReconciliation}
            onConfirmReconciliation={onConfirmRecoveryReconciliation}
            onRejectReconciliation={onRejectRecoveryReconciliation}
            onExportEvidence={onExportRecoveryEvidence}
            onOpenTask={(taskId) => {
              setPage("tasks");
              void onSelectTask(taskId);
            }}
          />
        ) : page === "activity" ? (
          <ActivityPage activity={activity} />
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
            providerStatus={providerStatus}
            apiKeyDraft={apiKeyDraft}
            setApiKeyDraft={setApiKeyDraft}
            providerAction={providerAction}
            onSaveApiKey={onSaveApiKey}
            onDeleteApiKey={onDeleteApiKey}
            onTestProvider={onTestProvider}
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

function ChatPage({ workspace, conversations, activeConversationId, setActiveConversationId, messages, pendingUser, streamingText, input, setInput, streaming, onSend, onNew, providerConfigured }: {
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
  providerConfigured: boolean;
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
          <span className="phase-chip">Phase 22 · 持久化多步骤工作计划</span>
        </div>
        <div className="messages">
          {!messages.length && !pendingUser && (
            <div className="empty-chat">
              <div className="brand-mark large">D</div>
              <h2>开始一个工作对话</h2>
              <p>{providerConfigured ? "可以直接提问。DeskAI 会先在当前 Workspace 检索相关资料，再由 AI 生成带来源编号的回答。" : "请先到“设置”中配置 OpenAI API Key，然后即可对当前 Workspace 的本地资料进行 AI 问答。"}</p>
            </div>
          )}
          {messages.map((message) => <MessageBubble key={message.id} role={message.role} content={message.content} citations={message.citations} />)}
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

function MessageBubble({ role, content, citations = [], pending = false }: { role: string; content: string; citations?: MessageCitation[]; pending?: boolean }) {
  return (
    <article className={`message ${role === "user" ? "user" : "assistant"} ${pending ? "pending-message" : ""}`}>
      <div className="message-avatar">{role === "user" ? "你" : "D"}</div>
      <div className="message-body">
        <strong>{role === "user" ? "你" : "DeskAI"}</strong>
        <p>{content}</p>
        {!!citations.length && (
          <div className="message-citations">
            {citations.map((citation, index) => (
              <span key={`${citation.chunk_id ?? citation.file_id ?? citation.label}-${index}`}>
                [{citation.source_index ?? index + 1}] {citation.label}
              </span>
            ))}
          </div>
        )}
      </div>
    </article>
  );
}

function WorkspacePage({ workspace, roots, files, counts, watcher, queue, parserStatus, onAddFolder, onScan, onToggleWatch, onToggleWrite, onRevoke, disabled }: {
  workspace: Workspace | null;
  roots: WorkspaceRoot[];
  files: IndexedFile[];
  counts: Record<string, number>;
  watcher: WatcherStatus | null;
  queue: IndexQueueSummary;
  parserStatus: ParserStatus | null;
  onAddFolder: () => void;
  onScan: () => void;
  onToggleWatch: (root: WorkspaceRoot) => void;
  onToggleWrite: (root: WorkspaceRoot) => void;
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
        <Metric label="待解析队列" value={String(queue.queued)} />
        <Metric label="已解析" value={String(counts.parsed ?? 0)} />
      </section>
      <section className="grid workspace-grid">
        <article className="panel">
          <div className="panel-head"><h3>授权目录</h3><span>Phase 2 读取 + Phase 11–16 写入边界</span></div>
          <div className="list-stack">
            {roots.length ? roots.map((root) => (
              <div className="root-row" key={root.id}>
                <div className="root-path"><strong>{root.path}</strong><span>读取：{root.read_allowed ? "已授权" : "关闭"} · 写入：{root.write_allowed ? "已授权" : "关闭"} · Watcher：{root.watch_enabled ? "开启" : "暂停"}</span></div>
                <div className="row-actions">
                  <button className={root.write_allowed ? "text-button danger" : "text-button"} onClick={() => onToggleWrite(root)} disabled={disabled}>{root.write_allowed ? "关闭写入" : "允许写入"}</button>
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
            <p><b>✓</b> Index Queue 当前待解析 {queue.queued} 个</p>
            <p><b>{parserStatus?.running ? "✓" : "·"}</b> Parser Worker {parserStatus?.running ? "后台运行" : "当前未运行"} · 已处理 {parserStatus?.processed ?? 0}</p>
            {watcher?.last_error && <p className="watcher-error"><b>!</b> {watcher.last_error}</p>}
            {parserStatus?.last_error && <p className="watcher-error"><b>!</b> {parserStatus.last_error}</p>}
          </div>
          <div className="mini-summary">待解析 {counts.pending ?? 0} · 已解析 {counts.parsed ?? 0} · 不支持 {counts.unsupported ?? 0} · 失败 {counts.failed ?? 0}</div>
        </article>
      </section>
    </>
  );
}

function FilesPage({ files, counts, queue, watcher, parserStatus, preview, previewLoading, onScan, onProcessQueue, onPreview, onClosePreview, disabled }: {
  files: IndexedFile[];
  counts: Record<string, number>;
  queue: IndexQueueSummary;
  watcher: WatcherStatus | null;
  parserStatus: ParserStatus | null;
  preview: ParsedPreview | null;
  previewLoading: boolean;
  onScan: () => void;
  onProcessQueue: () => void;
  onPreview: (file: IndexedFile) => void;
  onClosePreview: () => void;
  disabled: boolean;
}) {
  return (
    <section className="phase3-files-layout">
      <article className="panel files-page">
        <div className="panel-head files-head">
          <div><h3>工作区文件</h3><p className="muted small">发现 {files.length} · 待解析 {counts.pending ?? 0} · 已解析 {counts.parsed ?? 0} · Parser {parserStatus?.running ? "运行" : "暂停"}</p></div>
          <div className="button-row">
            <button className="secondary" onClick={onScan} disabled={disabled}>立即扫描</button>
            <button className="primary" onClick={onProcessQueue} disabled={disabled || queue.queued === 0}>立即解析</button>
          </div>
        </div>
        <div className="file-table-head phase2-table"><span>文件</span><span>类型</span><span>大小</span><span>SHA256</span><span>队列/状态</span></div>
        <div className="list-stack">
          {files.map((file) => <FileRow key={file.id} file={file} table onPreview={onPreview} />)}
          {!files.length && <p className="muted">当前工作区暂无文件。先在“工作区”授权一个资料文件夹。</p>}
        </div>
        <div className="mini-summary">Watcher {watcher?.workspace_watching ? "运行" : "暂停"} · 队列处理中 {queue.processing} · 解析失败 {queue.failed}</div>
      </article>
      {(preview || previewLoading) && (
        <aside className="panel parsed-preview">
          <div className="panel-head">
            <div><h3>解析预览</h3><p className="muted small">{preview?.filename ?? "正在读取…"}</p></div>
            <button className="text-button" onClick={onClosePreview}>关闭</button>
          </div>
          {previewLoading ? <p className="muted">正在读取本地结构化解析缓存…</p> : preview ? (
            <>
              <div className="preview-meta">
                <span>{preview.parser}</span><span>{preview.parser_version}</span><span>{preview.file_type.toUpperCase()}</span>
              </div>
              <pre className="preview-text">{preview.text || "该文件没有可提取的文本内容。"}</pre>
              <details>
                <summary>结构化定位与元数据</summary>
                <pre className="preview-json">{JSON.stringify({ metadata: preview.metadata, units: preview.units }, null, 2)}</pre>
              </details>
              {(preview.text_truncated || preview.units_truncated) && <p className="muted small">预览已截断；完整解析结果保存在本地 cache/document。</p>}
            </>
          ) : null}
        </aside>
      )}
    </section>
  );
}

function FileRow({ file, table = false, onPreview }: { file: IndexedFile; table?: boolean; onPreview?: (file: IndexedFile) => void }) {
  if (table) {
    return (
      <div className="file-table-row phase2-table">
        <button className="file-name-button" title={file.path} onClick={() => ["parsed", "indexed"].includes(file.status) && onPreview?.(file)} disabled={!["parsed", "indexed"].includes(file.status)}>{file.filename}</button>
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

function SearchPage({ workspace, query, setQuery, results, loading, knowledge, onSearch, onProcess, disabled }: {
  workspace: Workspace | null;
  query: string;
  setQuery: (value: string) => void;
  results: SearchHit[];
  loading: boolean;
  knowledge: KnowledgeStatus | null;
  onSearch: () => void;
  onProcess: () => void;
  disabled: boolean;
}) {
  return (
    <section className="search-page">
      <div className="knowledge-status-grid">
        <Metric label="已索引文件" value={String(knowledge?.indexed_files ?? 0)} />
        <Metric label="活动 Chunk" value={String(knowledge?.active_chunks ?? 0)} />
        <Metric label="待建立知识库" value={String(knowledge?.parsed_files ?? 0)} />
        <Metric label="知识 Worker" value={knowledge?.running ? "运行中" : "未运行"} />
      </div>
      <article className="panel search-panel">
        <div className="panel-head">
          <div>
            <h3>本地资料检索</h3>
            <p className="muted small">当前工作区：{workspace?.name ?? "未选择"} · FTS5 + 本地向量融合 · 结果只来自当前有效文件版本</p>
          </div>
          <button className="secondary" onClick={onProcess} disabled={disabled || (knowledge?.parsed_files ?? 0) === 0}>立即更新知识库</button>
        </div>
        <div className="knowledge-search-row">
          <input
            value={query}
            onChange={(event) => setQuery(event.target.value)}
            placeholder="例如：324水表数量、RRP-04、DN1500、某份报告中的结论…"
            onKeyDown={(event) => {
              if (event.key === "Enter") onSearch();
            }}
          />
          <button className="primary" onClick={onSearch} disabled={disabled || loading || !query.trim()}>
            {loading ? "检索中…" : "检索"}
          </button>
        </div>
        <div className="search-results">
          {results.map((hit) => (
            <article className="search-hit" key={hit.chunk_id}>
              <div className="search-hit-head">
                <strong>{hit.citation_label}</strong>
                <span>融合得分 {hit.score.toFixed(4)}</span>
              </div>
              <p>{hit.snippet}</p>
              <div className="search-hit-meta">
                <span>FTS {hit.lexical_rank ?? "—"}</span>
                <span>Vector {hit.vector_rank ?? "—"}</span>
                <span>{hit.embedding_provider}</span>
              </div>
            </article>
          ))}
          {!loading && query.trim() && !results.length && <p className="muted">没有找到匹配的当前版本资料。</p>}
          {!query.trim() && <p className="muted">输入关键词、工程编号、数量或资料中的短语开始检索。</p>}
        </div>
      </article>
    </section>
  );
}

function MemoryPage({ workspace, memories, status, disabled, onProcess, onRetry, onCreate, onUpdate, onDeactivate, onReactivate }: {
  workspace: Workspace | null;
  memories: MemoryRecord[];
  status: MemoryStatus | null;
  disabled: boolean;
  onProcess: () => void;
  onRetry: () => void;
  onCreate: (payload: {
    workspace_id: string | null;
    type: string;
    subject: string;
    predicate: string;
    value: string;
    importance: number;
  }) => Promise<void>;
  onUpdate: (memoryId: string, value: string) => Promise<void>;
  onDeactivate: (memoryId: string) => void;
  onReactivate: (memoryId: string) => void;
}) {
  const [query, setQuery] = useState("");
  const [showInactive, setShowInactive] = useState(false);
  const [scope, setScope] = useState<"global" | "workspace">("workspace");
  const [type, setType] = useState("decision");
  const [subject, setSubject] = useState("");
  const [predicate, setPredicate] = useState("");
  const [value, setValue] = useState("");
  const [importance, setImportance] = useState(0.8);
  const [editingId, setEditingId] = useState("");
  const [editingValue, setEditingValue] = useState("");

  const visible = useMemo(() => {
    const needle = query.trim().toLocaleLowerCase();
    return memories.filter((memory) => {
      if (!showInactive && memory.status !== "active") return false;
      if (!needle) return true;
      const haystack = [
        memory.type,
        memory.subject,
        memory.predicate,
        renderMemoryValue(memory.value),
        memory.workspace_id ? "workspace" : "global",
      ].join(" ").toLocaleLowerCase();
      return haystack.includes(needle);
    });
  }, [memories, query, showInactive]);

  const activeCount = memories.filter((item) => item.status === "active").length;
  const globalCount = memories.filter((item) => item.status === "active" && item.workspace_id === null).length;
  const workspaceCount = memories.filter((item) => item.status === "active" && item.workspace_id !== null).length;

  async function submitMemory() {
    if (!subject.trim() || !predicate.trim() || !value.trim()) return;
    await onCreate({
      workspace_id: scope === "global" ? null : workspace?.id ?? null,
      type,
      subject: subject.trim(),
      predicate: predicate.trim(),
      value: value.trim(),
      importance,
    });
    setSubject("");
    setPredicate("");
    setValue("");
  }

  async function saveEdit(memory: MemoryRecord) {
    if (!editingValue.trim()) return;
    await onUpdate(memory.id, editingValue.trim());
    setEditingId("");
    setEditingValue("");
  }

  return (
    <section className="memory-page">
      <div className="knowledge-status-grid">
        <Metric label="活动记忆" value={String(activeCount)} />
        <Metric label="全局记忆" value={String(globalCount)} />
        <Metric label="当前项目" value={String(workspaceCount)} />
        <Metric label="待学习任务" value={String(status?.queued_jobs ?? 0)} />
        <Metric label="Memory Worker" value={status?.running ? "运行中" : "未运行"} />
      </div>

      <div className="memory-layout">
        <article className="panel memory-list-panel">
          <div className="panel-head">
            <div>
              <h3>长期记忆</h3>
              <p className="muted small">全局记忆跨 Workspace 使用；项目记忆只在当前 Workspace 生效。当前用户指令始终优先于旧记忆。</p>
            </div>
            <div className="button-row">
              <button className="secondary" onClick={onProcess} disabled={disabled}>立即学习</button>
              <button className="secondary" onClick={onRetry} disabled={disabled || ((status?.failed_jobs ?? 0) + (status?.blocked_jobs ?? 0) === 0)}>重试失败任务</button>
            </div>
          </div>

          <div className="memory-toolbar">
            <input value={query} onChange={(event) => setQuery(event.target.value)} placeholder="搜索主题、规则、数值…" />
            <label className="compact-check"><input type="checkbox" checked={showInactive} onChange={(event) => setShowInactive(event.target.checked)} />显示已停用</label>
          </div>

          {status?.last_error && <p className="provider-error">最近学习错误：{status.last_error}</p>}

          <div className="memory-list">
            {visible.map((memory) => (
              <article className={`memory-card ${memory.status === "inactive" ? "inactive" : ""}`} key={memory.id}>
                <div className="memory-card-head">
                  <div className="memory-tags">
                    <span>{memory.workspace_id ? "当前项目" : "全局"}</span>
                    <span>{memory.type}</span>
                    <span>{memory.source_type === "conversation" ? "对话学习" : "手工"}</span>
                  </div>
                  <span className={memory.status === "active" ? "memory-state active" : "memory-state"}>{memory.status === "active" ? "活动" : "已停用"}</span>
                </div>
                <h4>{memory.subject}</h4>
                <p className="memory-predicate">{memory.predicate}</p>
                {editingId === memory.id ? (
                  <div className="memory-edit">
                    <textarea value={editingValue} onChange={(event) => setEditingValue(event.target.value)} />
                    <div className="button-row">
                      <button className="primary" onClick={() => saveEdit(memory)} disabled={disabled || !editingValue.trim()}>保存修改</button>
                      <button className="text-button" onClick={() => { setEditingId(""); setEditingValue(""); }}>取消</button>
                    </div>
                  </div>
                ) : (
                  <pre className="memory-value">{renderMemoryValue(memory.value)}</pre>
                )}
                <div className="memory-card-foot">
                  <span>置信度 {Math.round(memory.confidence * 100)}%</span>
                  <span>重要度 {Math.round(memory.importance * 100)}%</span>
                  <span>{formatDate(memory.updated_at)}</span>
                  <div className="memory-actions">
                    {memory.status === "active" && (
                      <>
                        <button className="text-button" onClick={() => { setEditingId(memory.id); setEditingValue(renderMemoryValue(memory.value)); }} disabled={disabled}>修改</button>
                        <button className="text-button danger" onClick={() => onDeactivate(memory.id)} disabled={disabled}>停用</button>
                      </>
                    )}
                    {memory.status === "inactive" && <button className="text-button" onClick={() => onReactivate(memory.id)} disabled={disabled}>恢复</button>}
                  </div>
                </div>
              </article>
            ))}
            {!visible.length && <p className="muted">当前筛选条件下没有记忆。</p>}
          </div>
        </article>

        <aside className="panel memory-create-panel">
          <div className="panel-head">
            <div><h3>手工新增记忆</h3><p className="muted small">适合明确的长期规则、决定或项目状态。敏感个人信息和密钥会被后端拒绝。</p></div>
          </div>
          <label>作用范围<select value={scope} onChange={(event) => setScope(event.target.value as "global" | "workspace")}><option value="workspace">当前 Workspace</option><option value="global">全局</option></select></label>
          <label>类型<select value={type} onChange={(event) => setType(event.target.value)}><option value="preference">preference</option><option value="decision">decision</option><option value="constraint">constraint</option><option value="correction">correction</option><option value="project_state">project_state</option><option value="workflow">workflow</option><option value="person_role">person_role</option></select></label>
          <label>主题<input value={subject} onChange={(event) => setSubject(event.target.value)} placeholder="例如：技术报告" /></label>
          <label>属性<input value={predicate} onChange={(event) => setPredicate(event.target.value)} placeholder="例如：默认语言" /></label>
          <label>值<textarea value={value} onChange={(event) => setValue(event.target.value)} placeholder="例如：中文" /></label>
          <label>重要度<input type="range" min="0" max="1" step="0.05" value={importance} onChange={(event) => setImportance(Number(event.target.value))} /><span className="range-value">{Math.round(importance * 100)}%</span></label>
          <button className="primary full" onClick={submitMemory} disabled={disabled || !subject.trim() || !predicate.trim() || !value.trim()}>保存记忆</button>
          <p className="memory-policy-note">自动学习只从用户对话中提取长期信息；文件中的工程事实继续由 Knowledge Base 管理，不重复写入 Memory。</p>
        </aside>
      </div>
    </section>
  );
}

function renderMemoryValue(value: unknown): string {
  if (typeof value === "string") return value;
  try {
    return JSON.stringify(value, null, 2);
  } catch {
    return String(value);
  }
}

function TasksPage({ workspace, tasks, status, detail, request, setRequest, disabled, onCreate, onCreatePlan, onSelect, onRetry, onProcess, onConfirmEdit, onRejectEdit, onRollbackEdit, onConfirmBatch, onRejectBatch, onRollbackBatch, onConfirmOrganization, onRejectOrganization, onRollbackOrganization, onConfirmOrganizationBatch, onRejectOrganizationBatch, onRollbackOrganizationBatch, onConfirmRecycle, onRejectRecycle, onRestoreRecycle, onConfirmRecycleBatch, onRejectRecycleBatch, onRestoreRecycleBatch, onStartWorkPlan, onResumeWorkPlan, onRetryWorkPlan, onCancelWorkPlan }: {
  workspace: Workspace | null;
  tasks: TaskRecord[];
  status: AgentStatus | null;
  detail: TaskDetail | null;
  request: string;
  setRequest: (value: string) => void;
  disabled: boolean;
  onCreate: () => void;
  onCreatePlan: () => void;
  onSelect: (taskId: string) => void;
  onRetry: (taskId: string) => void;
  onProcess: () => void;
  onConfirmEdit: (editId: string, taskId: string) => void;
  onRejectEdit: (editId: string, taskId: string) => void;
  onRollbackEdit: (editId: string, taskId: string) => void;
  onConfirmBatch: (batchId: string, taskId: string) => void;
  onRejectBatch: (batchId: string, taskId: string) => void;
  onRollbackBatch: (batchId: string, taskId: string) => void;
  onConfirmOrganization: (proposalId: string, taskId: string) => void;
  onRejectOrganization: (proposalId: string, taskId: string) => void;
  onRollbackOrganization: (proposalId: string, taskId: string) => void;
  onConfirmOrganizationBatch: (batchId: string, taskId: string) => void;
  onRejectOrganizationBatch: (batchId: string, taskId: string) => void;
  onRollbackOrganizationBatch: (batchId: string, taskId: string) => void;
  onConfirmRecycle: (proposalId: string, taskId: string) => void;
  onRejectRecycle: (proposalId: string, taskId: string) => void;
  onRestoreRecycle: (proposalId: string, taskId: string) => void;
  onConfirmRecycleBatch: (batchId: string, taskId: string) => void;
  onRejectRecycleBatch: (batchId: string, taskId: string) => void;
  onRestoreRecycleBatch: (batchId: string, taskId: string) => void;
  onStartWorkPlan: (plan: WorkPlanRecord) => void;
  onResumeWorkPlan: (plan: WorkPlanRecord) => void;
  onRetryWorkPlan: (plan: WorkPlanRecord) => void;
  onCancelWorkPlan: (plan: WorkPlanRecord) => void;
}) {
  const pending = tasks.filter((item) => ["pending", "planning", "planned"].includes(item.status)).length;
  const running = tasks.filter((item) => item.status === "running").length;
  const waitingConfirmation = tasks.filter((item) => item.status === "awaiting_confirmation").length;
  const completed = tasks.filter((item) => item.status === "completed").length;
  const attention = tasks.filter((item) => ["failed", "blocked"].includes(item.status)).length;
  const edits = detail?.file_edits ?? [];
  const batches = detail?.file_edit_batches ?? [];
  const singleEdits = edits.filter((edit) => !edit.batch_id);
  const fileOperations = detail?.file_operations ?? [];
  const fileOperationBatches = detail?.file_operation_batches ?? [];
  const singleFileOperations = fileOperations.filter((operation) => !operation.batch_id);
  const recycleProposals = detail?.recycle_proposals ?? [];
  const recycleBatches = detail?.recycle_batches ?? [];
  const singleRecycleProposals = recycleProposals.filter((proposal) => !proposal.batch_id);

  return (
    <section className="tasks-page">
      <div className="knowledge-status-grid">
        <Metric label="待执行" value={String(pending)} />
        <Metric label="执行中" value={String(running)} />
        <Metric label="待人工确认" value={String(waitingConfirmation)} />
        <Metric label="已完成" value={String(completed)} />
        <Metric label="需处理" value={String(attention)} />
        <Metric label="Agent Worker" value={status?.running ? "运行中" : "未运行"} />
      </div>

      <article className="panel task-create-panel">
        <div className="panel-head">
          <div>
            <h3>创建工作任务</h3>
            <p className="muted small">当前 Workspace：{workspace?.name ?? "未选择"}。可继续使用即时 Agent，也可以先生成 Phase 22 多步骤计划。计划会持久化步骤、依赖、风险和执行结果；现有文件编辑/整理/回收仍只生成原 Phase 11–16 提案，并在人工确认门禁处暂停。</p>
          </div>
          <button className="secondary" onClick={onProcess} disabled={disabled || pending === 0}>立即处理队列</button>
        </div>
        <textarea
          className="task-request"
          value={request}
          onChange={(event) => setRequest(event.target.value)}
          placeholder="例如：分析当前项目的CSV/XLSX统计表，核对数量与分组汇总，并生成一份Word结论和Excel结果表。"
          onKeyDown={(event) => {
            if (event.key === "Enter" && (event.ctrlKey || event.metaKey)) onCreate();
          }}
        />
        <div className="task-submit-row">
          <span>Ctrl/⌘ + Enter：即时 Agent</span>
          <div className="button-row">
            <button className="secondary" onClick={onCreatePlan} disabled={disabled || !request.trim()}>生成多步骤计划</button>
            <button className="primary" onClick={onCreate} disabled={disabled || !request.trim()}>交给 Agent</button>
          </div>
        </div>
      </article>

      <div className="task-layout">
        <article className="panel task-list-panel">
          <div className="panel-head"><h3>任务队列</h3><span>{tasks.length} 个</span></div>
          <div className="task-list">
            {tasks.map((task) => (
              <button
                className={detail?.id === task.id ? "task-row selected" : "task-row"}
                key={task.id}
                onClick={() => onSelect(task.id)}
              >
                <div>
                  <strong>{task.title}</strong>
                  <span>{task.execution_mode === "plan" ? "多步骤计划" : "即时 Agent"} · {formatDate(task.created_at)}</span>
                </div>
                <div className="task-row-status">
                  <span className={`task-status ${task.status}`}>{taskStatusLabel(task.status)}</span>
                  <small>{Math.round(task.progress * 100)}%</small>
                </div>
              </button>
            ))}
            {!tasks.length && <p className="muted">还没有 Agent 任务。</p>}
          </div>
        </article>

        <article className="panel task-detail-panel">
          {!detail ? (
            <div className="empty-task-detail"><h3>选择一个任务</h3><p className="muted">可查看结果、模型运行和完整工具调用记录。</p></div>
          ) : (
            <>
              <div className="panel-head">
                <div><h3>{detail.title}</h3><p className="muted small">{detail.user_request}</p></div>
                <span className={`task-status ${detail.status}`}>{taskStatusLabel(detail.status)}</span>
              </div>
              <div className="task-progress-track"><span style={{ width: `${Math.round(detail.progress * 100)}%` }} /></div>

              {detail.result_text && <div className="task-result"><strong>Agent 结果</strong><p>{detail.result_text}</p></div>}
              {detail.error_message && <div className="provider-error">状态说明：{detail.error_message}</div>}

              {detail.work_plans.map((plan) => (
                <WorkPlanPanel
                  key={plan.id}
                  plan={plan}
                  disabled={disabled}
                  onStart={onStartWorkPlan}
                  onResume={onResumeWorkPlan}
                  onRetry={onRetryWorkPlan}
                  onCancel={onCancelWorkPlan}
                />
              ))}

              {recycleBatches.length > 0 && (
                <div className="source-edit-section recycle-section batch-transaction-section">
                  <div className="artifact-section-head">
                    <strong>批量回收事务</strong>
                    <span>{recycleBatches.length} 个事务</span>
                  </div>
                  <p className="artifact-policy-note">每个事务包含 2–10 个文件。DeskAI 会先为全部成员准备并 SHA 校验隔离副本，再次全量校验源文件后才开始移除任何 Workspace 原件；事务成员不能单独确认。</p>
                  <div className="source-edit-list">
                    {recycleBatches.map((batch) => (
                      <article className="source-edit-card recycle-card batch-transaction-card" key={batch.id}>
                        <div className="source-edit-head">
                          <div>
                            <strong>{batch.summary}</strong>
                            <span>{batch.item_count} 个文件 · All-or-nothing · {recycleBatchStatusLabel(batch.status)}</span>
                          </div>
                          <span className={`task-status ${batch.status === "pending" ? "blocked" : batch.status === "recycled" || batch.status === "restored" ? "completed" : "failed"}`}>
                            {recycleBatchStatusLabel(batch.status)}
                          </span>
                        </div>
                        <div className="batch-member-list">
                          {batch.items.map((item) => (
                            <div className="batch-member" key={item.id}>
                              <div>
                                <strong>{item.filename}</strong>
                                <span>{item.summary}</span>
                              </div>
                              <div className="organization-path-change">
                                <code>{item.original_path}</code>
                                <span>→</span>
                                <code>DeskAI 私有回收隔离区</code>
                              </div>
                              <div className="source-edit-hash">
                                <small>SHA-256 {item.original_sha256.slice(0, 16)}…</small>
                                <small>{formatBytes(item.original_size)}</small>
                              </div>
                            </div>
                          ))}
                        </div>
                        {batch.error_message && <div className="provider-error">{batch.error_message}</div>}
                        {batch.status === "pending" && (
                          <div className="button-row">
                            <button className="primary danger" disabled={disabled} onClick={() => onConfirmRecycleBatch(batch.id, detail.id)}>确认整批回收</button>
                            <button className="secondary" disabled={disabled} onClick={() => onRejectRecycleBatch(batch.id, detail.id)}>拒绝整批</button>
                          </div>
                        )}
                        {batch.status === "recycled" && (
                          <div className="button-row">
                            <button className="secondary" disabled={disabled} onClick={() => onRestoreRecycleBatch(batch.id, detail.id)}>整批恢复原路径</button>
                            <span className="muted small">永久删除不可用；全部隔离副本持续保留。</span>
                          </div>
                        )}
                        {batch.status === "restored" && <p className="muted small">整批文件已恢复到原路径；隔离副本仍保留。</p>}
                        {batch.status === "rejected" && <p className="muted small">整批提案已拒绝，所有 Workspace 原件从未被移除。</p>}
                        {batch.status === "recovery_required" && (
                          <div className="provider-error">批量回收状态无法安全自动恢复。DeskAI 已停止继续操作，需要人工核对全部原路径与隔离副本。</div>
                        )}
                      </article>
                    ))}
                  </div>
                </div>
              )}

              {singleRecycleProposals.length > 0 && (
                <div className="source-edit-section recycle-section">
                  <div className="artifact-section-head">
                    <strong>回收站提案</strong>
                    <span>{singleRecycleProposals.length} 个</span>
                  </div>
                  <p className="artifact-policy-note">Phase 15 不执行永久删除。确认后 DeskAI 会先创建并校验私有隔离副本，再移除 Workspace 原件；隔离副本始终保留并支持恢复。</p>
                  <div className="source-edit-list">
                    {singleRecycleProposals.map((proposal) => (
                      <article className="source-edit-card recycle-card" key={proposal.id}>
                        <div className="source-edit-head">
                          <div>
                            <strong>{proposal.filename}</strong>
                            <span>{recycleStatusLabel(proposal.status)}</span>
                          </div>
                          <span className={`task-status ${proposal.status === "pending" ? "blocked" : proposal.status === "recycled" || proposal.status === "restored" ? "completed" : "failed"}`}>
                            {recycleStatusLabel(proposal.status)}
                          </span>
                        </div>
                        <p>{proposal.summary}</p>
                        <div className="organization-path-change">
                          <code>{proposal.original_path}</code>
                          <span>→</span>
                          <code>DeskAI 私有回收隔离区</code>
                        </div>
                        <div className="source-edit-hash">
                          <small>SHA-256 {proposal.original_sha256.slice(0, 16)}…</small>
                          <small>{formatBytes(proposal.original_size)}</small>
                        </div>
                        {proposal.error_message && <div className="provider-error">{proposal.error_message}</div>}
                        {proposal.status === "pending" && (
                          <div className="button-row">
                            <button className="primary danger" disabled={disabled} onClick={() => onConfirmRecycle(proposal.id, detail.id)}>确认移入回收站</button>
                            <button className="secondary" disabled={disabled} onClick={() => onRejectRecycle(proposal.id, detail.id)}>拒绝提案</button>
                          </div>
                        )}
                        {proposal.status === "recycled" && (
                          <div className="button-row">
                            <button className="secondary" disabled={disabled} onClick={() => onRestoreRecycle(proposal.id, detail.id)}>恢复到原路径</button>
                            <span className="muted small">永久删除不可用；隔离副本持续保留。</span>
                          </div>
                        )}
                        {proposal.status === "restored" && <p className="muted small">文件已恢复到原路径；隔离副本仍保留。</p>}
                        {proposal.status === "rejected" && <p className="muted small">提案已拒绝，原文件从未被移除。</p>}
                        {proposal.status === "recovery_required" && (
                          <div className="provider-error">回收状态无法安全自动恢复。DeskAI 已停止继续操作，需要人工核对原路径与隔离副本。</div>
                        )}
                      </article>
                    ))}
                  </div>
                </div>
              )}

              {fileOperationBatches.length > 0 && (
                <div className="source-edit-section batch-transaction-section">
                  <div className="artifact-section-head">
                    <strong>批量文件整理事务</strong>
                    <span>{fileOperationBatches.length} 个事务</span>
                  </div>
                  <p className="artifact-policy-note">每个事务包含 2–10 个独立重命名/移动操作；全部目标必须唯一且为空。事务成员不能单独确认，任何成员失败都会恢复此前已经移动的文件。</p>
                  <div className="source-edit-list">
                    {fileOperationBatches.map((batch) => (
                      <article className="source-edit-card batch-transaction-card" key={batch.id}>
                        <div className="source-edit-head">
                          <div>
                            <strong>{batch.summary}</strong>
                            <span>{batch.operation_count} 个路径操作 · All-or-nothing · {fileOrganizationBatchStatusLabel(batch.status)}</span>
                          </div>
                          <span className={`task-status ${batch.status === "pending" ? "blocked" : batch.status === "applied" || batch.status === "rolled_back" ? "completed" : "failed"}`}>
                            {fileOrganizationBatchStatusLabel(batch.status)}
                          </span>
                        </div>
                        <div className="batch-member-list">
                          {batch.operations.map((operation) => (
                            <div className="batch-member" key={operation.id}>
                              <div>
                                <strong>{operation.operation === "rename" ? "重命名" : "移动"} · {operation.filename}</strong>
                                <span>{operation.summary}</span>
                              </div>
                              <div className="organization-path-change">
                                <code>{operation.original_path}</code>
                                <span>→</span>
                                <code>{operation.target_path}</code>
                              </div>
                              <div className="source-edit-hash">
                                <small>SHA-256 {operation.original_sha256.slice(0, 16)}…</small>
                                <small>File ID {operation.file_id.slice(0, 8)}…</small>
                              </div>
                            </div>
                          ))}
                        </div>
                        {batch.error_message && <div className="provider-error">{batch.error_message}</div>}
                        {batch.status === "pending" && (
                          <div className="button-row">
                            <button className="primary" disabled={disabled} onClick={() => onConfirmOrganizationBatch(batch.id, detail.id)}>确认整批路径变更</button>
                            <button className="secondary" disabled={disabled} onClick={() => onRejectOrganizationBatch(batch.id, detail.id)}>拒绝整批</button>
                          </div>
                        )}
                        {batch.status === "applied" && (
                          <div className="button-row">
                            <button className="secondary" disabled={disabled} onClick={() => onRollbackOrganizationBatch(batch.id, detail.id)}>整体恢复原路径</button>
                            <span className="muted small">所有成员 File ID 与内容 SHA 保持不变。</span>
                          </div>
                        )}
                        {batch.status === "recovery_required" && (
                          <div className="provider-error">批量路径状态无法安全自动恢复。DeskAI 已停止继续操作，需要人工核对全部成员。</div>
                        )}
                        {batch.status === "rolled_back" && <p className="muted small">整批文件已恢复到原路径。</p>}
                        {batch.status === "rejected" && <p className="muted small">整批提案已拒绝，所有文件路径从未改变。</p>}
                      </article>
                    ))}
                  </div>
                </div>
              )}

              {singleFileOperations.length > 0 && (
                <div className="source-edit-section file-organization-section">
                  <div className="artifact-section-head">
                    <strong>文件整理提案</strong>
                    <span>{singleFileOperations.length} 个</span>
                  </div>
                  <p className="artifact-policy-note">Agent 只能提出单文件重命名/移动方案；路径变更必须人工确认，且目标不得存在、不得跨 Workspace 根目录、不得改变文件扩展名。</p>
                  <div className="source-edit-list">
                    {singleFileOperations.map((operation) => (
                      <article className="source-edit-card file-organization-card" key={operation.id}>
                        <div className="source-edit-head">
                          <div>
                            <strong>{operation.operation === "rename" ? "重命名" : "移动"} · {operation.filename}</strong>
                            <span>{fileOrganizationStatusLabel(operation.status)}</span>
                          </div>
                          <span className={`task-status ${operation.status === "pending" ? "blocked" : operation.status === "applied" || operation.status === "rolled_back" ? "completed" : "failed"}`}>
                            {fileOrganizationStatusLabel(operation.status)}
                          </span>
                        </div>
                        <p>{operation.summary}</p>
                        <div className="organization-path-change">
                          <code>{operation.original_path}</code>
                          <span>→</span>
                          <code>{operation.target_path}</code>
                        </div>
                        <div className="source-edit-hash">
                          <small>SHA-256 {operation.original_sha256.slice(0, 16)}…</small>
                          <small>File ID {operation.file_id.slice(0, 8)}…</small>
                        </div>
                        {operation.error_message && <div className="provider-error">{operation.error_message}</div>}
                        {operation.status === "pending" && (
                          <div className="button-row">
                            <button className="primary" disabled={disabled} onClick={() => onConfirmOrganization(operation.id, detail.id)}>确认路径变更</button>
                            <button className="secondary" disabled={disabled} onClick={() => onRejectOrganization(operation.id, detail.id)}>拒绝提案</button>
                          </div>
                        )}
                        {operation.status === "applied" && (
                          <div className="button-row">
                            <button className="secondary" disabled={disabled} onClick={() => onRollbackOrganization(operation.id, detail.id)}>恢复原路径</button>
                            <span className="muted small">内容 SHA 未改变，可在原路径空闲时恢复。</span>
                          </div>
                        )}
                        {operation.status === "recovery_required" && (
                          <div className="provider-error">路径状态无法安全自动判断。DeskAI 已停止继续操作，需要人工核对原路径与目标路径。</div>
                        )}
                        {operation.status === "rolled_back" && <p className="muted small">文件已恢复到原路径。</p>}
                        {operation.status === "rejected" && <p className="muted small">提案已拒绝，文件路径从未改变。</p>}
                      </article>
                    ))}
                  </div>
                </div>
              )}

              {batches.length > 0 && (
                <div className="source-edit-section batch-transaction-section">
                  <div className="artifact-section-head">
                    <strong>跨文件事务提案</strong>
                    <span>{batches.length} 个事务</span>
                  </div>
                  <p className="artifact-policy-note">每个事务会一次性预检、备份并提交全部成员；任何成员失败都会自动回滚已经写入的文件。事务成员不能单独确认。</p>
                  <div className="source-edit-list">
                    {batches.map((batch) => (
                      <article className="source-edit-card batch-transaction-card" key={batch.id}>
                        <div className="source-edit-head">
                          <div>
                            <strong>{batch.summary}</strong>
                            <span>{batch.edit_count} 个文件 · All-or-nothing · {sourceEditBatchStatusLabel(batch.status)}</span>
                          </div>
                          <span className={`task-status ${batch.status === "pending" ? "blocked" : batch.status === "applied" || batch.status === "rolled_back" ? "completed" : "failed"}`}>
                            {sourceEditBatchStatusLabel(batch.status)}
                          </span>
                        </div>
                        <div className="batch-member-list">
                          {batch.edits.map((edit) => (
                            <div className="batch-member" key={edit.id}>
                              <div>
                                <strong>{edit.filename}</strong>
                                <span>{edit.kind.toUpperCase()} · {edit.summary}</span>
                              </div>
                              <pre className="edit-diff-preview">{edit.diff_preview || "没有可显示的差异预览。"}</pre>
                              <div className="source-edit-hash">
                                <small>原 SHA-256 {edit.original_sha256.slice(0, 16)}…</small>
                                <small>候选 SHA-256 {edit.candidate_sha256.slice(0, 16)}…</small>
                              </div>
                            </div>
                          ))}
                        </div>
                        {batch.error_message && <div className="provider-error">{batch.error_message}</div>}
                        {batch.status === "pending" && (
                          <div className="button-row">
                            <button className="primary" disabled={disabled} onClick={() => onConfirmBatch(batch.id, detail.id)}>确认整批应用</button>
                            <button className="secondary" disabled={disabled} onClick={() => onRejectBatch(batch.id, detail.id)}>拒绝整批</button>
                          </div>
                        )}
                        {batch.status === "applied" && (
                          <div className="button-row">
                            <button className="secondary" disabled={disabled} onClick={() => onRollbackBatch(batch.id, detail.id)}>整体回滚</button>
                            <span className="muted small">全部成员已应用并保留独立备份。</span>
                          </div>
                        )}
                        {batch.status === "recovery_required" && (
                          <div className="provider-error">事务状态无法安全自动恢复。DeskAI 已停止继续写入，需人工核对成员文件。</div>
                        )}
                        {batch.status === "rolled_back" && <p className="muted small">整批文件已恢复到修改前状态。</p>}
                        {batch.status === "rejected" && <p className="muted small">整批提案已拒绝，源文件从未被修改。</p>}
                      </article>
                    ))}
                  </div>
                </div>
              )}

              {singleEdits.length > 0 && (
                <div className="source-edit-section">
                  <div className="artifact-section-head">
                    <strong>源文件编辑提案</strong>
                    <span>{singleEdits.length} 个</span>
                  </div>
                  <p className="artifact-policy-note">Agent 只能生成提案；只有你点击“确认应用”后，DeskAI 才会再次校验 SHA-256、创建原文件备份并覆盖源文件。</p>
                  <div className="source-edit-list">
                    {singleEdits.map((edit) => (
                      <article className="source-edit-card" key={edit.id}>
                        <div className="source-edit-head">
                          <div>
                            <strong>{edit.filename}</strong>
                            <span>{edit.kind.toUpperCase()} · {sourceEditStatusLabel(edit.status)}</span>
                          </div>
                          <span className={`task-status ${edit.status === "pending" ? "blocked" : edit.status === "applied" ? "completed" : "failed"}`}>
                            {sourceEditStatusLabel(edit.status)}
                          </span>
                        </div>
                        <p>{edit.summary}</p>
                        <pre className="edit-diff-preview">{edit.diff_preview || "没有可显示的文本差异预览。"}</pre>
                        <div className="source-edit-hash">
                          <small>原 SHA-256 {edit.original_sha256.slice(0, 16)}…</small>
                          <small>候选 SHA-256 {edit.candidate_sha256.slice(0, 16)}…</small>
                        </div>
                        {edit.error_message && <div className="provider-error">{edit.error_message}</div>}
                        {edit.status === "pending" && (
                          <div className="button-row">
                            <button className="primary" disabled={disabled} onClick={() => onConfirmEdit(edit.id, detail.id)}>确认应用</button>
                            <button className="secondary" disabled={disabled} onClick={() => onRejectEdit(edit.id, detail.id)}>拒绝提案</button>
                          </div>
                        )}
                        {edit.status === "applied" && (
                          <div className="button-row">
                            <button className="secondary" disabled={disabled} onClick={() => onRollbackEdit(edit.id, detail.id)}>回滚到修改前</button>
                            <span className="muted small">{edit.backup_created ? "原文件备份已创建" : "备份状态未知"}</span>
                          </div>
                        )}
                        {edit.status === "rolled_back" && <p className="muted small">已恢复修改前版本。</p>}
                        {edit.status === "rejected" && <p className="muted small">提案已拒绝，源文件从未被修改。</p>}
                      </article>
                    ))}
                  </div>
                </div>
              )}

              {detail.artifacts.length > 0 && (
                <div className="artifact-section">
                  <div className="artifact-section-head">
                    <strong>生成的工作成果</strong>
                    <span>{detail.artifacts.length} 个文件</span>
                  </div>
                  <div className="artifact-list">
                    {detail.artifacts.map((artifact) => (
                      <article className="artifact-card" key={artifact.id}>
                        <div className="artifact-icon">{artifact.kind === "xlsx" ? "XLSX" : artifact.kind === "docx" ? "DOCX" : artifact.kind.toUpperCase()}</div>
                        <div className="artifact-info">
                          <strong>{artifact.filename}</strong>
                          <span>{formatBytes(artifact.size)} · {formatDate(artifact.created_at)}</span>
                          <code>{artifact.path}</code>
                          <small>SHA-256 {artifact.sha256}</small>
                        </div>
                      </article>
                    ))}
                  </div>
                  <p className="artifact-policy-note">这些文件是 DeskAI 新生成的产物，保存在私有 generated/&lt;task_id&gt; 目录；原始 Workspace 文件没有被覆盖。</p>
                </div>
              )}

              {detail.execution_mode === "agent" && ["failed", "blocked"].includes(detail.status) && (
                <button className="secondary" onClick={() => onRetry(detail.id)} disabled={disabled}>重新入队</button>
              )}

              <div className="task-run-summary">
                <span>运行 {detail.runs.length} 次</span>
                <span>工作计划 {detail.work_plans.length} 个</span>
                <span>工具调用 {detail.tool_calls.length} 次</span>
                <span>生成文件 {detail.artifacts.length} 个</span>
                <span>单文件提案 {singleEdits.length} 个</span>
                <span>跨文件事务 {batches.length} 个</span>
                <span>单文件整理 {singleFileOperations.length} 个</span>
                <span>批量文件整理 {fileOperationBatches.length} 个</span>
                <span>单文件回收 {singleRecycleProposals.length} 个</span>
                <span>批量回收 {recycleBatches.length} 个</span>
                <span>开始 {detail.started_at ? formatDate(detail.started_at) : "—"}</span>
              </div>

              <div className="tool-call-list">
                {detail.tool_calls.map((call) => (
                  <article className="tool-call-card" key={call.id}>
                    <div><strong>{call.tool_name}</strong><span>风险 L{call.risk_level} · {call.status}</span></div>
                    <code>{JSON.stringify(call.arguments ?? {}, null, 2)}</code>
                    {call.result_summary && <p>{call.result_summary}</p>}
                  </article>
                ))}
                {!detail.tool_calls.length && <p className="muted small">该任务尚未产生工具调用。</p>}
              </div>
            </>
          )}
        </article>
      </div>
    </section>
  );
}

function WorkPlanPanel({ plan, disabled, onStart, onResume, onRetry, onCancel }: {
  plan: WorkPlanRecord;
  disabled: boolean;
  onStart: (plan: WorkPlanRecord) => void;
  onResume: (plan: WorkPlanRecord) => void;
  onRetry: (plan: WorkPlanRecord) => void;
  onCancel: (plan: WorkPlanRecord) => void;
}) {
  return (
    <div className="source-edit-section batch-transaction-section work-plan-section">
      <div className="artifact-section-head">
        <div>
          <span className="eyebrow">Phase 22 · 持久化工作计划</span>
          <strong>{plan.title}</strong>
        </div>
        <span className={`task-status ${plan.status}`}>{workPlanStatusLabel(plan.status)}</span>
      </div>
      <p>{plan.summary}</p>
      <div className="task-progress-track"><span style={{ width: `${Math.round(plan.progress * 100)}%` }} /></div>
      {plan.limitations.length > 0 && (
        <div className="recovery-guidance-grid">
          <div>
            <strong>计划边界</strong>
            <ul>{plan.limitations.map((item, index) => <li key={`${plan.id}:limit:${index}`}>{item}</li>)}</ul>
          </div>
        </div>
      )}
      {plan.error_message && <div className="provider-error">{plan.error_message}</div>}
      <div className="source-edit-list">
        {plan.steps.map((step) => (
          <article className="source-edit-card work-plan-step-card" key={step.id}>
            <div className="source-edit-head">
              <div>
                <strong>{step.position}. {step.title}</strong>
                <span>{step.tool_name} · 风险 L{step.risk_level} · {step.execution_mode === "proposal_gate" ? "人工确认门禁" : "自动步骤"}</span>
              </div>
              <span className={`task-status ${step.status}`}>{workPlanStepStatusLabel(step.status)}</span>
            </div>
            <p>{step.description}</p>
            <div className="source-edit-hash">
              <small>依赖：{step.dependencies.length ? step.dependencies.map((value) => `#${value}`).join(" / ") : "无"}</small>
              {step.tool_call_id && <small>ToolCall {step.tool_call_id.slice(0, 8)}…</small>}
            </div>
            <details className="recovery-evidence">
              <summary>查看计划参数</summary>
              <pre className="edit-diff-preview">{JSON.stringify(step.arguments, null, 2)}</pre>
            </details>
            {step.result_summary && (
              <details className="recovery-evidence">
                <summary>查看步骤结果</summary>
                <pre className="edit-diff-preview">{step.result_summary}</pre>
              </details>
            )}
            {step.error_message && <div className="provider-error">{step.error_message}</div>}
            {step.status === "awaiting_confirmation" && (
              <div className="provider-warning">
                已生成原有安全文件提案 {step.external_entity_type} / {step.external_entity_id?.slice(0, 8)}…。请在本任务下方对应提案卡片完成原人工确认；确认成功后再点击“继续计划”。
              </div>
            )}
          </article>
        ))}
      </div>
      <div className="button-row">
        {plan.can_start && <button className="primary" disabled={disabled} onClick={() => onStart(plan)}>开始执行计划</button>}
        {plan.can_resume && <button className="primary" disabled={disabled} onClick={() => onResume(plan)}>继续计划</button>}
        {plan.can_retry && <button className="secondary" disabled={disabled} onClick={() => onRetry(plan)}>重试中断/失败步骤</button>}
        {plan.can_cancel && <button className="secondary" disabled={disabled} onClick={() => onCancel(plan)}>取消计划</button>}
      </div>
      <p className="artifact-policy-note">计划执行仍通过原 ToolRegistry、PermissionGate 与 AuditLog。propose_* 步骤只创建既有文件事务提案；计划自身没有确认、覆盖、删除、回滚或恢复文件的特殊权限。</p>
    </div>
  );
}
function RecoveryPage({
  entries,
  disabled,
  onRecover,
  onSnapshot,
  onProposeReconciliation,
  onConfirmReconciliation,
  onRejectReconciliation,
  onExportEvidence,
  onOpenTask,
}: {
  entries: RecoveryEntry[];
  disabled: boolean;
  onRecover: (entry: RecoveryEntry) => void;
  onSnapshot: (entry: RecoveryEntry) => Promise<RecoverySnapshot>;
  onProposeReconciliation: (entry: RecoveryEntry) => Promise<RecoveryReconciliationProposal>;
  onConfirmReconciliation: (proposal: RecoveryReconciliationProposal) => Promise<RecoveryReconciliationProposal>;
  onRejectReconciliation: (proposal: RecoveryReconciliationProposal) => Promise<RecoveryReconciliationProposal>;
  onExportEvidence: (entry: RecoveryEntry) => Promise<RecoveryEvidencePackage>;
  onOpenTask: (taskId: string) => void;
}) {
  const [filter, setFilter] = useState<"recoverable" | "attention" | "history" | "all">("recoverable");
  const [snapshots, setSnapshots] = useState<Record<string, RecoverySnapshot>>({});
  const [snapshotErrors, setSnapshotErrors] = useState<Record<string, string>>({});
  const [snapshotLoading, setSnapshotLoading] = useState<string | null>(null);
  const [reconciliations, setReconciliations] = useState<Record<string, RecoveryReconciliationProposal>>({});
  const [reconciliationErrors, setReconciliationErrors] = useState<Record<string, string>>({});
  const [reconciliationLoading, setReconciliationLoading] = useState<string | null>(null);
  const [evidencePackages, setEvidencePackages] = useState<Record<string, RecoveryEvidencePackage>>({});
  const [evidenceErrors, setEvidenceErrors] = useState<Record<string, string>>({});
  const [evidenceLoading, setEvidenceLoading] = useState<string | null>(null);

  async function refreshSnapshot(entry: RecoveryEntry) {
    const key = recoverySnapshotKey(entry);
    setSnapshotLoading(key);
    setSnapshotErrors((current) => ({ ...current, [key]: "" }));
    try {
      const snapshot = await onSnapshot(entry);
      setSnapshots((current) => ({ ...current, [key]: snapshot }));
    } catch (error) {
      setSnapshotErrors((current) => ({
        ...current,
        [key]: error instanceof Error ? error.message : "恢复快照检测失败",
      }));
    } finally {
      setSnapshotLoading((current) => (current === key ? null : current));
    }
  }
  async function createReconciliation(entry: RecoveryEntry) {
    const key = recoverySnapshotKey(entry);
    setReconciliationLoading(key);
    setReconciliationErrors((current) => ({ ...current, [key]: "" }));
    try {
      const proposal = await onProposeReconciliation(entry);
      setReconciliations((current) => ({ ...current, [key]: proposal }));
    } catch (error) {
      setReconciliationErrors((current) => ({
        ...current,
        [key]: error instanceof Error ? error.message : "生成状态核对提案失败",
      }));
    } finally {
      setReconciliationLoading((current) => (current === key ? null : current));
    }
  }

  async function confirmReconciliation(
    entry: RecoveryEntry,
    proposal: RecoveryReconciliationProposal,
  ) {
    const key = recoverySnapshotKey(entry);
    const actionLabel = proposal.historical_action === "rollback" ? "回滚" : "恢复";
    if (!window.confirm(
      `确认将事务元数据从 recovery_required 修正为 ${proposal.target_status} 吗？DeskAI 会重新计算当前磁盘快照并校验指纹；不会修改用户文件。确认后只会重新开放原有“${actionLabel}”安全流程。`,
    )) return;

    setReconciliationLoading(key);
    setReconciliationErrors((current) => ({ ...current, [key]: "" }));
    try {
      const confirmed = await onConfirmReconciliation(proposal);
      setReconciliations((current) => ({ ...current, [key]: confirmed }));
    } catch (error) {
      setReconciliations((current) => {
        const next = { ...current };
        delete next[key];
        return next;
      });
      setReconciliationErrors((current) => ({
        ...current,
        [key]: error instanceof Error ? error.message : "状态核对确认失败",
      }));
    } finally {
      setReconciliationLoading((current) => (current === key ? null : current));
    }
  }

  async function rejectReconciliation(
    entry: RecoveryEntry,
    proposal: RecoveryReconciliationProposal,
  ) {
    const key = recoverySnapshotKey(entry);
    setReconciliationLoading(key);
    try {
      const rejected = await onRejectReconciliation(proposal);
      setReconciliations((current) => ({ ...current, [key]: rejected }));
    } catch (error) {
      setReconciliationErrors((current) => ({
        ...current,
        [key]: error instanceof Error ? error.message : "拒绝状态核对提案失败",
      }));
    } finally {
      setReconciliationLoading((current) => (current === key ? null : current));
    }
  }

  async function exportEvidence(entry: RecoveryEntry) {
    const key = recoverySnapshotKey(entry);
    setEvidenceLoading(key);
    setEvidenceErrors((current) => ({ ...current, [key]: "" }));
    try {
      const result = await onExportEvidence(entry);
      setEvidencePackages((current) => ({ ...current, [key]: result }));
    } catch (error) {
      setEvidenceErrors((current) => ({
        ...current,
        [key]: error instanceof Error ? error.message : "恢复证据包生成失败",
      }));
    } finally {
      setEvidenceLoading((current) => (current === key ? null : current));
    }
  }

  const recoverable = entries.filter((item) => item.action !== null);
  const attention = entries.filter((item) => item.recovery_required);
  const history = entries.filter((item) => item.action === null && !item.recovery_required);
  const shown =
    filter === "recoverable"
      ? recoverable
      : filter === "attention"
        ? attention
        : filter === "history"
          ? history
          : entries;

  return (
    <section className="recovery-page">
      <div className="knowledge-status-grid">
        <Metric label="可恢复" value={String(recoverable.length)} />
        <Metric label="需人工处理" value={String(attention.length)} />
        <Metric label="历史记录" value={String(history.length)} />
        <Metric label="总事务" value={String(entries.length)} />
      </div>

      <article className="panel recovery-panel">
        <div className="panel-head recovery-head">
          <div>
            <h3>统一恢复中心</h3>
            <p className="muted small">集中查看 Phase 11–21 的文件事务与恢复证据。Phase 20 只在最新快照证明 applied/recycled 且历史动作证据齐全时允许受控状态核对；Phase 21 可导出只读事故证据 ZIP，不包含用户文件内容，也不修改任何恢复状态。</p>
          </div>
          <div className="recovery-filters">
            {([
              ["recoverable", "可恢复"],
              ["attention", "需人工处理"],
              ["history", "历史"],
              ["all", "全部"],
            ] as const).map(([value, label]) => (
              <button
                key={value}
                className={filter === value ? "secondary active-filter" : "secondary"}
                onClick={() => setFilter(value)}
              >
                {label}
              </button>
            ))}
          </div>
        </div>

        <div className="recovery-list">
          {shown.map((entry) => (
            <article className={`recovery-card ${entry.recovery_required ? "needs-attention" : ""}`} key={`${entry.entity_type}:${entry.id}`}>
              <div className="recovery-card-head">
                <div>
                  <div className="recovery-kind-row">
                    <strong>{recoveryKindLabel(entry.entity_type)}</strong>
                    {entry.transactional && <span className="risk-badge">事务</span>}
                    <span className={`task-status ${entry.recovery_required ? "failed" : entry.action ? "blocked" : "completed"}`}>
                      {recoveryStatusLabel(entry.status)}
                    </span>
                  </div>
                  <span className="muted small">{formatDate(entry.updated_at)} · {entry.item_count} 个文件</span>
                </div>
                {entry.action && (
                  <button className="primary" disabled={disabled} onClick={() => onRecover(entry)}>
                    {entry.action === "restore" ? "恢复原文件" : "回滚事务"}
                  </button>
                )}
              </div>

              <p>{entry.summary || "无摘要"}</p>
              <div className="recovery-files">
                {entry.filenames.slice(0, 6).map((name, index) => (
                  <span key={`${name}:${index}`}>{name}</span>
                ))}
                {entry.filenames.length > 6 && <span>+{entry.filenames.length - 6}</span>}
              </div>
              {entry.paths.slice(0, 2).map((path) => <code className="recovery-path" key={path}>{path}</code>)}
              <div className="recovery-snapshot-actions">
                <button
                  className="secondary"
                  disabled={disabled || evidenceLoading === recoverySnapshotKey(entry)}
                  onClick={() => void exportEvidence(entry)}
                >
                  {evidenceLoading === recoverySnapshotKey(entry) ? "生成中…" : "导出恢复证据包"}
                </button>
                <span className="muted small">Phase 21：仅打包事务元数据、诊断、快照、SHA、核对历史和审计；不包含 Workspace/备份/隔离副本文件内容。</span>
              </div>
              {evidenceErrors[recoverySnapshotKey(entry)] && (
                <div className="provider-error">{evidenceErrors[recoverySnapshotKey(entry)]}</div>
              )}
              {evidencePackages[recoverySnapshotKey(entry)] && (
                <div className="recovery-evidence">
                  <strong>证据包已生成</strong>
                  <code>{evidencePackages[recoverySnapshotKey(entry)].artifact.filename}</code>
                  <code className="recovery-path">{evidencePackages[recoverySnapshotKey(entry)].artifact.path}</code>
                  <span className="muted small">ZIP SHA-256 {evidencePackages[recoverySnapshotKey(entry)].artifact.sha256}</span>
                </div>
              )}
              {entry.error_message && <div className="provider-error">{entry.error_message}</div>}
              {entry.recovery_required && (
                <>
                  {entry.diagnostic && (
                    <div className="recovery-diagnostic">
                      <div className="recovery-diagnostic-head">
                        <div>
                          <span className="eyebrow">Phase 18 · 只读诊断</span>
                          <strong>{entry.diagnostic.title}</strong>
                        </div>
                        <div className="recovery-diagnostic-badges">
                          <span className="risk-badge">{entry.diagnostic.code}</span>
                          <span className="risk-badge">置信度 {entry.diagnostic.confidence}</span>
                        </div>
                      </div>
                      <p>{entry.diagnostic.summary}</p>
                      <div className="recovery-guidance-grid">
                        <div>
                          <strong>建议核对顺序</strong>
                          <ol>
                            {entry.diagnostic.guided_checks.map((step, index) => (
                              <li key={`${entry.id}:check:${index}`}>{step}</li>
                            ))}
                          </ol>
                        </div>
                        <div>
                          <strong>禁止操作</strong>
                          <ul>
                            {entry.diagnostic.prohibited_actions.map((step, index) => (
                              <li key={`${entry.id}:blocked:${index}`}>{step}</li>
                            ))}
                          </ul>
                        </div>
                      </div>
                      <details className="recovery-evidence">
                        <summary>查看诊断证据</summary>
                        {entry.diagnostic.evidence.map((value, index) => (
                          <code key={`${entry.id}:evidence:${index}`}>{value}</code>
                        ))}
                      </details>
                      <p className="muted small">自动修复：关闭。Phase 18 只提供诊断和引导，不会创建 force overwrite、ignore SHA 或直接修改磁盘的新接口。</p>
                    </div>
                  )}
                  <div className="recovery-snapshot-actions">
                    <button
                      className="secondary"
                      disabled={disabled || snapshotLoading === recoverySnapshotKey(entry)}
                      onClick={() => void refreshSnapshot(entry)}
                    >
                      {snapshotLoading === recoverySnapshotKey(entry) ? "检测中…" : "重新检测磁盘状态"}
                    </button>
                    <span className="muted small">只读检测：不会改文件、不会写事务状态、不会解除 recovery_required。</span>
                  </div>
                  {snapshotErrors[recoverySnapshotKey(entry)] && (
                    <div className="provider-error">{snapshotErrors[recoverySnapshotKey(entry)]}</div>
                  )}
                  {snapshots[recoverySnapshotKey(entry)] && (
                    <RecoverySnapshotPanel snapshot={snapshots[recoverySnapshotKey(entry)]} />
                  )}
                  {snapshots[recoverySnapshotKey(entry)] && recoverySnapshotSupportsReconciliation(snapshots[recoverySnapshotKey(entry)]) && (
                    <RecoveryReconciliationPanel
                      proposal={reconciliations[recoverySnapshotKey(entry)]}
                      busy={disabled || reconciliationLoading === recoverySnapshotKey(entry)}
                      error={reconciliationErrors[recoverySnapshotKey(entry)]}
                      onCreate={() => void createReconciliation(entry)}
                      onConfirm={(proposal) => void confirmReconciliation(entry, proposal)}
                      onReject={(proposal) => void rejectReconciliation(entry, proposal)}
                    />
                  )}
                  {snapshots[recoverySnapshotKey(entry)] && !recoverySnapshotSupportsReconciliation(snapshots[recoverySnapshotKey(entry)]) && (
                    <p className="muted small">Phase 20 不会解冻当前快照：仅 consistent_applied + rollback 或 consistent_recycled + restore 可进入受控状态核对。</p>
                  )}
                  <div className="recovery-attention-row">
                    <span className="muted small">自动恢复仍处于冻结状态。Phase 20 只有在快照指纹二次复核完全一致后，才允许把元数据接回原 rollback/restore 状态机；不会自动执行文件恢复动作。</span>
                    {entry.task_id && <button className="secondary" onClick={() => onOpenTask(entry.task_id!)}>查看对应 Task</button>}
                  </div>
                </>
              )}
            </article>
          ))}
          {!shown.length && (
            <p className="muted">
              {filter === "recoverable"
                ? "当前没有可自动恢复的事务。"
                : filter === "attention"
                  ? "当前没有需要人工处理的恢复异常。"
                  : "当前筛选条件下没有记录。"}
            </p>
          )}
        </div>
      </article>
    </section>
  );
}

function recoverySnapshotKey(entry: RecoveryEntry) {
  return `${entry.entity_type}:${entry.id}`;
}

function RecoverySnapshotPanel({ snapshot }: { snapshot: RecoverySnapshot }) {
  const assessment = snapshot.assessment;
  return (
    <div className="recovery-snapshot">
      <div className="recovery-snapshot-head">
        <div>
          <span className="eyebrow">Phase 19 · 只读恢复快照</span>
          <strong>{recoverySnapshotStateLabel(assessment.state)}</strong>
          <span className="muted small">采集时间 {formatDate(snapshot.captured_at)}</span>
        </div>
        <div className="recovery-diagnostic-badges">
          <span className={`task-status ${assessment.safe_state_detected ? "completed" : "failed"}`}>
            {assessment.safe_state_detected ? "检测到一致安全态" : "状态仍不一致"}
          </span>
          <span className="risk-badge">持久状态仍冻结</span>
        </div>
      </div>

      <p>{assessment.reason}</p>
      <div className="recovery-snapshot-summary">
        <span>技术恢复前置条件：<b>{assessment.technical_action_preconditions_satisfied ? "已满足" : "未满足/无需动作"}</b></span>
        <span>允许直接重试：<b>{assessment.safe_to_retry_existing_action ? "是" : "否"}</b></span>
        <span>状态核对后可能动作：<b>{assessment.action_after_reconciliation === "rollback" ? "回滚" : assessment.action_after_reconciliation === "restore" ? "恢复" : "无"}</b></span>
      </div>

      <div className="recovery-snapshot-members">
        {snapshot.members.map((member) => (
          <article className="recovery-snapshot-member" key={member.id}>
            <div className="recovery-snapshot-member-head">
              <strong>{member.filename}</strong>
              <span className={`task-status ${member.supporting_evidence_valid ? "completed" : "failed"}`}>
                {recoveryMemberStateLabel(member.state)}
              </span>
            </div>
            {member.tracked_path && <code className="recovery-path">{member.tracked_path}</code>}
            <div className="recovery-observation-list">
              {member.observations.map((observation) => (
                <div className="recovery-observation" key={`${member.id}:${observation.role}`}>
                  <div>
                    <strong>{recoveryObservationRoleLabel(observation.role)}</strong>
                    <span>{observation.is_symlink ? "符号链接（未跟随）" : observation.exists ? observation.is_file ? "文件存在" : "路径存在但不是普通文件" : "不存在"}</span>
                  </div>
                  {observation.path && <code>{observation.path}</code>}
                  <div className="source-edit-hash">
                    <small>{observation.size === null ? "大小未知" : formatBytes(observation.size)}</small>
                    <small>{observation.sha256 ? `SHA-256 ${observation.sha256.slice(0, 20)}…` : "未计算 SHA-256"}</small>
                  </div>
                  {observation.matches.length > 0 && (
                    <span className="muted small">匹配事务证据：{observation.matches.join(" / ")}</span>
                  )}
                  {observation.error && <span className="provider-error">{observation.error}</span>}
                </div>
              ))}
            </div>
          </article>
        ))}
      </div>

      <details className="recovery-snapshot-audit">
        <summary>查看相关审计时间线（{snapshot.audit_timeline.length}）</summary>
        <div className="recovery-snapshot-audit-list">
          {snapshot.audit_timeline.map((event, index) => (
            <div key={`${event.timestamp}:${event.action}:${index}`}>
              <span>{formatDate(event.timestamp)}</span>
              <strong>{event.action}</strong>
              <span>风险 L{event.risk_level}</span>
              {event.result && <small>{event.result}</small>}
            </div>
          ))}
          {!snapshot.audit_timeline.length && <span className="muted small">没有找到相关审计事件。</span>}
        </div>
      </details>

      <p className="muted small">该快照是当前时点的只读观测结果，不写入数据库、不改变 recovery_required，也不构成自动恢复授权。</p>
    </div>
  );
}

function recoverySnapshotStateLabel(state: string) {
  const labels: Record<string, string> = {
    consistent_original: "磁盘一致：原始态",
    consistent_applied: "磁盘一致：已应用态",
    consistent_recycled: "磁盘一致：已回收态",
    consistent_restored: "磁盘一致：已恢复态",
    mixed_transaction: "批次成员状态混合",
    ambiguous: "磁盘状态仍存在歧义",
  };
  return labels[state] ?? state;
}

function recoveryMemberStateLabel(state: string) {
  const labels: Record<string, string> = {
    original: "原始态",
    applied: "已应用态",
    recycled: "已回收态",
    restored: "已恢复态",
    conflict: "双路径冲突",
    missing: "文件缺失",
    unknown: "无法识别",
    original_without_quarantine: "原件存在但隔离副本缺失",
  };
  return labels[state] ?? state;
}

function recoveryObservationRoleLabel(role: string) {
  const labels: Record<string, string> = {
    workspace_source: "Workspace 当前源文件",
    staged_candidate: "暂存候选副本",
    automatic_backup: "自动备份",
    original_path: "原路径",
    target_path: "目标路径",
    workspace_original: "Workspace 原路径",
    quarantine_copy: "私有隔离副本",
  };
  return labels[role] ?? role;
}

function recoverySnapshotSupportsReconciliation(snapshot: RecoverySnapshot) {
  const assessment = snapshot.assessment;
  return (
    assessment.safe_state_detected
    && assessment.technical_action_preconditions_satisfied
    && (
      (assessment.state === "consistent_applied" && assessment.action_after_reconciliation === "rollback")
      || (assessment.state === "consistent_recycled" && assessment.action_after_reconciliation === "restore")
    )
  );
}

function RecoveryReconciliationPanel({
  proposal,
  busy,
  error,
  onCreate,
  onConfirm,
  onReject,
}: {
  proposal?: RecoveryReconciliationProposal;
  busy: boolean;
  error?: string;
  onCreate: () => void;
  onConfirm: (proposal: RecoveryReconciliationProposal) => void;
  onReject: (proposal: RecoveryReconciliationProposal) => void;
}) {
  return (
    <div className="recovery-reconciliation">
      <div className="recovery-reconciliation-head">
        <div>
          <span className="eyebrow">Phase 20 · 受控状态核对</span>
          <strong>只修正事务元数据，不修改用户文件</strong>
        </div>
        <span className="risk-badge">二次人工确认</span>
      </div>
      {!proposal && (
        <>
          <p className="muted small">当前快照满足 Phase 20 门禁。生成提案后会固定快照指纹、目标状态和历史动作；确认时必须重新采集并完全匹配。</p>
          <button className="secondary" disabled={busy} onClick={onCreate}>
            {busy ? "生成中…" : "生成状态核对提案"}
          </button>
        </>
      )}
      {proposal && (
        <div className="recovery-reconciliation-proposal">
          <div className="recovery-snapshot-summary">
            <span>提案状态：<b>{proposal.status}</b></span>
            <span>目标状态：<b>{proposal.target_status}</b></span>
            <span>重新开放：<b>{proposal.historical_action === "rollback" ? "原 rollback" : "原 restore"}</b></span>
          </div>
          <code className="recovery-path">Snapshot fingerprint: {proposal.snapshot_fingerprint}</code>
          {proposal.status === "pending" && (
            <div className="button-row">
              <button className="primary" disabled={busy} onClick={() => onConfirm(proposal)}>
                {busy ? "复核中…" : "二次确认并解除事务冻结"}
              </button>
              <button className="secondary" disabled={busy} onClick={() => onReject(proposal)}>
                拒绝提案
              </button>
            </div>
          )}
          {proposal.status === "confirmed" && (
            <p className="muted small">状态核对已确认。Recovery Center 刷新后将只通过原 Phase 11–16 rollback/restore API 执行后续文件动作。</p>
          )}
          {proposal.status === "rejected" && (
            <p className="muted small">提案已拒绝，recovery_required 冻结保持不变。</p>
          )}
          {proposal.status === "stale" && (
            <p className="provider-error">快照已变化，此提案不可确认；请重新检测磁盘状态并生成新提案。</p>
          )}
        </div>
      )}
      {error && <div className="provider-error">{error}</div>}
      <p className="muted small">禁止：force overwrite、ignore SHA、自动恢复、修改用户文件、删除备份/隔离副本、拆分批次。</p>
    </div>
  );
}

function recoveryKindLabel(kind: string) {
  const labels: Record<string, string> = {
    source_edit: "单文件内容编辑",
    source_edit_batch: "批量内容编辑",
    file_organization: "单文件路径整理",
    file_organization_batch: "批量路径整理",
    file_recycle: "单文件回收",
    file_recycle_batch: "批量回收",
  };
  return labels[kind] ?? kind;
}

function recoveryStatusLabel(status: string) {
  const labels: Record<string, string> = {
    applied: "已应用，可回滚",
    rolling_back: "回滚中",
    rolled_back: "已回滚",
    recycled: "已回收，可恢复",
    restoring: "恢复中",
    restored: "已恢复",
    recovery_required: "需要人工处理",
  };
  return labels[status] ?? status;
}

function ActivityPage({ activity }: { activity: ActivityRecord[] }) {
  return (
    <section className="activity-page">
      <article className="panel activity-panel">
        <div className="panel-head">
          <div><h3>Agent 审计日志</h3><p className="muted small">每次 Agent 启动、工具完成、拒绝和失败都会留下持久化记录。</p></div>
          <span>{activity.length} 条</span>
        </div>
        <div className="activity-list">
          {activity.map((item) => (
            <article className="activity-row" key={item.id}>
              <div className="activity-time">{formatDate(item.timestamp)}</div>
              <div className="activity-main">
                <div>
                  <strong>{activityLabel(item.action)}</strong>
                  {item.tool && <span className="activity-tool">{item.tool}</span>}
                  <span className={`risk-badge risk-${Math.min(item.risk_level, 8)}`}>L{item.risk_level}</span>
                </div>
                {item.result && <p>{item.result}</p>}
                <small>{item.task_id ? `Task ${item.task_id.slice(0, 8)}` : "系统事件"}</small>
              </div>
            </article>
          ))}
          {!activity.length && <p className="muted">当前 Workspace 还没有 Agent 审计事件。</p>}
        </div>
      </article>
    </section>
  );
}

function recycleBatchStatusLabel(status: string) {
  const labels: Record<string, string> = {
    pending: "等待整批确认",
    recycling: "整批安全回收中",
    recycled: "整批已回收，可恢复",
    restoring: "整批恢复中",
    restored: "整批已恢复",
    rejected: "整批已拒绝",
    recovery_required: "需要人工恢复",
  };
  return labels[status] ?? status;
}

function recycleStatusLabel(status: string) {
  const labels: Record<string, string> = {
    pending: "等待确认",
    recycling: "安全回收中",
    recycled: "已回收，可恢复",
    restoring: "恢复中",
    restored: "已恢复",
    rejected: "已拒绝",
    recovery_required: "需要人工恢复",
  };
  return labels[status] ?? status;
}

function fileOrganizationBatchStatusLabel(status: string) {
  const labels: Record<string, string> = {
    pending: "等待整批确认",
    applying: "整批路径变更中",
    applied: "整批已应用",
    rolling_back: "整体恢复原路径中",
    rolled_back: "整批已恢复",
    rejected: "整批已拒绝",
    recovery_required: "需要人工恢复",
  };
  return labels[status] ?? status;
}

function fileOrganizationStatusLabel(status: string) {
  const labels: Record<string, string> = {
    pending: "等待确认",
    applying: "路径变更中",
    applied: "已应用",
    rolling_back: "恢复原路径中",
    rolled_back: "已恢复原路径",
    rejected: "已拒绝",
    recovery_required: "需要人工恢复",
  };
  return labels[status] ?? status;
}

function sourceEditBatchStatusLabel(status: string) {
  const labels: Record<string, string> = {
    pending: "等待整批确认",
    applying: "事务提交中",
    applied: "整批已应用",
    rolling_back: "整体回滚中",
    rolled_back: "整批已回滚",
    rejected: "整批已拒绝",
    recovery_required: "需要人工恢复",
  };
  return labels[status] ?? status;
}

function sourceEditStatusLabel(status: string) {
  const labels: Record<string, string> = {
    pending: "等待确认",
    applied: "已应用",
    rejected: "已拒绝",
    rolled_back: "已回滚",
  };
  return labels[status] ?? status;
}

function taskStatusLabel(status: string) {
  const labels: Record<string, string> = {
    planning: "计划生成中",
    planned: "待开始计划",
    pending: "待执行",
    running: "执行中",
    awaiting_confirmation: "等待人工确认",
    completed: "已完成",
    failed: "失败",
    blocked: "已阻断",
    cancelled: "已取消",
  };
  return labels[status] ?? status;
}

function workPlanStatusLabel(status: string) {
  const labels: Record<string, string> = {
    ready: "待开始",
    running: "执行中",
    awaiting_confirmation: "等待人工确认",
    completed: "已完成",
    failed: "失败",
    blocked: "已阻断",
    paused: "中断待重试",
    cancelled: "已取消",
  };
  return labels[status] ?? status;
}

function workPlanStepStatusLabel(status: string) {
  const labels: Record<string, string> = {
    pending: "待执行",
    running: "执行中",
    awaiting_confirmation: "等待人工确认",
    completed: "已完成",
    failed: "失败",
    interrupted: "中断",
    cancelled: "已取消",
  };
  return labels[status] ?? status;
}

function activityLabel(action: string) {
  const labels: Record<string, string> = {
    agent_started: "Agent 开始",
    agent_completed: "Agent 完成",
    agent_failed: "Agent 失败",
    agent_blocked: "Agent 被策略阻断",
    agent_interrupted: "Agent 异常中断",
    worker_failed: "Worker 失败",
    work_plan_drafted: "工作计划已生成",
    work_plan_started: "工作计划开始",
    work_plan_resumed: "工作计划继续",
    work_plan_retried: "工作计划重试",
    work_plan_step_completed: "计划步骤完成",
    work_plan_step_failed: "计划步骤失败",
    work_plan_waiting_confirmation: "计划等待人工确认",
    work_plan_confirmation_observed: "计划已识别人工确认",
    work_plan_gate_blocked: "计划确认门禁阻断",
    work_plan_completed: "工作计划完成",
    work_plan_failed: "工作计划失败",
    work_plan_cancelled: "工作计划取消",
    work_plan_interrupted: "工作计划中断",
    tool_completed: "工具调用完成",
    tool_failed: "工具调用失败",
    tool_denied: "工具调用被拒绝",
    source_edit_applied: "源文件编辑已应用",
    source_edit_rejected: "源文件编辑已拒绝",
    source_edit_rolled_back: "源文件编辑已回滚",
    source_edit_batch_applied: "跨文件事务已应用",
    source_edit_batch_rejected: "跨文件事务已拒绝",
    source_edit_batch_rolled_back: "跨文件事务已回滚",
    source_edit_batch_apply_failed_restored: "跨文件事务失败并已自动恢复",
    source_edit_batch_rollback_failed_reapplied: "批量回滚失败并已恢复应用态",
    source_edit_batch_startup_recovered: "启动时已恢复中断事务",
    source_edit_batch_startup_rollback_completed: "启动时已完成中断回滚",
    source_edit_batch_recovery_required: "跨文件事务需要人工恢复",
    file_organization_applied: "文件路径变更已应用",
    file_organization_rejected: "文件整理提案已拒绝",
    file_organization_rolled_back: "文件路径已恢复",
    file_organization_apply_failed: "文件路径变更失败",
    file_organization_rollback_failed: "文件路径恢复失败",
    file_organization_startup_recovered_pending: "启动时恢复未完成路径变更",
    file_organization_startup_completed_apply: "启动时完成路径变更恢复",
    file_organization_startup_recovered_applied: "启动时恢复已应用路径状态",
    file_organization_startup_completed_rollback: "启动时完成路径回滚",
    file_organization_recovery_required: "文件路径状态需要人工恢复",
    file_organization_batch_applied: "批量文件整理已应用",
    file_organization_batch_rejected: "批量文件整理已拒绝",
    file_organization_batch_rolled_back: "批量文件整理已回滚",
    file_organization_batch_apply_failed_restored: "批量文件整理失败并已恢复",
    file_organization_batch_rollback_failed_reapplied: "批量路径回滚失败并已恢复应用态",
    file_organization_batch_startup_recovered: "启动时已恢复中断的批量文件整理",
    file_organization_batch_startup_rollback_completed: "启动时已完成批量路径回滚",
    file_organization_batch_recovery_required: "批量文件整理需要人工恢复",
    file_recycled: "文件已移入回收隔离区",
    file_recycle_rejected: "回收提案已拒绝",
    file_restore_completed: "回收文件已恢复",
    file_recycle_failed_source_retained: "回收失败，原文件仍保留",
    file_restore_failed_recycled_retained: "恢复失败，隔离副本仍保留",
    file_recycle_startup_recovered_pending: "启动时恢复未完成回收",
    file_recycle_startup_completed: "启动时完成回收状态恢复",
    file_restore_startup_completed: "启动时完成文件恢复",
    file_restore_startup_recovered_recycled: "启动时恢复回收状态",
    file_recycle_recovery_required: "回收状态需要人工恢复",
    file_recycle_batch_recycled: "批量回收事务已应用",
    file_recycle_batch_rejected: "批量回收事务已拒绝",
    file_recycle_batch_restored: "批量回收事务已恢复",
    file_recycle_batch_failed_restored: "批量回收失败并已恢复原件",
    file_recycle_batch_restore_failed_recycled: "批量恢复失败并已撤回到回收态",
    file_recycle_batch_startup_recovered_pending: "启动时已恢复中断的批量回收",
    file_recycle_batch_startup_restore_completed: "启动时已完成批量恢复",
    file_recycle_batch_recovery_required: "批量回收状态需要人工恢复",
  };
  return labels[action] ?? action;
}

function SettingsPage({ values, onChange, dirty, busy, onSave, providerStatus, apiKeyDraft, setApiKeyDraft, providerAction, onSaveApiKey, onDeleteApiKey, onTestProvider }: {
  values: DesktopSettings;
  onChange: (values: DesktopSettings) => void;
  dirty: boolean;
  busy: boolean;
  onSave: () => void;
  providerStatus: OpenAIProviderStatus | null;
  apiKeyDraft: string;
  setApiKeyDraft: (value: string) => void;
  providerAction: "save" | "delete" | "test" | null;
  onSaveApiKey: () => void;
  onDeleteApiKey: () => void;
  onTestProvider: () => void;
}) {
  return (
    <section className="settings-grid">
      <article className="panel settings-card">
        <div className="panel-head"><div><h3>AI 与隐私</h3><p className="muted small">非敏感设置保存在本地 SQLite；API Key 与这些设置严格分离。</p></div></div>
        <label>隐私模式<select value={values.privacy_mode} onChange={(e) => onChange({ ...values, privacy_mode: e.target.value as DesktopSettings["privacy_mode"] })}><option value="local">Local Only</option><option value="hybrid">Hybrid</option><option value="cloud">Cloud</option></select></label>
        <label>默认模型<input value={values.default_model} onChange={(e) => onChange({ ...values, default_model: e.target.value })} /></label>
        <label>推理级别<select value={values.reasoning_level} onChange={(e) => onChange({ ...values, reasoning_level: e.target.value as DesktopSettings["reasoning_level"] })}><option value="low">Low</option><option value="medium">Medium</option><option value="high">High</option></select></label>
        <label className="toggle-row"><input type="checkbox" checked={values.auto_index} onChange={(e) => onChange({ ...values, auto_index: e.target.checked })} /><span><strong>自动索引入队</strong><small>Watcher 发现目录变化时自动进入 Index Queue；关闭后仍可手动扫描入队</small></span></label>
        <label className="toggle-row"><input type="checkbox" checked={values.memory_auto_learn} onChange={(e) => onChange({ ...values, memory_auto_learn: e.target.checked })} /><span><strong>自动学习长期记忆</strong><small>仅从用户对话提取长期偏好、决策、约束、纠正、项目状态和工作流程</small></span></label>
        <label>记忆最低置信度<input type="number" min="0.5" max="1" step="0.01" value={values.memory_min_confidence} onChange={(e) => onChange({ ...values, memory_min_confidence: Number(e.target.value) })} /></label>
        <button className="primary save-settings" disabled={!dirty || busy || !values.default_model.trim()} onClick={onSave}>{busy ? "保存中…" : dirty ? "保存设置" : "已保存"}</button>
      </article>

      <article className="panel settings-card provider-card">
        <div className="panel-head">
          <div><h3>OpenAI Provider</h3><p className="muted small">密钥不会写入 React、SQLite 或日志；用户输入的 Key 仅交给本地 Engine 保存到 Windows 凭据存储。</p></div>
          <span className={providerStatus?.configured ? "provider-badge configured" : "provider-badge"}>
            {providerStatus?.configured ? "已配置" : "未配置"}
          </span>
        </div>
        <div className="provider-summary">
          <span>当前模型</span><strong>{providerStatus?.model ?? values.default_model}</strong>
          <span>密钥来源</span><strong>{providerStatus?.source === "environment" ? "环境变量" : providerStatus?.source === "credential_manager" ? "Windows 凭据存储" : "未配置"}</strong>
        </div>
        {providerStatus?.source !== "environment" && (
          <label>OpenAI API Key
            <input
              type="password"
              autoComplete="off"
              value={apiKeyDraft}
              onChange={(event) => setApiKeyDraft(event.target.value)}
              placeholder={providerStatus?.configured ? "输入新 Key 可覆盖当前凭据" : "输入 API Key"}
              disabled={providerAction !== null || providerStatus?.writable === false}
            />
          </label>
        )}
        {providerStatus?.error && <p className="provider-error">{providerStatus.error}</p>}
        <div className="button-row provider-actions">
          {providerStatus?.source !== "environment" && (
            <button className="primary" onClick={onSaveApiKey} disabled={!apiKeyDraft.trim() || providerAction !== null || providerStatus?.writable === false}>
              {providerAction === "save" ? "保存中…" : providerStatus?.configured ? "更新 Key" : "保存 Key"}
            </button>
          )}
          <button className="secondary" onClick={onTestProvider} disabled={!providerStatus?.configured || providerAction !== null}>
            {providerAction === "test" ? "测试中…" : "测试连接"}
          </button>
          {providerStatus?.configured && providerStatus.source !== "environment" && (
            <button className="text-button danger" onClick={onDeleteApiKey} disabled={providerAction !== null}>
              {providerAction === "delete" ? "删除中…" : "删除 Key"}
            </button>
          )}
        </div>
      </article>

      <article className="panel settings-card">
        <div className="panel-head"><h3>安全状态</h3><span>Phase 22</span></div>
        <div className="security-list">
          <p><b>✓</b> Engine 仅监听 127.0.0.1</p>
          <p><b>✓</b> Tauri 与 Engine 使用临时 Session Token</p>
          <p><b>✓</b> API Key 不进入前端持久化或 SQLite</p>
          <p><b>✓</b> Hybrid 模式只发送检索命中的必要片段</p>
          <p><b>✓</b> Responses API 请求显式使用 store=false</p>
          <p><b>✓</b> Local Only 模式不会调用云模型</p>
          <p><b>✓</b> 自动记忆不保存密钥、身份/金融凭据及敏感个人信息</p>
          <p><b>✓</b> 记忆修改保留版本历史，可随时停用</p>
          <p><b>✓</b> Agent 读取能力仍受 Workspace 隔离，全部工具写入 ToolCall/AuditLog</p>
          <p><b>✓</b> Phase 8 仅新增 DOCX/XLSX 到 DeskAI 私有 generated 目录，不覆盖源文件</p>
          <p><b>✓</b> Phase 9 表格分析只读取已解析 CSV/XLSX；数学计算不支持 import、文件或系统命令</p>\n          <p><b>✓</b> Phase 10 Web Research 仅通过 Provider 托管搜索，保留 Source；Local Only 禁用，并阻断疑似密钥查询</p>\n          <p><b>✓</b> Web Research 不提供任意 URL 抓取、下载、浏览器控制或网页指令执行</p>\n          <p><b>✓</b> Phase 11 Agent 只能生成源文件编辑提案，不能直接覆盖源文件</p>\n          <p><b>✓</b> 源文件编辑需目录 write_allowed + 逐次人工确认 + SHA-256 复核 + 自动备份</p>\n          <p><b>✓</b> 已应用编辑只有在文件未再次变化时才允许自动回滚</p>
          <p><b>✓</b> Phase 12 支持 2–10 个文件的 All-or-nothing 事务提案与一次确认</p>
          <p><b>✓</b> 批量提交先全量预检与备份，任一写入失败会恢复已写入成员</p>
          <p><b>✓</b> Engine 启动会恢复中断的 applying/rolling_back 事务；无法安全判断时进入 recovery_required</p>
          <p><b>✓</b> Phase 13 重命名/移动只能在同一授权可写根目录内执行，目标不得已存在</p>
          <p><b>✓</b> 文件整理不改内容 SHA、不改 File ID、不允许目录操作、跨根移动、扩展名变更或删除</p>
          <p><b>✓</b> Phase 14 支持 2–10 个独立路径操作的 All-or-nothing 批量事务</p>
          <p><b>✓</b> 批量整理要求目标彼此唯一且为空，不支持交换/循环重命名，失败会逆序恢复已移动成员</p>
          <p><b>✓</b> Phase 15 “删除”只允许进入私有回收隔离区：先复制并 SHA 校验，再移除 Workspace 原件</p>
          <p><b>✓</b> 回收隔离副本可恢复且不会在 Phase 15/16 永久清理；没有 purge/unlink 隔离副本的 Agent/API 能力</p>
          <p><b>✓</b> Phase 16 支持 2–10 文件 All-or-nothing 回收：全部隔离副本验证完成后才允许移除第一个原件</p>
          <p><b>✓</b> 批量回收/恢复中途失败会自动回到完整原件态或完整回收态；歧义状态冻结为 recovery_required</p>
          <p><b>✓</b> Phase 17 恢复中心只聚合既有事务记录，不新增文件写入、删除或绕过确认的能力</p>
          <p><b>✓</b> Phase 18 对 recovery_required 仅提供基于持久化事务证据的诊断、核对顺序与禁止操作，不执行自动修复</p>
          <p><b>✓</b> recovery_required 不提供强制覆盖、忽略 SHA、删除备份/隔离副本或拆分批次的“修复”按钮</p>
          <p><b>✓</b> Phase 19 恢复快照只在人工点击时读取事务已知路径；符号链接不会跟随，检测结果不写数据库、不解除冻结</p>
          <p><b>✓</b> Phase 20 仅允许 verified applied/recycled 快照生成元数据核对提案；确认时必须重新匹配完整快照指纹</p>
          <p><b>✓</b> Phase 20 解冻只修正事务/File 元数据，绝不修改用户文件；文件动作仍只能走原 rollback/restore 安全 API</p>
          <p><b>✓</b> Phase 21 证据包只写入 DeskAI generated 沙箱，包含元数据/诊断/快照/SHA/审计，不嵌入 Workspace、备份或隔离副本文件内容</p>
          <p><b>✓</b> Phase 21 不改变 recovery/reconciliation 状态，不执行 rollback/restore，不提供 Agent 导出或任意输出路径</p>
          <p><b>✓</b> Phase 22 多步骤计划的工具风险由本地 PermissionGate 重新计算，模型不能降低风险等级或发明未注册工具</p>
          <p><b>✓</b> Phase 22 现有文件动作只能运行 propose_* 提案工具，并在原人工确认门禁处暂停；Resume 只在原事务状态证明 applied/recycled 后继续</p>
          <p><b>✓</b> Engine 中断不会自动重放正在执行的计划步骤；计划冻结为 paused/interrupted，必须人工 Retry</p>
        </div>
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
  if (page === "files") return "文件解析与预览";
  if (page === "search") return "资料检索与引用";
  if (page === "memory") return "长期记忆与自主学习";
  if (page === "tasks") return "Agent 任务";
  if (page === "recovery") return "统一恢复中心";
  if (page === "activity") return "Agent 活动与审计";
  return "设置";
}

function statusLabel(status: string) {
  const labels: Record<string, string> = {
    pending: "待解析",
    parsed: "已解析",
    indexed: "已索引",
    unsupported: "暂不支持",
    deleted: "已删除",
    recycled: "回收站",
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
