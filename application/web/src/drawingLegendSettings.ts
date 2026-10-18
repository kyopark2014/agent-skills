/** Persist which legend rows are checked per drawing floor (label + summary tabs). */

const STORAGE_PREFIX = "agent-skills:drawing-legend:";

type StoredLegend = {
  selected: string[];
  known: string[];
};

function storageKey(drawingId: string, floor: string): string {
  return `${STORAGE_PREFIX}${encodeURIComponent(drawingId)}:${encodeURIComponent(floor)}`;
}

function readStored(drawingId: string, floor: string): StoredLegend | null {
  try {
    const raw = localStorage.getItem(storageKey(drawingId, floor));
    if (!raw) return null;
    const parsed = JSON.parse(raw) as Partial<StoredLegend>;
    if (!Array.isArray(parsed.selected) || !Array.isArray(parsed.known)) return null;
    const selected = parsed.selected.filter((name): name is string => typeof name === "string");
    const known = parsed.known.filter((name): name is string => typeof name === "string");
    return { selected, known };
  } catch {
    return null;
  }
}

/** Restore checked rows. Unknown/new labels default on; previously unchecked stay off. */
export function resolveLegendSelection(
  available: readonly string[],
  stored: StoredLegend | null,
): Set<string> {
  if (!stored) return new Set(available);
  const known = new Set(stored.known);
  const picked = new Set(stored.selected);
  const next = new Set<string>();
  for (const name of available) {
    if (!known.has(name) || picked.has(name)) next.add(name);
  }
  return next;
}

export function loadLegendSelection(drawingId: string, floor: string, available: readonly string[]): Set<string> {
  return resolveLegendSelection(available, readStored(drawingId, floor));
}

export function saveLegendSelection(
  drawingId: string,
  floor: string,
  selected: Set<string>,
  known: readonly string[],
): void {
  try {
    const payload: StoredLegend = {
      selected: known.filter((name) => selected.has(name)),
      known: [...known],
    };
    localStorage.setItem(storageKey(drawingId, floor), JSON.stringify(payload));
  } catch {
    /* ignore quota / private mode */
  }
}
