import { useEffect, useMemo, useRef, useState, type PointerEvent as ReactPointerEvent } from "react";
import {
  api,
  type DrawingCatalog,
  type DrawingFloorLabels,
  type DrawingLabelItem,
  type DrawingLabelTag,
  type DrawingRating,
} from "../api";
import { MenuIcon } from "./SidebarIcons";

const MIN_ZOOM = 0.25;
const MAX_ZOOM = 16;
const ZOOM_STEP = 1.25;

const RATING_OPTIONS: Array<{ value: DrawingRating; label: string }> = [
  { value: "pending", label: "대기" },
  { value: "good", label: "좋음" },
  { value: "neutral", label: "중립" },
  { value: "bad", label: "나쁨" },
  { value: "review", label: "검토" },
  { value: "confirmed", label: "확인" },
  { value: "retest", label: "다시" },
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

const PREVIEWS = [
  { kind: "original", label: "원본", file: "floor_original.png" },
  { kind: "wall", label: "Wall", file: "floor_wall_original.png" },
  { kind: "validated", label: "검증", file: "floor_wall_validated.png" },
  { kind: "label", label: "라벨", file: "floor_label_detected.png" },
  { kind: "summary", label: "요약", file: "floor_original.png" },
] as const;

type PreviewKind = (typeof PREVIEWS)[number]["kind"];

interface Props {
  drawingId: string;
  initialFloor?: string | null;
  onMenuClick?: () => void;
  onBack: () => void;
}

function imageKind(kind: PreviewKind): "original" | "wall" | "validated" | "label" {
  return kind === "summary" ? "original" : kind;
}

function imageSrc(drawingId: string, floor: string, kind: PreviewKind): string {
  const fileKind = imageKind(kind);
  return `/api/drawings/${encodeURIComponent(drawingId)}/floors/${encodeURIComponent(floor)}/${fileKind}`;
}

function showsLegend(kind: PreviewKind): boolean {
  return kind === "label" || kind === "summary";
}

function rgbaCss(color: number[], alpha: number): string {
  const [r = 0, g = 0, b = 0] = color;
  return `rgba(${r}, ${g}, ${b}, ${alpha})`;
}

function polygonPoints(points: number[][]): string {
  return points.map(([x = 0, y = 0]) => `${x},${y}`).join(" ");
}

function clampZoom(value: number): number {
  return Math.min(MAX_ZOOM, Math.max(MIN_ZOOM, value));
}

function rgbCss(color: number[]): string {
  const [r = 0, g = 0, b = 0] = color;
  return `rgb(${r}, ${g}, ${b})`;
}

function formatArea(value: number): string {
  return `${value.toLocaleString("ko-KR", {
    minimumFractionDigits: 2,
    maximumFractionDigits: 2,
  })} ㎡`;
}

function inkWidth(text: string, fontSize: number): number {
  let width = 0;
  for (const ch of text) {
    width += ch.charCodeAt(0) > 127 ? fontSize : fontSize * 0.55;
  }
  return Math.max(width, fontSize);
}

function tagFontSize(overlayWidth: number): number {
  return Math.max(14, Math.min(42, Math.round(overlayWidth / 380)));
}

function SummaryLabelTag({
  tag,
  color,
  fontSize,
}: {
  tag: DrawingLabelTag;
  color: number[];
  fontSize: number;
}) {
  const areaText = formatArea(tag.area_m2);
  const textW = Math.max(inkWidth(tag.text, fontSize), inkWidth(areaText, fontSize));
  const padX = fontSize * 0.45;
  const padY = fontSize * 0.28;
  const gap = fontSize * 0.22;
  const boxW = textW + padX * 2;
  const boxH = padY * 2 + fontSize * 2 + gap;
  const x0 = tag.x - boxW / 2;
  const y0 = tag.y - boxH / 2;
  const stroke = Math.max(1.5, fontSize / 16);
  return (
    <g>
      <rect
        x={x0}
        y={y0}
        width={boxW}
        height={boxH}
        rx={Math.max(2, fontSize / 10)}
        fill="rgba(255, 255, 255, 0.96)"
        stroke={rgbCss(color)}
        strokeWidth={stroke}
        vectorEffect="non-scaling-stroke"
      />
      <text
        x={tag.x}
        y={y0 + padY + fontSize * 0.82}
        textAnchor="middle"
        fontSize={fontSize}
        fill="#141414"
        fontFamily="Apple SD Gothic Neo, Malgun Gothic, sans-serif"
      >
        {tag.text}
      </text>
      <text
        x={tag.x}
        y={y0 + padY + fontSize + gap + fontSize * 0.82}
        textAnchor="middle"
        fontSize={fontSize}
        fill="#141414"
        fontFamily="Apple SD Gothic Neo, Malgun Gothic, sans-serif"
      >
        {areaText}
      </text>
    </g>
  );
}

/** Legend rows with the same displayed area count once in the sum. */
function uniqueSelectedArea(labels: DrawingLabelItem[], selected: Set<string>): number {
  const seen = new Set<number>();
  let total = 0;
  for (const item of labels) {
    if (!selected.has(item.label)) continue;
    // Match the 2-decimal value shown next to each legend row.
    const key = Math.round(item.area_m2 * 100) / 100;
    if (seen.has(key)) continue;
    seen.add(key);
    total += key;
  }
  return Math.round(total * 100) / 100;
}

function containLayout(stage: HTMLElement, width: number, height: number) {
  const fit = Math.min(stage.clientWidth / width, stage.clientHeight / height);
  return {
    fit,
    pan: {
      x: (stage.clientWidth - width * fit) / 2,
      y: (stage.clientHeight - height * fit) / 2,
    },
  };
}

function ZoomOutIcon() {
  return (
    <svg viewBox="0 0 16 16" aria-hidden="true">
      <circle cx="7" cy="7" r="4.25" fill="none" stroke="currentColor" strokeWidth="1.3" />
      <path d="M10.2 10.2 13.2 13.2" fill="none" stroke="currentColor" strokeWidth="1.3" strokeLinecap="round" />
      <path d="M5 7h4" fill="none" stroke="currentColor" strokeWidth="1.3" strokeLinecap="round" />
    </svg>
  );
}

function ZoomInIcon() {
  return (
    <svg viewBox="0 0 16 16" aria-hidden="true">
      <circle cx="7" cy="7" r="4.25" fill="none" stroke="currentColor" strokeWidth="1.3" />
      <path d="M10.2 10.2 13.2 13.2" fill="none" stroke="currentColor" strokeWidth="1.3" strokeLinecap="round" />
      <path d="M5 7h4M7 5v4" fill="none" stroke="currentColor" strokeWidth="1.3" strokeLinecap="round" />
    </svg>
  );
}

export function DrawingView({ drawingId, initialFloor, onMenuClick, onBack }: Props) {
  const [floor, setFloor] = useState("");
  const [floorQuery, setFloorQuery] = useState("");
  const [kind, setKind] = useState<PreviewKind>("label");
  const [catalog, setCatalog] = useState<DrawingCatalog | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [imageState, setImageState] = useState<"loading" | "ready" | "missing">("loading");
  const [zoom, setZoom] = useState(1);
  const [pan, setPan] = useState({ x: 0, y: 0 });
  const [fit, setFit] = useState(1);
  const [panning, setPanning] = useState(false);
  const [labelData, setLabelData] = useState<DrawingFloorLabels | null>(null);
  const [labelError, setLabelError] = useState<string | null>(null);
  const [legendHidden, setLegendHidden] = useState(false);
  const [selectedLabels, setSelectedLabels] = useState<Set<string>>(() => new Set());
  const [legendPos, setLegendPos] = useState<{ left: number; top: number } | null>(null);
  const [legendDragging, setLegendDragging] = useState(false);
  const [rating, setRating] = useState<DrawingRating>("pending");
  const [ratingBusy, setRatingBusy] = useState(false);

  const stageRef = useRef<HTMLDivElement>(null);
  const legendRef = useRef<HTMLDivElement>(null);
  const zoomRef = useRef(zoom);
  const panRef = useRef(pan);
  const fitRef = useRef(fit);
  const naturalRef = useRef({ width: 0, height: 0 });
  const dragRef = useRef<{ x: number; y: number; panX: number; panY: number } | null>(null);
  const legendDragRef = useRef<{
    x: number;
    y: number;
    left: number;
    top: number;
  } | null>(null);

  zoomRef.current = zoom;
  panRef.current = pan;
  fitRef.current = fit;

  const preview = PREVIEWS.find((item) => item.kind === kind) ?? PREVIEWS[0];
  const floors = catalog?.floors ?? [];
  const floorNeedle = floorQuery.trim().toLowerCase();
  const floorLabel = (item: (typeof floors)[number]) => {
    const id = item.id;
    const count = item.label_count ?? 0;
    return count > 0 ? `${id} (${count})` : `${id} (라벨없음)`;
  };
  const visibleFloors = floorNeedle
    ? floors.filter((item) =>
        [item.id, item.title, floorLabel(item)].some((value) =>
          (value || "").toLowerCase().includes(floorNeedle),
        ),
      )
    : floors;
  const selected = floors.find((item) => item.id === floor);
  const available = selected ? Boolean(selected[imageKind(kind)]) : null;
  const src = floor ? imageSrc(drawingId, floor, kind) : "";
  const headerTitle = catalog?.source_filename || catalog?.drawing_id || drawingId;
  const legendActive = showsLegend(kind);

  useEffect(() => {
    let cancelled = false;
    setCatalog(null);
    setFloor("");
    setError(null);
    setLabelData(null);
    setLabelError(null);
    setRating("pending");
    setRatingBusy(false);
    api
      .getDrawingFloors(drawingId)
      .then((data) => {
        if (cancelled) return;
        setCatalog(data);
        setRating(normalizeRating(data.rating));
        const preferred =
          initialFloor && data.floors.some((item) => item.id === initialFloor) ? initialFloor : "";
        const next =
          preferred ||
          data.floors.find((item) => item.original || item.wall || item.validated || item.label)?.id ||
          data.floors[0]?.id ||
          "";
        setFloor(next);
      })
      .catch((err: unknown) => {
        if (!cancelled) {
          setError(err instanceof Error ? err.message : "도면 목록을 불러오지 못했습니다.");
        }
      });
    return () => {
      cancelled = true;
    };
  }, [drawingId, initialFloor]);

  useEffect(() => {
    if (!legendActive || !floor) {
      setLabelData(null);
      setLabelError(null);
      setSelectedLabels(new Set());
      return;
    }
    let cancelled = false;
    setLabelData(null);
    setLabelError(null);
    setSelectedLabels(new Set());
    setLegendHidden(false);
    setLegendPos(null);
    api
      .getDrawingFloorLabels(drawingId, floor)
      .then((data) => {
        if (cancelled) return;
        setLabelData(data);
        setSelectedLabels(new Set(data.labels.map((item) => item.label)));
      })
      .catch((err: unknown) => {
        if (!cancelled) {
          setLabelError(err instanceof Error ? err.message : "라벨 목록을 불러오지 못했습니다.");
        }
      });
    return () => {
      cancelled = true;
    };
  }, [drawingId, floor, kind, legendActive]);

  const selectedArea = useMemo(
    () => (labelData ? uniqueSelectedArea(labelData.labels, selectedLabels) : 0),
    [labelData, selectedLabels],
  );

  function toggleLabel(name: string) {
    setSelectedLabels((prev) => {
      const next = new Set(prev);
      if (next.has(name)) next.delete(name);
      else next.add(name);
      return next;
    });
  }

  function clampLegendPos(left: number, top: number) {
    const stage = stageRef.current;
    const panel = legendRef.current;
    if (!stage || !panel) return { left, top };
    const maxLeft = Math.max(8, stage.clientWidth - panel.offsetWidth - 8);
    const maxTop = Math.max(8, stage.clientHeight - panel.offsetHeight - 8);
    return {
      left: Math.min(maxLeft, Math.max(8, left)),
      top: Math.min(maxTop, Math.max(8, top)),
    };
  }

  function onLegendDragStart(event: ReactPointerEvent<HTMLElement>) {
    if (event.button !== 0) return;
    event.stopPropagation();
    event.preventDefault();
    const stage = stageRef.current;
    const panel = legendRef.current;
    if (!stage || !panel) return;
    const stageRect = stage.getBoundingClientRect();
    const panelRect = panel.getBoundingClientRect();
    const left = panelRect.left - stageRect.left;
    const top = panelRect.top - stageRect.top;
    legendDragRef.current = {
      x: event.clientX,
      y: event.clientY,
      left,
      top,
    };
    setLegendPos({ left, top });
    setLegendDragging(true);
    event.currentTarget.setPointerCapture(event.pointerId);
  }

  function onLegendDragMove(event: ReactPointerEvent<HTMLElement>) {
    const drag = legendDragRef.current;
    if (!drag) return;
    event.stopPropagation();
    setLegendPos(
      clampLegendPos(drag.left + (event.clientX - drag.x), drag.top + (event.clientY - drag.y)),
    );
  }

  function onLegendDragEnd(event: ReactPointerEvent<HTMLElement>) {
    if (!legendDragRef.current) return;
    event.stopPropagation();
    legendDragRef.current = null;
    setLegendDragging(false);
    if (event.currentTarget.hasPointerCapture(event.pointerId)) {
      event.currentTarget.releasePointerCapture(event.pointerId);
    }
  }

  function beginImage() {
    setImageState("loading");
    setZoom(1);
    zoomRef.current = 1;
  }

  function applyContain() {
    const stage = stageRef.current;
    const { width, height } = naturalRef.current;
    if (!stage || width <= 0 || height <= 0) return;
    const next = containLayout(stage, width, height);
    fitRef.current = next.fit;
    panRef.current = next.pan;
    zoomRef.current = 1;
    setFit(next.fit);
    setPan(next.pan);
    setZoom(1);
  }

  function applyZoom(nextZoom: number, originX: number, originY: number) {
    const currentFit = fitRef.current;
    const currentZoom = zoomRef.current;
    const currentPan = panRef.current;
    const prev = currentFit * currentZoom;
    if (prev <= 0) return;
    const clamped = clampZoom(nextZoom);
    const next = currentFit * clamped;
    const contentX = (originX - currentPan.x) / prev;
    const contentY = (originY - currentPan.y) / prev;
    const nextPan = {
      x: originX - contentX * next,
      y: originY - contentY * next,
    };
    zoomRef.current = clamped;
    panRef.current = nextPan;
    setZoom(clamped);
    setPan(nextPan);
  }

  function zoomBy(factor: number) {
    const stage = stageRef.current;
    if (!stage) return;
    applyZoom(zoomRef.current * factor, stage.clientWidth / 2, stage.clientHeight / 2);
  }

  useEffect(() => {
    const stage = stageRef.current;
    if (!stage || imageState !== "ready") return;

    function onWheel(event: WheelEvent) {
      event.preventDefault();
      const rect = stage!.getBoundingClientRect();
      const factor = event.deltaY < 0 ? ZOOM_STEP : 1 / ZOOM_STEP;
      applyZoom(zoomRef.current * factor, event.clientX - rect.left, event.clientY - rect.top);
    }

    stage.addEventListener("wheel", onWheel, { passive: false });
    return () => stage.removeEventListener("wheel", onWheel);
  }, [imageState, floor, kind]);

  useEffect(() => {
    if (imageState !== "ready") return;
    const stage = stageRef.current;
    if (!stage) return;
    const observer = new ResizeObserver(() => {
      if (zoomRef.current === 1) applyContain();
    });
    observer.observe(stage);
    return () => observer.disconnect();
  }, [imageState, floor, kind]);

  async function changeRating(next: DrawingRating) {
    const previous = normalizeRating(rating);
    if (previous === next) return;
    setRatingBusy(true);
    setError(null);
    setRating(next);
    setCatalog((prev) => (prev ? { ...prev, rating: next } : prev));
    try {
      await api.setDrawingRating(drawingId, next);
    } catch (err) {
      setRating(previous);
      setCatalog((prev) => (prev ? { ...prev, rating: previous } : prev));
      setError(err instanceof Error ? err.message : "평가를 저장하지 못했습니다.");
    } finally {
      setRatingBusy(false);
    }
  }

  function onPointerDown(event: ReactPointerEvent<HTMLDivElement>) {
    if (event.button !== 0 || imageState !== "ready") return;
    event.currentTarget.setPointerCapture(event.pointerId);
    dragRef.current = {
      x: event.clientX,
      y: event.clientY,
      panX: panRef.current.x,
      panY: panRef.current.y,
    };
    setPanning(true);
  }

  function onPointerMove(event: ReactPointerEvent<HTMLDivElement>) {
    const drag = dragRef.current;
    if (!drag) return;
    const next = {
      x: drag.panX + (event.clientX - drag.x),
      y: drag.panY + (event.clientY - drag.y),
    };
    panRef.current = next;
    setPan(next);
  }

  function endPan(event: ReactPointerEvent<HTMLDivElement>) {
    const drag = dragRef.current;
    if (!drag) return;
    const moved = Math.hypot(event.clientX - drag.x, event.clientY - drag.y) > 4;
    dragRef.current = null;
    setPanning(false);
    if (event.currentTarget.hasPointerCapture(event.pointerId)) {
      event.currentTarget.releasePointerCapture(event.pointerId);
    }
    // Label/summary: click the canvas (not a drag) to toggle the legend, like graph.
    if (!moved && legendActive && (labelData || labelError)) {
      setLegendHidden((value) => !value);
    }
  }

  const displayScale = fit * zoom;

  return (
    <div className="drawing-view">
      <header className="main-header">
        <button
          type="button"
          className="menu-btn"
          aria-label="메뉴 열기"
          onClick={onMenuClick}
        >
          <MenuIcon className="sidebar-icon" />
        </button>
        <span className="main-header-title">
          {headerTitle}
          {floor ? ` · ${floor}` : ""}
        </span>
        <button type="button" className="drawing-back-btn" onClick={onBack}>
          목록
        </button>
      </header>
      <div className="drawing-body">
        <nav className="drawing-floors" aria-label="층 선택">
          {floors.length > 0 ? (
            <input
              type="search"
              className="drawing-search-input"
              value={floorQuery}
              placeholder="층 검색"
              aria-label="층 검색"
              onChange={(event) => setFloorQuery(event.target.value)}
            />
          ) : null}
          <div className="drawing-floor-list">
            {visibleFloors.map((item) => {
              const floorTitle = (item.title || "").trim();
              const floorIdLabel = floorLabel(item);
              return (
                <button
                  key={item.id}
                  type="button"
                  className={`drawing-floor-btn${floor === item.id ? " is-active" : ""}`}
                  aria-current={floor === item.id ? "true" : undefined}
                  title={
                    floorTitle && floorTitle !== item.id
                      ? `${floorIdLabel} · ${floorTitle}`
                      : floorIdLabel
                  }
                  onClick={() => {
                    if (item.id === floor) return;
                    setFloor(item.id);
                    beginImage();
                  }}
                >
                  <span className="drawing-floor-id">{floorIdLabel}</span>
                  {floorTitle && floorTitle !== item.id ? (
                    <span className="drawing-floor-title">{floorTitle}</span>
                  ) : null}
                </button>
              );
            })}
            {floorNeedle && visibleFloors.length === 0 ? (
              <p className="drawing-floor-empty">일치하는 층이 없습니다.</p>
            ) : null}
          </div>
        </nav>
        <div className="drawing-main">
          <div className="drawing-toolbar">
            <div className="drawing-tabs" role="tablist" aria-label="도면 종류">
              {PREVIEWS.map((item) => (
                <button
                  key={item.kind}
                  type="button"
                  role="tab"
                  className={`drawing-tab${kind === item.kind ? " is-active" : ""}`}
                  aria-selected={kind === item.kind}
                  title={item.file}
                  onClick={() => {
                    if (item.kind === kind) return;
                    setKind(item.kind);
                    beginImage();
                  }}
                >
                  {item.label}
                </button>
              ))}
            </div>
            <div className="drawing-zoom">
              <select
                className={`drawing-list-select is-rating-${rating}`}
                aria-label={`${headerTitle} 평가`}
                title="평가"
                value={rating}
                disabled={ratingBusy || !catalog}
                onChange={(event) => void changeRating(normalizeRating(event.target.value))}
              >
                {RATING_OPTIONS.map((option) => (
                  <option key={option.value} value={option.value}>
                    {option.label}
                  </option>
                ))}
              </select>
              {legendActive && (labelData || labelError) ? (
                <button
                  type="button"
                  className="drawing-legend-toggle"
                  onClick={() => setLegendHidden((value) => !value)}
                >
                  {legendHidden ? "범례 보이기" : "범례 숨기기"}
                </button>
              ) : null}
              <button
                type="button"
                className="drawing-zoom-btn"
                aria-label="축소"
                title="축소"
                disabled={imageState !== "ready"}
                onClick={() => zoomBy(1 / ZOOM_STEP)}
              >
                <ZoomOutIcon />
              </button>
              <button
                type="button"
                className="drawing-zoom-label"
                title="화면에 맞추기"
                disabled={imageState !== "ready"}
                onClick={applyContain}
              >
                {Math.round(zoom * 100)}%
              </button>
              <button
                type="button"
                className="drawing-zoom-btn"
                aria-label="확대"
                title="확대"
                disabled={imageState !== "ready"}
                onClick={() => zoomBy(ZOOM_STEP)}
              >
                <ZoomInIcon />
              </button>
            </div>
          </div>
          {error && <p className="drawing-error">{error}</p>}
          <div
            ref={stageRef}
            className={`drawing-stage${panning ? " is-panning" : ""}`}
            onPointerDown={onPointerDown}
            onPointerMove={onPointerMove}
            onPointerUp={endPan}
            onPointerCancel={endPan}
          >
            {!catalog && !error ? (
              <p className="drawing-card-status">불러오는 중…</p>
            ) : !floor || available === false || imageState === "missing" ? (
              <p className="drawing-card-status">
                {floors.length === 0 ? "표시할 층이 없습니다." : "이미지를 불러오지 못했습니다."}
              </p>
            ) : (
              <>
                {imageState === "loading" && (
                  <p className="drawing-card-status">불러오는 중…</p>
                )}
                <img
                  key={`${kind}:${src}`}
                  src={src}
                  alt={`${floor} ${preview.label}`}
                  draggable={false}
                  style={{
                    width: naturalRef.current.width || undefined,
                    height: naturalRef.current.height || undefined,
                    transform: `translate(${pan.x}px, ${pan.y}px) scale(${displayScale})`,
                    visibility: imageState === "ready" ? "visible" : "hidden",
                  }}
                  onLoad={(event) => {
                    const img = event.currentTarget;
                    naturalRef.current = {
                      width: img.naturalWidth,
                      height: img.naturalHeight,
                    };
                    setImageState("ready");
                    applyContain();
                  }}
                  onError={() => setImageState("missing")}
                />
                {kind === "summary" && imageState === "ready" && labelData ? (
                  <svg
                    className="drawing-label-overlay"
                    width={naturalRef.current.width || labelData.overlay?.width || 0}
                    height={naturalRef.current.height || labelData.overlay?.height || 0}
                    viewBox={`0 0 ${naturalRef.current.width || labelData.overlay?.width || 0} ${naturalRef.current.height || labelData.overlay?.height || 0}`}
                    style={{
                      transform: `translate(${pan.x}px, ${pan.y}px) scale(${displayScale})`,
                    }}
                    aria-hidden="true"
                  >
                    {labelData.labels.map((item) => {
                      if (!selectedLabels.has(item.label)) return null;
                      return (item.polygons_px ?? []).map((poly, index) => (
                        <polygon
                          key={`${item.label}-poly-${index}`}
                          points={polygonPoints(poly)}
                          fill={rgbaCss(item.color, 0.35)}
                          stroke={rgbaCss(item.color, 0.9)}
                          strokeWidth={2}
                          vectorEffect="non-scaling-stroke"
                        />
                      ));
                    })}
                    {labelData.labels.map((item) => {
                      if (!selectedLabels.has(item.label)) return null;
                      const fontSize = tagFontSize(
                        naturalRef.current.width || labelData.overlay?.width || 2000,
                      );
                      return (item.tags ?? []).map((tag, index) => (
                        <SummaryLabelTag
                          key={`${item.label}-tag-${index}`}
                          tag={tag}
                          color={item.color}
                          fontSize={fontSize}
                        />
                      ));
                    })}
                  </svg>
                ) : null}
              </>
            )}
            {legendActive && !legendHidden && (labelData || labelError) ? (
              <div
                ref={legendRef}
                className={`drawing-legend-panel${legendDragging ? " is-dragging" : ""}`}
                aria-label="라벨 범례"
                style={
                  legendPos
                    ? { left: legendPos.left, top: legendPos.top, bottom: "auto", right: "auto" }
                    : undefined
                }
                onPointerDown={(event) => event.stopPropagation()}
              >
                {labelError ? (
                  <p className="drawing-legend-empty">{labelError}</p>
                ) : labelData && labelData.labels.length === 0 ? (
                  <p className="drawing-legend-empty">표시할 라벨이 없습니다.</p>
                ) : labelData ? (
                  <>
                    <div
                      className="drawing-legend-total"
                      title="드래그하여 이동"
                      onPointerDown={onLegendDragStart}
                      onPointerMove={onLegendDragMove}
                      onPointerUp={onLegendDragEnd}
                      onPointerCancel={onLegendDragEnd}
                    >
                      <span className="drawing-legend-total-label">선택 합계</span>
                      <span className="drawing-legend-total-value">{formatArea(selectedArea)}</span>
                    </div>
                    <div className="drawing-legend-list">
                      {labelData.labels.map((item) => {
                        const checked = selectedLabels.has(item.label);
                        return (
                          <label
                            key={item.label}
                            className={`drawing-legend-item${checked ? "" : " is-off"}`}
                          >
                            <input
                              type="checkbox"
                              className="drawing-legend-check"
                              checked={checked}
                              onChange={() => toggleLabel(item.label)}
                            />
                            <div
                              className="drawing-legend-dot"
                              style={{ background: rgbCss(item.color) }}
                            />
                            <span className="drawing-legend-label" title={item.label}>
                              {item.label}
                            </span>
                            <span className="drawing-legend-count">{formatArea(item.area_m2)}</span>
                          </label>
                        );
                      })}
                    </div>
                  </>
                ) : null}
              </div>
            ) : null}
          </div>
        </div>
      </div>
    </div>
  );
}
