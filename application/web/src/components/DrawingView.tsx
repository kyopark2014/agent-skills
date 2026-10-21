import {
  useEffect,
  useLayoutEffect,
  useMemo,
  useRef,
  useState,
  type PointerEvent as ReactPointerEvent,
} from "react";
import {
  api,
  type DrawingCatalog,
  type DrawingFloorLabels,
  type DrawingLabelItem,
  type DrawingLabelTag,
  type DrawingRating,
} from "../api";
import { loadLegendSelection, saveLegendSelection } from "../drawingLegendSettings";
import { MenuIcon } from "./SidebarIcons";

const MIN_ZOOM = 0.25;
const MAX_ZOOM = 16;
const ZOOM_STEP = 1.25;
const PAN_KEY_STEP = 64;

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
  // Validated walls match label overlay mm→px space; fall back to wall/original if missing.
  { kind: "summary", label: "요약", file: "floor_wall_validated.png" },
] as const;

type PreviewKind = (typeof PREVIEWS)[number]["kind"];
type ImageFileKind = "original" | "wall" | "validated" | "label";

interface Props {
  drawingId: string;
  initialFloor?: string | null;
  onMenuClick?: () => void;
  onBack: () => void;
}

function imageKind(kind: PreviewKind): ImageFileKind {
  if (kind === "summary") return "validated";
  return kind;
}

/** Summary background: always try validated first, then wall, then original. */
function summaryFallbackChain(
  files: { wall?: boolean; validated?: boolean; original?: boolean } | undefined,
): ImageFileKind[] {
  const chain: ImageFileKind[] = ["validated"];
  if (!files || files.wall) chain.push("wall");
  if (!files || files.original) chain.push("original");
  return chain;
}

function imageSrc(
  drawingId: string,
  floor: string,
  fileKind: ImageFileKind,
): string {
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

function pointInPolygon(x: number, y: number, poly: number[][]): boolean {
  let inside = false;
  for (let i = 0, j = poly.length - 1; i < poly.length; j = i++) {
    const xi = poly[i]?.[0] ?? 0;
    const yi = poly[i]?.[1] ?? 0;
    const xj = poly[j]?.[0] ?? 0;
    const yj = poly[j]?.[1] ?? 0;
    if (yi > y !== yj > y && x < ((xj - xi) * (y - yi)) / (yj - yi || Number.EPSILON) + xi) {
      inside = !inside;
    }
  }
  return inside;
}

function findPendingLabelAtPx(
  labels: DrawingLabelItem[],
  x: number,
  y: number,
): string | null {
  for (let i = labels.length - 1; i >= 0; i -= 1) {
    const item = labels[i];
    if (!item || item.source !== "pending") continue;
    for (const poly of item.polygons_px ?? []) {
      if (poly.length >= 3 && pointInPolygon(x, y, poly)) return item.label;
    }
  }
  return null;
}

/** Topmost visible (checked) label polygon under the click — same hit order as pending. */
function findSelectedLabelAtPx(
  labels: DrawingLabelItem[],
  selected: Set<string>,
  x: number,
  y: number,
): string | null {
  for (let i = labels.length - 1; i >= 0; i -= 1) {
    const item = labels[i];
    if (!item || !selected.has(item.label)) continue;
    for (const poly of item.polygons_px ?? []) {
      if (poly.length >= 3 && pointInPolygon(x, y, poly)) return item.label;
    }
  }
  return null;
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
  // Match drawing-totalroom `_add_label_tag`: seed is the TEXT insert (left edge),
  // not the badge center. Centering here shifts every tag left of the label PNG.
  const areaText = formatArea(displayAreaM2(tag.text, tag.area_m2));
  const textW = Math.max(inkWidth(tag.text, fontSize), inkWidth(areaText, fontSize));
  const padX = fontSize * 0.4;
  const padY = fontSize * 0.28;
  const gap = fontSize * 0.22;
  const boxW = textW + padX * 2;
  const boxH = padY * 2 + fontSize * 2 + gap;
  const x0 = tag.x - padX;
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
        textAnchor="start"
        fontSize={fontSize}
        fill="#141414"
        fontFamily="Apple SD Gothic Neo, Malgun Gothic, sans-serif"
      >
        {tag.text}
      </text>
      <text
        x={tag.x}
        y={y0 + padY + fontSize + gap + fontSize * 0.82}
        textAnchor="start"
        fontSize={fontSize}
        fill="#141414"
        fontFamily="Apple SD Gothic Neo, Malgun Gothic, sans-serif"
      >
        {areaText}
      </text>
    </g>
  );
}

/** Manual exclude labels (제외-N) are subtracted from area totals. */
function isExcludeAreaLabel(label: string): boolean {
  return /^제외-\d+$/.test(label.trim());
}

/** Signed area for legend/tags: 제외-N shows as negative (e.g. -24.33). */
function displayAreaM2(label: string, areaM2: number): number {
  const key = Math.round(Math.abs(areaM2) * 100) / 100;
  return isExcludeAreaLabel(label) ? -key : key;
}

/** Legend rows with the same displayed area count once; 제외-N is subtracted. */
function uniqueSelectedArea(labels: DrawingLabelItem[], selected: Set<string>): number {
  const seenInclude = new Set<number>();
  const seenExclude = new Set<number>();
  let include = 0;
  let exclude = 0;
  for (const item of labels) {
    if (!selected.has(item.label)) continue;
    // Match the 2-decimal value shown next to each legend row.
    const key = Math.round(Math.abs(item.area_m2) * 100) / 100;
    if (isExcludeAreaLabel(item.label)) {
      if (seenExclude.has(key)) continue;
      seenExclude.add(key);
      exclude += key;
      continue;
    }
    if (seenInclude.has(key)) continue;
    seenInclude.add(key);
    include += key;
  }
  return Math.round((include - exclude) * 100) / 100;
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
  const [kind, setKind] = useState<PreviewKind>("summary");
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
  const [addLabelMode, setAddLabelMode] = useState(false);
  const [addLabelBusy, setAddLabelBusy] = useState(false);
  const [rectDrawMode, setRectDrawMode] = useState(false);
  const [excludeDrawMode, setExcludeDrawMode] = useState(false);
  const [rectDraft, setRectDraft] = useState<{
    x0: number;
    y0: number;
    x1: number;
    y1: number;
  } | null>(null);
  const [summaryBgIndex, setSummaryBgIndex] = useState(0);

  const stageRef = useRef<HTMLDivElement>(null);
  const legendRef = useRef<HTMLDivElement>(null);
  const imgRef = useRef<HTMLImageElement | null>(null);
  const zoomRef = useRef(zoom);
  const panRef = useRef(pan);
  const fitRef = useRef(fit);
  const naturalRef = useRef({ width: 0, height: 0 });
  const addLabelBusyRef = useRef(false);
  const pendingCountRef = useRef(0);
  const rectDrawModeRef = useRef(false);
  const excludeDrawModeRef = useRef(false);
  const spacePanRef = useRef(false);
  const rectDragRef = useRef<{ x0: number; y0: number } | null>(null);
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
  addLabelBusyRef.current = addLabelBusy;
  rectDrawModeRef.current = rectDrawMode;
  excludeDrawModeRef.current = excludeDrawMode;
  const areaPickMode = rectDrawMode || excludeDrawMode;

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
  const headerTitle = catalog?.source_filename || catalog?.drawing_id || drawingId;
  const legendActive = showsLegend(kind);
  const canAddLabel = kind === "summary";

  useEffect(() => {
    let cancelled = false;
    setCatalog(null);
    setFloor("");
    setError(null);
    setLabelData(null);
    setLabelError(null);
    setRating("pending");
    setRatingBusy(false);
    setAddLabelMode(false);
    setAddLabelBusy(false);
    setRectDrawMode(false);
    setExcludeDrawMode(false);
    setRectDraft(null);
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
    setRectDrawMode(false);
    setExcludeDrawMode(false);
    setRectDraft(null);
    api
      .getDrawingFloorLabels(drawingId, floor)
      .then((data) => {
        if (cancelled) return;
        const names = data.labels.map((item) => item.label);
        setLabelData(data);
        setSelectedLabels(loadLegendSelection(drawingId, floor, names));
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

  useEffect(() => {
    if (!canAddLabel) {
      setAddLabelMode(false);
      setRectDrawMode(false);
      setExcludeDrawMode(false);
      setRectDraft(null);
    }
  }, [canAddLabel]);

  const selectedArea = useMemo(
    () => (labelData ? uniqueSelectedArea(labelData.labels, selectedLabels) : 0),
    [labelData, selectedLabels],
  );

  const pendingCount = useMemo(() => {
    if (!labelData) return 0;
    if (typeof labelData.pending_count === "number") return labelData.pending_count;
    return labelData.labels.filter((item) => item.source === "pending").length;
  }, [labelData]);
  pendingCountRef.current = pendingCount;

  const showLabelEditBar =
    canAddLabel && (addLabelMode || rectDrawMode || excludeDrawMode || pendingCount > 0);
  const summaryChain = summaryFallbackChain(selected);
  const fileKind: ImageFileKind =
    kind === "summary"
      ? summaryChain[Math.min(summaryBgIndex, summaryChain.length - 1)] ?? "validated"
      : imageKind(kind);
  // Summary may load validated even if the catalog flag is briefly stale.
  const available =
    kind === "summary"
      ? selected
        ? Boolean(selected.validated || selected.wall || selected.original)
        : null
      : selected
        ? Boolean(selected[fileKind])
        : null;
  const src = floor ? imageSrc(drawingId, floor, fileKind) : "";

  const overlayLabels = useMemo(() => {
    if (!labelData) return [];
    if (kind === "label") {
      // Label PNG already paints detected rooms; overlay only pending + manual adds.
      return labelData.labels.filter(
        (item) => item.source === "pending" || (item.source === "modified" && !item.from_detected),
      );
    }
    if (kind === "summary") return labelData.labels;
    return [];
  }, [kind, labelData]);

  function applyLabelPayload(data: DrawingFloorLabels) {
    const prevNames = new Set((labelData?.labels ?? []).map((item) => item.label));
    const names = data.labels.map((item) => item.label);
    setLabelData(data);
    setLabelError(null);
    setSelectedLabels((prev) => {
      const next = new Set<string>();
      for (const name of names) {
        if (!prevNames.has(name) || prev.has(name)) next.add(name);
      }
      saveLegendSelection(drawingId, floor, next, names);
      return next;
    });
  }

  function toggleLabel(name: string) {
    setSelectedLabels((prev) => {
      const next = new Set(prev);
      if (next.has(name)) next.delete(name);
      else next.add(name);
      const known = (labelData?.labels ?? []).map((item) => item.label);
      if (floor && known.length > 0) {
        saveLegendSelection(drawingId, floor, next, known);
      }
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

  useEffect(() => {
    setSummaryBgIndex(0);
  }, [kind, floor, drawingId]);

  useEffect(() => {
    if (kind !== "summary") return;
    beginImage();
  }, [kind, floor, summaryBgIndex]);

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

  function markImageReady(img: HTMLImageElement) {
    if (img.naturalWidth <= 0 || img.naturalHeight <= 0) return;
    naturalRef.current = {
      width: img.naturalWidth,
      height: img.naturalHeight,
    };
    setImageState("ready");
    applyContain();
  }

  // Cached images (e.g. original after leaving label-edit validated bg) may skip onLoad.
  useLayoutEffect(() => {
    const img = imgRef.current;
    if (!img) return;
    if (img.complete) {
      if (img.naturalWidth > 0) markImageReady(img);
      else setImageState("missing");
    }
  }, [src]);

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

  function clientToImagePx(clientX: number, clientY: number): { x: number; y: number } | null {
    const stage = stageRef.current;
    const scale = fitRef.current * zoomRef.current;
    if (!stage || scale <= 0) return null;
    const rect = stage.getBoundingClientRect();
    return {
      x: (clientX - rect.left - panRef.current.x) / scale,
      y: (clientY - rect.top - panRef.current.y) / scale,
    };
  }

  /** Map image pixels onto overlay/label JSON space (validated PNG may differ from original). */
  function imagePxToOverlayPx(pt: { x: number; y: number }): { x: number; y: number } {
    const imageW = naturalRef.current.width || labelData?.overlay?.width || 0;
    const imageH = naturalRef.current.height || labelData?.overlay?.height || 0;
    const overlayW = labelData?.overlay?.width || imageW;
    const overlayH = labelData?.overlay?.height || imageH;
    if (imageW <= 0 || imageH <= 0 || overlayW <= 0 || overlayH <= 0) return pt;
    if (imageW === overlayW && imageH === overlayH) return pt;
    return {
      x: (pt.x / imageW) * overlayW,
      y: (pt.y / imageH) * overlayH,
    };
  }

  async function addManualLabelAt(
    clientX: number,
    clientY: number,
    boundary: "wall" | "entity" = "entity",
    kind: "unnamed" | "exclude" = "unnamed",
  ) {
    if (!floor || addLabelBusyRef.current || imageState !== "ready") return;
    const pt = clientToImagePx(clientX, clientY);
    if (!pt) return;
    const width = naturalRef.current.width || labelData?.overlay?.width || 0;
    const height = naturalRef.current.height || labelData?.overlay?.height || 0;
    if (width > 0 && height > 0 && (pt.x < 0 || pt.y < 0 || pt.x > width || pt.y > height)) {
      setError("도면 안을 클릭해 주세요.");
      return;
    }
    setAddLabelBusy(true);
    setError(null);
    try {
      const data = await api.addDrawingFloorLabelManual(drawingId, floor, {
        x_px: pt.x,
        y_px: pt.y,
        boundary,
        kind,
      });
      applyLabelPayload(data);
    } catch (err) {
      setError(err instanceof Error ? err.message : "라벨을 추가하지 못했습니다.");
    } finally {
      setAddLabelBusy(false);
    }
  }

  async function addManualRectLabel(
    x0: number,
    y0: number,
    x1: number,
    y1: number,
    kind: "unnamed" | "exclude" = "unnamed",
  ) {
    if (!floor || addLabelBusyRef.current || imageState !== "ready") return;
    if (Math.abs(x1 - x0) < 4 || Math.abs(y1 - y0) < 4) {
      setError("사각형을 더 크게 그려 주세요.");
      return;
    }
    setAddLabelBusy(true);
    setError(null);
    try {
      const data = await api.addDrawingFloorLabelManual(drawingId, floor, {
        x0_px: x0,
        y0_px: y0,
        x1_px: x1,
        y1_px: y1,
        kind,
      });
      applyLabelPayload(data);
    } catch (err) {
      setError(err instanceof Error ? err.message : "사각형 라벨을 추가하지 못했습니다.");
    } finally {
      setAddLabelBusy(false);
    }
  }

  async function undoManualLabel(label?: string) {
    if (!floor || addLabelBusyRef.current || pendingCountRef.current <= 0) return;
    setAddLabelBusy(true);
    setError(null);
    try {
      const data = await api.undoDrawingFloorLabelManual(drawingId, floor, label);
      applyLabelPayload(data);
    } catch (err) {
      setError(err instanceof Error ? err.message : "라벨을 되돌리지 못했습니다.");
    } finally {
      setAddLabelBusy(false);
    }
  }

  async function undoLastManualLabel() {
    await undoManualLabel();
  }

  async function savePendingLabels() {
    if (!floor || addLabelBusyRef.current || pendingCountRef.current <= 0) return;
    setAddLabelBusy(true);
    setError(null);
    try {
      const data = await api.commitDrawingFloorLabelManual(drawingId, floor);
      applyLabelPayload(data);
      setAddLabelMode(false);
      setRectDrawMode(false);
      setExcludeDrawMode(false);
      setRectDraft(null);
    } catch (err) {
      setError(err instanceof Error ? err.message : "라벨을 저장하지 못했습니다.");
    } finally {
      setAddLabelBusy(false);
    }
  }

  async function cancelPendingLabels() {
    if (!floor || addLabelBusyRef.current || pendingCountRef.current <= 0) return;
    setAddLabelBusy(true);
    setError(null);
    try {
      const data = await api.cancelDrawingFloorLabelManual(drawingId, floor);
      applyLabelPayload(data);
      setAddLabelMode(false);
      setRectDrawMode(false);
      setExcludeDrawMode(false);
      setRectDraft(null);
    } catch (err) {
      setError(err instanceof Error ? err.message : "임시 라벨을 취소하지 못했습니다.");
    } finally {
      setAddLabelBusy(false);
    }
  }

  useEffect(() => {
    function isTypingTarget(target: EventTarget | null) {
      if (!(target instanceof HTMLElement)) return false;
      const tag = target.tagName;
      return tag === "INPUT" || tag === "TEXTAREA" || tag === "SELECT" || target.isContentEditable;
    }

    function nudgePan(dx: number, dy: number) {
      const next = { x: panRef.current.x + dx, y: panRef.current.y + dy };
      panRef.current = next;
      setPan(next);
    }

    function onKeyDown(event: KeyboardEvent) {
      if (isTypingTarget(event.target)) return;

      if (event.code === "Space" && !event.repeat) {
        spacePanRef.current = true;
        event.preventDefault();
        return;
      }

      // 방향키: 도면 이동 (추가하기·라벨 추가 중 포함). 화살표 방향으로 시야 이동.
      if (
        event.code === "ArrowLeft" ||
        event.code === "ArrowRight" ||
        event.code === "ArrowUp" ||
        event.code === "ArrowDown"
      ) {
        if (event.metaKey || event.ctrlKey || event.altKey) return;
        event.preventDefault();
        if (event.code === "ArrowLeft") nudgePan(PAN_KEY_STEP, 0);
        else if (event.code === "ArrowRight") nudgePan(-PAN_KEY_STEP, 0);
        else if (event.code === "ArrowUp") nudgePan(0, PAN_KEY_STEP);
        else nudgePan(0, -PAN_KEY_STEP);
        return;
      }

      if (!(event.metaKey || event.ctrlKey) || event.altKey) return;
      if (event.key !== "z" && event.key !== "Z") return;
      if (event.shiftKey) return;
      if (!canAddLabel || pendingCountRef.current <= 0 || addLabelBusyRef.current) return;
      event.preventDefault();
      void undoLastManualLabel();
    }

    function onKeyUp(event: KeyboardEvent) {
      if (event.code === "Space") spacePanRef.current = false;
    }

    function onBlur() {
      spacePanRef.current = false;
    }

    function onVisibility() {
      if (document.visibilityState !== "visible") spacePanRef.current = false;
    }

    window.addEventListener("keydown", onKeyDown);
    window.addEventListener("keyup", onKeyUp);
    window.addEventListener("blur", onBlur);
    document.addEventListener("visibilitychange", onVisibility);
    return () => {
      window.removeEventListener("keydown", onKeyDown);
      window.removeEventListener("keyup", onKeyUp);
      window.removeEventListener("blur", onBlur);
      document.removeEventListener("visibilitychange", onVisibility);
    };
  }, [canAddLabel, drawingId, floor]);

  function activateRectDrawMode() {
    spacePanRef.current = false;
    setRectDrawMode(true);
    setExcludeDrawMode(false);
    setAddLabelMode(false);
    setRectDraft(null);
    setError(null);
  }

  function activateExcludeDrawMode() {
    spacePanRef.current = false;
    setExcludeDrawMode(true);
    setRectDrawMode(false);
    setAddLabelMode(false);
    setRectDraft(null);
    setError(null);
  }

  function activatePolygonDrawMode() {
    spacePanRef.current = false;
    setAddLabelMode(true);
    setRectDrawMode(false);
    setExcludeDrawMode(false);
    setRectDraft(null);
    setError(null);
  }

  function beginPanDrag(event: ReactPointerEvent<HTMLDivElement>) {
    event.currentTarget.setPointerCapture(event.pointerId);
    dragRef.current = {
      x: event.clientX,
      y: event.clientY,
      panX: panRef.current.x,
      panY: panRef.current.y,
    };
    setPanning(true);
  }

  function onPointerDown(event: ReactPointerEvent<HTMLDivElement>) {
    if (imageState !== "ready" || addLabelBusy) return;
    // Middle / right button always pans (라벨 추가·사각형 모드에서도 이동).
    if (event.button === 1 || event.button === 2) {
      event.preventDefault();
      beginPanDrag(event);
      return;
    }
    if (event.button !== 0) return;

    // 추가하기·제외하기: Shift+드래그 = 수동 사각형. 일반 클릭 = WALL 실 선택 (endPan).
    if ((rectDrawModeRef.current || excludeDrawModeRef.current) && event.shiftKey) {
      const pt = clientToImagePx(event.clientX, event.clientY);
      if (!pt) return;
      event.currentTarget.setPointerCapture(event.pointerId);
      rectDragRef.current = { x0: pt.x, y0: pt.y };
      setRectDraft({ x0: pt.x, y0: pt.y, x1: pt.x, y1: pt.y });
      return;
    }

    beginPanDrag(event);
  }

  function onPointerMove(event: ReactPointerEvent<HTMLDivElement>) {
    if (rectDragRef.current) {
      const pt = clientToImagePx(event.clientX, event.clientY);
      if (!pt) return;
      const start = rectDragRef.current;
      setRectDraft({ x0: start.x0, y0: start.y0, x1: pt.x, y1: pt.y });
      return;
    }
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
    if (rectDragRef.current) {
      const start = rectDragRef.current;
      rectDragRef.current = null;
      const pt = clientToImagePx(event.clientX, event.clientY);
      setRectDraft(null);
      if (event.currentTarget.hasPointerCapture(event.pointerId)) {
        event.currentTarget.releasePointerCapture(event.pointerId);
      }
      if (!pt || addLabelBusy) return;
      const rectKind = excludeDrawModeRef.current ? "exclude" : "unnamed";
      void addManualRectLabel(start.x0, start.y0, pt.x, pt.y, rectKind);
      return;
    }
    const drag = dragRef.current;
    if (!drag) return;
    const moved = Math.hypot(event.clientX - drag.x, event.clientY - drag.y) > 4;
    const wasLeftButton = event.button === 0 || event.buttons === 0;
    dragRef.current = null;
    setPanning(false);
    if (event.currentTarget.hasPointerCapture(event.pointerId)) {
      event.currentTarget.releasePointerCapture(event.pointerId);
    }
    if (moved) return;
    if (addLabelBusy) return;
    if (wasLeftButton && event.button === 0) {
      const pt = clientToImagePx(event.clientX, event.clientY);
      const overlayPt = pt ? imagePxToOverlayPx(pt) : null;
      if (canAddLabel && overlayPt && pendingCountRef.current > 0 && labelData) {
        // 임시 영역을 다시 클릭하면 해당 pending 라벨 제거.
        const pendingHit = findPendingLabelAtPx(labelData.labels, overlayPt.x, overlayPt.y);
        if (pendingHit) {
          void undoManualLabel(pendingHit);
          return;
        }
      }
      // 보이는(체크된) 라벨 영역 클릭 → 범례 체크 토글 (기존·추가 라벨 공통).
      // 추가하기·제외하기 모드에서도 이미 라벨이 있는 실은 새로 넣지 않고 표시만 끈다.
      if (
        overlayPt &&
        labelData &&
        legendActive &&
        !rectDrawModeRef.current &&
        !excludeDrawModeRef.current
      ) {
        const hit = findSelectedLabelAtPx(
          labelData.labels,
          selectedLabels,
          overlayPt.x,
          overlayPt.y,
        );
        if (hit) {
          toggleLabel(hit);
          return;
        }
      }
      if (canAddLabel) {
        // 추가하기·제외하기: 클릭 → WALL 기준 실. 다각형 추가: 클릭 → 엔티티 기준.
        if (rectDrawModeRef.current) {
          void addManualLabelAt(event.clientX, event.clientY, "wall", "unnamed");
          return;
        }
        if (excludeDrawModeRef.current) {
          void addManualLabelAt(event.clientX, event.clientY, "wall", "exclude");
          return;
        }
        if (addLabelMode) {
          void addManualLabelAt(event.clientX, event.clientY, "entity");
          return;
        }
      }
    }
    // Label/summary: click the canvas (not a drag) to toggle the legend, like graph.
    if (
      legendActive &&
      (labelData || labelError) &&
      !rectDrawModeRef.current &&
      !excludeDrawModeRef.current
    ) {
      setLegendHidden((value) => !value);
    }
  }

  function onContextMenu(event: React.MouseEvent<HTMLDivElement>) {
    // Right-drag pans; suppress the browser menu on the stage.
    event.preventDefault();
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
              {canAddLabel ? (
                <button
                  type="button"
                  className={`drawing-legend-toggle${addLabelMode || rectDrawMode || excludeDrawMode || pendingCount > 0 ? " is-active" : ""}`}
                  aria-pressed={addLabelMode || rectDrawMode || excludeDrawMode || pendingCount > 0}
                  disabled={imageState !== "ready" || addLabelBusy}
                  title={
                    addLabelMode || rectDrawMode || excludeDrawMode || pendingCount > 0
                      ? "라벨 편집 바 닫기 (임시 라벨이 있으면 유지)"
                      : "라벨 편집: 기본은 추가하기(사각형)"
                  }
                  onClick={() => {
                    if (addLabelMode || rectDrawMode || excludeDrawMode) {
                      setAddLabelMode(false);
                      setRectDrawMode(false);
                      setExcludeDrawMode(false);
                      setRectDraft(null);
                    } else {
                      activateRectDrawMode();
                    }
                  }}
                >
                  라벨 추가
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
            className={`drawing-stage${panning ? " is-panning" : ""}${addLabelMode ? " is-adding-label" : ""}${areaPickMode ? " is-drawing-rect" : ""}${excludeDrawMode ? " is-drawing-exclude" : ""}${addLabelBusy ? " is-busy" : ""}`}
            onPointerDown={onPointerDown}
            onPointerMove={onPointerMove}
            onPointerUp={endPan}
            onPointerCancel={endPan}
            onContextMenu={onContextMenu}
          >
            {addLabelBusy ? (
              <div className="drawing-label-busy" role="status" aria-live="polite">
                <div className="sync-progress-spinner" aria-hidden="true" />
                <p>라벨 처리 중…</p>
              </div>
            ) : null}
            {showLabelEditBar ? (
              <div
                className="drawing-label-edit-bar"
                role="toolbar"
                aria-label="임시 라벨 편집"
                onPointerDown={(event) => event.stopPropagation()}
              >
                <span className="drawing-label-edit-count">
                  임시 {pendingCount}개
                </span>
                <button
                  type="button"
                  className="drawing-label-edit-btn is-primary"
                  disabled={addLabelBusy || pendingCount <= 0}
                  onClick={() => void savePendingLabels()}
                >
                  저장
                </button>
                <button
                  type="button"
                  className="drawing-label-edit-btn"
                  disabled={addLabelBusy || pendingCount <= 0}
                  onClick={() => void cancelPendingLabels()}
                >
                  취소
                </button>
                <button
                  type="button"
                  className="drawing-label-edit-btn"
                  disabled={addLabelBusy || pendingCount <= 0}
                  title="⌘Z / Ctrl+Z"
                  onClick={() => void undoLastManualLabel()}
                >
                  되돌리기
                </button>
                <button
                  type="button"
                  className={`drawing-label-edit-btn${rectDrawMode ? " is-active" : ""}`}
                  disabled={addLabelBusy}
                  aria-pressed={rectDrawMode}
                  title="클릭: WALL 실 선택 · Shift+드래그: 수동 사각형 · 드래그/방향키로 이동"
                  onClick={activateRectDrawMode}
                >
                  추가하기
                </button>
                <button
                  type="button"
                  className={`drawing-label-edit-btn${excludeDrawMode ? " is-active" : ""}`}
                  disabled={addLabelBusy}
                  aria-pressed={excludeDrawMode}
                  title="클릭: WALL 실을 제외-N으로 지정 · Shift+드래그: 수동 사각형 · 면적합계에서 제외"
                  onClick={activateExcludeDrawMode}
                >
                  제외하기
                </button>
                <button
                  type="button"
                  className={`drawing-label-edit-btn${addLabelMode ? " is-active" : ""}`}
                  disabled={addLabelBusy}
                  aria-pressed={addLabelMode}
                  title="클릭한 지점을 둘러싼 엔티티(선) 경계로 다각형 추가 · 임시 영역 다시 클릭으로 제거"
                  onClick={activatePolygonDrawMode}
                >
                  다각형 추가
                </button>
              </div>
            ) : null}
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
                  ref={imgRef}
                  src={src}
                  alt={`${floor} ${preview.label}`}
                  draggable={false}
                  style={{
                    width: naturalRef.current.width || undefined,
                    height: naturalRef.current.height || undefined,
                    transform: `translate(${pan.x}px, ${pan.y}px) scale(${displayScale})`,
                    visibility: imageState === "ready" ? "visible" : "hidden",
                  }}
                  onLoad={(event) => markImageReady(event.currentTarget)}
                  onError={() => {
                    if (kind === "summary" && summaryBgIndex < summaryChain.length - 1) {
                      setSummaryBgIndex((index) => index + 1);
                      return;
                    }
                    setImageState("missing");
                  }}
                />
                {(overlayLabels.length > 0 || rectDraft) && imageState === "ready" ? (
                  <svg
                    className="drawing-label-overlay"
                    width={naturalRef.current.width || labelData?.overlay?.width || 0}
                    height={naturalRef.current.height || labelData?.overlay?.height || 0}
                    viewBox={`0 0 ${labelData?.overlay?.width || naturalRef.current.width || 0} ${labelData?.overlay?.height || naturalRef.current.height || 0}`}
                    style={{
                      transform: `translate(${pan.x}px, ${pan.y}px) scale(${displayScale})`,
                    }}
                    aria-hidden="true"
                  >
                    {overlayLabels.map((item) => {
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
                    {overlayLabels.map((item) => {
                      if (!selectedLabels.has(item.label)) return null;
                      const fontSize = tagFontSize(
                        labelData?.overlay?.width || naturalRef.current.width || 2000,
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
                    {rectDraft ? (
                      <rect
                        x={Math.min(rectDraft.x0, rectDraft.x1)}
                        y={Math.min(rectDraft.y0, rectDraft.y1)}
                        width={Math.abs(rectDraft.x1 - rectDraft.x0)}
                        height={Math.abs(rectDraft.y1 - rectDraft.y0)}
                        fill={
                          excludeDrawMode
                            ? "rgba(196, 72, 72, 0.22)"
                            : "rgba(16, 163, 127, 0.22)"
                        }
                        stroke={
                          excludeDrawMode
                            ? "rgba(196, 72, 72, 0.95)"
                            : "rgba(16, 163, 127, 0.95)"
                        }
                        strokeWidth={2}
                        strokeDasharray="6 4"
                        vectorEffect="non-scaling-stroke"
                      />
                    ) : null}
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
                      <span
                        className="drawing-legend-total-label"
                        title="제외-N 면적은 음수로 합계에서 뺍니다"
                      >
                        선택 합계
                      </span>
                      <span className="drawing-legend-total-value">{formatArea(selectedArea)}</span>
                    </div>
                    <div className="drawing-legend-list">
                      {labelData.labels.map((item) => {
                        const checked = selectedLabels.has(item.label);
                        const areaShown = displayAreaM2(item.label, item.area_m2);
                        return (
                          <label
                            key={item.label}
                            className={`drawing-legend-item${checked ? "" : " is-off"}${isExcludeAreaLabel(item.label) ? " is-exclude" : ""}`}
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
                            <span className="drawing-legend-count">{formatArea(areaShown)}</span>
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
