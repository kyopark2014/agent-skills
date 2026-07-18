import type { ToolEvent } from "../types";

interface Props {
  event: ToolEvent;
}

function formatToolInput(input: unknown): string {
  if (input === undefined || input === null) {
    return "(매개변수 없음)";
  }
  if (typeof input === "object" && !Array.isArray(input)) {
    const keys = Object.keys(input as Record<string, unknown>);
    if (keys.length === 0) {
      return "(매개변수 없음 — 기본값 사용)";
    }
  }
  return JSON.stringify(input, null, 2);
}

export function ToolCallCard({ event }: Props) {
  if (event.type === "tool") {
    return (
      <details className="tool-card">
        <summary>Tool: {event.tool}</summary>
        <pre>{formatToolInput(event.input)}</pre>
      </details>
    );
  }
  if (event.type === "tool_result") {
    const label = event.tool ? `Tool result: ${event.tool}` : "Tool result";
    return (
      <details className="tool-card">
        <summary>{label}</summary>
        <pre>{event.data}</pre>
      </details>
    );
  }
  return (
    <details className="tool-card">
      <summary>Info</summary>
      <pre>{event.data}</pre>
    </details>
  );
}
