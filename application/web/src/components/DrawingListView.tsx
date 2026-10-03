import { useEffect, useState } from "react";
import { createPortal } from "react-dom";
import { api, type DrawingSummary } from "../api";
import { MenuIcon } from "./SidebarIcons";

interface Props {
  onMenuClick?: () => void;
  onBack: () => void;
  onOpen: (drawingId: string) => void;
}

function formatCreatedAt(value: string | undefined): string {
  const raw = (value || "").trim();
  if (!raw) return "—";
  const date = new Date(raw);
  if (Number.isNaN(date.getTime())) return raw;
  return date.toLocaleString("ko-KR", {
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
  });
}

export function DrawingListView({ onMenuClick, onBack, onOpen }: Props) {
  const [drawings, setDrawings] = useState<DrawingSummary[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [deletingId, setDeletingId] = useState<string | null>(null);
  const [pendingDelete, setPendingDelete] = useState<DrawingSummary | null>(null);

  useEffect(() => {
    let cancelled = false;
    (async () => {
      setLoading(true);
      setError(null);
      try {
        const data = await api.listDrawings();
        if (!cancelled) setDrawings(data.drawings ?? []);
      } catch (err) {
        if (!cancelled) {
          setError(err instanceof Error ? err.message : "도면 목록을 불러오지 못했습니다.");
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
    if (!pendingDelete) return;
    function onKey(event: KeyboardEvent) {
      if (event.key === "Escape") setPendingDelete(null);
    }
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [pendingDelete]);

  async function confirmDelete() {
    const drawing = pendingDelete;
    if (!drawing) return;
    setPendingDelete(null);
    setDeletingId(drawing.drawing_id);
    setError(null);
    try {
      await api.deleteDrawing(drawing.drawing_id);
      setDrawings((prev) => prev.filter((item) => item.drawing_id !== drawing.drawing_id));
    } catch (err) {
      setError(err instanceof Error ? err.message : "도면을 삭제하지 못했습니다.");
    } finally {
      setDeletingId(null);
    }
  }

  const deleteLabel =
    pendingDelete?.source_filename || pendingDelete?.folder || pendingDelete?.drawing_id || "도면";

  return (
    <div className="drawing-view">
      <header className="main-header">
        <button type="button" className="menu-btn" aria-label="메뉴 열기" onClick={onMenuClick}>
          <MenuIcon className="sidebar-icon" />
        </button>
        <span className="main-header-title">Drawing</span>
        <button type="button" className="drawing-back-btn" onClick={onBack}>
          채팅으로
        </button>
      </header>
      <div className="drawing-list-body">
        {loading ? (
          <p className="drawing-list-muted">도면 목록을 불러오는 중…</p>
        ) : drawings.length === 0 ? (
          error ? (
            <p className="drawing-error" role="alert">
              {error}
            </p>
          ) : (
            <p className="drawing-list-empty">진행 중인 도면이 없습니다.</p>
          )
        ) : (
          <>
            {error ? (
              <p className="drawing-error" role="alert">
                {error}
              </p>
            ) : null}
            <ul className="drawing-list">
              {drawings.map((drawing) => {
                const title = drawing.source_filename || drawing.drawing_id;
                const busy = deletingId === drawing.drawing_id;
                return (
                  <li key={drawing.drawing_id} className="drawing-list-item">
                    <div className="drawing-list-meta">
                      <span className="drawing-list-name" title={title}>
                        {title}
                      </span>
                      <span className="drawing-list-sub">
                        생성 {formatCreatedAt(drawing.created_at)}
                        {" · "}
                        폴더 {drawing.folder || "—"}
                        {" · "}
                        상태 {drawing.status || "—"}
                      </span>
                    </div>
                    <div className="drawing-list-actions">
                      <button
                        type="button"
                        className="drawing-list-btn"
                        disabled={busy}
                        onClick={() => onOpen(drawing.drawing_id)}
                      >
                        열기
                      </button>
                      <button
                        type="button"
                        className="drawing-list-btn drawing-list-btn-danger"
                        disabled={busy}
                        onClick={() => setPendingDelete(drawing)}
                      >
                        {busy ? "삭제 중…" : "삭제"}
                      </button>
                    </div>
                  </li>
                );
              })}
            </ul>
          </>
        )}
      </div>
      {pendingDelete
        ? createPortal(
            <div
              className="modal-overlay"
              role="presentation"
              onMouseDown={() => setPendingDelete(null)}
            >
              <div
                className="modal"
                role="dialog"
                aria-modal="true"
                aria-labelledby="drawing-delete-title"
                onMouseDown={(event) => event.stopPropagation()}
              >
                <h2 id="drawing-delete-title">도면 삭제</h2>
                <p>{`"${deleteLabel}" 도면과 관련 파일을 삭제할까요?`}</p>
                <p>
                  {`폴더 ${pendingDelete.folder || pendingDelete.drawing_id}의 산출물과 목록 항목이 삭제됩니다. 작업 공간 안의 원본 파일도 함께 지웁니다. 이 작업은 되돌릴 수 없습니다.`}
                </p>
                <div className="modal-actions">
                  <button
                    type="button"
                    className="drawing-list-btn"
                    onClick={() => setPendingDelete(null)}
                  >
                    취소
                  </button>
                  <button
                    type="button"
                    className="send-btn drawing-delete-confirm"
                    onClick={() => void confirmDelete()}
                  >
                    삭제
                  </button>
                </div>
              </div>
            </div>,
            document.body,
          )
        : null}
    </div>
  );
}
