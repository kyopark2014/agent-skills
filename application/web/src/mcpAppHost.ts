/**
 * Host-side helpers for MCP Apps (https://claude.com/docs/connectors/building/mcp-apps).
 *
 * - style variables: token *names* follow Claude's host contract; *values* come from
 *   this app's own theme so apps blend into agentic-work rather than into Claude.
 * - per-resource CSP: built from the `_meta.ui.csp` a `ui://` resource declares.
 * - resource loading: the host reads a `ui://` resource once and caches it, instead of
 *   shipping the HTML with every tool result.
 */

export type AppTheme = "light" | "dark";

export interface AppCspMeta {
  connectDomains?: string[];
  resourceDomains?: string[];
  frameDomains?: string[];
  baseUriDomains?: string[];
}

export interface AppResourceMeta {
  csp?: AppCspMeta;
  prefersBorder?: boolean;
}

export interface AppResource {
  html: string;
  mimeType?: string;
  meta: AppResourceMeta;
}

export function currentTheme(): AppTheme {
  return document.documentElement.getAttribute("data-theme") === "light"
    ? "light"
    : "dark";
}

function cssVar(name: string, fallback: string): string {
  const v = getComputedStyle(document.documentElement).getPropertyValue(name).trim();
  return v || fallback;
}

const mix = (color: string, pct: number) =>
  `color-mix(in srgb, ${color} ${pct}%, transparent)`;

// Status colors have no equivalent in this app's theme; use Claude's published values.
const STATUS = {
  light: {
    bg: { info: "#D6E4F6", danger: "#F7ECEC", success: "#E9F1DC", warning: "#F6EEDF" },
    text: { info: "#3266AD", danger: "#7F2C28", success: "#265B19", warning: "#5A4815" },
    border: { info: "#4682D5", danger: "#A73D39", success: "#437426", warning: "#805C1F" },
  },
  dark: {
    bg: { info: "#253E5F", danger: "#602A28", success: "#1B4614", warning: "#483A0F" },
    text: { info: "#80AADD", danger: "#EE8884", success: "#7AB948", warning: "#D1A041" },
    border: { info: "#4682D5", danger: "#CD5C58", success: "#599130", warning: "#A87829" },
  },
} as const;

/** `hostContext.styles.variables` for the current theme (keys include the `--` prefix). */
export function buildStyleVariables(theme: AppTheme = currentTheme()): Record<string, string> {
  const bg1 = cssVar("--bg-primary", theme === "light" ? "#ffffff" : "#0d0d0d");
  const bg2 = cssVar("--bg-secondary", theme === "light" ? "#f7f7f9" : "#171717");
  const bg3 = cssVar("--bg-tertiary", theme === "light" ? "#ececf1" : "#212121");
  const t1 = cssVar("--text-primary", theme === "light" ? "#0d0d0d" : "#ececec");
  const t2 = cssVar("--text-secondary", "#9b9b9b");
  const t3 = cssVar("--text-muted", "#6b6b6b");
  const font = cssVar("--font", "system-ui, sans-serif");
  const s = STATUS[theme];

  const v: Record<string, string> = {
    // Background
    "--color-background-primary": bg1,
    "--color-background-secondary": bg2,
    "--color-background-tertiary": bg3,
    "--color-background-inverse": t1,
    "--color-background-ghost": "transparent",
    "--color-background-info": s.bg.info,
    "--color-background-danger": s.bg.danger,
    "--color-background-success": s.bg.success,
    "--color-background-warning": s.bg.warning,
    "--color-background-disabled": mix(bg1, 50),
    // Text
    "--color-text-primary": t1,
    "--color-text-secondary": t2,
    "--color-text-tertiary": t3,
    "--color-text-inverse": bg1,
    "--color-text-ghost": mix(t3, 50),
    "--color-text-info": s.text.info,
    "--color-text-danger": s.text.danger,
    "--color-text-success": s.text.success,
    "--color-text-warning": s.text.warning,
    "--color-text-disabled": mix(t1, 50),
    // Border
    "--color-border-primary": mix(t1, 40),
    "--color-border-secondary": mix(t1, 30),
    "--color-border-tertiary": mix(t1, 15),
    "--color-border-inverse": mix(bg1, 30),
    "--color-border-ghost": "transparent",
    "--color-border-info": s.border.info,
    "--color-border-danger": s.border.danger,
    "--color-border-success": s.border.success,
    "--color-border-warning": s.border.warning,
    "--color-border-disabled": mix(t1, 10),
    // Ring
    "--color-ring-primary": mix(t1, 70),
    "--color-ring-secondary": mix(t2, 70),
    "--color-ring-inverse": mix(bg1, 70),
    "--color-ring-info": mix(s.text.info, 50),
    "--color-ring-danger": mix(s.border.danger, 50),
    "--color-ring-success": mix(s.border.success, 50),
    "--color-ring-warning": mix(s.border.warning, 50),
    // Typography
    "--font-sans": font,
    "--font-mono": "ui-monospace, monospace",
    "--font-weight-normal": "400",
    "--font-weight-medium": "500",
    "--font-weight-semibold": "600",
    "--font-weight-bold": "700",
    "--font-text-xs-size": "12px",
    "--font-text-sm-size": "14px",
    "--font-text-md-size": "16px",
    "--font-text-lg-size": "20px",
    "--font-heading-xs-size": "12px",
    "--font-heading-sm-size": "14px",
    "--font-heading-md-size": "16px",
    "--font-heading-lg-size": "20px",
    "--font-heading-xl-size": "24px",
    "--font-heading-2xl-size": "28px",
    "--font-heading-3xl-size": "36px",
    "--font-text-xs-line-height": "1.4",
    "--font-text-sm-line-height": "1.4",
    "--font-text-md-line-height": "1.4",
    "--font-text-lg-line-height": "1.25",
    "--font-heading-xs-line-height": "1.4",
    "--font-heading-sm-line-height": "1.4",
    "--font-heading-md-line-height": "1.4",
    "--font-heading-lg-line-height": "1.25",
    "--font-heading-xl-line-height": "1.25",
    "--font-heading-2xl-line-height": "1.1",
    "--font-heading-3xl-line-height": "1",
    // Shape
    "--border-radius-xs": "4px",
    "--border-radius-sm": "6px",
    "--border-radius-md": "8px",
    "--border-radius-lg": "10px",
    "--border-radius-xl": "12px",
    "--border-radius-full": "9999px",
    "--border-width-regular": "0.5px",
    "--shadow-hairline": "0 1px 2px 0 rgba(0, 0, 0, 0.05)",
    "--shadow-sm": "0 1px 3px 0 rgba(0, 0, 0, 0.1), 0 1px 2px -1px rgba(0, 0, 0, 0.1)",
    "--shadow-md": "0 4px 6px -1px rgba(0, 0, 0, 0.1), 0 2px 4px -2px rgba(0, 0, 0, 0.1)",
    "--shadow-lg": "0 10px 15px -3px rgba(0, 0, 0, 0.1), 0 4px 6px -4px rgba(0, 0, 0, 0.1)",
  };
  return v;
}

// --------------------------------------------------------------------------- CSP

// Origins must start with a scheme (https://, wss://, ...) or be `blob:`/`data:`.
const ORIGIN_RE = /^(https?|wss?):\/\/[^\s;,'"]+$|^(blob|data):$/i;

function origins(list: string[] | undefined): string[] {
  return (list || []).filter((o) => typeof o === "string" && ORIGIN_RE.test(o.trim())).map((o) => o.trim());
}

/**
 * Content-Security-Policy for one `ui://` resource, built only from what it declared
 * (everything external is blocked by default). Applied as a <meta> inside the srcdoc, so
 * it can only further restrict what the host document's own policy already allows.
 */
export function buildAppCsp(csp?: AppCspMeta): string {
  const res = origins(csp?.resourceDomains).join(" ");
  const connect = origins(csp?.connectDomains).join(" ");
  const frames = origins(csp?.frameDomains).join(" ");
  return [
    "default-src 'none'",
    `script-src 'unsafe-inline' ${res}`.trim(),
    `style-src 'unsafe-inline' ${res}`.trim(),
    `img-src data: blob: ${res}`.trim(),
    `font-src data: ${res}`.trim(),
    `media-src data: blob: ${res}`.trim(),
    `worker-src blob: ${res}`.trim(),
    connect ? `connect-src ${connect}` : "connect-src 'none'",
    frames ? `frame-src ${frames}` : "frame-src 'none'",
    "base-uri 'none'",
    "form-action 'none'",
  ].join("; ");
}

/** Insert the CSP <meta> before any script so it governs the whole document. */
export function injectCspMeta(html: string, policy: string): string {
  const meta = `<meta http-equiv="Content-Security-Policy" content="${policy.replace(/"/g, "&quot;")}">`;
  const head = /<head[^>]*>/i.exec(html);
  if (head) return html.slice(0, head.index + head[0].length) + meta + html.slice(head.index + head[0].length);
  const root = /<html[^>]*>/i.exec(html);
  if (root) return html.slice(0, root.index + root[0].length) + `<head>${meta}</head>` + html.slice(root.index + root[0].length);
  const doctype = /<!doctype[^>]*>/i.exec(html);
  if (doctype) return html.slice(0, doctype.index + doctype[0].length) + `<head>${meta}</head>` + html.slice(doctype.index + doctype[0].length);
  return `<head>${meta}</head>` + html;
}

// ---------------------------------------------------------------------- resources

const resourceCache = new Map<string, Promise<AppResource>>();

/** Read a `ui://` resource through the host (cached per server+uri for this page). */
export function fetchMcpAppResource(server: string, uri: string): Promise<AppResource> {
  const key = `${server}\n${uri}`;
  let pending = resourceCache.get(key);
  if (!pending) {
    pending = (async () => {
      const qs = new URLSearchParams({ server, uri });
      const res = await fetch(`/api/mcp-apps/resource?${qs}`, { credentials: "include" });
      const data = await res.json().catch(() => ({}));
      if (!res.ok) throw new Error(data?.detail || `HTTP ${res.status}`);
      return data as AppResource;
    })();
    resourceCache.set(key, pending);
    pending.catch(() => resourceCache.delete(key)); // do not cache failures
  }
  return pending;
}
