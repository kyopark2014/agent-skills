import { useCallback, useEffect, useRef, useState } from "react";
import { api } from "./api";
import { uiError, uiLog } from "./debug";
import { formatBrandTitle } from "./formatBrandTitle";
import { useChatStream } from "./hooks/useChatStream";
import { randomUUID } from "./randomUUID";
import type { AppConfig, Message, Task } from "./types";
import { Sidebar } from "./components/Sidebar";
import { ChatThread } from "./components/ChatThread";
import { ChatInput } from "./components/ChatInput";
import { UserIdModal } from "./components/UserIdModal";

type DrawerKind = "skill" | "mcp" | "model" | null;

function sortTasks(tasks: Task[]): Task[] {
  return [...tasks].sort((a, b) => {
    if (a.pinned !== b.pinned) return a.pinned ? -1 : 1;
    return new Date(b.updated_at).getTime() - new Date(a.updated_at).getTime();
  });
}

function titleFromPrompt(prompt: string): string {
  return prompt.trim().slice(0, 50) || "New task";
}

/** Keep optimistic `pending-*` ids when server rows match, to avoid remount flicker. */
function stabilizeMessageKeys(prev: Message[], next: Message[]): Message[] {
  if (prev.length === 0) return next;
  const used = new Set<string>();
  return next.map((msg) => {
    const match = prev.find(
      (p) =>
        !used.has(p.id) &&
        p.id.startsWith("pending") &&
        p.role === msg.role &&
        p.content === msg.content,
    );
    if (!match) return msg;
    used.add(match.id);
    return { ...msg, id: match.id };
  });
}

export default function App() {
  const [userId, setUserId] = useState<string | null>(null);
  const [authReady, setAuthReady] = useState(false);
  const [bootError, setBootError] = useState<string | null>(null);
  const [config, setConfig] = useState<AppConfig | null>(null);
  const [tasks, setTasks] = useState<Task[]>([]);
  const [activeTaskId, setActiveTaskId] = useState<string | null>(null);
  const [messages, setMessages] = useState<Message[]>([]);
  const [drawer, setDrawer] = useState<DrawerKind>(null);
  const [sidebarOpen, setSidebarOpen] = useState(false);
  const { getStreamForTask, sendMessage } = useChatStream();
  // Survives React Strict Mode remount so empty-list bootstrap creates only one task.
  const emptyTaskBootstrapRef = useRef<Promise<Task> | null>(null);
  const tasksBootstrappedForUserRef = useRef<string | null>(null);
  const activeTaskIdRef = useRef<string | null>(null);

  const activeTask = tasks.find((t) => t.id === activeTaskId) ?? null;
  const activeStream = getStreamForTask(activeTaskId);

  useEffect(() => {
    activeTaskIdRef.current = activeTaskId;
  }, [activeTaskId]);

  const loadMessages = useCallback(async (taskId: string) => {
    uiLog("messages:load start", { taskId });
    const { messages: rows } = await api.getMessages(taskId);
    uiLog("messages:load complete", { taskId, count: rows.length, roles: rows.map((m) => m.role) });
    setMessages((prev) => stabilizeMessageKeys(prev, rows));
  }, []);

  const refreshTasks = useCallback(async () => {
    const { tasks: rows } = await api.listTasks();
    setTasks(sortTasks(rows));
    return sortTasks(rows);
  }, []);

  const refreshConfig = useCallback(async () => {
    const cfg = await api.getConfig();
    setConfig(cfg);
    return cfg;
  }, []);

  useEffect(() => {
    (async () => {
      try {
        const cfg = await api.getConfig();
        setConfig(cfg);
        const session = await api.getSession();
        const id = session?.user_id?.trim();
        if (id) {
          setUserId(id);
        }
      } catch (err) {
        uiError("boot failed", err);
        setBootError(err instanceof Error ? err.message : String(err));
      } finally {
        setAuthReady(true);
      }
    })();
  }, []);

  useEffect(() => {
    if (!config?.projectName || !userId) return;
    document.title = formatBrandTitle(config.projectName, userId);
  }, [config?.projectName, userId]);

  useEffect(() => {
    if (!userId || !config) return;
    if (tasksBootstrappedForUserRef.current === userId) return;

    let cancelled = false;

    (async () => {
      const rows = await refreshTasks();
      if (cancelled) return;

      if (rows.length === 0) {
        if (!emptyTaskBootstrapRef.current) {
          emptyTaskBootstrapRef.current = (async () => {
            const latest = sortTasks((await api.listTasks()).tasks);
            if (latest.length > 0) return latest[0];
            return api.createTask({
              model_name: config.default_model,
              skills: config.default_skills,
              mcp_servers: config.default_mcp_servers,
              memory_enabled: true,
            });
          })();
        }
        const task = await emptyTaskBootstrapRef.current;
        if (cancelled) return;
        setTasks([task]);
        setActiveTaskId(task.id);
        setMessages([]);
      } else {
        setActiveTaskId(rows[0].id);
        await loadMessages(rows[0].id);
      }
      if (!cancelled) {
        tasksBootstrappedForUserRef.current = userId;
      }
    })();

    return () => {
      cancelled = true;
    };
  }, [userId, config, refreshTasks, loadMessages]);

  useEffect(() => {
    if (activeTaskId) {
      loadMessages(activeTaskId);
    }
  }, [activeTaskId, loadMessages]);

  useEffect(() => {
    if (!sidebarOpen) return;
    function onKeyDown(e: KeyboardEvent) {
      if (e.key === "Escape") setSidebarOpen(false);
    }
    document.body.style.overflow = "hidden";
    window.addEventListener("keydown", onKeyDown);
    return () => {
      document.body.style.overflow = "";
      window.removeEventListener("keydown", onKeyDown);
    };
  }, [sidebarOpen]);

  useEffect(() => {
    function onResize() {
      if (window.innerWidth > 768) setSidebarOpen(false);
    }
    window.addEventListener("resize", onResize);
    return () => window.removeEventListener("resize", onResize);
  }, []);

  async function handleLogin(id: string) {
    setBootError(null);
    try {
      await api.setSession(id);
      setUserId(id.trim());
    } catch (err) {
      setBootError(err instanceof Error ? err.message : String(err));
    }
  }

  async function handleLogout() {
    setBootError(null);
    try {
      await api.clearSession();
    } catch (err) {
      uiError("logout failed", err);
    }
    tasksBootstrappedForUserRef.current = null;
    emptyTaskBootstrapRef.current = null;
    setUserId(null);
    setTasks([]);
    setActiveTaskId(null);
    setMessages([]);
    setDrawer(null);
    if (config?.projectName) {
      document.title = formatBrandTitle(config.projectName);
    }
  }

  async function handleNewTask() {
    if (!config) return;
    const task = await api.createTask({
      model_name: activeTask?.model_name ?? config.default_model,
      skills: activeTask?.skills ?? config.default_skills,
      mcp_servers: activeTask?.mcp_servers ?? config.default_mcp_servers,
      guardrail_enabled: activeTask?.guardrail_enabled ?? false,
      llm_gateway_enabled: activeTask?.llm_gateway_enabled ?? false,
      memory_enabled: activeTask?.memory_enabled ?? true,
    });
    setTasks((prev) => [task, ...prev]);
    setActiveTaskId(task.id);
    setMessages([]);
  }

  async function handleSelectTask(id: string) {
    setActiveTaskId(id);
    setSidebarOpen(false);
    await loadMessages(id);
  }

  async function handlePatchTask(taskId: string, patch: Partial<Task>) {
    const updated = await api.patchTask(taskId, patch);
    setTasks((prev) => sortTasks(prev.map((t) => (t.id === updated.id ? updated : t))));
  }

  async function handleDeleteTask(taskId: string) {
    await api.deleteTask(taskId);
    const rows = await refreshTasks();
    if (activeTaskId !== taskId) return;
    if (rows.length > 0) {
      setActiveTaskId(rows[0].id);
      await loadMessages(rows[0].id);
      return;
    }
    if (!config) return;
    const task = await api.createTask({
      model_name: config.default_model,
      skills: config.default_skills,
      mcp_servers: config.default_mcp_servers,
      memory_enabled: true,
    });
    setTasks([task]);
    setActiveTaskId(task.id);
    setMessages([]);
  }

  async function handleRagUploadComplete(message: string) {
    if (!activeTaskId) return;
    const notice: Message = {
      id: `rag-upload-${randomUUID()}`,
      task_id: activeTaskId,
      role: "assistant",
      content: message,
      images: [],
      tool_events: [],
      created_at: new Date().toISOString(),
    };
    setMessages((prev) => [...prev, notice]);
  }

  async function handleSend(prompt: string, files: string[] = []) {
    if (!activeTaskId) {
      uiError("chat:send skipped — no active task");
      return;
    }

    const taskId = activeTaskId;
    const displayPrompt =
      prompt.trim() ||
      (files.length > 0 ? "첨부한 이미지를 분석해주세요." : "");
    uiLog("chat:handleSend", { taskId, prompt: displayPrompt, files });
    const optimistic: Message = {
      id: `pending-${randomUUID()}`,
      task_id: taskId,
      role: "user",
      content: displayPrompt,
      images: files,
      tool_events: [],
      created_at: new Date().toISOString(),
    };
    setMessages((prev) => [...prev, optimistic]);
    setTasks((prev) =>
      sortTasks(
        prev.map((task) =>
          task.id === taskId && (task.title === "New task" || !task.title)
            ? {
                ...task,
                title: titleFromPrompt(displayPrompt),
                updated_at: new Date().toISOString(),
              }
            : task,
        ),
      ),
    );

    await sendMessage(
      taskId,
      displayPrompt,
      async (final) => {
        // Only update the open thread if the user is still viewing this task.
        if (activeTaskIdRef.current === taskId) {
          if (final && (final.content || final.tool_events.length > 0)) {
            setMessages((prev) => [
              ...prev,
              {
                id: `pending-assistant-${randomUUID()}`,
                task_id: taskId,
                role: "assistant",
                content: final.content,
                images: final.images,
                tool_events: final.tool_events,
                created_at: new Date().toISOString(),
              },
            ]);
          }
          await loadMessages(taskId);
        }
        await refreshTasks();
      },
      files,
    );
  }

  async function handleNewTaskAndCloseSidebar() {
    await handleNewTask();
    setSidebarOpen(false);
  }

  // Wait for session check before showing login — otherwise a saved cookie
  // briefly flashes the User ID modal, then the main app.
  if (!authReady) {
    return <div className="boot-loading">불러오는 중…</div>;
  }

  if (!userId) {
    return (
      <UserIdModal
        onSubmit={handleLogin}
        error={bootError}
        projectName={config?.projectName}
      />
    );
  }

  return (
    <div className={`app-shell${sidebarOpen ? " sidebar-open" : ""}`}>
      {sidebarOpen && (
        <button
          type="button"
          className="sidebar-backdrop"
          aria-label="메뉴 닫기"
          onClick={() => setSidebarOpen(false)}
        />
      )}
      <Sidebar
        userId={userId}
        tasks={tasks}
        activeTask={activeTask}
        config={config}
        drawer={drawer}
        open={sidebarOpen}
        onClose={() => setSidebarOpen(false)}
        onNewTask={handleNewTaskAndCloseSidebar}
        onSelectTask={handleSelectTask}
        onOpenDrawer={setDrawer}
        onCloseDrawer={() => setDrawer(null)}
        onPatchTask={handlePatchTask}
        onDeleteTask={handleDeleteTask}
        onLogout={handleLogout}
        onRefreshConfig={refreshConfig}
      />
      <div className="main-panel">
        <ChatThread
          messages={messages}
          streaming={activeStream.streaming}
          streamText={activeStream.streamText}
          streamEvents={activeStream.streamEvents}
          taskTitle={activeTask?.title ?? "New task"}
          onMenuClick={() => setSidebarOpen(true)}
          footer={
            <ChatInput
              disabled={!activeTask || activeStream.streaming}
              onSend={handleSend}
              onRagUploadComplete={handleRagUploadComplete}
            />
          }
        />
      </div>
    </div>
  );
}
