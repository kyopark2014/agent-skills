import { randomUUID } from "../randomUUID";
import type { Message, TaskRun, ToolEvent } from "../types";

/** Keep optimistic `pending-*` ids when server rows match, to avoid remount flicker. */
export function stabilizeMessageKeys(prev: Message[], next: Message[]): Message[] {
  if (prev.length === 0) return next;
  const used = new Set<string>();
  return next.map((msg) => {
    const match = prev.find(
      (prevMsg) =>
        !used.has(prevMsg.id) &&
        prevMsg.id.startsWith("pending") &&
        prevMsg.role === msg.role &&
        prevMsg.content === msg.content,
    );
    if (!match) return msg;
    used.add(match.id);
    return { ...msg, id: match.id };
  });
}

export function buildDisplayPrompt(prompt: string, files: string[]): string {
  if (prompt.trim()) return prompt.trim();
  if (files.length === 0) return "";
  const hasLocalPath = files.some(
    (f) => f.startsWith("/") && !f.startsWith("/api/") && !/^https?:\/\//i.test(f),
  );
  const hasHttpImage = files.some((f) => /^https?:\/\//i.test(f));
  if (hasLocalPath && !hasHttpImage) {
    return "첨부한 파일을 분석해주세요.";
  }
  return "첨부한 이미지를 분석해주세요.";
}

export function buildOptimisticUserMessage(
  taskId: string,
  content: string,
  files: string[],
): Message {
  return {
    id: `pending-${randomUUID()}`,
    task_id: taskId,
    role: "user",
    content,
    images: files,
    tool_events: [],
    created_at: new Date().toISOString(),
  };
}

export function buildRagUploadNotice(taskId: string, message: string): Message {
  return {
    id: `rag-upload-${randomUUID()}`,
    task_id: taskId,
    role: "assistant",
    content: message,
    images: [],
    tool_events: [],
    created_at: new Date().toISOString(),
  };
}

export function buildPendingAssistantMessage(
  taskId: string,
  content: string,
  images: string[],
  toolEvents: Message["tool_events"],
): Message {
  return {
    id: `pending-assistant-${randomUUID()}`,
    task_id: taskId,
    role: "assistant",
    content,
    images,
    tool_events: toolEvents,
    created_at: new Date().toISOString(),
  };
}

export function shouldAppendAssistantMessage(
  final: {
    content?: string;
    tool_events: Message["tool_events"];
  } | null | undefined,
): boolean {
  return Boolean(final && (final.content || final.tool_events.length > 0));
}

/**
 * Merge a live /run draft into the open thread without re-fetching /messages.
 * Used during refresh recovery while the agent is still running.
 */
export function applyRunDraftToMessages(
  prev: Message[],
  taskId: string,
  run: Pick<TaskRun, "content" | "images" | "tool_events">,
): Message[] {
  const content = run.content || "";
  const images = run.images || [];
  const toolEvents: ToolEvent[] = run.tool_events || [];
  if (!content && images.length === 0 && toolEvents.length === 0) {
    return prev;
  }

  const last = prev[prev.length - 1];
  if (last?.role === "assistant" && last.status === "streaming") {
    return [
      ...prev.slice(0, -1),
      {
        ...last,
        content,
        images,
        tool_events: toolEvents,
        status: "streaming",
      },
    ];
  }

  if (last?.role === "user") {
    return [
      ...prev,
      {
        id: `recovery-assistant-${taskId}`,
        task_id: taskId,
        role: "assistant",
        content,
        images,
        tool_events: toolEvents,
        status: "streaming",
        created_at: new Date().toISOString(),
      },
    ];
  }

  return prev;
}
