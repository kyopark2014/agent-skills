import { useEffect, useRef, useState, type PointerEvent as ReactPointerEvent } from "react";
import { api, type DrawingCatalog } from "../api";
import { MenuIcon } from "./SidebarIcons";

const MIN_ZOOM = 0.25;
const MAX_ZOOM = 16;
const ZOOM_STEP = 1.25;

const PREVIEWS = [
  { kind: "original", label: "원본", file: "floor_original.png" },
  { kind: "wall", label: "Wall", file: "floor_wall_original.png" },
  { kind: "validated", label: "검증", file: "floor_wall_validated.png" },
] as const;

type PreviewKind = (typeof PREVIEWS)[number]["kind"];

interface Props {
  drawingId: string;
  onMenuClick?: () => void;
  onBack: () => void;
}

function imageSrc(drawingId: string, floor: string, kind: PreviewKind): string {
  return `/api/drawings/${encodeURIComponent(drawingId)}/floors/${encodeURIComponent(floor)}/${kind}`;
}

function clampZoom(value: number): number {
  return Math.min(MAX_ZOOM, Math.max(MIN_ZOOM, value));
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

export function DrawingView({ drawingId, onMenuClick, onBack }: Props) {
  const [floor, setFloor] = useState("");
  const [kind, setKind] = useState<PreviewKind>("original");
  const [catalog, setCatalog] = useState<DrawingCatalog | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [imageState, setImageState] = useState<"loading" | "ready" | "missing">("loading");
  const [zoom, setZoom] = useState(1);
  const [pan, setPan] = useState({ x: 0, y: 0 });
  const [fit, setFit] = useState(1);
  const [panning, setPanning] = useState(false);

  const stageRef = useRef<HTMLDivElement>(null);
  const zoomRef = useRef(zoom);
  const panRef = useRef(pan);
  const fitRef = useRef(fit);
  const naturalRef = useRef({ width: 0, height: 0 });
  const dragRef = useRef<{ x: number; y: number; panX: number; panY: number } | null>(null);

  zoomRef.current = zoom;
  panRef.current = pan;
  fitRef.current = fit;

  const preview = PREVIEWS.find((item) => item.kind === kind) ?? PREVIEWS[0];
  const floors = catalog?.floors ?? [];
  const selected = floors.find((item) => item.id === floor);
  const available = selected ? Boolean(selected[kind]) : null;
  const src = floor ? imageSrc(drawingId, floor, kind) : "";
  const headerTitle = catalog?.source_filename || catalog?.drawing_id || drawingId;

  useEffect(() => {
    let cancelled = false;
    setCatalog(null);
    setFloor("");
    setError(null);
    api
      .getDrawingFloors(drawingId)
      .then((data) => {
        if (cancelled) return;
        setCatalog(data);
        const next =
          data.floors.find((item) => item.original || item.wall || item.validated)?.id ||
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
  }, [drawingId]);

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
    if (!dragRef.current) return;
    dragRef.current = null;
    setPanning(false);
    if (event.currentTarget.hasPointerCapture(event.pointerId)) {
      event.currentTarget.releasePointerCapture(event.pointerId);
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
          {floors.map((item) => (
            <button
              key={item.id}
              type="button"
              className={`drawing-floor-btn${floor === item.id ? " is-active" : ""}`}
              aria-current={floor === item.id ? "true" : undefined}
              title={item.status ? `${item.id} · ${item.status}` : item.id}
              onClick={() => {
                if (item.id === floor) return;
                setFloor(item.id);
                beginImage();
              }}
            >
              {item.id}
            </button>
          ))}
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
                  key={src}
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
              </>
            )}
          </div>
        </div>
      </div>
    </div>
  );
}
