import { useState } from "react";

interface Props {
  /** Display name, e.g. "Notion" or "Google Drive". */
  label: string;
  /** MCP name used in status hints, e.g. "notion-remote" / "drive". */
  mcpName: string;
  /** Short label on the connect button (defaults to `label`). Drive uses "Drive". */
  connectLabel?: string;
  connected: boolean;
  fresh: boolean;
  /** Present only for providers with a second (generate/edit) scope tier. */
  write?: boolean;
  supportsWrite?: boolean;
  busy: boolean;
  error: string | null;
  onConnect: (write: boolean) => void;
  onDisconnect: () => void;
  /**
   * Manual paste-back flow (loopback-only OAuth redirects). Unused while Canva is disabled.
   */
  manualPending?: boolean;
  onSubmitManual?: (url: string) => void;
  onCancelManual?: () => void;
}

/** Connection status + connect/disconnect buttons shown under the MCP drawer. */
export function OAuthMcpFooter({
  label,
  mcpName,
  connectLabel,
  connected,
  fresh,
  write,
  supportsWrite,
  busy,
  error,
  onConnect,
  onDisconnect,
  manualPending,
  onSubmitManual,
  onCancelManual,
}: Props) {
  const [pasted, setPasted] = useState("");
  const buttonLabel = connectLabel || label;
  // Keep the same "{mcp} MCP …" sentence shape for connected / expired / needed.
  const status = connected
    ? fresh
      ? `${mcpName} MCP 연결됨${supportsWrite ? (write ? " (읽기+쓰기)" : " (읽기 전용)") : ""}`
      : `${mcpName} MCP 토큰 만료 — 다시 연결하세요`
    : `${mcpName} MCP 사용 전 ${label} 연결 필요`;

  return (
    <div>
      <div className="config-popover-footer-status">{status}</div>
      <div className="config-popover-footer-actions">
        {!connected || !fresh ? (
          <button type="button" disabled={busy} onClick={() => onConnect(false)}>
            {busy ? "연결 중…" : connected ? "다시 연결" : `${buttonLabel} 연결`}
          </button>
        ) : null}
        {supportsWrite && connected && !write ? (
          <button type="button" disabled={busy} onClick={() => onConnect(true)}>
            쓰기 권한 추가
          </button>
        ) : null}
        {connected ? (
          <button type="button" disabled={busy} onClick={onDisconnect}>
            연결 해제
          </button>
        ) : null}
      </div>
      {manualPending ? (
        <div className="config-popover-footer-manual">
          <div className="config-popover-footer-status">
            {label} 승인 후 &quot;사이트에 연결할 수 없음&quot; 같은 페이지가 나오면 정상입니다.
            그 창의 <b>주소창 URL 전체</b>를 복사해 아래에 붙여넣으세요.
          </div>
          <div className="config-popover-footer-actions">
            <input
              type="text"
              value={pasted}
              placeholder="http://localhost:…?code=…&state=…"
              disabled={busy}
              onChange={(e) => setPasted(e.target.value)}
              style={{ flex: 1, minWidth: 0 }}
            />
            <button
              type="button"
              disabled={busy || !pasted.trim()}
              onClick={() => onSubmitManual?.(pasted.trim())}
            >
              연결 완료
            </button>
            <button type="button" disabled={busy} onClick={onCancelManual}>
              취소
            </button>
          </div>
        </div>
      ) : null}
      {error ? <div className="config-popover-footer-error">{error}</div> : null}
    </div>
  );
}
