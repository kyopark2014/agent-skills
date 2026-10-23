import { useEffect, useState } from "react";
import { createPortal } from "react-dom";
import {
  api,
  type DrawingFloorRef,
  type DrawingMemo,
  type DrawingRating,
  type DrawingSummary,
} from "../api";
import { MenuIcon } from "./SidebarIcons";

const RATING_OPTIONS: Array<{ value: DrawingRating; label: string }> = [
  { value: "pending", label: "대기" },
  { value: "good", label: "좋음" },
  { value: "neutral", label: "중립" },
  { value: "bad", label: "나쁨" },
  { value: "review", label: "검토" },
  { value: "confirmed", label: "확인" },
  { value: "retest", label: "제외" },
];

function normalizeRating(value: string | undefined | null): DrawingRating {
  if (
    value === "pending" ||
    value === "good" ||
    value === "bad" ||
    value === "neutral" ||
    value === "review" ||
    value === "confirmed" ||
    value === "retest"
  ) {
    return value;
  }
  return "pending";
}

interface Props {
  onMenuClick?: () => void;
  onBack: () => void;
  onOpenUploads?: () => void;
  onOpen: (drawingId: string, floorId?: string) => void;
  onOpenLog: (taskId: string) => void;
  onRestart: (drawing: DrawingSummary) => Promise<void>;
}

function formatListStamp(value: string | undefined): string {
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

/** Prefer updated_at so the list tracks recent edits, not first create. */
function listStamp(drawing: DrawingSummary): string {
  return (drawing.updated_at || drawing.created_at || "").trim();
}

function byListStampDesc(a: DrawingSummary, b: DrawingSummary): number {
  return listStamp(b).localeCompare(listStamp(a));
}

/** Same folder (or same source file) can appear more than once after re-extract; keep newest. */
function dedupeKey(drawing: DrawingSummary): string {
  const folder = (drawing.folder || "").trim();
  if (folder) return `folder:${folder}`;
  const name = (drawing.source_filename || "").trim();
  if (name) return `file:${name}`;
  return `id:${drawing.drawing_id}`;
}

function keepLatestDrawings(drawings: DrawingSummary[]): DrawingSummary[] {
  const sorted = drawings.slice().sort(byListStampDesc);
  const seen = new Set<string>();
  const out: DrawingSummary[] = [];
  for (const drawing of sorted) {
    const key = dedupeKey(drawing);
    if (seen.has(key)) continue;
    seen.add(key);
    out.push(drawing);
  }
  return out;
}

function formatFileSize(bytes: number | null | undefined): string {
  if (bytes == null || !Number.isFinite(bytes) || bytes < 0) return "—";
  if (bytes < 1024) return `${bytes}B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)}K`;
  return `${(bytes / (1024 * 1024)).toFixed(1)}M`;
}

function formatStatus(value: string | undefined): string {
  const raw = (value || "").trim();
  if (!raw) return "—";
  return raw.charAt(0).toUpperCase() + raw.slice(1);
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
  const [memoDrawing, setMemoDrawing] = useState<DrawingSummary | null>(null);
  const [memos, setMemos] = useState<DrawingMemo[]>([]);
  const [memoText, setMemoText] = useState("");
  const [memoLoading, setMemoLoading] = useState(false);
  const [memoSaving, setMemoSaving] = useState(false);
  const [memoDeletingId, setMemoDeletingId] = useState<string | null>(null);
  const [memoEditingId, setMemoEditingId] = useState<string | null>(null);
  const [memoComposing, setMemoComposing] = useState(false);
  const [memoError, setMemoError] = useState<string | null>(null);
  const [query, setQuery] = useState("");

  useEffect(() => {
    let cancelled = false;
    (async () => {
      setLoading(true);
      setError(null);
      try {
        const data = await api.listDrawings();
        if (!cancelled) {
          setDrawings(keepLatestDrawings(data.drawings ?? []));
        }
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
    if (!pendingDelete && !memoDrawing) return;
    function onKey(event: KeyboardEvent) {
      if (event.key !== "Escape") return;
      if (memoDrawing) {
        closeMemo();
        return;
      }
      setPendingDelete(null);
    }
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [pendingDelete, memoDrawing]);

  function closeMemo() {
    setMemoDrawing(null);
    setMemos([]);
    setMemoText("");
    setMemoError(null);
    setMemoLoading(false);
    setMemoSaving(false);
    setMemoDeletingId(null);
    setMemoEditingId(null);
    setMemoComposing(false);
  }

  function cancelMemoCompose() {
    if (memos.length === 0) {
      closeMemo();
      return;
    }
    setMemoComposing(false);
    setMemoEditingId(null);
    setMemoText("");
    setMemoError(null);
  }

  function startMemoEdit(memo: DrawingMemo) {
    setMemoError(null);
    setMemoEditingId(memo.id);
    setMemoText(memo.text);
    setMemoComposing(true);
  }

  async function openMemo(drawing: DrawingSummary) {
    setMemoDrawing(drawing);
    setMemos([]);
    setMemoText("");
    setMemoError(null);
    setMemoEditingId(null);
    // Empty drawings open straight into compose; list says so via memo_count.
    setMemoComposing((drawing.memo_count ?? 0) === 0);
    setMemoLoading(true);
    try {
      const data = await api.listDrawingMemos(drawing.drawing_id);
      const next = data.memos ?? [];
      setMemos(next);
      setMemoComposing(next.length === 0);
    } catch (err) {
      setMemoError(err instanceof Error ? err.message : "메모를 불러오지 못했습니다.");
    } finally {
      setMemoLoading(false);
    }
  }

  async function saveMemo() {
    const drawing = memoDrawing;
    if (!drawing) return;
    const text = memoText.trim();
    if (!text) {
      setMemoError("메모 내용을 입력하세요.");
      return;
    }
    setMemoSaving(true);
    setMemoError(null);
    try {
      const data = memoEditingId
        ? await api.updateDrawingMemo(drawing.drawing_id, memoEditingId, text)
        : await api.addDrawingMemo(drawing.drawing_id, text);
      const next = data.memos ?? [];
      setMemos(next);
      setMemoText("");
      setMemoEditingId(null);
      setMemoComposing(false);
      setDrawings((prev) =>
        prev.map((item) =>
          item.drawing_id === drawing.drawing_id
            ? { ...item, memo_count: next.length }
            : item,
        ),
      );
    } catch (err) {
      setMemoError(err instanceof Error ? err.message : "메모를 저장하지 못했습니다.");
    } finally {
      setMemoSaving(false);
    }
  }

  async function deleteMemo(memoId: string) {
    const drawing = memoDrawing;
    if (!drawing || !memoId) return;
    setMemoDeletingId(memoId);
    setMemoError(null);
    try {
      const data = await api.deleteDrawingMemo(drawing.drawing_id, memoId);
      const next = data.memos ?? [];
      setMemos(next);
      setDrawings((prev) =>
        prev.map((item) =>
          item.drawing_id === drawing.drawing_id
            ? { ...item, memo_count: next.length }
            : item,
        ),
      );
      if (memoEditingId === memoId) {
        setMemoEditingId(null);
        setMemoText("");
      }
      if (next.length === 0) {
        setMemoComposing(true);
      }
    } catch (err) {
      setMemoError(err instanceof Error ? err.message : "메모를 삭제하지 못했습니다.");
    } finally {
      setMemoDeletingId(null);
    }
  }

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
    // Rating is marking-only; keep list order (do not bump updated_at).
    setDrawings((prev) =>
      prev.map((item) =>
        item.drawing_id === drawing.drawing_id ? { ...item, rating } : item,
      ),
    );
    try {
      await api.setDrawingRating(drawing.drawing_id, rating);
    } catch (err) {
      setDrawings((prev) =>
        prev.map((item) =>
          item.drawing_id === drawing.drawing_id
            ? { ...item, rating: previous }
            : item,
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
                          {`수정: ${formatListStamp(listStamp(drawing))}`}
                          {" · "}
                          {`폴더: ${drawing.folder || "—"}`}
                          {" · "}
                          {`상태: ${formatStatus(drawing.status)}`}
                          {" · "}
                          {`크기: ${formatFileSize(drawing.size)}`}
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
                      <select
                        className={`drawing-list-select is-rating-${normalizeRating(drawing.rating)}`}
                        aria-label={`${title} 평가`}
                        title="평가"
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
                      <button
                        type="button"
                        className={`drawing-list-btn${
                          (drawing.memo_count ?? 0) > 0 ? " is-memo-present" : ""
                        }`}
                        disabled={busy}
                        onClick={() => void openMemo(drawing)}
                      >
                        메모
                      </button>
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
      {memoDrawing
        ? createPortal(
            <div className="modal-overlay" role="presentation" onMouseDown={closeMemo}>
              <div
                className="modal drawing-memo-modal"
                role="dialog"
                aria-modal="true"
                aria-labelledby="drawing-memo-title"
                onMouseDown={(event) => event.stopPropagation()}
              >
                <div className="drawing-memo-header">
                  <div>
                    <h2 id="drawing-memo-title">메모</h2>
                    <p className="drawing-memo-target">
                      {memoDrawing.source_filename ||
                        memoDrawing.folder ||
                        memoDrawing.drawing_id}
                    </p>
                  </div>
                  {!memoComposing ? (
                    <button
                      type="button"
                      className="send-btn drawing-memo-add-btn"
                      disabled={memoLoading}
                      onClick={() => {
                        setMemoError(null);
                        setMemoEditingId(null);
                        setMemoText("");
                        setMemoComposing(true);
                      }}
                    >
                      추가
                    </button>
                  ) : null}
                </div>
                {memoComposing ? (
                  <div className="drawing-memo-compose">
                    <label className="drawing-memo-label" htmlFor="drawing-memo-input">
                      {memoEditingId ? "메모 수정" : "새 메모"}
                    </label>
                    <textarea
                      id="drawing-memo-input"
                      className="drawing-memo-input"
                      rows={3}
                      maxLength={2000}
                      value={memoText}
                      placeholder="이 도면에 대한 간단한 코멘트를 남기세요."
                      disabled={memoSaving}
                      autoFocus
                      onChange={(event) => setMemoText(event.target.value)}
                    />
                    <div className="modal-actions">
                      <button
                        type="button"
                        className="drawing-list-btn"
                        disabled={memoSaving}
                        onClick={cancelMemoCompose}
                      >
                        취소
                      </button>
                      <button
                        type="button"
                        className="send-btn"
                        disabled={memoSaving || !memoText.trim()}
                        onClick={() => void saveMemo()}
                      >
                        {memoSaving ? "저장 중…" : "저장"}
                      </button>
                    </div>
                  </div>
                ) : null}
                {memoError ? (
                  <p className="drawing-error" role="alert">
                    {memoError}
                  </p>
                ) : null}
                <div className="drawing-memo-history">
                  {memoLoading ? (
                    <p className="drawing-list-muted">불러오는 중…</p>
                  ) : memos.length === 0 ? (
                    <p className="drawing-list-muted">저장된 메모가 없습니다.</p>
                  ) : (
                    <ul className="drawing-memo-list">
                      {memos.map((memo) => (
                        <li key={memo.id} className="drawing-memo-entry">
                          <div className="drawing-memo-entry-header">
                            <time className="drawing-memo-stamp" dateTime={memo.created_at}>
                              {formatListStamp(memo.created_at)}
                            </time>
                            <div className="drawing-memo-entry-actions">
                              <button
                                type="button"
                                className="drawing-list-btn drawing-memo-delete-btn"
                                disabled={memoSaving || memoDeletingId !== null}
                                onClick={() => startMemoEdit(memo)}
                              >
                                수정
                              </button>
                              <button
                                type="button"
                                className="drawing-list-btn drawing-memo-delete-btn"
                                disabled={memoSaving || memoDeletingId !== null}
                                onClick={() => void deleteMemo(memo.id)}
                              >
                                {memoDeletingId === memo.id ? "삭제 중…" : "삭제"}
                              </button>
                            </div>
                          </div>
                          <div className="drawing-memo-item">
                            <p className="drawing-memo-text">{memo.text}</p>
                          </div>
                        </li>
                      ))}
                    </ul>
                  )}
                </div>
                {!memoComposing ? (
                  <div className="modal-actions">
                    <button type="button" className="drawing-list-btn" onClick={closeMemo}>
                      닫기
                    </button>
                  </div>
                ) : null}
              </div>
            </div>,
            document.body,
          )
        : null}
    </div>
  );
}
