import { useEffect, useRef, useState } from "react";
import type { McpAppPayload } from "../types";

interface Props {
  app: McpAppPayload;
}

type JsonRpcMessage = {
  jsonrpc?: string;
  id?: string | number;
  method?: string;
  params?: Record<string, unknown>;
  result?: unknown;
  error?: { code: number; message: string; data?: unknown };
};

const PROTOCOL_VERSION = "2026-01-26";

/**
 * Embed the initial tool result in the document so the app can render even when
 * the host↔iframe postMessage handshake fails (common with sandboxed srcdoc).
 */
function buildSrcDoc(
  html: string,
  toolResult?: McpAppPayload["toolResult"],
): string {
  if (!toolResult) return html;
  const payload = JSON.stringify(toolResult).replace(/</g, "\\u003c");
  const boot = `<script>(function(){
  var result = ${payload};
  window.__MCP_HOST_BOOTSTRAP__ = { toolResult: result };
  function deliver(){
    try {
      if (typeof applyResult === "function") {
        applyResult(result);
        return;
      }
    } catch (err) {}
    try {
      window.dispatchEvent(new MessageEvent("message", {
        data: {
          jsonrpc: "2.0",
          method: "ui/notifications/tool-result",
          params: result
        }
      }));
    } catch (err) {}
  }
  // Run after the app script has registered applyResult / message listener.
  setTimeout(deliver, 0);
  setTimeout(deliver, 50);
})();</script>`;
  const lower = html.toLowerCase();
  const idx = lower.lastIndexOf("</body>");
  if (idx >= 0) return html.slice(0, idx) + boot + html.slice(idx);
  return html + boot;
}

export function McpAppFrame({ app }: Props) {
  const iframeRef = useRef<HTMLIFrameElement>(null);
  const [height, setHeight] = useState(420);
  const [status, setStatus] = useState("UI 연결 중…");
  const [srcDoc, setSrcDoc] = useState<string | undefined>(undefined);
  const initializedRef = useRef(false);
  const pendingToolResultRef = useRef(app.toolResult);
  const hostInfoName = "agent-skills";

  useEffect(() => {
    pendingToolResultRef.current = app.toolResult;
    if (!initializedRef.current || !app.toolResult) return;
    const win = iframeRef.current?.contentWindow;
    if (!win) return;
    win.postMessage(
      {
        jsonrpc: "2.0",
        method: "ui/notifications/tool-result",
        params: app.toolResult,
      },
      "*",
    );
    setStatus("");
  }, [app.toolResult]);

  useEffect(() => {
    initializedRef.current = false;
    setStatus(app.toolResult ? "" : "UI 연결 중…");
    setSrcDoc(undefined);

    function reply(id: string | number | undefined, result: unknown) {
      const win = iframeRef.current?.contentWindow;
      if (!win || id === undefined) return;
      win.postMessage({ jsonrpc: "2.0", id, result }, "*");
    }

    function replyError(id: string | number | undefined, message: string) {
      const win = iframeRef.current?.contentWindow;
      if (!win || id === undefined) return;
      win.postMessage(
        {
          jsonrpc: "2.0",
          id,
          error: { code: -32000, message },
        },
        "*",
      );
    }

    function notify(method: string, params: Record<string, unknown>) {
      const win = iframeRef.current?.contentWindow;
      if (!win) return;
      win.postMessage({ jsonrpc: "2.0", method, params }, "*");
    }

    function sendToolResult() {
      const result = pendingToolResultRef.current;
      if (!result) return;
      notify("ui/notifications/tool-result", result as Record<string, unknown>);
      setStatus("");
    }

    async function handleToolsCall(
      id: string | number | undefined,
      params: Record<string, unknown> | undefined,
    ) {
      const name = typeof params?.name === "string" ? params.name : app.tool;
      const args =
        params?.arguments && typeof params.arguments === "object"
          ? (params.arguments as Record<string, unknown>)
          : {};
      const mcpServer = app.mcpServer;
      if (!mcpServer || !name) {
        replyError(id, "MCP server/tool is not available for this app");
        return;
      }
      try {
        setStatus("도구 호출 중…");
        const res = await fetch("/api/mcp-apps/call-tool", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          credentials: "include",
          body: JSON.stringify({
            mcpServer,
            tool: name,
            arguments: args,
          }),
        });
        const data = await res.json();
        if (!res.ok) {
          throw new Error(data?.detail || `HTTP ${res.status}`);
        }
        pendingToolResultRef.current = data;
        reply(id, data);
        setStatus("");
      } catch (err) {
        const message = err instanceof Error ? err.message : String(err);
        setStatus(`도구 호출 실패: ${message}`);
        replyError(id, message);
      }
    }

    function onMessage(event: MessageEvent) {
      const iframe = iframeRef.current;
      if (!iframe) return;
      // Sandboxed srcdoc frames sometimes report event.source as null.
      // Reject only when we can positively identify a different window.
      if (event.source != null && event.source !== iframe.contentWindow) return;
      const data = event.data as JsonRpcMessage;
      if (!data || data.jsonrpc !== "2.0") return;

      if (data.method === "ui/initialize") {
        reply(data.id, {
          protocolVersion: PROTOCOL_VERSION,
          hostCapabilities: {
            openLinks: {},
            serverTools: {},
            logging: {},
          },
          hostInfo: { name: hostInfoName, version: "1.0.0" },
          hostContext: {
            theme:
              document.documentElement.getAttribute("data-theme") === "light"
                ? "light"
                : "dark",
            platform: "web",
            displayMode: "inline",
            availableDisplayModes: ["inline"],
            containerDimensions: {
              maxHeight: 720,
              width: iframe.clientWidth || 640,
            },
          },
        });
        return;
      }

      if (data.method === "ui/notifications/initialized") {
        initializedRef.current = true;
        sendToolResult();
        return;
      }

      if (data.method === "ui/notifications/size-changed") {
        const next = Number(data.params?.height);
        if (Number.isFinite(next) && next > 120) {
          setHeight(Math.min(Math.ceil(next) + 8, 900));
        }
        return;
      }

      if (data.method === "ui/open-link") {
        const url = data.params?.url;
        if (typeof url === "string" && /^https?:\/\//i.test(url)) {
          window.open(url, "_blank", "noopener,noreferrer");
          reply(data.id, {});
        } else {
          replyError(data.id, "Invalid URL");
        }
        return;
      }

      if (data.method === "tools/call") {
        void handleToolsCall(data.id, data.params);
        return;
      }

      if (data.id !== undefined && data.method) {
        replyError(data.id, `Unsupported method: ${data.method}`);
      }
    }

    window.addEventListener("message", onMessage);
    setSrcDoc(buildSrcDoc(app.html, app.toolResult));
    return () => {
      window.removeEventListener("message", onMessage);
      setSrcDoc(undefined);
    };
  }, [app.html, app.mcpServer, app.tool, app.resourceUri, app.toolResult]);

  return (
    <div className="mcp-app-frame">
      {status ? <div className="mcp-app-status">{status}</div> : null}
      <iframe
        ref={iframeRef}
        className="mcp-app-iframe"
        title={app.resourceUri || "MCP App"}
        sandbox="allow-scripts allow-same-origin allow-forms allow-popups allow-popups-to-escape-sandbox"
        srcDoc={srcDoc}
        style={{ height }}
      />
    </div>
  );
}
