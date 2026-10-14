import { useEffect, useState } from "react";
import { createPortal } from "react-dom";
import { api, type UploadDrawing } from "../api";
import { MenuIcon } from "./SidebarIcons";

interface Props {
  onMenuClick?: () => void;
  onBackToDrawings: () => void;
  onBackToChat: () => void;
  onOpen: (drawingId: string) => void;
  onStart: (file: UploadDrawing) => Promise<void>;
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

function formatFileSize(bytes: number): string {
  if (!Number.isFinite(bytes) || bytes < 0) return "—";
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}

export function UploadDrawingListView({
  onMenuClick,
  onBackToDrawings,
  onBackToChat,
  onOpen,
  onStart,
}: Props) {
  const [files, setFiles] = useState<UploadDrawing[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [deletingName, setDeletingName] = useState<string | null>(null);
  const [startingName, setStartingName] = useState<string | null>(null);
  const [pendingDelete, setPendingDelete] = useState<UploadDrawing | null>(null);

  useEffect(() => {
    let cancelled = false;
    (async () => {
      setLoading(true);
      setError(null);
      try {
        const data = await api.listUploadDrawings();
        if (!cancelled) setFiles(data.files ?? []);
      } catch (err) {
        if (!cancelled) {
          setError(err instanceof Error ? err.message : "업로드 도면 목록을 불러오지 못했습니다.");
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
    const file = pendingDelete;
    if (!file) return;
    setPendingDelete(null);
    setDeletingName(file.file_name);
    setError(null);
    try {
      await api.deleteUploadDrawing(file.file_name);
      setFiles((prev) => prev.filter((item) => item.file_name !== file.file_name));
    } catch (err) {
      setError(err instanceof Error ? err.message : "파일을 삭제하지 못했습니다.");
    } finally {
      setDeletingName(null);
    }
  }

  async function start(file: UploadDrawing) {
    setStartingName(file.file_name);
    setError(null);
    try {
      await onStart(file);
    } catch (err) {
      setError(err instanceof Error ? err.message : "새 작업을 시작하지 못했습니다.");
      setStartingName(null);
    }
  }

  return (
    <div className="drawing-view">
      <header className="main-header">
        <button type="button" className="menu-btn" aria-label="메뉴 열기" onClick={onMenuClick}>
          <MenuIcon className="sidebar-icon" />
        </button>
        <span className="main-header-title">Drawing</span>
        <div className="drawing-header-actions">
          <button type="button" className="drawing-back-btn" onClick={onBackToDrawings}>
            도면 목록
          </button>
          <button type="button" className="drawing-back-btn" onClick={onBackToChat}>
            채팅으로
          </button>
        </div>
      </header>
      <div className="drawing-list-body">
        {loading ? (
          <p className="drawing-list-muted">업로드 도면을 불러오는 중…</p>
        ) : files.length === 0 ? (
          error ? (
            <p className="drawing-error" role="alert">
              {error}
            </p>
          ) : (
            <p className="drawing-list-empty">업로드된 dwg, dxf, dwf 도면이 없습니다.</p>
          )
        ) : (
          <>
            {error ? (
              <p className="drawing-error" role="alert">
                {error}
              </p>
            ) : null}
            <ul className="drawing-list">
              {files.map((file) => {
                const busy = deletingName === file.file_name || startingName === file.file_name;
                const canView = Boolean(file.drawing_id);
                return (
                  <li key={file.file_name} className="drawing-list-item">
                    <div className="drawing-list-meta">
                      <span className="drawing-list-name" title={file.file_name}>
                        {file.file_name}
                      </span>
                      <span className="drawing-list-sub">
                        생성 {formatCreatedAt(file.created_at)}
                        {" · "}
                        {formatFileSize(file.size)}
                        {" · "}
                        상태 {file.status}
                      </span>
                    </div>
                    <div className="drawing-list-actions">
                      <button
                        type="button"
                        className="drawing-list-btn"
                        disabled={busy}
                        onClick={() => void start(file)}
                      >
                        {startingName === file.file_name ? "시작 중…" : "시작"}
                      </button>
                      <button
                        type="button"
                        className="drawing-list-btn"
                        disabled={!canView || busy}
                        onClick={() => {
                          if (file.drawing_id) onOpen(file.drawing_id);
                        }}
                      >
                        보기
                      </button>
                      <button
                        type="button"
                        className="drawing-list-btn drawing-list-btn-danger"
                        disabled={busy}
                        onClick={() => setPendingDelete(file)}
                      >
                        {deletingName === file.file_name ? "삭제 중…" : "삭제"}
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
                aria-labelledby="upload-drawing-delete-title"
                onMouseDown={(event) => event.stopPropagation()}
              >
                <h2 id="upload-drawing-delete-title">파일 삭제</h2>
                <p>{`"${pendingDelete.file_name}" 파일을 업로드 폴더에서 삭제할까요?`}</p>
                <p>
                  Load files와 같은 위치의 원본만 삭제됩니다. 이미 만들어진 도면 산출물은 그대로 남습니다. 이 작업은 되돌릴 수 없습니다.
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
