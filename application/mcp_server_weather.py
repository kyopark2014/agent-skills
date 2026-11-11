"""
MCP Apps weather server for 수도권기상청 (KMA Metropolitan).

Uses the same weather.go.kr REST endpoints embedded in
https://www.kma.go.kr/metropolitan/html/main/index.jsp
(current observation / nowcast / daily forecast for ASOS stations).
"""
from __future__ import annotations

import json
import logging
import sys
from datetime import datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

import requests
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
SOURCE_PAGE = "https://www.kma.go.kr/metropolitan/html/main/index.jsp"
NOW_URL = f"{WEATHER_DOMAIN}/w/renew2021/rest/main/current-weather-now-stn.do"
OBS_URL = f"{WEATHER_DOMAIN}/w/renew2021/rest/main/current-weather-obs-stn.do"
FCT_URL = f"{WEATHER_DOMAIN}/w/renew2021/rest/main/daily-forecast-stn.do"
SHORT_RPT_URL = f"{WEATHER_DOMAIN}/w/wnuri-fct2021/rest/weather/short-term-rpt.do"
MID_RPT_URL = f"{WEATHER_DOMAIN}/w/wnuri-fct2021/rest/weather/mid-term-rpt.do"
ICON_OBS = f"{WEATHER_DOMAIN}/w/resources/icon/NY@64/Light"
ICON_NOW = f"{WEATHER_DOMAIN}/w/resources/icon/DY@64/Light"

# Same station set as the metropolitan main page.
METRO_STATIONS: dict[str, dict[str, str]] = {
    "108": {"ko": "서울", "aliases": ["서울", "서울특별시", "seoul"]},
    "112": {"ko": "인천", "aliases": ["인천", "인천광역시", "incheon"]},
    "119": {"ko": "수원", "aliases": ["수원", "수원시", "suwon"]},
    "99": {"ko": "파주", "aliases": ["파주", "파주시", "paju"]},
    "98": {"ko": "동두천", "aliases": ["동두천", "동주천", "dongducheon"]},
    "102": {"ko": "백령도", "aliases": ["백령도", "백령", "baengnyeong"]},
    "201": {"ko": "강화", "aliases": ["강화", "강화도", "ganghwa"]},
    "202": {"ko": "양평", "aliases": ["양평", "양평군", "yangpyeong"]},
    "203": {"ko": "이천", "aliases": ["이천", "이천시", "icheon"]},
}
METRO_STN_IDS = list(METRO_STATIONS.keys())
METRO_OFFICE_STN = "109"  # 수도권기상청 통보문

VIEW_URI = "ui://kma-metropolitan/weather.html"

REQUEST_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    ),
    "Accept": "application/json, text/javascript, */*; q=0.01",
    "Accept-Language": "ko-KR,ko;q=0.9,en;q=0.8",
    "Referer": SOURCE_PAGE,
    "X-Requested-With": "XMLHttpRequest",
}

WEATHER_HTML = r"""<!doctype html>
<html lang="ko">
<head>
  <meta charset="UTF-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>수도권기상청 날씨</title>
  <style>
    :root {
      --bg: #0b1f33;
      --panel: rgba(255,255,255,0.08);
      --panel-solid: #12324f;
      --text: #f4f8fc;
      --muted: #9db4c8;
      --accent: #3db8ff;
      --warm: #ffb454;
      --cool: #7ed0ff;
      --line: rgba(255,255,255,0.12);
      --ok: #5ddea5;
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
    .wrap { padding: 16px; max-width: 920px; margin: 0 auto; }
    header {
      display: flex; justify-content: space-between; gap: 12px; align-items: flex-start;
      margin-bottom: 14px;
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
    .grid {
      display: grid;
      grid-template-columns: repeat(auto-fill, minmax(180px, 1fr));
      gap: 10px;
    }
    .card {
      background: linear-gradient(160deg, rgba(255,255,255,0.10), rgba(255,255,255,0.04));
      border: 1px solid var(--line);
      border-radius: 16px;
      padding: 12px;
      min-height: 150px;
      display: flex; flex-direction: column; gap: 6px;
    }
    .card.selected { outline: 2px solid var(--accent); }
    .city { font-weight: 700; font-size: 1rem; }
    .temp { font-size: 1.8rem; font-weight: 700; letter-spacing: -0.03em; }
    .temp span { font-size: 0.95rem; color: var(--muted); margin-left: 2px; }
    .row { display: flex; align-items: center; gap: 8px; color: var(--muted); font-size: 0.82rem; }
    .row img { width: 28px; height: 28px; }
    .meta { margin-top: auto; font-size: 0.75rem; color: var(--muted); }
    .section-title {
      margin: 18px 0 8px; font-size: 0.92rem; color: var(--cool); font-weight: 650;
    }
    .forecast {
      display: grid; grid-template-columns: repeat(auto-fill, minmax(92px, 1fr)); gap: 8px;
    }
    .slot {
      background: var(--panel); border: 1px solid var(--line); border-radius: 12px;
      padding: 10px 8px; text-align: center; font-size: 0.75rem;
    }
    .slot .when { color: var(--muted); margin-bottom: 4px; }
    .slot .ta { font-size: 1rem; font-weight: 700; margin: 4px 0; }
    .slot img { width: 36px; height: 36px; }
    .links { display: flex; gap: 10px; flex-wrap: wrap; margin-top: 14px; }
    .links a { color: var(--accent); font-size: 0.8rem; text-decoration: none; }
    .links a:hover { text-decoration: underline; }
    .empty { color: var(--muted); padding: 24px 8px; text-align: center; }
    .badge {
      display: inline-flex; align-items: center; gap: 4px;
      background: rgba(61,184,255,0.15); color: var(--cool);
      border-radius: 999px; padding: 2px 8px; font-size: 0.72rem;
    }
  </style>
</head>
<body>
  <div class="wrap">
    <header>
      <div>
        <h1>수도권기상청 날씨</h1>
        <div class="sub">kma.go.kr/metropolitan · 관측·실황·예보</div>
      </div>
      <div class="actions">
        <select id="city-filter" title="지점 필터">
          <option value="">전체 지점</option>
        </select>
        <button class="primary" id="refresh-btn">새로고침</button>
      </div>
    </header>
    <div class="status" id="status">불러오는 중…</div>
    <div id="cards" class="grid"></div>
    <div class="section-title" id="forecast-title" style="display:none;">예보</div>
    <div id="forecast" class="forecast"></div>
    <div class="links">
      <a href="https://www.kma.go.kr/metropolitan/html/main/index.jsp" target="_blank" rel="noreferrer">수도권기상청</a>
      <a id="short-rpt" href="#" target="_blank" rel="noreferrer">단기예보 통보문</a>
      <a id="mid-rpt" href="#" target="_blank" rel="noreferrer">중기예보 통보문</a>
      <a href="https://www.weather.go.kr/w/index.do" target="_blank" rel="noreferrer">날씨누리</a>
    </div>
  </div>
  <script>
    const statusEl = document.getElementById("status");
    const cardsEl = document.getElementById("cards");
    const forecastEl = document.getElementById("forecast");
    const forecastTitle = document.getElementById("forecast-title");
    const cityFilter = document.getElementById("city-filter");
    const refreshBtn = document.getElementById("refresh-btn");
    const shortRpt = document.getElementById("short-rpt");
    const midRpt = document.getElementById("mid-rpt");

    let payload = null;
    let selectedCity = "";
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

    function parsePayload(result) {
      const text = result?.content?.find((c) => c.type === "text")?.text;
      if (!text) return null;
      try {
        const parsed = JSON.parse(text);
        if (parsed && typeof parsed === "object") return parsed;
        return { summary: text, stations: [] };
      } catch {
        return { summary: text, stations: [] };
      }
    }

    function fillCityOptions(stations) {
      const current = cityFilter.value;
      cityFilter.innerHTML = '<option value="">전체 지점</option>';
      for (const s of stations || []) {
        const opt = document.createElement("option");
        opt.value = s.name;
        opt.textContent = s.name;
        cityFilter.appendChild(opt);
      }
      if ([...cityFilter.options].some((o) => o.value === current)) {
        cityFilter.value = current;
      }
    }

    function reportSize() {
      const height = Math.ceil(document.documentElement.getBoundingClientRect().height);
      post({
        jsonrpc: "2.0",
        method: "ui/notifications/size-changed",
        params: { height: height || 420, width: window.innerWidth },
      });
    }

    function render() {
      if (!payload) {
        statusEl.textContent = "날씨 데이터가 없습니다.";
        cardsEl.innerHTML = '<div class="empty">도구를 다시 호출해 주세요.</div>';
        reportSize();
        return;
      }
      const stations = (payload.stations || []).filter(
        (s) => !selectedCity || s.name === selectedCity
      );
      statusEl.textContent = [
        payload.obs_time ? ("관측 " + payload.obs_time) : null,
        payload.now_time ? ("실황 " + payload.now_time) : null,
        payload.fetched_at ? ("갱신 " + payload.fetched_at) : null,
      ].filter(Boolean).join(" · ");

      fillCityOptions(payload.stations);
      cardsEl.innerHTML = "";
      if (!stations.length) {
        cardsEl.innerHTML = '<div class="empty">선택한 지점의 날씨가 없습니다.</div>';
      }
      for (const s of stations) {
        const card = document.createElement("div");
        card.className = "card" + (selectedCity === s.name ? " selected" : "");
        const icon = s.obs_icon_url || s.now_icon_url;
        card.innerHTML =
          '<div class="city">' + s.name + '</div>' +
          '<div class="temp">' + dash(s.temp) + '<span>℃</span></div>' +
          '<div class="row">' +
            (icon ? '<img src="' + icon + '" alt="' + dash(s.condition) + '" />' : '') +
            '<span>' + dash(s.condition) + '</span>' +
            '<span class="badge">' + dash(s.wind_dir) + ' ' + dash(s.wind_speed) + '㎧</span>' +
          '</div>' +
          '<div class="meta">1시간 강수 ' + dash(s.rain_1h) + ' mm</div>';
        card.addEventListener("click", () => {
          selectedCity = s.name;
          cityFilter.value = s.name;
          render();
        });
        cardsEl.appendChild(card);
      }

      const focus = selectedCity
        ? (payload.stations || []).find((s) => s.name === selectedCity)
        : (payload.stations || [])[0];
      forecastEl.innerHTML = "";
      if (focus && focus.forecast && focus.forecast.length) {
        forecastTitle.style.display = "block";
        forecastTitle.textContent = focus.name + " 예보";
        for (const f of focus.forecast) {
          const slot = document.createElement("div");
          slot.className = "slot";
          slot.innerHTML =
            '<div class="when">' + dash(f.label) + '</div>' +
            (f.icon_url ? '<img src="' + f.icon_url + '" alt="' + dash(f.condition) + '" />' : '') +
            '<div class="ta">' + dash(f.temp) + '℃</div>' +
            '<div>' + dash(f.condition) + '</div>' +
            '<div style="color:var(--muted)">강수 ' + dash(f.rain_prob) + '%</div>';
          forecastEl.appendChild(slot);
        }
      } else {
        forecastTitle.style.display = "none";
      }

      if (payload.short_term_report_url) shortRpt.href = payload.short_term_report_url;
      if (payload.mid_term_report_url) midRpt.href = payload.mid_term_report_url;
      reportSize();
    }

    function applyResult(result) {
      payload = parsePayload(result);
      if (payload && payload.filter_city) selectedCity = payload.filter_city;
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

    refreshBtn.addEventListener("click", async () => {
      statusEl.textContent = "새로고침 중…";
      try {
        const result = await request("tools/call", {
          name: "get_metropolitan_weather",
          arguments: { city: cityFilter.value || "" },
        });
        applyResult(result);
      } catch (err) {
        statusEl.textContent = "새로고침 실패: " + (err && err.message ? err.message : err);
      }
    });

    cityFilter.addEventListener("change", () => {
      selectedCity = cityFilter.value;
      render();
    });

    (async function connect() {
      try {
        await request("ui/initialize", {
          appInfo: { name: "KMA Metropolitan Weather", version: "1.0.0" },
          appCapabilities: {},
          protocolVersion: "2026-01-26",
        });
        post({ jsonrpc: "2.0", method: "ui/notifications/initialized" });
        reportSize();
      } catch (err) {
        statusEl.textContent = "호스트 연결 실패: " + (err && err.message ? err.message : err);
      }
    })();
  </script>
</body>
</html>
"""


def _fetch_json(url: str, params: dict[str, Any]) -> dict[str, Any]:
    resp = requests.get(url, params=params, headers=REQUEST_HEADERS, timeout=15)
    resp.raise_for_status()
    return resp.json()


def _fmt_tm(tm: str | None, fmt: str = "%Y.%m.%d %H:%M") -> str | None:
    if not tm:
        return None
    raw = "".join(ch for ch in str(tm) if ch.isdigit())
    # Prefer exact digit lengths: YYYYMMDDHHmm / YYYYMMDDHH / YYYYMMDD
    candidates = (
        (12, "%Y%m%d%H%M"),
        (10, "%Y%m%d%H"),
        (8, "%Y%m%d"),
    )
    for length, pattern in candidates:
        if len(raw) == length:
            try:
                return datetime.strptime(raw, pattern).replace(tzinfo=KST).strftime(fmt)
            except ValueError:
                continue
    return tm


def _resolve_city(city: str | None) -> tuple[list[str], str | None]:
    """Return (stn_ids, matched_ko_name). Empty city => all stations."""
    if not city or not city.strip():
        return METRO_STN_IDS, None
    needle = city.strip().lower().replace(" ", "")
    for stn_id, meta in METRO_STATIONS.items():
        aliases = [meta["ko"].lower(), *[a.lower().replace(" ", "") for a in meta["aliases"]]]
        if needle in aliases or any(needle in a or a in needle for a in aliases):
            return [stn_id], meta["ko"]
    # Partial match fallback
    for stn_id, meta in METRO_STATIONS.items():
        if needle in meta["ko"].lower():
            return [stn_id], meta["ko"]
    raise ValueError(
        f"지원하지 않는 지점입니다: {city}. "
        f"가능 지점: {', '.join(m['ko'] for m in METRO_STATIONS.values())}"
    )


def _forecast_labels(tm: str | None, is_am_start: bool, count: int) -> list[str]:
    if tm:
        try:
            base = datetime.strptime(tm[:12].ljust(12, "0"), "%Y%m%d%H%M")
        except ValueError:
            base = datetime.now(KST).replace(tzinfo=None)
    else:
        base = datetime.now(KST).replace(tzinfo=None)
    cursor = base.replace(hour=1 if is_am_start else 13, minute=0, second=0, microsecond=0)
    labels = []
    weekdays = ["월", "화", "수", "목", "금", "토", "일"]
    for _ in range(count):
        ampm = "오전" if cursor.hour < 12 else "오후"
        labels.append(f"{cursor.day}({weekdays[cursor.weekday()]}) {ampm}")
        cursor += timedelta(hours=12)
    return labels


def _report_url(api_url: str) -> str | None:
    try:
        data = _fetch_json(api_url, {"stnId": METRO_OFFICE_STN})
        file_name = data.get("fileName")
        if file_name:
            return f"{WEATHER_DOMAIN}/w/repositary/xml/fct/{file_name}"
    except Exception as exc:
        logger.warning("report fetch failed %s: %s", api_url, exc)
    return None


def fetch_metropolitan_weather(city: str | None = None) -> dict[str, Any]:
    stn_ids, filter_city = _resolve_city(city)
    stns = ",".join(stn_ids)

    now_data: dict[str, Any] = {"data": []}
    obs_data: dict[str, Any] = {"data": []}
    fct_data: dict[str, Any] = {"data": []}

    try:
        now_data = _fetch_json(NOW_URL, {"stns": stns})
    except Exception as exc:
        logger.error("now fetch failed: %s", exc)
    try:
        obs_data = _fetch_json(OBS_URL, {"stns": stns})
    except Exception as exc:
        logger.error("obs fetch failed: %s", exc)
    try:
        fct_data = _fetch_json(FCT_URL, {"stns": stns})
    except Exception as exc:
        logger.error("forecast fetch failed: %s", exc)

    now_by_id = {str(item.get("stnId")): item for item in now_data.get("data") or []}
    obs_by_id = {str(item.get("stnId")): item for item in obs_data.get("data") or []}
    fct_by_id = {str(item.get("stnId")): item for item in fct_data.get("data") or []}

    is_am_start = bool(fct_data.get("isAmStart"))
    fct_tm = fct_data.get("tm")

    stations: list[dict[str, Any]] = []
    for stn_id in stn_ids:
        meta = METRO_STATIONS[stn_id]
        now = now_by_id.get(stn_id, {})
        obs = obs_by_id.get(stn_id, {})
        fct = fct_by_id.get(stn_id, {})

        condition = obs.get("wwKo") or now.get("ww") or "-"
        if condition in ("-", ""):
            condition = obs.get("ww") or now.get("wwEn") or "-"

        temp = obs.get("ta") or now.get("temp")
        wind_dir = obs.get("wdKo") or now.get("wdKor") or "-"
        wind_speed = obs.get("ws") or now.get("ws")
        rain_1h = obs.get("rnHr1") or now.get("rnHr1") or "-"

        obs_icon = obs.get("icon")
        now_icon = now.get("wwIcon")
        obs_icon_url = f"{ICON_OBS}/NB{obs_icon}.png" if obs_icon else None
        now_icon_url = f"{ICON_NOW}/{now_icon}.png" if now_icon else None

        forecast_items = fct.get("forecast") or []
        labels = _forecast_labels(fct_tm, is_am_start, len(forecast_items))
        forecast = []
        for idx, item in enumerate(forecast_items):
            code = item.get("wfCode")
            forecast.append(
                {
                    "label": labels[idx] if idx < len(labels) else str(item.get("numEf")),
                    "temp": item.get("ta"),
                    "condition": item.get("wf"),
                    "rain_prob": item.get("rnSt"),
                    "rain_yn": item.get("rnYn"),
                    "icon_url": f"{ICON_OBS}/{code}.png" if code else None,
                }
            )

        stations.append(
            {
                "stn_id": stn_id,
                "name": meta["ko"],
                "temp": temp,
                "condition": condition,
                "wind_dir": wind_dir,
                "wind_speed": wind_speed,
                "rain_1h": rain_1h,
                "obs_icon_url": obs_icon_url,
                "now_icon_url": now_icon_url,
                "forecast": forecast,
                "lat": obs.get("lat") or now.get("lat"),
                "lon": obs.get("lon") or now.get("lon"),
            }
        )

    payload = {
        "source": SOURCE_PAGE,
        "office": "수도권기상청",
        "filter_city": filter_city,
        "fetched_at": datetime.now(KST).strftime("%Y.%m.%d %H:%M"),
        "obs_time": _fmt_tm(obs_data.get("tm")),
        "now_time": _fmt_tm(now_data.get("tm")),
        "forecast_time": _fmt_tm(fct_tm),
        "stations": stations,
        "short_term_report_url": _report_url(SHORT_RPT_URL),
        "mid_term_report_url": _report_url(MID_RPT_URL),
    }
    return payload


def format_weather_text(payload: dict[str, Any]) -> str:
    lines = [
        f"[{payload.get('office')}] 수도권 날씨",
        f"출처: {payload.get('source')}",
    ]
    if payload.get("obs_time"):
        lines.append(f"관측 시각: {payload['obs_time']}")
    if payload.get("filter_city"):
        lines.append(f"조회 지점: {payload['filter_city']}")
    lines.append("")

    for stn in payload.get("stations") or []:
        lines.append(
            f"- {stn['name']}: {stn.get('temp', '-')}℃ · {stn.get('condition', '-')} · "
            f"바람 {stn.get('wind_dir', '-')} {stn.get('wind_speed', '-')}㎧ · "
            f"1시간 강수 {stn.get('rain_1h', '-')}mm"
        )
        if stn.get("forecast"):
            brief = ", ".join(
                f"{f['label']} {f.get('temp', '-')}℃/{f.get('condition', '-')}"
                for f in stn["forecast"][:4]
            )
            lines.append(f"  예보: {brief}")

    if payload.get("short_term_report_url"):
        lines.append(f"\n단기예보 통보문: {payload['short_term_report_url']}")
    if payload.get("mid_term_report_url"):
        lines.append(f"중기예보 통보문: {payload['mid_term_report_url']}")
    return "\n".join(lines)


apps = Apps()


@apps.tool(
    resource_uri=VIEW_URI,
    name="get_metropolitan_weather",
    title="수도권기상청 날씨",
    description=(
        "수도권기상청(https://www.kma.go.kr/metropolitan/html/main/index.jsp)에 "
        "표시되는 관측·실황·예보를 조회합니다. "
        "city를 비우면 서울/인천/수원/파주/동두천/백령도/강화/양평/이천 전체를, "
        "지정하면 해당 지점만 조회합니다."
    ),
    meta={"ui/resourceUri": VIEW_URI},
)
def get_metropolitan_weather(city: str = "", ctx: Context = None) -> str:  # type: ignore[assignment]
    """수도권기상청 메인 페이지와 동일한 지점 날씨를 조회합니다."""
    logger.info("get_metropolitan_weather city=%r", city)
    try:
        payload = fetch_metropolitan_weather(city)
    except ValueError as exc:
        return str(exc)
    except Exception as exc:
        logger.exception("weather fetch failed")
        return f"날씨 조회 중 오류가 발생했습니다: {exc}"

    # Always return JSON so MCP Apps hosts (and our web UI iframe) can render
    # stations. `summary` keeps a prose form for models / text-only hosts.
    payload_out = {
        "summary": format_weather_text(payload),
        **payload,
    }
    return json.dumps(payload_out, ensure_ascii=False)


@apps.tool(
    resource_uri=VIEW_URI,
    name="get_weather",
    title="도시 날씨 조회",
    description=(
        "도시명으로 수도권기상청 관할 지점 날씨를 조회합니다. "
        "지원: 서울, 인천, 수원, 파주, 동두천, 백령도, 강화, 양평, 이천."
    ),
    meta={"ui/resourceUri": VIEW_URI},
)
def get_weather(city: str, ctx: Context = None) -> str:  # type: ignore[assignment]
    """도시명으로 수도권 지점 날씨를 조회합니다."""
    return get_metropolitan_weather(city=city or "", ctx=ctx)


apps.add_html_resource(
    VIEW_URI,
    WEATHER_HTML,
    title="수도권기상청 날씨",
    description="수도권기상청 관측·실황·예보 UI",
    csp=ResourceCsp(
        resource_domains=["https://www.weather.go.kr"],
    ),
)

try:
    mcp = MCPServer(
        name="weather",
        instructions=(
            "수도권기상청 날씨 정보를 제공합니다. "
            "서울·인천·수원 등 수도권 지점의 현재 날씨와 예보를 조회할 때 "
            "get_metropolitan_weather 또는 get_weather를 사용하세요."
        ),
        extensions=[apps],
    )
    logger.info("Weather MCP Apps server initialized")
except Exception as e:
    logger.error("Error initializing weather MCP server: %s", e)
    raise


if __name__ == "__main__":
    mcp.run(transport="stdio")
