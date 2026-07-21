import { useEffect, useRef, useState, type MouseEvent } from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import type { Message, ToolEvent } from "../types";
import { ToolCallCard } from "./ToolCallCard";

interface Props {
  role: "user" | "assistant";
  content: string;
  images?: string[];
  toolEvents?: ToolEvent[];
}

interface ContextMenuState {
  x: number;
  y: number;
  markdown: string;
}

function normalizeText(value: string): string {
  return value.trim().replace(/\s+/g, " ");
}

function isStreamingPrefixOfFinal(partial: string, finalText: string): boolean {
  if (!partial || !finalText) return false;
  if (finalText.startsWith(partial) || partial.startsWith(finalText)) return true;
  const headLen = Math.min(partial.length, finalText.length, 80);
  return partial.slice(0, headLen) === finalText.slice(0, headLen);
}

function filterSupersededTextEvents(events: ToolEvent[], content: string): ToolEvent[] {
  const normalizedContent = normalizeText(content);
  const textIndexes = events
    .map((event, index) => (event.type === "text" ? index : -1))
    .filter((index) => index >= 0);
  const hidden = new Set<number>();

  for (let i = 0; i < textIndexes.length; i += 1) {
    const index = textIndexes[i];
    const text = normalizeText(events[index].data ?? "");
    for (let j = i + 1; j < textIndexes.length; j += 1) {
      const laterIndex = textIndexes[j];
      const later = normalizeText(events[laterIndex].data ?? "");
      if (isStreamingPrefixOfFinal(text, later) && text.length < later.length) {
        hidden.add(index);
        break;
      }
    }
    if (
      !hidden.has(index) &&
      normalizedContent &&
      isStreamingPrefixOfFinal(text, normalizedContent) &&
      text.length < normalizedContent.length
    ) {
      hidden.add(index);
    }
  }

  return events.filter((_, index) => !hidden.has(index));
}

function MarkdownText({ content }: { content: string }) {
  return <ReactMarkdown remarkPlugins={[remarkGfm]}>{content}</ReactMarkdown>;
}

async function copyMarkdownToClipboard(markdown: string): Promise<void> {
  if (navigator.clipboard?.writeText) {
    await navigator.clipboard.writeText(markdown);
    return;
  }

  // Fallback for older browsers / non-secure contexts
  const textarea = document.createElement("textarea");
  textarea.value = markdown;
  textarea.setAttribute("readonly", "");
  textarea.style.position = "fixed";
  textarea.style.left = "-9999px";
  document.body.appendChild(textarea);
  textarea.select();
  document.execCommand("copy");
  document.body.removeChild(textarea);
}

function MarkdownContextMenu({
  menu,
  onClose,
}: {
  menu: ContextMenuState;
  onClose: () => void;
}) {
  const menuRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    function onDocMouseDown(e: globalThis.MouseEvent) {
      if (menuRef.current && !menuRef.current.contains(e.target as Node)) {
        onClose();
      }
    }
    function onKeyDown(e: KeyboardEvent) {
      if (e.key === "Escape") onClose();
    }
    function onScroll() {
      onClose();
    }
    document.addEventListener("mousedown", onDocMouseDown);
    document.addEventListener("keydown", onKeyDown);
    window.addEventListener("scroll", onScroll, true);
    return () => {
      document.removeEventListener("mousedown", onDocMouseDown);
      document.removeEventListener("keydown", onKeyDown);
      window.removeEventListener("scroll", onScroll, true);
    };
  }, [onClose]);

  useEffect(() => {
    const el = menuRef.current;
    if (!el) return;
    const rect = el.getBoundingClientRect();
    const pad = 8;
    let left = menu.x;
    let top = menu.y;
    if (left + rect.width > window.innerWidth - pad) {
      left = Math.max(pad, window.innerWidth - rect.width - pad);
    }
    if (top + rect.height > window.innerHeight - pad) {
      top = Math.max(pad, window.innerHeight - rect.height - pad);
    }
    el.style.left = `${left}px`;
    el.style.top = `${top}px`;
  }, [menu.x, menu.y]);

  return (
    <div
      ref={menuRef}
      className="message-context-menu"
      role="menu"
      style={{ left: menu.x, top: menu.y }}
    >
      <button
        type="button"
        role="menuitem"
        onClick={async () => {
          try {
            await copyMarkdownToClipboard(menu.markdown);
          } finally {
            onClose();
          }
        }}
      >
        <svg viewBox="0 0 16 16" aria-hidden="true">
          <path
            fill="currentColor"
            d="M5 2a2 2 0 0 0-2 2v7h1.5V4a.5.5 0 0 1 .5-.5h6V2H5Zm3 3a2 2 0 0 0-2 2v6a2 2 0 0 0 2 2h5a2 2 0 0 0 2-2V7a2 2 0 0 0-2-2H8Zm0 1.5h5a.5.5 0 0 1 .5.5v6a.5.5 0 0 1-.5.5H8a.5.5 0 0 1-.5-.5V7a.5.5 0 0 1 .5-.5Z"
          />
        </svg>
        Copy markdown contents
      </button>
    </div>
  );
}

function MarkdownBubble({ content }: { content: string }) {
  const [menu, setMenu] = useState<ContextMenuState | null>(null);

  function onContextMenu(e: MouseEvent<HTMLDivElement>) {
    if (!content.trim()) return;
    e.preventDefault();
    setMenu({ x: e.clientX, y: e.clientY, markdown: content });
  }

  return (
    <>
      <div className="message-bubble" onContextMenu={onContextMenu}>
        <MarkdownText content={content} />
      </div>
      {menu && <MarkdownContextMenu menu={menu} onClose={() => setMenu(null)} />}
    </>
  );
}

function renderTimelineEvent(
  event: ToolEvent,
  role: "user" | "assistant",
  index: number,
) {
  if (event.type === "text") {
    if (role === "assistant") {
      return <MarkdownBubble key={`text-${index}`} content={event.data ?? ""} />;
    }
    return (
      <div key={`text-${index}`} className="message-bubble">
        {event.data}
      </div>
    );
  }
  return (
    <ToolCallCard key={`${event.type}-${event.toolUseId ?? index}`} event={event} />
  );
}

export function MessageBubble({ role, content, images = [], toolEvents = [] }: Props) {
  const visibleEvents = filterSupersededTextEvents(toolEvents, content);
  const hasTimelineText = visibleEvents.some((event) => event.type === "text");
  const normalizedContent = normalizeText(content);
  const contentCoveredByTimeline = visibleEvents.some(
    (event) => event.type === "text" && normalizeText(event.data ?? "") === normalizedContent,
  );
  const showTrailingContent = normalizedContent.length > 0 && !contentCoveredByTimeline;

  if (hasTimelineText) {
    return (
      <div className={`message-row ${role}`}>
        <div className="message-timeline">
          {visibleEvents.map((event, index) => renderTimelineEvent(event, role, index))}
        </div>
        {showTrailingContent &&
          (role === "assistant" ? (
            <MarkdownBubble content={content} />
          ) : (
            <div className="message-bubble">{content}</div>
          ))}
        {images.length > 0 && (
          <div className="message-images">
            {images.map((url) => (
              <img key={url} src={url} alt="" />
            ))}
          </div>
        )}
      </div>
    );
  }

  return (
    <div className={`message-row ${role}`}>
      {toolEvents.length > 0 && (
        <div className="tool-events">
          {toolEvents.map((event, index) => (
            <ToolCallCard key={`${event.type}-${event.toolUseId ?? index}`} event={event} />
          ))}
        </div>
      )}
      {role === "user" && images.length > 0 && (
        <div className="message-images">
          {images.map((url) => (
            <img key={url} src={url} alt="" />
          ))}
        </div>
      )}
      {content.trim() &&
        (role === "assistant" ? (
          <MarkdownBubble content={content} />
        ) : (
          <div className="message-bubble">{content}</div>
        ))}
      {role !== "user" && images.length > 0 && (
        <div className="message-images">
          {images.map((url) => (
            <img key={url} src={url} alt="" />
          ))}
        </div>
      )}
    </div>
  );
}

export function MessageFromRecord({ message }: { message: Message }) {
  return (
    <MessageBubble
      role={message.role}
      content={message.content}
      images={message.images}
      toolEvents={message.tool_events}
    />
  );
}
