"""
MCP Apps weather server for 기상청 단기예보 종합 + 시간별 예보.

Sources:
- https://www.weather.go.kr/w/forecast/overall/short-term.do?stnId={region}
- https://www.weather.go.kr/w/wnuri-fct2021/main/digital-forecast.do (hourly chart)
"""
from __future__ import annotations

import json
import logging
import re
import sys
from datetime import datetime
from typing import Any
from urllib.parse import urljoin
from zoneinfo import ZoneInfo

import requests
from bs4 import BeautifulSoup
from mcp.server.apps import Apps, ResourceCsp
from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.context import Context

logging.basicConfig(
    level=logging.INFO,
    format="%(filename)s:%(lineno)d | %(message)s",
    handlers=[logging.StreamHandler(sys.stderr)],
)
logger = logging.getLogger("mcp-server-weather")

KST = ZoneInfo("Asia/Seoul")
WEATHER_DOMAIN = "https://www.weather.go.kr"
SOURCE_PAGE = f"{WEATHER_DOMAIN}/w/forecast/overall/short-term.do"
DIGITAL_FORECAST_URL = f"{WEATHER_DOMAIN}/w/wnuri-fct2021/main/digital-forecast.do"
ICON_BASE = f"{WEATHER_DOMAIN}/w/resources/icon"
VIEW_URI = "ui://kma-short-term/weather.html"

# Regional forecast-office tabs on short-term.do + representative dong for hourly
REGIONS: dict[str, dict[str, Any]] = {
    "108": {
        "ko": "전국",
        "aliases": ["전국", "korea", "national", "전체"],
        "site": {
            "label": "서울 종로구",
            "code": "1111053000",
            "lat": 37.5691469686793,
            "lon": 126.978647068151,
        },
    },
    "109": {
        "ko": "서울·인천·경기",
        "aliases": [
            "서울", "인천", "경기", "수도권", "서울특별시", "인천광역시",
            "경기도", "수원", "파주", "동두천", "강화", "양평", "이천",
            "seoul", "incheon", "gyeonggi", "metropolitan",
        ],
        "site": {
            "label": "서울 종로구",
            "code": "1111053000",
            "lat": 37.5691469686793,
            "lon": 126.978647068151,
        },
    },
    "159": {
        "ko": "부산·울산·경남",
        "aliases": [
            "부산", "울산", "경남", "경상남도", "부울경", "창원",
            "busan", "ulsan", "gyeongnam",
        ],
        "site": {
            "label": "부산 중구",
            "code": "2611056000",
            "lat": 35.0988678741728,
            "lon": 129.029045595229,
        },
    },
    "143": {
        "ko": "대구·경북",
        "aliases": ["대구", "경북", "경상북도", "포항", "daegu", "gyeongbuk"],
        "site": {
            "label": "대구 중구",
            "code": "2711068000",
            "lat": 35.8602851249823,
            "lon": 128.6069533801216,
        },
    },
    "156": {
        "ko": "전남광주통합특별시",
        "aliases": [
            "광주", "전남", "전라남도", "광주광역시", "목포", "여수",
            "gwangju", "jeonnam",
        ],
        "site": {
            "label": "광주 동구",
            "code": "1221052500",
            "lat": 35.1474913380556,
            "lon": 126.919801652795,
        },
    },
    "146": {
        "ko": "전북특별자치도",
        "aliases": ["전북", "전라북도", "전주", "군산", "jeonbuk"],
        "site": {
            "label": "전주",
            "code": "5211153000",
            "lat": 35.81477744329797,
            "lon": 127.15255700142177,
        },
    },
    "133": {
        "ko": "대전·세종·충남",
        "aliases": [
            "대전", "세종", "충남", "충청남도", "천안",
            "daejeon", "sejong", "chungnam",
        ],
        "site": {
            "label": "대전 서구",
            "code": "3017065000",
            "lat": 36.36994889192411,
            "lon": 127.38801706985069,
        },
    },
    "131": {
        "ko": "충북",
        "aliases": ["충북", "충청북도", "청주", "chungbuk"],
        "site": {
            "label": "청주",
            "code": "4311453000",
            "lat": 36.6623007044479,
            "lon": 127.5413954468505,
        },
    },
    "105": {
        "ko": "강원특별자치도",
        "aliases": ["강원", "강원도", "춘천", "강릉", "gangwon"],
        "site": {
            "label": "춘천",
            "code": "5111057000",
            "lat": 37.86433423002352,
            "lon": 127.69129278762853,
        },
    },
    "184": {
        "ko": "제주특별자치도",
        "aliases": ["제주", "제주도", "서귀포", "jeju"],
        "site": {
            "label": "제주시",
            "code": "5011054000",
            "lat": 33.4966553222693,
            "lon": 126.53592579247683,
        },
    },
}

REQUEST_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "ko-KR,ko;q=0.9,en;q=0.8",
    "Referer": SOURCE_PAGE,
}

WEATHER_HTML = r"""<!doctype html>
<html lang="ko">
<head>
  <meta charset="UTF-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>기상청 단기예보 종합</title>
  <style>
    :root {
      --bg: #0b1f33;
      --panel: rgba(255,255,255,0.08);
      --text: #f4f8fc;
      --muted: #9db4c8;
      --accent: #3db8ff;
      --cool: #7ed0ff;
      --warm: #ffb454;
      --line: rgba(255,255,255,0.12);
      --chart: #4aa3ff;
    }
    * { box-sizing: border-box; }
    html, body {
      margin: 0; padding: 0;
      background: radial-gradient(1200px 500px at 10% -10%, #1a4d7a 0%, transparent 55%),
                  radial-gradient(900px 420px at 100% 0%, #0f3a5c 0%, transparent 50%),
                  var(--bg);
      color: var(--text);
      font-family: "Pretendard", "Apple SD Gothic Neo", "Noto Sans KR", sans-serif;
      min-height: 100%;
    }
    .wrap { padding: 16px; max-width: 960px; margin: 0 auto; }
    header {
      display: flex; justify-content: space-between; gap: 12px; align-items: flex-start;
      margin-bottom: 12px;
    }
    h1 { margin: 0; font-size: 1.15rem; letter-spacing: -0.02em; }
    .sub { color: var(--muted); font-size: 0.8rem; margin-top: 4px; }
    .actions { display: flex; gap: 8px; flex-wrap: wrap; }
    button, select {
      appearance: none; border: 1px solid var(--line); background: var(--panel);
      color: var(--text); border-radius: 10px; padding: 8px 12px; font-size: 0.85rem;
      cursor: pointer;
    }
    button:hover, select:hover { border-color: var(--accent); }
    button.primary { background: linear-gradient(135deg, #1f6fad, #2aa0df); border-color: transparent; }
    .status { color: var(--muted); font-size: 0.78rem; margin-bottom: 10px; min-height: 1.1em; }
    .panel {
      background: linear-gradient(160deg, rgba(255,255,255,0.10), rgba(255,255,255,0.04));
      border: 1px solid var(--line);
      border-radius: 16px;
      padding: 14px;
      margin-bottom: 12px;
    }
    .period { font-size: 1rem; font-weight: 700; margin-bottom: 6px; }
    .announce { color: var(--muted); font-size: 0.82rem; margin-bottom: 10px; }
    .summary { display: flex; flex-direction: column; gap: 6px; }
    .line {
      font-size: 0.88rem; line-height: 1.45;
      padding: 8px 10px; border-radius: 10px; background: rgba(0,0,0,0.18);
    }
    .line.depth_1 { border-left: 3px solid var(--accent); font-weight: 650; }
    .line.depth_2 { border-left: 3px solid var(--cool); }
    .line.depth_3, .line.depth_4 { border-left: 3px solid var(--warm); color: #dce8f3; font-size: 0.82rem; }
    .section-title {
      margin: 4px 0 8px; font-size: 0.92rem; color: var(--cool); font-weight: 650;
      display: flex; justify-content: space-between; align-items: baseline; gap: 8px;
    }
    .section-title .hint { color: var(--muted); font-size: 0.75rem; font-weight: 400; }
    .table-wrap { overflow-x: auto; -webkit-overflow-scrolling: touch; }
    table {
      width: 100%; border-collapse: collapse; min-width: 560px;
      font-size: 0.8rem;
    }
    th, td {
      border: 1px solid var(--line); padding: 8px 6px; text-align: center;
      white-space: nowrap;
    }
    th { background: rgba(61,184,255,0.12); color: var(--cool); font-weight: 650; }
    td.label, th.label { text-align: left; color: var(--text); background: rgba(0,0,0,0.16); }
    .links { display: flex; gap: 10px; flex-wrap: wrap; margin-top: 8px; }
    .links a { color: var(--accent); font-size: 0.8rem; text-decoration: none; }
    .links a:hover { text-decoration: underline; }
    .empty { color: var(--muted); padding: 24px 8px; text-align: center; }

    /* Hourly forecast chart (날씨누리 시간별 예보 스타일) */
    .hourly-wrap { overflow-x: auto; -webkit-overflow-scrolling: touch; }
    .hourly {
      display: grid;
      grid-template-columns: 72px 1fr;
      min-width: 100%;
      font-size: 0.72rem;
    }
    .hourly-labels {
      display: flex; flex-direction: column;
      color: var(--muted); font-weight: 650;
      border-right: 1px solid var(--line);
      background: rgba(0,0,0,0.12);
    }
    .hourly-labels > div {
      height: 36px; display: flex; align-items: center; padding: 0 8px;
      border-bottom: 1px solid var(--line);
    }
    .hourly-labels > .chart-label { height: 88px; }
    .hourly-scroll { overflow: visible; }
    .hourly-cols {
      position: relative;
      display: grid;
      grid-auto-flow: column;
      grid-auto-columns: 56px;
    }
    .hcol {
      border-right: 1px solid rgba(255,255,255,0.06);
      text-align: center;
    }
    .hcol > div {
      height: 36px; display: flex; align-items: center; justify-content: center;
      border-bottom: 1px solid var(--line); padding: 0 2px;
    }
    .hcol .date-cell {
      color: #fff; font-weight: 700; font-size: 0.68rem;
      background: linear-gradient(180deg, #2f6dad, #1d4f80);
    }
    .hcol .date-cell.empty-date { background: transparent; color: transparent; }
    .hcol img.wic { width: 28px; height: 28px; }
    .hcol .chart-cell {
      height: 88px; position: relative; overflow: visible;
    }
    .hcol .temp-point {
      position: absolute; left: 50%; transform: translate(-50%, -50%);
      color: var(--chart); font-weight: 700; font-size: 0.78rem;
      white-space: nowrap; z-index: 2;
    }
    .hourly-svg {
      position: absolute;
      left: 0;
      top: 108px; /* 날짜+시각+날씨 = 36*3 */
      height: 88px;
      overflow: visible;
      pointer-events: none;
      z-index: 1;
    }
  </style>
</head>
<body>
  <div class="wrap">
    <header>
      <div>
        <h1>기상청 단기예보 종합</h1>
        <div class="sub">weather.go.kr · 예보 종합 / 단기예보 · 시간별 예보</div>
      </div>
      <div class="actions">
        <select id="region-filter" title="지역"></select>
        <button class="primary" id="refresh-btn">새로고침</button>
      </div>
    </header>
    <div class="status" id="status">불러오는 중…</div>
    <div id="main"></div>
    <div class="links">
      <a id="source-link" href="https://www.weather.go.kr/w/forecast/overall/short-term.do" target="_blank" rel="noreferrer">단기예보 종합</a>
      <a id="pdf-link" href="#" target="_blank" rel="noreferrer" style="display:none;">PDF 통보문</a>
      <a href="https://www.weather.go.kr/w/index.do" target="_blank" rel="noreferrer">날씨누리</a>
    </div>
  </div>
  <script>
    const statusEl = document.getElementById("status");
    const mainEl = document.getElementById("main");
    const regionFilter = document.getElementById("region-filter");
    const refreshBtn = document.getElementById("refresh-btn");
    const sourceLink = document.getElementById("source-link");
    const pdfLink = document.getElementById("pdf-link");

    let payload = null;
    let nextId = 1;
    const pending = new Map();

    function post(msg) {
      parent.postMessage(msg, "*");
    }

    function request(method, params) {
      const id = nextId++;
      return new Promise((resolve, reject) => {
        pending.set(id, { resolve, reject });
        post({ jsonrpc: "2.0", id, method, params });
        setTimeout(() => {
          if (pending.has(id)) {
            pending.delete(id);
            reject(new Error("timeout"));
          }
        }, 30000);
      });
    }

    function dash(v) {
      if (v === null || v === undefined || v === "" || v === "-") return "-";
      return String(v);
    }

    function escapeHtml(s) {
      return String(s)
        .replaceAll("&", "&amp;")
        .replaceAll("<", "&lt;")
        .replaceAll(">", "&gt;")
        .replaceAll('"', "&quot;");
    }

    function parsePayload(result) {
      const text = result?.content?.find((c) => c.type === "text")?.text;
      if (!text) return null;
      try {
        const parsed = JSON.parse(text);
        if (parsed && typeof parsed === "object") return parsed;
        return { summary: text };
      } catch {
        return { summary: text };
      }
    }

    function fillRegionOptions(regions, currentId) {
      regionFilter.innerHTML = "";
      for (const r of regions || []) {
        const opt = document.createElement("option");
        opt.value = r.stn_id;
        opt.textContent = r.name;
        regionFilter.appendChild(opt);
      }
      if (currentId) regionFilter.value = String(currentId);
    }

    function reportSize() {
      const height = Math.ceil(document.documentElement.getBoundingClientRect().height);
      post({
        jsonrpc: "2.0",
        method: "ui/notifications/size-changed",
        params: { height: height || 480, width: window.innerWidth },
      });
    }

    function weekdayKo(dateStr) {
      try {
        const d = new Date(dateStr + "T00:00:00");
        return ["일","월","화","수","목","금","토"][d.getDay()];
      } catch { return ""; }
    }

    function renderTable(days, rows) {
      if (!days || !days.length || !rows || !rows.length) return "";
      let head = "<tr><th class='label'>예보요소</th>";
      for (const d of days) head += "<th>" + escapeHtml(d) + "</th>";
      head += "</tr>";
      let body = "";
      for (const row of rows) {
        body += "<tr><td class='label'>" + escapeHtml(row.label) + "</td>";
        const vals = row.values || [];
        for (let i = 0; i < days.length; i++) {
          body += "<td>" + escapeHtml(dash(vals[i])) + "</td>";
        }
        body += "</tr>";
      }
      return (
        '<div class="section-title">기온·해상</div>' +
        '<div class="panel table-wrap"><table><thead>' + head +
        "</thead><tbody>" + body + "</tbody></table></div>"
      );
    }

    function renderHourly(hourly, site) {
      if (!hourly || !hourly.length) return "";
      const temps = hourly.map((h) => {
        const n = parseFloat(h.temp);
        return Number.isFinite(n) ? n : null;
      });
      const valid = temps.filter((t) => t !== null);
      const tMin = valid.length ? Math.min(...valid) - 1 : 0;
      const tMax = valid.length ? Math.max(...valid) + 1 : 30;
      const span = Math.max(tMax - tMin, 1);
      const chartH = 88;
      const padY = 14;
      const ys = temps.map((t) =>
        t === null ? null : padY + ((tMax - t) / span) * (chartH - padY * 2)
      );

      const colW = 56;
      let poly = [];
      for (let i = 0; i < ys.length; i++) {
        if (ys[i] === null) continue;
        poly.push((i * colW + colW / 2) + "," + ys[i]);
      }
      const svgW = hourly.length * colW;
      const svg =
        '<svg class="hourly-svg" width="' + svgW + '" height="' + chartH +
        '" viewBox="0 0 ' + svgW + " " + chartH +
        '" preserveAspectRatio="none" aria-hidden="true">' +
        '<polyline fill="none" stroke="#4aa3ff" stroke-width="2" points="' +
        poly.join(" ") + '" />' +
        poly.map((p) => {
          const [x, y] = p.split(",");
          return '<circle cx="' + x + '" cy="' + y + '" r="2.5" fill="#4aa3ff" />';
        }).join("") +
        "</svg>";

      let cols = "";
      let prevDate = "";
      for (let i = 0; i < hourly.length; i++) {
        const h = hourly[i];
        const date = h.date || "";
        const showDate = date && date !== prevDate;
        prevDate = date || prevDate;
        const dayNum = date ? Number(date.split("-")[2]) : "";
        const dateLabel = showDate
          ? (dayNum + "일(" + weekdayKo(date) + ")")
          : "";
        const y = ys[i];
        const tempLabel = h.temp != null && h.temp !== "" ? (dash(h.temp) + "℃") : "-";
        cols +=
          '<div class="hcol">' +
            '<div class="date-cell' + (showDate ? "" : " empty-date") + '">' +
              escapeHtml(dateLabel || ".") +
            "</div>" +
            "<div>" + escapeHtml(dash(h.hour_label || h.time)) + "</div>" +
            "<div>" +
              (h.icon_url
                ? '<img class="wic" src="' + escapeHtml(h.icon_url) + '" alt="' +
                  escapeHtml(dash(h.condition)) + '" title="' +
                  escapeHtml(dash(h.condition)) + '" />'
                : escapeHtml(dash(h.condition))) +
            "</div>" +
            '<div class="chart-cell">' +
              (y !== null
                ? '<span class="temp-point" style="top:' + y + 'px">' +
                  escapeHtml(tempLabel) + "</span>"
                : '<span class="temp-point" style="top:50%">-</span>') +
            "</div>" +
            "<div>" + escapeHtml(dash(h.feels_like) + (h.feels_like && h.feels_like !== "-" ? "℃" : "")) + "</div>" +
            "<div>" + escapeHtml(dash(h.precip_intensity)) + "</div>" +
            "<div>" + escapeHtml(dash(h.precip_prob)) + "</div>" +
            "<div title=\"" + escapeHtml(dash(h.wind)) + "\">" +
              escapeHtml(dash(h.wind_short || h.wind)) +
            "</div>" +
            "<div>" + escapeHtml(dash(h.humidity)) + "</div>" +
          "</div>";
      }

      const siteHint = site && site.label
        ? ('대표지점 ' + site.label + ' · 3시간 간격')
        : "3시간 간격";

      return (
        '<div class="section-title">시간별 예보' +
          '<span class="hint">' + escapeHtml(siteHint) + "</span></div>" +
        '<div class="panel hourly-wrap"><div class="hourly">' +
          '<div class="hourly-labels">' +
            "<div>날짜</div><div>시각</div><div>날씨</div>" +
            '<div class="chart-label">기온</div>' +
            "<div>체감온도</div><div>강수강도</div><div>강수확률</div>" +
            "<div>바람</div><div>습도</div>" +
          "</div>" +
          '<div class="hourly-scroll"><div class="hourly-cols">' +
            svg + cols +
          "</div></div></div></div>"
      );
    }

    function render() {
      if (!payload || !payload.region) {
        statusEl.textContent = "단기예보 데이터가 없습니다.";
        mainEl.innerHTML = '<div class="empty">도구를 다시 호출해 주세요.</div>';
        reportSize();
        return;
      }

      fillRegionOptions(payload.regions, payload.region.stn_id);
      statusEl.textContent = [
        payload.region.name ? ("지역 " + payload.region.name) : null,
        payload.announce || null,
        payload.fetched_at ? ("갱신 " + payload.fetched_at) : null,
      ].filter(Boolean).join(" · ");

      const lines = (payload.summary_lines || []).map((item) => {
        const depth = item.depth || "depth_2";
        const text = typeof item === "string" ? item : item.text;
        return '<div class="line ' + escapeHtml(depth) + '">' + escapeHtml(text) + "</div>";
      }).join("");

      mainEl.innerHTML =
        '<div class="panel">' +
          '<div class="period">' + escapeHtml(dash(payload.period)) + "</div>" +
          '<div class="announce">' + escapeHtml(dash(payload.announce)) + "</div>" +
          '<div class="summary">' + (lines || '<div class="empty">요약 없음</div>') + "</div>" +
        "</div>" +
        renderHourly(payload.hourly, payload.hourly_site) +
        renderTable(payload.days, payload.rows);

      if (payload.source_url) sourceLink.href = payload.source_url;
      if (payload.pdf_url) {
        pdfLink.href = payload.pdf_url;
        pdfLink.style.display = "";
      } else {
        pdfLink.style.display = "none";
      }
      reportSize();
    }

    function applyResult(result) {
      payload = parsePayload(result);
      render();
    }

    window.addEventListener("message", (event) => {
      const data = event.data;
      if (!data || data.jsonrpc !== "2.0") return;
      if (data.id !== undefined && (data.result !== undefined || data.error)) {
        const waiter = pending.get(data.id);
        if (!waiter) return;
        pending.delete(data.id);
        if (data.error) waiter.reject(new Error(data.error.message || "error"));
        else waiter.resolve(data.result);
        return;
      }
      if (data.method === "ui/notifications/tool-result") {
        applyResult(data.params || {});
      }
    });

    async function reload(region) {
      statusEl.textContent = "새로고침 중…";
      try {
        const result = await request("tools/call", {
          name: "get_weather",
          arguments: { region: region || regionFilter.value || "전국" },
        });
        applyResult(result);
      } catch (err) {
        statusEl.textContent = "새로고침 실패: " + (err && err.message ? err.message : err);
      }
    }

    refreshBtn.addEventListener("click", () => reload());
    regionFilter.addEventListener("change", () => reload(regionFilter.value));

    (async function connect() {
      const boot = window.__MCP_HOST_BOOTSTRAP__;
      if (boot && boot.toolResult) {
        applyResult(boot.toolResult);
      }
      try {
        await request("ui/initialize", {
          appInfo: { name: "KMA Short-term Forecast", version: "2.1.0" },
          appCapabilities: {},
          protocolVersion: "2026-01-26",
        });
        post({ jsonrpc: "2.0", method: "ui/notifications/initialized" });
        reportSize();
      } catch (err) {
        // Keep bootstrapped forecast visible when host handshake fails.
        if (!payload) {
          statusEl.textContent = "호스트 연결 실패: " + (err && err.message ? err.message : err);
        }
      }
    })();
  </script>
</body>
</html>
"""


def _norm(text: str | None) -> str:
    if not text:
        return ""
    return re.sub(r"[\s·⋅•./]", "", text.strip().lower())


def _region_catalog() -> list[dict[str, str]]:
    return [{"stn_id": sid, "name": meta["ko"]} for sid, meta in REGIONS.items()]


def resolve_region(region: str | None) -> tuple[str, str]:
    """Return (stn_id, region_name). Empty => 전국(108)."""
    if not region or not str(region).strip():
        return "108", REGIONS["108"]["ko"]

    raw = str(region).strip()
    if raw in REGIONS:
        return raw, REGIONS[raw]["ko"]

    needle = _norm(raw)
    for stn_id, meta in REGIONS.items():
        names = [_norm(meta["ko"]), *[_norm(a) for a in meta["aliases"]]]
        if needle in names:
            return stn_id, meta["ko"]

    for stn_id, meta in REGIONS.items():
        names = [_norm(meta["ko"]), *[_norm(a) for a in meta["aliases"]]]
        if any(needle in n or n in needle for n in names if n):
            return stn_id, meta["ko"]

    options = ", ".join(f"{sid}:{meta['ko']}" for sid, meta in REGIONS.items())
    raise ValueError(f"지원하지 않는 지역입니다: {region}. 가능 지역: {options}")


def _parse_summary_lines(summary) -> list[dict[str, str]]:
    lines: list[dict[str, str]] = []
    if not summary:
        return lines
    spans = summary.select("span")
    if spans:
        for sp in spans:
            text = " ".join(sp.stripped_strings)
            if not text:
                continue
            classes = sp.get("class") or []
            depth = next((c for c in classes if str(c).startswith("depth_")), "depth_2")
            lines.append({"text": text, "depth": depth})
        return lines
    for ln in summary.get_text("\n").splitlines():
        text = ln.strip()
        if text:
            lines.append({"text": text, "depth": "depth_2"})
    return lines


def _parse_table(section) -> tuple[list[str], list[dict[str, Any]]]:
    table = section.select_one("table.table-col")
    if not table:
        return [], []

    heads = [th.get_text(" ", strip=True) for th in table.select("thead th")]
    days = heads[1:] if len(heads) > 1 else heads
    rows: list[dict[str, Any]] = []
    pending_group: str | None = None

    for tr in table.select("tbody tr"):
        cells = [c.get_text(" ", strip=True) for c in tr.find_all(["th", "td"], recursive=False)]
        if not cells:
            continue
        if cells[0].startswith("파고"):
            pending_group = cells[0]
            if len(cells) >= 2:
                label = f"{pending_group} {cells[1]}" if cells[1] else pending_group
                values = cells[2:]
                rows.append({"label": label, "values": _align_values(days, values)})
            continue
        if pending_group and not any(x in cells[0] for x in ("최저기온", "최고기온")):
            label = f"{pending_group} {cells[0]}"
            values = cells[1:]
            rows.append({"label": label, "values": _align_values(days, values)})
            continue
        pending_group = None
        label = cells[0]
        values = cells[1:]
        rows.append({"label": label, "values": _align_values(days, values)})
    return days, rows


def _align_values(days: list[str], values: list[str]) -> list[str]:
    if len(values) == len(days):
        return values
    if len(values) < len(days):
        pad = len(days) - len(values)
        return (["-"] * pad) + values
    return values[: len(days)]


def _weather_icon_code(el) -> str | None:
    if not el:
        return None
    for cls in el.get("class") or []:
        text = str(cls)
        if text.startswith(("DB", "NB")):
            return text
    return None


def _weather_icon_url(code: str | None) -> str | None:
    if not code or code in {"DB00", "NB00"}:
        return None
    if code.startswith("NB"):
        folder = "NY@64/Light"
    else:
        folder = "DY@64/Light"
    return f"{ICON_BASE}/{folder}/{code}.png"


def _li_field(item, *labels: str) -> str | None:
    for li in item.select("li"):
        hid = li.select_one("span.hid")
        if not hid:
            continue
        label = hid.get_text(" ", strip=True).rstrip(":： ").replace(" ", "")
        if not any(lab.replace(" ", "") in label for lab in labels):
            continue
        for child in li.find_all("span"):
            classes = child.get("class") or []
            if "hid" in classes:
                continue
            text = child.get_text(" ", strip=True)
            if text:
                return text
        texts = []
        for child in li.children:
            name = getattr(child, "name", None)
            if name is None:
                t = str(child).strip()
                if t:
                    texts.append(t)
                continue
            classes = child.get("class") or []
            if "hid" in classes:
                continue
            texts.append(child.get_text(" ", strip=True))
        val = re.sub(r"\s+", " ", " ".join(texts)).strip()
        return val or None
    return None


def _num_celsius(value: str | None) -> str | None:
    if not value:
        return None
    m = re.search(r"([-\d.]+)", value)
    return m.group(1) if m else None


def parse_hourly_forecast(html: str) -> list[dict[str, Any]]:
    if not html:
        return []
    soup = BeautifulSoup(html, "html.parser")
    hourly: list[dict[str, Any]] = []
    for item in soup.select("ul.item[data-time]"):
        date = item.get("data-date") or ""
        time = item.get("data-time") or ""
        if not (date and time):
            continue

        temp = None
        feel_el = item.select_one("span.feel")
        if feel_el:
            temp = _num_celsius(feel_el.get_text(" ", strip=True))
        if temp is None:
            temp = _num_celsius(_li_field(item, "기온"))

        feels = _num_celsius(_li_field(item, "체감온도"))
        sky_el = item.select_one(".wic")
        icon = _weather_icon_code(sky_el)
        condition = None
        if sky_el:
            condition = sky_el.get("title") or sky_el.get_text(strip=True)

        wind = _li_field(item, "바람") or "-"
        wind_short = wind
        wspd = item.select_one(".wspd")
        wdir = item.select_one(".wdic")
        if wdir or wspd:
            parts = []
            if wdir:
                parts.append(wdir.get("title") or wdir.get_text(strip=True))
            if wspd:
                parts.append(wspd.get_text(strip=True))
            wind = " ".join(p for p in parts if p) or wind
            wind_short = wspd.get_text(strip=True) if wspd else wind

        intensity = _li_field(item, "강수강도")
        if intensity is None:
            # KMA chart places intensity as an unlabeled <li> after 강수량.
            lis = item.select("li")
            for idx, li in enumerate(lis):
                hid = li.select_one("span.hid")
                if hid and "강수량" in hid.get_text(" ", strip=True):
                    if idx + 1 < len(lis) and not lis[idx + 1].select_one("span.hid"):
                        intensity = lis[idx + 1].get_text(" ", strip=True) or "-"
                    break
        if not intensity:
            intensity = "-"

        hour_label = time
        if re.match(r"^\d{2}:\d{2}$", time):
            hour_label = f"{int(time[:2])}시"
        elif time == "24:00":
            hour_label = "24시"

        hourly.append(
            {
                "date": date,
                "time": time,
                "hour_label": hour_label,
                "temp": temp,
                "feels_like": feels,
                "condition": condition or "-",
                "icon": icon,
                "icon_url": _weather_icon_url(icon),
                "precip_amount": _li_field(item, "강수량") or "-",
                "precip_intensity": intensity,
                "precip_prob": _li_field(item, "강수확률") or "-",
                "wind": wind,
                "wind_short": wind_short,
                "humidity": _li_field(item, "습도") or "-",
            }
        )
    return hourly


def fetch_hourly_forecast(stn_id: str) -> tuple[list[dict[str, Any]], dict[str, Any] | None]:
    meta = REGIONS.get(stn_id) or {}
    site = meta.get("site")
    if not site:
        return [], None
    params = {
        "code": site["code"],
        "unit": "m/s",
        "hr1": "N",  # 3-hour interval (matches KMA chart default screenshot)
        "lat": site["lat"],
        "lon": site["lon"],
    }
    headers = {
        **REQUEST_HEADERS,
        "Accept": "text/html, */*; q=0.01",
        "X-Requested-With": "XMLHttpRequest",
        "Referer": f"{WEATHER_DOMAIN}/w/index.do",
    }
    resp = requests.get(DIGITAL_FORECAST_URL, params=params, headers=headers, timeout=20)
    resp.raise_for_status()
    resp.encoding = resp.apparent_encoding or "utf-8"
    return parse_hourly_forecast(resp.text), {
        "label": site["label"],
        "code": site["code"],
        "lat": site["lat"],
        "lon": site["lon"],
        "interval": "3h",
    }


def fetch_short_term_forecast(region: str | None = None) -> dict[str, Any]:
    stn_id, region_name = resolve_region(region)
    url = f"{SOURCE_PAGE}?stnId={stn_id}"
    resp = requests.get(url, headers=REQUEST_HEADERS, timeout=20)
    resp.raise_for_status()
    resp.encoding = resp.apparent_encoding or "utf-8"
    html = resp.text

    soup = BeautifulSoup(html, "html.parser")
    section = soup.select_one("section.cmp-view")
    if not section:
        raise RuntimeError("단기예보 본문을 찾지 못했습니다.")

    announce_el = section.select_one(".cmp-view-announce")
    announce = " ".join(announce_el.stripped_strings) if announce_el else ""
    header_el = section.select_one(".cmp-view-header h4, .cmp-view-header h3")
    period = header_el.get_text(strip=True) if header_el else ""
    summary_lines = _parse_summary_lines(section.select_one("p.summary"))
    days, rows = _parse_table(section)

    pdf_el = section.select_one("a.pdf-download") or soup.select_one('a[href*="rpt_wid_day"]')
    pdf_url = None
    if pdf_el and pdf_el.get("href"):
        pdf_url = urljoin(WEATHER_DOMAIN, pdf_el["href"])

    hourly: list[dict[str, Any]] = []
    hourly_site: dict[str, Any] | None = None
    try:
        hourly, hourly_site = fetch_hourly_forecast(stn_id)
    except Exception as exc:
        logger.warning("hourly forecast fetch failed for %s: %s", stn_id, exc)

    return {
        "source": SOURCE_PAGE,
        "source_url": url,
        "office": "기상청",
        "fetched_at": datetime.now(KST).strftime("%Y.%m.%d %H:%M"),
        "region": {"stn_id": stn_id, "name": region_name},
        "regions": _region_catalog(),
        "announce": announce,
        "period": period,
        "summary_lines": summary_lines,
        "days": days,
        "rows": rows,
        "pdf_url": pdf_url,
        "hourly": hourly,
        "hourly_site": hourly_site,
    }


def format_weather_text(payload: dict[str, Any]) -> str:
    region = payload.get("region") or {}
    lines = [
        f"[기상청] 단기예보 종합 — {region.get('name', '')}",
        f"출처: {payload.get('source_url') or payload.get('source')}",
    ]
    if payload.get("announce"):
        lines.append(payload["announce"])
    if payload.get("period"):
        lines.append(f"기간: {payload['period']}")
    lines.append("")

    for item in payload.get("summary_lines") or []:
        text = item.get("text") if isinstance(item, dict) else str(item)
        if text:
            lines.append(text)

    days = payload.get("days") or []
    temp_rows = [
        r
        for r in (payload.get("rows") or [])
        if "기온" in str(r.get("label", ""))
    ]
    if days and temp_rows:
        lines.append("")
        lines.append("기온: " + " | ".join(days))
        for row in temp_rows:
            vals = " | ".join(dash for dash in (row.get("values") or []))
            lines.append(f"- {row.get('label')}: {vals}")

    hourly = payload.get("hourly") or []
    site = payload.get("hourly_site") or {}
    if hourly:
        lines.append("")
        site_label = site.get("label") or "대표지점"
        lines.append(f"시간별 예보 ({site_label}, 3시간 간격):")
        for h in hourly[:12]:
            lines.append(
                f"- {h.get('date')} {h.get('hour_label')}: "
                f"{h.get('temp', '-')}℃/{h.get('condition', '-')} · "
                f"강수확률 {h.get('precip_prob', '-')} · "
                f"바람 {h.get('wind', '-')} · 습도 {h.get('humidity', '-')}"
            )

    if payload.get("pdf_url"):
        lines.append(f"\nPDF 통보문: {payload['pdf_url']}")
    return "\n".join(lines)


def _tool_result(region: str | None) -> str:
    try:
        payload = fetch_short_term_forecast(region)
    except ValueError as exc:
        return str(exc)
    except Exception as exc:
        logger.exception("short-term forecast fetch failed")
        return f"단기예보 조회 중 오류가 발생했습니다: {exc}"

    payload_out = {
        "summary": format_weather_text(payload),
        **payload,
    }
    return json.dumps(payload_out, ensure_ascii=False)


apps = Apps()


@apps.tool(
    resource_uri=VIEW_URI,
    name="get_weather",
    title="단기예보 종합 조회",
    description=(
        "기상청 단기예보 종합(https://www.weather.go.kr/w/forecast/overall/short-term.do)과 "
        "권역 대표지점의 시간별 예보(3시간 간격)를 조회합니다. "
        "region 예: 전국, 서울, 부산, 제주, 강원, "
        "또는 stnId(108/109/159/143/156/146/133/131/105/184)."
    ),
    meta={"ui/resourceUri": VIEW_URI},
)
def get_weather(region: str = "전국", city: str = "", ctx: Context = None) -> str:  # type: ignore[assignment]
    """기상청 단기예보 종합과 시간별 예보를 조회합니다."""
    query = region or city or "전국"
    logger.info("get_weather region=%r", query)
    return _tool_result(query)


@apps.tool(
    resource_uri=VIEW_URI,
    name="get_short_term_forecast",
    title="단기예보 종합",
    description=(
        "기상청 예보 종합 단기예보와 시간별 예보를 조회합니다. "
        "get_weather와 동일하며 region/city로 권역을 지정합니다."
    ),
    meta={"ui/resourceUri": VIEW_URI},
)
def get_short_term_forecast(region: str = "전국", city: str = "", ctx: Context = None) -> str:  # type: ignore[assignment]
    """단기예보 종합 조회 (get_weather 별칭)."""
    return get_weather(region=region or city or "전국", ctx=ctx)


@apps.tool(
    resource_uri=VIEW_URI,
    name="get_metropolitan_weather",
    title="수도권 단기예보",
    description=(
        "수도권(서울·인천·경기) 단기예보 종합과 시간별 예보를 조회합니다. "
        "호환용 도구이며 내부적으로 short-term.do?stnId=109 를 사용합니다. "
        "다른 지역은 get_weather(region=...)를 사용하세요."
    ),
    meta={"ui/resourceUri": VIEW_URI},
)
def get_metropolitan_weather(city: str = "", ctx: Context = None) -> str:  # type: ignore[assignment]
    """수도권 단기예보 종합 (호환 별칭)."""
    query = city.strip() if city and city.strip() else "서울·인천·경기"
    logger.info("get_metropolitan_weather city=%r -> region=%r", city, query)
    return _tool_result(query)


apps.add_html_resource(
    VIEW_URI,
    WEATHER_HTML,
    title="기상청 단기예보 종합",
    description="기상청 단기예보 종합·시간별 예보 UI",
    csp=ResourceCsp(
        resource_domains=["https://www.weather.go.kr"],
    ),
)

try:
    mcp = MCPServer(
        name="weather",
        instructions=(
            "기상청 단기예보 종합과 권역 대표지점 시간별 예보를 제공합니다. "
            "지역별 예보 요약·기온·해상·시간별 차트는 get_weather 또는 "
            "get_short_term_forecast를 사용하세요."
        ),
        extensions=[apps],
    )
    logger.info("Weather MCP Apps server initialized (short-term + hourly)")
except Exception as e:
    logger.error("Error initializing weather MCP server: %s", e)
    raise


if __name__ == "__main__":
    mcp.run(transport="stdio")
