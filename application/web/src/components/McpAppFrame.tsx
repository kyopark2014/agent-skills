import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import type { McpAppPayload } from "../types";
import {
  buildAppCsp,
  buildStyleVariables,
  currentTheme,
  fetchMcpAppResource,
  injectCspMeta,
  type AppResource,
} from "../mcpAppHost";

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

type DisplayMode = "inline" | "fullscreen";

const PROTOCOL_VERSION = "2026-01-26";
const INLINE_MAX_HEIGHT = 720;
const HOST_MODES: DisplayMode[] = ["inline", "fullscreen"];

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
  setTimeout(deliver, 0);
  setTimeout(deliver, 50);
})();</script>`;
  const lower = html.toLowerCase();
  const idx = lower.lastIndexOf("</body>");
  if (idx >= 0) return html.slice(0, idx) + boot + html.slice(idx);
  return html + boot;
}

/** The chat column minus the composer: where a fullscreen app is laid out. */
function fullscreenInsets() {
  const main = document.querySelector(".main-panel")?.getBoundingClientRect();
  const composer = document.querySelector(".chat-input-area")?.getBoundingClientRect();
  return {
    top: main?.top ?? 0,
    left: main?.left ?? 0,
    right: main ? Math.max(0, window.innerWidth - main.right) : 0,
    bottom: composer ? Math.max(0, window.innerHeight - composer.top) : 0,
  };
}

export function McpAppFrame({ app }: Props) {
  const iframeRef = useRef<HTMLIFrameElement>(null);
  const closeRef = useRef<HTMLButtonElement>(null);
  const [resource, setResource] = useState<AppResource | null>(() =>
    app.html
      ? {
          html: app.html,
          mimeType: app.mimeType,
          meta: {
            csp: (app.csp as AppResource["meta"]["csp"]) ?? undefined,
            prefersBorder: app.prefersBorder,
          },
        }
      : null,
  );
  const [loadError, setLoadError] = useState<string | null>(null);
  const [mode, setMode] = useState<DisplayMode>("inline");
  const [height, setHeight] = useState(0);
  const [booted, setBooted] = useState(false);
  const [status, setStatus] = useState("");
  const [fsRect, setFsRect] = useState(fullscreenInsets);
  const [canExpand, setCanExpand] = useState(true);
  const [theme, setTheme] = useState(currentTheme);

  const appRef = useRef(app);
  appRef.current = app;

  const initializedRef = useRef(false);
  const pendingToolResultRef = useRef(app.toolResult);
  const modeRef = useRef<DisplayMode>("inline");
  modeRef.current = mode;

  useEffect(() => {
    if (app.html) {
      setResource({
        html: app.html,
        mimeType: app.mimeType,
        meta: {
          csp: (app.csp as AppResource["meta"]["csp"]) ?? undefined,
          prefersBorder: app.prefersBorder,
        },
      });
      setLoadError(null);
      return;
    }
    if (!app.mcpServer || !app.resourceUri) {
      setLoadError("MCP App 리소스를 알 수 없습니다.");
      return;
    }
    let cancelled = false;
    setLoadError(null);
    fetchMcpAppResource(app.mcpServer, app.resourceUri)
      .then((r) => !cancelled && setResource(r))
      .catch((e) => !cancelled && setLoadError(e instanceof Error ? e.message : String(e)));
    return () => {
      cancelled = true;
    };
  }, [app.html, app.mcpServer, app.resourceUri, app.csp, app.mimeType, app.prefersBorder]);

  const srcDoc = useMemo(() => {
    if (!resource) return undefined;
    const policy = buildAppCsp(resource.meta?.csp);
    return buildSrcDoc(injectCspMeta(resource.html, policy), app.toolResult);
  }, [resource, app.toolResult]);

  const prefersBorder = resource?.meta?.prefersBorder === true;

  const post = useCallback((message: Record<string, unknown>) => {
    iframeRef.current?.contentWindow?.postMessage({ jsonrpc: "2.0", ...message }, "*");
  }, []);

  const containerDimensions = useCallback((m: DisplayMode) => {
    const el = iframeRef.current;
    const width = el?.clientWidth || 640;
    return m === "fullscreen"
      ? { width, height: el?.clientHeight || window.innerHeight }
      : { width, maxHeight: INLINE_MAX_HEIGHT };
  }, []);

  const hostContext = useCallback(
    (m: DisplayMode) => ({
      theme: currentTheme(),
      styles: { variables: buildStyleVariables() },
      platform: "web",
      displayMode: m,
      availableDisplayModes: HOST_MODES,
      containerDimensions: containerDimensions(m),
      safeAreaInsets: { top: 0, right: 0, bottom: 0, left: 0 },
      locale: navigator.language,
    }),
    [containerDimensions],
  );

  const changeMode = useCallback(
    (next: DisplayMode) => {
      if (modeRef.current === next) return;
      modeRef.current = next;
      setMode(next);
      if (next === "fullscreen") setFsRect(fullscreenInsets());
      requestAnimationFrame(() =>
        requestAnimationFrame(() => {
          if (!initializedRef.current) return;
          post({
            method: "ui/notifications/host-context-changed",
            params: { displayMode: next, containerDimensions: containerDimensions(next) },
          });
        }),
      );
    },
    [containerDimensions, post],
  );

  useEffect(() => {
    const obs = new MutationObserver(() => {
      setTheme(currentTheme());
      if (!initializedRef.current) return;
      post({
        method: "ui/notifications/host-context-changed",
        params: { theme: currentTheme(), styles: { variables: buildStyleVariables() } },
      });
    });
    obs.observe(document.documentElement, { attributes: true, attributeFilter: ["data-theme"] });
    return () => obs.disconnect();
  }, [post]);

  useEffect(() => {
    if (mode !== "fullscreen") return;
    closeRef.current?.focus();
    const relayout = () => setFsRect(fullscreenInsets());
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && changeMode("inline");
    window.addEventListener("resize", relayout);
    window.addEventListener("keydown", onKey);
    const composer = document.querySelector(".chat-input-area");
    const ro =
      composer && typeof ResizeObserver !== "undefined"
        ? new ResizeObserver(relayout)
        : null;
    ro?.observe(composer as Element);
    relayout();
    return () => {
      window.removeEventListener("resize", relayout);
      window.removeEventListener("keydown", onKey);
      ro?.disconnect();
    };
  }, [mode, changeMode]);

  useEffect(() => {
    const el = iframeRef.current;
    if (!el || typeof ResizeObserver === "undefined") return;
    const ro = new ResizeObserver(() => {
      if (!initializedRef.current) return;
      post({
        method: "ui/notifications/host-context-changed",
        params: { containerDimensions: containerDimensions(modeRef.current) },
      });
    });
    ro.observe(el);
    return () => ro.disconnect();
  }, [srcDoc, post, containerDimensions]);

  useEffect(() => {
    pendingToolResultRef.current = app.toolResult;
    if (!initializedRef.current || !app.toolResult) return;
    post({ method: "ui/notifications/tool-result", params: app.toolResult });
    setStatus("");
  }, [app.toolResult, post]);

  useEffect(() => {
    if (!srcDoc) return;
    initializedRef.current = false;
    setBooted(false);
    setHeight(0);

    function reply(id: string | number | undefined, result: unknown) {
      if (id === undefined) return;
      post({ id, result });
    }
    function replyError(id: string | number | undefined, message: string) {
      if (id === undefined) return;
      post({ id, error: { code: -32000, message } });
    }

    async function handleToolsCall(
      id: string | number | undefined,
      params: Record<string, unknown> | undefined,
    ) {
      const name = typeof params?.name === "string" ? params.name : appRef.current.tool;
      const args =
        params?.arguments && typeof params.arguments === "object"
          ? (params.arguments as Record<string, unknown>)
          : {};
      const mcpServer = appRef.current.mcpServer;
      if (!mcpServer || !name) {
        replyError(id, "MCP server/tool is not available for this app");
        return;
      }
      try {
        setStatus("");
        const res = await fetch("/api/mcp-apps/call-tool", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          credentials: "include",
          body: JSON.stringify({ mcpServer, tool: name, arguments: args }),
        });
        const data = await res.json();
        if (!res.ok) throw new Error(data?.detail || `HTTP ${res.status}`);
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
      if (event.source != null && event.source !== iframe.contentWindow) return;
      const data = event.data as JsonRpcMessage;
      if (!data || data.jsonrpc !== "2.0") return;

      switch (data.method) {
        case "ui/initialize": {
          const modes = (data.params?.appCapabilities as { availableDisplayModes?: string[] } | undefined)
            ?.availableDisplayModes;
          setCanExpand(!modes || modes.includes("fullscreen"));
          reply(data.id, {
            protocolVersion: PROTOCOL_VERSION,
            hostCapabilities: { openLinks: {}, serverTools: {}, logging: {} },
            hostInfo: { name: "agent-skills", version: "1.0.0" },
            hostContext: hostContext(modeRef.current),
          });
          return;
        }
        case "ui/notifications/initialized": {
          initializedRef.current = true;
          const toolInput = appRef.current.toolInput;
          if (toolInput) {
            post({ method: "ui/notifications/tool-input", params: { arguments: toolInput } });
          }
          const result = pendingToolResultRef.current;
          if (result) {
            post({ method: "ui/notifications/tool-result", params: result });
            setStatus("");
          }
          return;
        }
        case "ui/notifications/size-changed": {
          setBooted(true);
          const next = Number(data.params?.height);
          if (modeRef.current === "inline" && Number.isFinite(next) && next > 0) {
            setHeight(Math.min(Math.ceil(next) + 2, INLINE_MAX_HEIGHT * 1.25));
          }
          return;
        }
        case "ui/open-link": {
          const url = data.params?.url;
          if (typeof url === "string" && /^https?:\/\//i.test(url)) {
            window.open(url, "_blank", "noopener,noreferrer");
            reply(data.id, {});
          } else {
            replyError(data.id, "Invalid URL");
          }
          return;
        }
        case "ui/request-display-mode": {
          const wanted = data.params?.mode;
          if (wanted === "inline" || wanted === "fullscreen") {
            changeMode(wanted);
            reply(data.id, { mode: wanted });
          } else {
            replyError(data.id, `Invalid display mode: ${String(wanted)}`);
          }
          return;
        }
        case "ui/update-model-context":
        case "ui/message":
          reply(data.id, {});
          return;
        case "ping":
          reply(data.id, {});
          return;
        case "tools/call":
          void handleToolsCall(data.id, data.params);
          return;
        default:
          if (data.id !== undefined && data.method) {
            replyError(data.id, `Unsupported method: ${data.method}`);
          }
      }
    }

    window.addEventListener("message", onMessage);
    const t = window.setTimeout(() => setBooted(true), 8000);
    return () => {
      window.removeEventListener("message", onMessage);
      window.clearTimeout(t);
    };
  }, [srcDoc, hostContext, changeMode, post]);

  const isFullscreen = mode === "fullscreen";
  const frameStyle = isFullscreen
    ? { top: fsRect.top, left: fsRect.left, right: fsRect.right, bottom: fsRect.bottom }
    : undefined;
  const iframeHeight = isFullscreen ? "100%" : height > 0 ? height : 160;

  return (
    <div className={`mcp-app-slot${isFullscreen ? " is-fullscreen" : ""}`}>
      <div
        className={`mcp-app-frame${prefersBorder ? " has-border" : ""}${isFullscreen ? " is-fullscreen" : ""}`}
        style={frameStyle}
        role={isFullscreen ? "dialog" : undefined}
        aria-label={isFullscreen ? app.tool : undefined}
      >
        {isFullscreen ? (
          <div className="mcp-app-titlebar">
            <span className="mcp-app-title">{app.tool}</span>
            <button ref={closeRef} type="button" onClick={() => changeMode("inline")} aria-label="전체 화면 닫기">
              닫기
            </button>
          </div>
        ) : canExpand && booted && !loadError ? (
          <div className="mcp-app-bar">
            <button
              type="button"
              className="mcp-app-expand"
              onClick={() => changeMode("fullscreen")}
              aria-label="전체 화면으로 보기"
              title="전체 화면"
            >
              ⤢
            </button>
          </div>
        ) : null}
        {loadError ? (
          <div className="mcp-app-error">UI를 불러오지 못했습니다: {loadError}</div>
        ) : null}
        {!booted && !loadError ? <div className="mcp-app-skeleton" aria-hidden="true" /> : null}
        {status ? <div className="mcp-app-status">{status}</div> : null}
        {srcDoc ? (
          <iframe
            ref={iframeRef}
            className="mcp-app-iframe"
            title={app.resourceUri || "MCP App"}
            sandbox="allow-scripts allow-same-origin allow-forms allow-popups allow-popups-to-escape-sandbox"
            srcDoc={srcDoc}
            style={{ height: iframeHeight, colorScheme: theme }}
          />
        ) : null}
      </div>
      {isFullscreen ? <div className="mcp-app-placeholder" style={{ height: 56 }} /> : null}
    </div>
  );
}
