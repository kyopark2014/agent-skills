import { useEffect, useState } from "react";
import { createPortal } from "react-dom";
import { api, type DrawingFloorRef, type DrawingRating, type DrawingSummary } from "../api";
import { MenuIcon } from "./SidebarIcons";

const RATING_OPTIONS: Array<{ value: DrawingRating; label: string }> = [
  { value: "good", label: "좋음" },
  { value: "neutral", label: "중립" },
  { value: "bad", label: "나쁨" },
];

function normalizeRating(value: string | undefined | null): DrawingRating {
  if (value === "good" || value === "bad" || value === "neutral") return value;
  return "neutral";
}

interface Props {
  onMenuClick?: () => void;
  onBack: () => void;
  onOpenUploads?: () => void;
  onOpen: (drawingId: string, floorId?: string) => void;
  onOpenLog: (taskId: string) => void;
  onRestart: (drawing: DrawingSummary) => Promise<void>;
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

export function DrawingListView({
  onMenuClick,
  onBack,
  onOpenUploads,
  onOpen,
  onOpenLog,
  onRestart,
}: Props) {
  const [drawings, setDrawings] = useState<DrawingSummary[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [deletingId, setDeletingId] = useState<string | null>(null);
  const [restartingId, setRestartingId] = useState<string | null>(null);
  const [ratingBusyId, setRatingBusyId] = useState<string | null>(null);
  const [pendingDelete, setPendingDelete] = useState<DrawingSummary | null>(null);
  const [query, setQuery] = useState("");

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

  async function restart(drawing: DrawingSummary) {
    setRestartingId(drawing.drawing_id);
    setError(null);
    try {
      await onRestart(drawing);
    } catch (err) {
      setError(err instanceof Error ? err.message : "도면을 재시작하지 못했습니다.");
      setRestartingId(null);
    }
  }

  async function changeRating(drawing: DrawingSummary, rating: DrawingRating) {
    const previous = normalizeRating(drawing.rating);
    if (previous === rating) return;
    setRatingBusyId(drawing.drawing_id);
    setError(null);
    setDrawings((prev) =>
      prev.map((item) => (item.drawing_id === drawing.drawing_id ? { ...item, rating } : item)),
    );
    try {
      await api.setDrawingRating(drawing.drawing_id, rating);
    } catch (err) {
      setDrawings((prev) =>
        prev.map((item) =>
          item.drawing_id === drawing.drawing_id ? { ...item, rating: previous } : item,
        ),
      );
      setError(err instanceof Error ? err.message : "평가를 저장하지 못했습니다.");
    } finally {
      setRatingBusyId(null);
    }
  }

  const deleteLabel =
    pendingDelete?.source_filename || pendingDelete?.folder || pendingDelete?.drawing_id || "도면";
  const needle = query.trim().toLowerCase();
  const visible = drawings.flatMap((drawing) => {
    const floors = drawing.floors ?? [];
    if (!needle) return [{ drawing, floors: [] as DrawingFloorRef[] }];
    const drawingHit = [drawing.source_filename, drawing.folder, drawing.drawing_id, drawing.status].some(
      (value) => (value || "").toLowerCase().includes(needle),
    );
    const floorHits = floors.filter((floor) =>
      [floor.id, floor.title].some((value) => (value || "").toLowerCase().includes(needle)),
    );
    if (!drawingHit && floorHits.length === 0) return [];
    return [{ drawing, floors: floorHits }];
  });

  return (
    <div className="drawing-view">
      <header className="main-header">
        <button type="button" className="menu-btn" aria-label="메뉴 열기" onClick={onMenuClick}>
          <MenuIcon className="sidebar-icon" />
        </button>
        <span className="main-header-title">Drawing</span>
        {!loading && drawings.length > 0 ? (
          <input
            type="search"
            className="drawing-search-input drawing-header-search"
            value={query}
            placeholder="도면, 층, 파일명 검색"
            aria-label="도면 검색"
            onChange={(event) => setQuery(event.target.value)}
          />
        ) : null}
        <div className="drawing-header-actions">
          <button type="button" className="drawing-back-btn" onClick={onOpenUploads}>
            도면 가져오기
          </button>
          <button type="button" className="drawing-back-btn" onClick={onBack}>
            채팅으로
          </button>
        </div>
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
        ) : visible.length === 0 ? (
          <p className="drawing-list-empty">일치하는 도면이 없습니다.</p>
        ) : (
          <>
            {error ? (
              <p className="drawing-error" role="alert">
                {error}
              </p>
            ) : null}
            <ul className="drawing-list">
              {visible.map(({ drawing, floors }) => {
                const title = drawing.source_filename || drawing.drawing_id;
                const busy =
                  deletingId === drawing.drawing_id ||
                  restartingId === drawing.drawing_id ||
                  ratingBusyId === drawing.drawing_id;
                const canRestart = Boolean((drawing.source_filename || "").trim());
                return (
                  <li key={drawing.drawing_id} className="drawing-list-item">
                    <div className="drawing-list-row">
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
                        className="drawing-list-btn"
                        disabled={busy}
                        onClick={() => setPendingDelete(drawing)}
                      >
                        {deletingId === drawing.drawing_id ? "삭제 중…" : "삭제"}
                      </button>
                      <button
                        type="button"
                        className="drawing-list-btn"
                        disabled={busy || !drawing.task_id}
                        onClick={() => {
                          if (drawing.task_id) onOpenLog(drawing.task_id);
                        }}
                      >
                        로그
                      </button>
                      <button
                        type="button"
                        className="drawing-list-btn"
                        disabled={busy || !canRestart}
                        onClick={() => void restart(drawing)}
                      >
                        {restartingId === drawing.drawing_id ? "재시작 중…" : "재시작"}
                      </button>
                      <label className="drawing-rating">
                        <span className="drawing-rating-label">평가</span>
                        <select
                          className="drawing-list-select"
                          aria-label={`${title} 평가`}
                          value={normalizeRating(drawing.rating)}
                          disabled={busy}
                          onChange={(event) =>
                            void changeRating(drawing, normalizeRating(event.target.value))
                          }
                        >
                          {RATING_OPTIONS.map((option) => (
                            <option key={option.value} value={option.value}>
                              {option.label}
                            </option>
                          ))}
                        </select>
                      </label>
                    </div>
                    </div>
                    {floors.length > 0 ? (
                      <ul className="drawing-floor-matches" aria-label={`${title} 일치 층`}>
                        {floors.map((floor) => {
                          const floorTitle = (floor.title || "").trim();
                          return (
                            <li key={floor.id}>
                              <button
                                type="button"
                                className="drawing-floor-match"
                                disabled={busy}
                                onClick={() => onOpen(drawing.drawing_id, floor.id)}
                              >
                                <span className="drawing-floor-match-id">{floor.id}</span>
                                {floorTitle && floorTitle !== floor.id ? (
                                  <span className="drawing-floor-match-title">{floorTitle}</span>
                                ) : null}
                              </button>
                            </li>
                          );
                        })}
                      </ul>
                    ) : null}
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
                <p>{`"${deleteLabel}" 도면을 삭제할까요?`}</p>
                <p>
                  {`폴더 ${pendingDelete.folder || pendingDelete.drawing_id}의 산출물과 목록(JSON)만 삭제됩니다. 업로드 원본은 그대로 두며, 원본 삭제는 「도면 가져오기」에서 하세요.`}
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
