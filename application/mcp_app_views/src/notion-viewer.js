// Notion viewer (MCP App) for the hosted Notion MCP tool results.
// Follows Claude's MCP Apps design guidelines: inline carousel / card, fullscreen
// master-detail, text entry stays in chat, no menus, host style tokens, skeleton loading.
const { App } = window.__ext;
const root = document.getElementById("root");
const MAX_INLINE = 8;

const app = new App(
  { name: "Notion viewer", version: "1.0.0" },
  { availableDisplayModes: ["inline", "fullscreen"] },
);

const S = {
  ctx: {},            // merged hostContext (updates carry only changed fields)
  input: null,        // arguments the model passed to the tool
  result: undefined,  // undefined = waiting, null = unreadable
  filter: "all",
  selected: null,     // item shown in the fullscreen reader
  page: null,         // fetched page for the reader
  pageLoading: false,
  pageError: null,
  tab: "list",        // compact fullscreen: "list" | "reader"
};

// ---------------------------------------------------------------- data
function parse(result) {
  for (const b of (result && result.content) || []) {
    if (b && typeof b.text === "string") {
      try { return JSON.parse(b.text); } catch (_) { return { _text: b.text }; }
    }
  }
  return null;
}
const iconOf = (ic) => (!ic ? "📄" : typeof ic === "string" ? (/^https?:/i.test(ic) ? "📄" : ic) : ic.emoji || "📄");
const items = () => (S.result && Array.isArray(S.result.results) ? S.result.results : []);
const kind = () => (!S.result ? "none" : Array.isArray(S.result.results) ? "list" : S.result.title !== undefined || S.result.text !== undefined ? "page" : "raw");
const isFs = () => S.ctx.displayMode === "fullscreen";
const width = () => (S.ctx.containerDimensions && S.ctx.containerDimensions.width) || window.innerWidth;
const compact = () => width() < 640;
function heading() {
  const q = S.input && (S.input.query || S.input.search);
  if (q) return `검색: ${q}`;
  return items().some((r) => r.highlight) ? "검색 결과" : "페이지";
}
const filtered = () => items().filter((r) => S.filter === "all" || (r.type || "page") === S.filter);

// ---------------------------------------------------------------- views
const meta = (r) => [r.type, r.timestamp ? kit.fmtDate(r.timestamp) : ""].filter(Boolean).join(" · ");

function card(r, i) {
  return `<article class="nv-card" role="listitem">
    <span class="nv-icon" aria-hidden="true">${kit.esc(iconOf(r.icon))}</span>
    <div class="nv-title">${kit.esc(r.title || "제목 없음")}</div>
    <div class="k-caption k-faint">${kit.esc(meta(r))}</div>
    ${r.highlight ? `<div class="nv-snip k-caption k-muted">${kit.esc(String(r.highlight).slice(0, 200))}</div>` : ""}
    <button class="k-btn" data-act="open" data-i="${i}" aria-label="${kit.esc((r.title || "") + " 열기")}">열기</button>
  </article>`;
}

function inlineList() {
  const all = items();
  if (!all.length) return `<div class="k-empty k-muted">표시할 페이지가 없습니다.</div>`;
  const shown = all.slice(0, MAX_INLINE);
  const more = all.length > MAX_INLINE;
  return `<div class="nv-head">
      <div><span class="k-strong">Notion</span> <span class="k-caption k-faint">· ${kit.esc(heading())} · ${all.length}개</span></div>
      ${more || canFullscreen() ? `<button class="k-btn" data-act="expand">${more ? `전체 ${all.length}개 보기` : "전체 화면"}</button>` : ""}
    </div>
    <div class="nv-carousel" role="list" aria-label="Notion 페이지">${shown.map(card).join("")}</div>`;
}

function inlinePage() {
  const p = S.result;
  return `<div class="nv-page">
    <div style="display:flex;gap:8px;align-items:flex-start">
      <span class="nv-icon" aria-hidden="true">${kit.esc(iconOf(p.icon))}</span>
      <div style="min-width:0"><div class="nv-title">${kit.esc(p.title || "제목 없음")}</div>
      <div class="k-caption k-faint">${p.page_last_edited_at ? "수정 " + kit.esc(kit.fmtDate(p.page_last_edited_at)) : ""}</div></div>
    </div>
    <div class="nv-excerpt k-muted">${kit.esc(kit.excerpt(p.text || p._text, 320))}</div>
    <div class="nv-actions">
      ${kit.safeUrl(p.url) ? `<button class="k-btn k-primary" data-act="openpage">Notion에서 열기</button>` : ""}
      ${canFullscreen() ? `<button class="k-btn" data-act="expand">전체 보기</button>` : ""}
    </div></div>`;
}

function reader() {
  if (S.pageLoading) return `<div class="nv-reader" aria-busy="true">${kit.skeleton(1, 28)}${kit.skeleton(4, 16)}</div>`;
  if (S.pageError) return `<div class="nv-reader"><div class="k-error" role="alert">${kit.esc(S.pageError)}</div></div>`;
  const p = S.page || (kind() === "page" ? S.result : null);
  if (!p) return `<div class="nv-reader k-empty k-muted">왼쪽에서 페이지를 선택하세요.</div>`;
  return `<div class="nv-reader">
    <div class="nv-reader-head"><h2>${kit.esc(iconOf(p.icon))} ${kit.esc(p.title || "제목 없음")}</h2>
      ${kit.safeUrl(p.url) ? `<button class="k-btn" data-act="openurl" data-url="${kit.esc(p.url)}">Notion에서 열기</button>` : ""}</div>
    <div class="k-caption k-faint" style="margin-bottom:8px">${p.page_last_edited_at ? "수정 " + kit.esc(kit.fmtDate(p.page_last_edited_at)) : ""}</div>
    <div class="k-md">${kit.renderMarkdown(p.text || p._text || "")}</div></div>`;
}

function listPane() {
  const types = [...new Set(items().map((r) => r.type || "page"))];
  const seg = types.length > 1
    ? `<div class="k-seg nv-filters" role="group" aria-label="유형">${["all", ...types].map((t) =>
        `<button data-act="filter" data-f="${kit.esc(t)}" aria-pressed="${S.filter === t}">${t === "all" ? "전체" : t === "page" ? "페이지" : t === "database" ? "데이터베이스" : kit.esc(t)}</button>`).join("")}</div>`
    : "";
  const rows = filtered().map((r) => {
    const i = items().indexOf(r);
    return `<button class="nv-row" data-act="select" data-i="${i}" aria-current="${S.selected === r}">
      <span class="nv-icon" aria-hidden="true">${kit.esc(iconOf(r.icon))}</span>
      <span style="min-width:0"><span class="nv-title">${kit.esc(r.title || "제목 없음")}</span>
      <span class="k-caption k-faint" style="display:block">${kit.esc(meta(r))}</span></span></button>`;
  }).join("");
  return `<div class="nv-list">${seg}${rows || `<div class="k-empty k-muted">해당하는 항목이 없습니다.</div>`}</div>`;
}

function fullscreen() {
  if (kind() === "page") return `<div class="nv-fs nv-compact" style="grid-template-rows:minmax(0,1fr)">${reader()}</div>`;
  if (compact()) {
    const tabs = `<div class="k-seg" role="group" aria-label="보기">
      <button data-act="tab" data-t="list" aria-pressed="${S.tab === "list"}">목록</button>
      <button data-act="tab" data-t="reader" aria-pressed="${S.tab === "reader"}">본문</button></div>`;
    return `<div class="nv-fs nv-compact"><div>${tabs}</div>${S.tab === "list" ? listPane() : reader()}</div>`;
  }
  return `<div class="nv-fs">${listPane()}${reader()}</div>`;
}

const canFullscreen = () => (S.ctx.availableDisplayModes || []).includes("fullscreen") && !isFs();

function render() {
  if (S.result === undefined) { root.innerHTML = `<div aria-busy="true"><div class="k-skel" style="height:176px"></div></div>`; return; }
  if (isFs()) { root.innerHTML = fullscreen(); return; }
  const k = kind();
  root.innerHTML =
    k === "list" ? inlineList()
    : k === "page" ? inlinePage()
    : `<div class="k-empty k-muted">${kit.esc(S.result && S.result._text ? kit.excerpt(S.result._text, 300) : "표시할 내용이 없습니다.")}</div>`;
}

// ---------------------------------------------------------------- actions
const openUrl = (u) => { const url = kit.safeUrl(u); if (url) app.openLink({ url }).catch(() => {}); };

async function selectItem(r) {
  S.selected = r; S.page = null; S.pageError = null; S.pageLoading = true; S.tab = "reader"; render();
  try {
    const res = await app.callServerTool({ name: "notion-fetch", arguments: { id: r.url || r.id } });
    if (res && res.isError) throw new Error("페이지를 불러오지 못했습니다.");
    S.page = parse(res);
  } catch (e) {
    S.pageError = e && e.message ? e.message : String(e);
  } finally {
    S.pageLoading = false; render();
  }
}

root.addEventListener("click", async (ev) => {
  const el = ev.target.closest("[data-act],[data-href]");
  if (!el) return;
  if (el.dataset.href) { openUrl(el.dataset.href); return; }
  const act = el.dataset.act, i = Number(el.dataset.i), r = items()[i];
  if (act === "open" && r) openUrl(r.url);
  else if (act === "openpage") openUrl(S.result && S.result.url);
  else if (act === "openurl") openUrl(el.dataset.url);
  else if (act === "expand") await app.requestDisplayMode({ mode: "fullscreen" }).catch(() => {});
  else if (act === "select" && r) selectItem(r);
  else if (act === "filter") { S.filter = el.dataset.f; render(); }
  else if (act === "tab") { S.tab = el.dataset.t; render(); }
});

// ---------------------------------------------------------------- lifecycle
app.ontoolinput = (p) => { S.input = (p && p.arguments) || null; };
app.ontoolresult = (r) => {
  S.result = r && r.isError ? null : parse(r);
  S.selected = null; S.page = null; S.pageError = null; S.tab = "list";
  render();
};
app.addEventListener("hostcontextchanged", (changed) => {
  S.ctx = { ...S.ctx, ...changed };
  kit.applyHost(changed);
  render();
});

render();
await app.connect();
S.ctx = { ...(app.getHostContext() || {}) };
kit.applyHost(S.ctx);
render();
