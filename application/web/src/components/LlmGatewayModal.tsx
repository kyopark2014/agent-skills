import { useEffect, useState } from "react";
import { createPortal } from "react-dom";
import { api, type LlmGatewayVerifyResult } from "../api";

interface Props {
  enabled: boolean;
  onConfirmEnable: (uiModels?: string[]) => Promise<void> | void;
  onDisable: () => Promise<void> | void;
  onClose: () => void;
}

export function LlmGatewayModal({
  enabled,
  onConfirmEnable,
  onDisable,
  onClose,
}: Props) {
  const [url, setUrl] = useState("");
  const [key, setKey] = useState("");
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [success, setSuccess] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    (async () => {
      setLoading(true);
      setError(null);
      try {
        const data = await api.getLlmGateway();
        if (!cancelled) {
          setUrl(data.url || "");
          setKey(data.key || "");
        }
      } catch (err) {
        if (!cancelled) {
          setError(err instanceof Error ? err.message : String(err));
        }
      } finally {
        if (!cancelled) setLoading(false);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, []);

  useEffect(() => {
    function onKeyDown(e: KeyboardEvent) {
      if (e.key === "Escape" && !busy) onClose();
    }
    window.addEventListener("keydown", onKeyDown);
    return () => window.removeEventListener("keydown", onKeyDown);
  }, [busy, onClose]);

  async function handleConfirm() {
    setError(null);
    setSuccess(null);

    const nextUrl = url.trim();
    const nextKey = key.trim();
    if (!nextUrl || !nextKey) {
      setError("url과 key가 모두 필요합니다.");
      return;
    }

    setBusy(true);
    try {
      const result: LlmGatewayVerifyResult = await api.verifyLlmGateway({
        url: nextUrl,
        key: nextKey,
      });
      if (!result.ok) {
        setError(result.message || "LLM Gateway 모델 확인에 실패했습니다.");
        return;
      }
      setSuccess(result.message || "모델 확인 성공");
      await onConfirmEnable(result.ui_models);
      onClose();
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setBusy(false);
    }
  }

  async function handleDisable() {
    setBusy(true);
    setError(null);
    try {
      await onDisable();
      onClose();
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setBusy(false);
    }
  }

  return createPortal(
    <div
      className="modal-overlay"
      role="dialog"
      aria-modal="true"
      aria-labelledby="llm-gateway-title"
      onMouseDown={(e) => {
        if (e.target === e.currentTarget && !busy) onClose();
      }}
    >
      <div className="modal llm-gateway-modal">
        <h2 id="llm-gateway-title">LLM Gateway</h2>
        <p>
          URL·Key를 수정한 뒤 확인하면 모델 목록 조회에 성공했을 때만 저장·활성화합니다.
          {enabled ? " (현재 사용 중)" : ""}
        </p>

        {loading ? (
          <p className="llm-gateway-muted">설정 불러오는 중…</p>
        ) : (
          <form
            className="llm-gateway-fields"
            onSubmit={(e) => {
              e.preventDefault();
              if (!busy && !loading) void handleConfirm();
            }}
          >
            <label className="llm-gateway-field">
              <span>URL</span>
              <input
                type="text"
                value={url}
                disabled={busy}
                autoComplete="off"
                placeholder="https://gateway.example.com"
                onChange={(e) => setUrl(e.target.value)}
              />
            </label>
            <label className="llm-gateway-field">
              <span>Key</span>
              <input
                type="password"
                value={key}
                disabled={busy}
                autoComplete="off"
                placeholder="sk-..."
                onChange={(e) => setKey(e.target.value)}
              />
            </label>
            <button type="submit" hidden aria-hidden="true" tabIndex={-1} />
          </form>
        )}

        {error && (
          <p className="modal-error" role="alert">
            {error}
          </p>
        )}
        {success && <p className="llm-gateway-success">{success}</p>}

        <div className="modal-actions">
          <button
            type="button"
            className="modal-btn-secondary"
            disabled={busy}
            onClick={onClose}
          >
            취소
          </button>
          {enabled && (
            <button
              type="button"
              className="modal-btn-secondary"
              disabled={busy || loading}
              onClick={handleDisable}
            >
              끄기
            </button>
          )}
          <button
            type="button"
            className="send-btn"
            disabled={busy || loading}
            onClick={handleConfirm}
          >
            {busy ? "확인 중…" : "확인"}
          </button>
        </div>
      </div>
    </div>,
    document.body,
  );
}
