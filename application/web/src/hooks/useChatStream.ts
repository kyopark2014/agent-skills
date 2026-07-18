import { useCallback, useRef, useState } from "react";
import type { ToolEvent } from "../types";
import { api } from "../api";
import { uiError, uiLog, uiWarn } from "../debug";

const TOOL_INPUT_INFO_RE = /^Tool: .+?, Input:/s;
const TOOL_RESULT_INFO_RE = /^Tool Result: /s;

function upsertToolEvent(prev: ToolEvent[], event: ToolEvent): ToolEvent[] {
  if (event.type === "info") {
    const data = event.data ?? "";
    if (TOOL_INPUT_INFO_RE.test(data) || TOOL_RESULT_INFO_RE.test(data)) {
      return prev;
    }
  }
  if (event.type === "tool" || event.type === "tool_result") {
    const idx = prev.findIndex(
      (e) => e.type === event.type && e.toolUseId === event.toolUseId,
    );
    if (idx >= 0) {
      const next = [...prev];
      next[idx] = event;
      return next;
    }
    if (event.type === "tool" && event.tool) {
      const byName = prev.findIndex(
        (e) => e.type === "tool" && e.tool === event.tool,
      );
      if (byName >= 0) {
        const next = [...prev];
        next[byName] =
          event.toolUseId && event.toolUseId !== event.tool
            ? event
            : { ...next[byName], ...event };
        return next;
      }
    }
  }
  return [...prev, event];
}

function appendTextSegment(prev: ToolEvent[], text: string): ToolEvent[] {
  const trimmed = text.trim();
  if (!trimmed) return prev;
  const last = prev[prev.length - 1];
  if (last?.type === "text" && last.data === trimmed) return prev;
  return [...prev, { type: "text", data: trimmed }];
}

function isSegmentReset(previous: string, next: string): boolean {
  if (!previous.trim()) return false;
  if (!next) return true;
  return !next.startsWith(previous);
}

export interface ChatFinalMessage {
  content: string;
  images: string[];
  tool_events: ToolEvent[];
}

interface TaskStreamState {
  text: string;
  events: ToolEvent[];
}

const EMPTY_STREAM: TaskStreamState = { text: "", events: [] };

export function useChatStream() {
  const [streamsByTaskId, setStreamsByTaskId] = useState<
    Record<string, TaskStreamState>
  >({});
  const streamTextRefs = useRef<Record<string, string>>({});
  const streamingTaskIdsRef = useRef<Set<string>>(new Set());

  const patchStream = useCallback(
    (taskId: string, updater: (prev: TaskStreamState) => TaskStreamState) => {
      setStreamsByTaskId((prev) => {
        const current = prev[taskId] ?? EMPTY_STREAM;
        return { ...prev, [taskId]: updater(current) };
      });
    },
    [],
  );

  const sendMessage = useCallback(
    async (
      taskId: string,
      prompt: string,
      onDone: (final?: ChatFinalMessage) => void | Promise<void>,
      files: string[] = [],
    ) => {
      if (streamingTaskIdsRef.current.has(taskId)) {
        uiWarn("chat:send skipped — task already streaming", { taskId });
        return;
      }

      uiLog("chat:send start", { taskId, prompt, files });
      streamingTaskIdsRef.current.add(taskId);
      streamTextRefs.current[taskId] = "";
      setStreamsByTaskId((prev) => ({
        ...prev,
        [taskId]: { text: "", events: [] },
      }));

      let finalMessage: ChatFinalMessage | undefined;

      const flushTextSegment = () => {
        const text = (streamTextRefs.current[taskId] ?? "").trim();
        if (!text) return;
        patchStream(taskId, (s) => ({
          ...s,
          text: "",
          events: appendTextSegment(s.events, text),
        }));
        streamTextRefs.current[taskId] = "";
      };

      const teardownStreaming = () => {
        streamingTaskIdsRef.current.delete(taskId);
        delete streamTextRefs.current[taskId];
        setStreamsByTaskId((prev) => {
          if (!(taskId in prev)) return prev;
          const next = { ...prev };
          delete next[taskId];
          return next;
        });
      };

      try {
        for await (const event of api.streamChat(taskId, prompt, files)) {
          if (event.type === "token" && event.data !== undefined) {
            const previous = streamTextRefs.current[taskId] ?? "";
            const next = event.data;
            if (isSegmentReset(previous, next)) {
              flushTextSegment();
            }
            streamTextRefs.current[taskId] = next;
            patchStream(taskId, (s) => ({ ...s, text: next }));
          } else if (event.type === "text" && event.data) {
            patchStream(taskId, (s) => ({
              ...s,
              text: "",
              events: appendTextSegment(s.events, event.data!),
            }));
            streamTextRefs.current[taskId] = "";
          } else if (event.type === "tool") {
            flushTextSegment();
            patchStream(taskId, (s) => ({
              ...s,
              events: upsertToolEvent(s.events, event as ToolEvent),
            }));
          } else if (event.type === "tool_result" || event.type === "info") {
            patchStream(taskId, (s) => ({
              ...s,
              events: upsertToolEvent(s.events, event as ToolEvent),
            }));
          } else if (event.type === "error") {
            const msg = event.data ?? "Unknown error";
            uiError("chat:send stream error", msg);
            finalMessage = {
              content: msg.startsWith("Error:") ? msg : `Error: ${msg}`,
              images: [],
              tool_events: [],
            };
          } else if (event.type === "done") {
            uiLog("chat:send done event", {
              contentLength: event.content?.length ?? 0,
              images: event.images?.length ?? 0,
              toolEvents: event.tool_events?.length ?? 0,
            });
            finalMessage = {
              content: event.content ?? "",
              images: event.images ?? [],
              tool_events: event.tool_events ?? [],
            };
          }
        }

        if (!finalMessage) {
          const partial = (streamTextRefs.current[taskId] ?? "").trim();
          uiError("chat:send stream closed before done", {
            partialLength: partial.length,
          });
          finalMessage = {
            content: partial
              ? `${partial}\n\nError: Connection closed before the response completed. Try again or refresh messages.`
              : "Error: Connection closed before the response completed. The agent may still be running — refresh or try again.",
            images: [],
            tool_events: [],
          };
        }
      } catch (err) {
        uiError("chat:send failed", err);
        finalMessage = {
          content: `Error: ${err instanceof Error ? err.message : String(err)}`,
          images: [],
          tool_events: [],
        };
      } finally {
        // Call onDone first so setMessages is scheduled in this same turn,
        // then tear down streaming — React 18 batches both into one commit
        // and avoids an empty frame between stream UI and the final bubble.
        let refresh: void | Promise<void> | undefined = undefined;
        try {
          uiLog("chat:send refreshing messages");
          refresh = onDone(finalMessage);
        } catch (err) {
          uiWarn("chat:send refresh failed", err);
        } finally {
          teardownStreaming();
        }
        try {
          await refresh;
          uiLog("chat:send refresh complete");
        } catch (err) {
          uiWarn("chat:send refresh failed", err);
        } finally {
          uiLog("chat:send finished", { taskId });
        }
      }
    },
    [patchStream],
  );

  const getStreamForTask = useCallback(
    (taskId: string | null) => {
      if (!taskId) {
        return { streaming: false, streamText: "", streamEvents: [] as ToolEvent[] };
      }
      const stream = streamsByTaskId[taskId];
      if (!stream) {
        return { streaming: false, streamText: "", streamEvents: [] as ToolEvent[] };
      }
      return {
        streaming: true,
        streamText: stream.text,
        streamEvents: stream.events,
      };
    },
    [streamsByTaskId],
  );

  return { getStreamForTask, sendMessage };
}
