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

function renderTimelineEvent(
  event: ToolEvent,
  role: "user" | "assistant",
  index: number,
) {
  if (event.type === "text") {
    return (
      <div key={`text-${index}`} className="message-bubble">
        {role === "assistant" ? (
          <MarkdownText content={event.data ?? ""} />
        ) : (
          event.data
        )}
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
        {showTrailingContent && (
          <div className="message-bubble">
            {role === "assistant" ? <MarkdownText content={content} /> : content}
          </div>
        )}
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
      {content.trim() && (
        <div className="message-bubble">
          {role === "assistant" ? <MarkdownText content={content} /> : content}
        </div>
      )}
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
