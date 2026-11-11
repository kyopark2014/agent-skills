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

function buildSrcDoc(html: string): string {
  // Keep the app HTML intact; hosts typically inject via sandbox proxy, but
  // srcdoc + sandboxed iframe is enough for our trusted local MCP servers.
  return html;
}

export function McpAppFrame({ app }: Props) {
  const iframeRef = useRef<HTMLIFrameElement>(null);
  const [height, setHeight] = useState(420);
  const [status, setStatus] = useState("UI 연결 중…");
  const initializedRef = useRef(false);
  const pendingToolResultRef = useRef(app.toolResult);

  useEffect(() => {
    pendingToolResultRef.current = app.toolResult;
  }, [app.toolResult]);

  useEffect(() => {
    initializedRef.current = false;
    setStatus("UI 연결 중…");

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
      notify("ui/notifications/tool-result", result);
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
      if (!iframe || event.source !== iframe.contentWindow) return;
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
          hostInfo: { name: "agent-skills", version: "1.0.0" },
          hostContext: {
            theme:
              document.documentElement.getAttribute("data-theme") === "light"
                ? "light"
                : "dark",
            platform: "web",
            displayMode: "inline",
            availableDisplayModes: ["inline"],
            containerDimensions: { maxHeight: 720, width: iframe.clientWidth || 640 },
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

      // Ignore other notifications/requests for now.
      if (data.id !== undefined && data.method) {
        replyError(data.id, `Unsupported method: ${data.method}`);
      }
    }

    window.addEventListener("message", onMessage);
    return () => window.removeEventListener("message", onMessage);
  }, [app.html, app.mcpServer, app.tool, app.resourceUri]);

  return (
    <div className="mcp-app-frame">
      {status ? <div className="mcp-app-status">{status}</div> : null}
      <iframe
        ref={iframeRef}
        className="mcp-app-iframe"
        title={app.resourceUri || "MCP App"}
        sandbox="allow-scripts allow-forms allow-popups allow-popups-to-escape-sandbox"
        srcDoc={buildSrcDoc(app.html)}
        style={{ height }}
      />
    </div>
  );
}
