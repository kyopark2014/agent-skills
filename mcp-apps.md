# MCP Apps 기능 확장

MCP Apps 확장 스펙(SEP-1865)과 이 스펙 위에서 Anthropic이 확장한 Interactive Connectors(2026-11-10 발표)를 정리한 노트입니다. 앞부분은 호스트에 상관없이 적용되는 **표준 스펙**이고, 뒷부분은 **Claude에만 해당하는 확장과 제약**입니다.

관련 노트: [[MCP OAuth 인증]] · [[AgentCore Gateway — 인바운드 인증, OAuth 2.0 접속(Claude Code 포함), 승인 흐름 (Interceptor Lambda)]] · [[Agent Plugins 스펙]] · [[Claude Tag]]

## 목차

- [1. 한눈에 보기](#1-한눈에-보기)
- [2. 등장 배경](#2-등장-배경)
- [3. MCP Apps 스펙 — 핵심 구성](#3-mcp-apps-스펙--핵심-구성)
- [4. UI Resource 형식](#4-ui-resource-형식)
- [5. Tool–UI 연결과 Visibility](#5-toolui-연결과-visibility)
- [6. 통신 프로토콜과 Lifecycle](#6-통신-프로토콜과-lifecycle)
- [7. MCP Apps 전용 메시지](#7-mcp-apps-전용-메시지)
- [8. Display Modes와 Theming](#8-display-modes와-theming)
- [9. App-Provided Tools](#9-app-provided-tools)
- [10. Capability Negotiation과 하위 호환](#10-capability-negotiation과-하위-호환)
- [11. 보안 모델](#11-보안-모델)
- [12. SDK와 개발 시작](#12-sdk와-개발-시작)
- [13. Anthropic Interactive Connectors 확장 (2026-11-10)](#13-anthropic-interactive-connectors-확장-2026-11-10)
- [14. Claude 구현 상세 — 개발자 관점](#14-claude-구현-상세--개발자-관점)
- [15. 유사 기술 비교](#15-유사-기술-비교)
- [16. AWS AgentCore 관점 적용 포인트](#16-aws-agentcore-관점-적용-포인트)
- [17. 확인하지 못한 부분](#17-확인하지-못한-부분)
- [18. 참고 문서](#18-참고-문서)

---

## 1. 한눈에 보기

MCP Apps는 MCP 도구가 텍스트나 JSON 대신 **인터랙티브 UI**(차트, 폼, 대시보드, 에디터 등)를 돌려주게 하는 **MCP 최초의 공식 확장**입니다. 호스트(Claude, ChatGPT, VS Code 등)는 이 UI를 대화 화면 안의 **샌드박스 iframe**에 그립니다.


| 항목      | 내용                                                                 |
| ------- | ------------------------------------------------------------------ |
| 정식 명칭   | SEP-1865: MCP Apps: Interactive User Interfaces for MCP            |
| 확장 식별자  | `io.modelcontextprotocol/ui`                                       |
| 공식 출시   | 2026-01-26 (MCP 공식 블로그, 프로덕션 사용 가능)                                |
| 기반이 된 것 | MCP-UI(커뮤니티) + OpenAI Apps SDK(2025-11)                            |
| 콘텐츠 타입  | `text/html;profile=mcp-app` (지금은 HTML만 지원하고, 다른 형식은 예약해 두었습니다)     |
| SDK     | `@modelcontextprotocol/ext-apps` (Apache 2.0)                      |
| 지원 호스트  | Claude, ChatGPT, VS Code, Goose, Postman, MCPJam, mcp-use, Alpic 등 |
| 필수 여부   | 선택 확장입니다. 클라이언트와 서버가 capability negotiation으로 명시적으로 합의해야 동작합니다     |


```
User ─ 대화 ─► Host(Claude) ─ tools/call ─► MCP Server
                 │                              │
                 │◄──── resources/read(ui://) ──┘
                 ▼
        ┌───────────────────────┐
        │ View (샌드박스 iframe)  │◄─ postMessage(JSON-RPC 2.0) ─► Host
        │ HTML/JS UI            │    · 도구 결과 수신
        └───────────────────────┘    · 서버 도구 호출 / 메시지 전송 / 모델 컨텍스트 갱신
```

## 2. 등장 배경

- MCP 도구가 돌려주는 것은 텍스트와 구조화된 데이터뿐입니다. 그래서 차트, 폼, 디자인 캔버스, 비디오 플레이어처럼 **사람이 직접 조작해야 하는 UI**는 표현할 수 없었습니다.
- 예를 들어 DB 조회 결과가 수백 행이면 "지난주 것만", "매출순 정렬", "47번 행 상세"처럼 조작할 때마다 프롬프트를 새로 써야 했습니다. MCP Apps를 쓰면 UI가 이 조작을 직접 처리하고, 모델은 사용자의 행동을 계속 볼 수 있습니다(모델이 루프에서 빠지지 않습니다).
- 표준이 없으면 호스트마다 동작이 다르고, 보안과 감사 방식이 제각각이며, 개발자는 MCP-UI용과 Apps SDK용 어댑터를 따로 만들어야 합니다. SEP-1865는 두 방식을 **하나의 오픈 표준**으로 합칩니다.
- 공식 블로그가 든 대표 시나리오는 다음과 같습니다.
  - **데이터 탐색**: 영업 분석 대시보드에서 지역 필터, 드릴다운, 리포트 내보내기
  - **설정 마법사**: production을 고르면 보안 옵션이 추가로 나타나는 식의 의존 필드 폼
  - **문서 검토**: 계약서 PDF에서 조항을 하이라이트하고 승인 또는 플래그 처리하면 모델이 결정을 실시간으로 인지
  - **실시간 모니터링**: 도구를 다시 실행하지 않아도 갱신되는 서버 헬스 메트릭

## 3. MCP Apps 스펙 — 핵심 구성


| 구성 요소                           | 설명                                        |
| ------------------------------- | ----------------------------------------- |
| **UI Resources**                | `ui://` URI 스킴으로 **미리 선언**하는 HTML 리소스입니다  |
| **Tool–UI Linkage**             | 도구가 `_meta.ui.resourceUri`로 UI 리소스를 참조합니다 |
| **Bidirectional Communication** | iframe과 호스트가 표준 MCP JSON-RPC로 통신합니다       |
| **Security Model**              | iframe 샌드박싱이 필수이고, 모든 통신을 감사할 수 있습니다      |


역할 관계는 다음과 같습니다.

- **View(iframe)** 는 개념적으로 **MCP 클라이언트**입니다(`postMessage` transport를 씁니다).
- **Host** 는 View에게 **MCP 서버**처럼 행동하며, 실제 MCP 서버로 가는 프록시 역할을 합니다.

## 4. UI Resource 형식

### 4.1 리소스 선언

```typescript
interface UIResource {
  uri: string;          // 반드시 "ui://" 로 시작 (예: "ui://weather-dashboard")
  name: string;         // 사람이 읽는 이름
  description?: string;
  mimeType: string;     // "text/html;profile=mcp-app"
  _meta?: { ui?: UIResourceMeta };
}
```

`resources/read` 응답의 `contents[]`에는 `uri`, `mimeType`, `text`(HTML 문자열) 또는 `blob`(base64 HTML), `_meta.ui`가 들어갑니다. 콘텐츠는 **유효한 HTML5 문서**여야 합니다.

### 4.2 `_meta.ui` (UIResourceMeta)


| 필드                    | 의미                                                                                   | 매핑되는 CSP / 정책                                                          |
| --------------------- | ------------------------------------------------------------------------------------ | ---------------------------------------------------------------------- |
| `csp.connectDomains`  | fetch/XHR/WebSocket 허용 origin                                                        | `connect-src`                                                          |
| `csp.resourceDomains` | 이미지·스크립트·스타일·폰트·미디어 origin (wildcard 서브도메인 가능)                                       | `img-src`, `script-src`, `style-src`, `font-src`, `media-src`          |
| `csp.frameDomains`    | 중첩 iframe 허용 origin                                                                  | `frame-src` (없으면 `'none'`)                                             |
| `csp.baseUriDomains`  | 허용 base URI                                                                          | `base-uri` (없으면 `'self'`)                                              |
| `permissions`         | `camera`, `microphone`, `geolocation`, `clipboardWrite`                              | iframe `allow` 속성 (호스트가 허용할지는 선택(MAY)입니다)                              |
| `domain`              | View의 **고정 sandbox origin**입니다. OAuth 콜백, CORS, API 키 allowlist에 필요하며 형식은 호스트마다 다릅니다 | 예: `{hash}.claudemcpcontent.com`, `www-example-com.oaiusercontent.com` |
| `prefersBorder`       | 호스트가 테두리와 배경을 그릴지 여부                                                                 | true / false / 생략(호스트가 결정)                                             |


**메타데이터 위치**

- `resources/list` 항목에 두면 정적 기본값이 되어, 호스트가 연결 시점에 보안 설정을 검토할 수 있습니다.
- `resources/read`의 content item에 두면 응답마다 다르게 줄 수 있는 동적 값이 됩니다.
- 둘 다 있으면 **content item이 우선**합니다. 호스트는 두 위치를 모두 확인해야 합니다.

### 4.3 호스트의 CSP 동작

- 선언된 도메인을 바탕으로 CSP 헤더를 반드시 만들어야 합니다.
- `ui.csp`가 없으면 다음의 **엄격한 기본값**을 써야 합니다.

```
default-src 'none';
script-src 'self' 'unsafe-inline';
style-src 'self' 'unsafe-inline';
img-src 'self' data:;
media-src 'self' data:;
object-src 'none';
connect-src 'none';
```

- **No Loosening**: 호스트는 더 엄격하게 제한할 수는 있지만, 선언되지 않은 도메인을 허용해서는 안 됩니다.
- **Audit Trail**: CSP 설정을 로그로 남기기를 권장합니다.

## 5. Tool–UI 연결과 Visibility

```json
{
  "name": "get_weather",
  "description": "Get current weather for a location",
  "inputSchema": { "type": "object", "properties": { "location": { "type": "string" } } },
  "_meta": {
    "ui": {
      "resourceUri": "ui://weather-server/dashboard-template",
      "visibility": ["model", "app"]
    }
  }
}
```


| `visibility`            | 의미                                                                                             |
| ----------------------- | ---------------------------------------------------------------------------------------------- |
| `["model","app"]` (기본값) | 모델과 UI 모두 호출할 수 있습니다                                                                           |
| `["model"]`             | 모델만 호출할 수 있습니다                                                                                 |
| `["app"]`               | **같은 서버 연결의 UI만** 호출할 수 있습니다. 호스트는 이 도구를 모델의 `tools/list`에서 **빼야 합니다**. 예: 새로고침, 1회용 로그인 토큰 발급 |


- 호스트는 `"app"`이 없는 도구에 대한 App의 `tools/call`을 **거부해야 합니다**.
- app 전용 도구는 **다른 서버에서 호출할 수 없도록 항상 차단**됩니다.
- 예전 형식인 `_meta["ui/resourceUri"]`는 **deprecated**이며 GA 전에 제거될 예정입니다.

**동작 규칙**

- 호스트가 MCP Apps를 지원하면 지정된 UI로 결과를 그리고, 지원하지 않으면 일반 텍스트 도구로 동작합니다.
- 호스트는 반드시 `resources/read`로 리소스를 가져와야 하며, prefetch와 캐시는 허용됩니다(MAY).
- UI 전용 리소스는 `resources/list`에서 빼도 됩니다(MAY).

**템플릿과 데이터를 나눠서 얻는 이점**: 미리 받아두기(Performance), 연결 시점 검토(Security), 정적·동적 분리(Caching), 모든 UI를 열거하고 검사할 수 있음(Auditability).

## 6. 통신 프로토콜과 Lifecycle

### 6.1 Transport

- iframe과 호스트는 `postMessage` **위에서 JSON-RPC 2.0**으로 통신합니다. tool call이나 prompt 같은 기능은 MCP 기존 프로토콜을 그대로 씁니다.
- SDK가 없어도 `window.parent.postMessage({jsonrpc:"2.0", id, method, params}, '*')`로 직접 구현할 수 있습니다.

### 6.2 렌더링 보안 요구사항 (호스트 측)

- 웹 호스트는 View를 **반드시 iframe으로 감싸야 합니다**.
- 호스트와 iframe은 **서로 다른 origin**이어야 합니다.
- iframe sandbox 플래그는 `allow-scripts`, `allow-same-origin`입니다.
- 호스트는 View로부터 `ui/notifications/initialized`를 받기 전에는 어떤 메시지도 보내면 안 됩니다.
- iframe 문서는 선언된 CSP 헤더와 함께 서빙해야 하며, `object-src 'none'`이 필수입니다.
- **iframe 문서는 호스트가 통제하는 서버가 서빙해야 합니다.** 서드파티 MCP 서버가 직접 서빙하는 것은 허용되지 않습니다(그렇게 하면 CSP 같은 보안 속성을 통제할 수 없기 때문입니다).
- 렌더링 방식은 정해져 있지 않습니다(non-normative 예시만 있습니다).
  - **단일 샌드박스 iframe**: shim이 `postMessage`로 HTML을 받아 `blob:` URL로 이동하는 방식(Google Gemini의 SafeContentFrame과 비슷합니다), 또는 cross-origin 엔드포인트로 스트리밍하는 방식(더 빠릅니다)
  - **이중 샌드박스 iframe**: 바깥 iframe은 호스트가 통제하는 trusted shim(proxy)이고, 안쪽 iframe에 실제 View가 들어갑니다. 초기 스펙에서 필수였던 방식이며 `ui/notifications/sandbox-*` 메시지는 이 방식 전용입니다

### 6.3 Lifecycle

```
[1] Connection & Discovery
    Server → Host : resources/list (ui:// 포함), tools/list (_meta.ui 포함)

[2] Embedding
    Host → Server : tools/call  (병렬로)  Host: iframe 렌더링
    View → Host   : ui/initialize (appCapabilities)
    Host → View   : McpUiInitializeResult (hostContext, hostCapabilities)
    View → Host   : ui/notifications/initialized
    Host → View   : ui/notifications/tool-input-partial (0..n, 스트리밍)
    Host → View   : ui/notifications/tool-input (완전한 인자, 최대 1회)
    Host → View   : ui/notifications/tool-result | tool-cancelled

[3] Interactive Phase (반복)
    사용자 클릭 → View → Host : tools/call → Server
                 View → Host : ui/message / ui/update-model-context / resources/read / notifications/message(log)
                 View → Host : ui/notifications/size-changed
                 Host → View : ui/notifications/host-context-changed

[4] Teardown
    (선택) View → Host : ui/notifications/request-teardown
    Host → View : ui/resource-teardown → View 정리 → 응답 → Host가 iframe 제거
```

예전 MCP-UI와 다른 점은 `iframe-ready` 대신 MCP 방식의 `ui/initialize` / `ui/notifications/initialized` 핸드셰이크를 쓴다는 것입니다.

## 7. MCP Apps 전용 메시지

### 7.1 Requests (View → Host)


| 메서드                            | 용도                                         | 호스트 동작                                                                                                                     |
| ------------------------------ | ------------------------------------------ | -------------------------------------------------------------------------------------------------------------------------- |
| `ui/open-link`                 | 외부 URL 열기                                  | 기본 브라우저나 새 탭으로 엽니다(SHOULD)                                                                                                 |
| `ui/download-file`             | 파일 다운로드(EmbeddedResource 또는 ResourceLink)  | 확인 대화상자를 띄우고(SHOULD), 파일명을 정리(sanitize)합니다. 정책에 따라 거부할 수 있습니다. iframe은 `allow-downloads`가 없어 직접 다운로드할 수 없으므로 호스트가 대신 처리합니다 |
| `ui/message`                   | 대화창에 메시지 보내기(`role: "user"`)               | 대화 컨텍스트에 추가하고 후속 응답을 진행합니다. 사용자 동의를 요청할 수 있습니다                                                                             |
| `ui/request-display-mode`      | `inline` / `fullscreen` / `pip` 전환         | 실제로 적용된 mode를 돌려줍니다                                                                                                        |
| `ui/update-model-context`      | 모델 컨텍스트 갱신(`content`, `structuredContent`) | 다음 턴에 반영합니다. **이전 값을 덮어쓰며**, 여러 번 오면 마지막 값만 보냅니다(SHOULD). 다음 사용자 메시지까지 미룰 수 있습니다                                           |
| `tools/call`, `resources/read` | 서버 도구나 리소스를 프록시로 호출                        | 실제 서버로 전달합니다                                                                                                               |


### 7.2 Requests (Host → App)

- App이 `tools` capability를 선언하면 호스트가 App에 `tools/list`와 `tools/call`을 보낼 수 있습니다(9장 참고).

### 7.3 Notifications (Host → View)


| 알림                                      | 설명                                                                    |
| --------------------------------------- | --------------------------------------------------------------------- |
| `ui/notifications/tool-input`           | 도구 인자 전체입니다. initialize가 끝난 뒤 **최대 1회**, 그리고 tool-result보다 먼저 보내야 합니다 |
| `ui/notifications/tool-input-partial`   | 스트리밍 중인 미완성 인자입니다. 닫히지 않은 JSON을 자동으로 닫아 복구한 값이며, View는 무시해도 됩니다       |
| `ui/notifications/tool-result`          | 도구 실행 결과입니다                                                           |
| `ui/notifications/tool-cancelled`       | 도구가 취소되었다는 알림입니다                                                      |
| `ui/notifications/host-context-changed` | 테마, displayMode 등 호스트 컨텍스트가 바뀌었다는 알림입니다                               |
| `ui/resource-teardown`                  | 종료 요청입니다                                                              |


### 7.4 Notifications (View → Host)

- `ui/notifications/initialized`, `ui/notifications/size-changed`, `ui/notifications/request-teardown`, `notifications/message`(로그)

## 8. Display Modes와 Theming

### 8.1 Display Modes


| 모드           | 설명                                 |
| ------------ | ---------------------------------- |
| `inline`     | 기본값이며, 대화 흐름 안에 들어갑니다              |
| `fullscreen` | 화면 또는 창 전체를 씁니다                    |
| `pip`        | Picture-in-Picture 방식의 플로팅 오버레이입니다 |


- View는 `ui/initialize`의 `appCapabilities.availableDisplayModes`로 자신이 지원하는 모드를 **모두** 선언해야 합니다.
- 호스트는 `HostContext.availableDisplayModes`로 자기가 지원하는 모드를 알립니다. View는 이 값을 확인한 뒤 전환을 요청해야 합니다.
- 호스트는 View가 선언하지 않은 모드로 전환해서는 안 되고, 요청 결과(바뀌었든 아니든)를 항상 돌려줘야 합니다.

### 8.2 Theming

- 호스트는 `HostContext.styles.variables`로 표준 CSS 변수를 넘길 수 있습니다.
  - 예: `--color-background-primary`, `--color-text-secondary`, `--color-border-danger`, `--color-ring-primary` 등(background, text, border, ring 계열)
- View가 이 변수를 쓰면 호스트 디자인(라이트/다크 등)과 자연스럽게 어울립니다. 커스텀 폰트도 지원합니다.

## 9. App-Provided Tools

Interactive Connectors 확장에서 Claude가 새로 지원한 "app tools"의 바탕이 되는 기능입니다.

### 9.1 필요한 이유 — Semantic Introspection

- iframe은 샌드박스이기 때문에 호스트도 모델도 **UI 내부 상태를 볼 수 없습니다**(opaque).
- App이 도구를 등록하면 모델은 `tools/list`로 할 수 있는 작업을 찾고, `get_board_state` 같은 도구로 상태를 조회하고, `make_move` 같은 도구로 조작할 수 있습니다.
- App은 화면 대신 **구조화된 데이터**를 돌려줍니다.


| 방식       | 메커니즘                      | 성격                       |
| -------- | ------------------------- | ------------------------ |
| **Push** | `ui/update-model-context` | App이 먼저 상태를 모델에게 알립니다    |
| **Pull** | App-provided tools        | 모델이 필요할 때 App에 묻거나 명령합니다 |


두 방식은 함께 쓸 수 있습니다.

### 9.2 등록 예시

```typescript
import { App } from '@modelcontextprotocol/ext-apps';
import { z } from 'zod';

const app = new App(
  { name: "TicTacToe", version: "1.0.0" },
  { tools: { listChanged: true } }          // tools capability 선언
);

app.registerTool("tictactoe_move", {
  description: "Make a move in the tic-tac-toe game",
  inputSchema: z.object({
    position: z.number().int().min(0).max(8),
    player: z.enum(['X', 'O'])
  }),
  outputSchema: z.object({
    board: z.array(z.string()).length(9),
    winner: z.enum(['X', 'O', 'draw']).nullable()
  }),
  annotations: { readOnlyHint: false }      // 부작용 있음
}, async (p) => ({
  content: [{ type: "text", text: `Move made at position ${p.position}` }],
  structuredContent: { board: makeMove(p.position, p.player), winner: null }
}));

await app.connect(new PostMessageTransport(window.parent));
```


| 등록 옵션          | 필수  | 설명                                                          |
| -------------- | --- | ----------------------------------------------------------- |
| `name`         | O   | 고유 식별자입니다                                                   |
| `description`  | –   | 에이전트가 읽는 설명입니다                                              |
| `inputSchema`  | –   | Standard Schema(zod 등)이며 `tools/list`에서 JSON Schema로 직렬화됩니다 |
| `outputSchema` | –   | `structuredContent`를 검증합니다                                  |
| `annotations`  | –   | `readOnlyHint` 등입니다                                         |
| `_meta`        | –   | 커스텀 메타데이터입니다                                                |


- SDK 없이 `app.oncalltool` / `app.onlisttools` 핸들러를 직접 구현할 수도 있습니다.
- **Lifecycle**: `tool.enable()`, `disable()`, `update()`, `remove()`를 호출하면 `notifications/tools/list_changed`가 자동으로 발송됩니다.
- **App 동작 규칙**: `tools`를 선언했다면 `oncalltool`과 `onlisttools`를 반드시 구현해야 합니다. 실패하면 `isError: true`를 돌려주는 것을 권장합니다.

### 9.3 WebMCP 상호운용

- W3C 인큐베이션 중인 WebMCP(`document.modelContext.registerTool()`)와 **같은 기능의 두 표면**으로 보고, 둘이 서로 호환되도록 정의합니다.
- **SDK mirroring**: `registerTool()`로 등록한 도구를 `document.modelContext`에도 올립니다. 이때 iframe에 `allow="tools"` 권한 위임이 필요합니다.
- **SDK bridging**: 페이지가 WebMCP로 등록한 도구를 App의 `tools/list`·`tools/call` 채널로도 제공합니다.
- **중복 노출 금지**: 호스트는 View 하나당 하나의 표면만 써야 하며, native WebMCP를 권장합니다.

### 9.4 보안 고려사항

- App 도구는 **신뢰할 수 없는 샌드박스** 안에서 실행됩니다. 설명을 속여 쓸 가능성이 있습니다.
- 서버 도구와 이름이 겹치지 않도록 네임스페이스가 필요합니다.
- 최대 도구 수, 실행 타임아웃 같은 리소스 제한이 필요합니다.
- App 도구 호출에 대한 감사 로그가 필요합니다.
- 부작용이 있는 도구는 사용자 확인을 거쳐야 합니다.

## 10. Capability Negotiation과 하위 호환

### 10.1 Client(Host) → Server

```json
{
  "method": "initialize",
  "params": {
    "capabilities": {
      "extensions": {
        "io.modelcontextprotocol/ui": { "mimeTypes": ["text/html;profile=mcp-app"] }
      }
    },
    "clientInfo": { "name": "claude-desktop", "version": "1.0.0" }
  }
}
```

`mimeTypes`는 필수입니다. 앞으로 `features`, `sandboxPolicies` 같은 설정이 추가될 수 있습니다.

### 10.2 Server 동작

```typescript
import { getUiCapability, RESOURCE_MIME_TYPE } from "@modelcontextprotocol/ext-apps/server";

const uiCap = getUiCapability(clientCapabilities);
if (uiCap?.mimeTypes?.includes(RESOURCE_MIME_TYPE)) {
  // UI 버전 도구 등록 (_meta.ui.resourceUri 포함)
} else {
  // 텍스트 전용 도구 등록
}
```

- **Graceful Degradation**: 서버는 UI가 있는 모든 도구에 텍스트 전용 fallback을 두는 것을 권장합니다(SHOULD). UI가 있더라도 `content` 배열에는 **의미 있는 텍스트를 반드시** 넣어야 합니다.

### 10.3 App(Guest UI) ↔ Host

- App은 `ui/initialize`에서 `appInfo`와 `appCapabilities`(`tools`, `availableDisplayModes`)를 보냅니다.
- 호스트는 `hostInfo`, `hostCapabilities`(서버 도구 프록시, sandbox permissions 등), `hostContext`로 응답합니다.

## 11. 보안 모델


| 위협                | 대응                                                                                      |
| ----------------- | --------------------------------------------------------------------------------------- |
| 악성 UI 코드 실행       | 샌드박스 iframe 필수, 호스트와 다른 origin, 호스트가 통제하는 서버에서 문서 서빙                                    |
| 데이터 유출            | 선언 기반 CSP 강제, 엄격한 기본값, No Loosening                                                     |
| 보이지 않는 동작         | 모든 통신이 JSON-RPC라 감사 가능, 리소스를 미리 선언하므로 연결 시점에 검토 가능                                      |
| 모델을 거치지 않는 도구 오남용 | `visibility`로 model 전용과 app 전용을 분리, 다른 서버의 app 전용 도구 호출 차단                              |
| App 도구 기만         | 네임스페이스, 리소스 제한, 감사 로그, 부작용 도구의 사용자 확인                                                   |
| 디바이스 권한 남용        | `permissions`는 요청일 뿐이고 허용 여부는 호스트가 정합니다(MAY). App은 JS feature detection으로 fallback을 둡니다 |


## 12. SDK와 개발 시작

### 12.1 패키지


| 패키지                                         | 용도                                                        |
| ------------------------------------------- | --------------------------------------------------------- |
| `@modelcontextprotocol/ext-apps`            | View 개발용(`App` 클래스, `PostMessageTransport`)               |
| `@modelcontextprotocol/ext-apps/react`      | React hooks(`useApp`, `useHostStyles` 등)                  |
| `@modelcontextprotocol/ext-apps/app-bridge` | 호스트(채팅 클라이언트)에 View를 임베드할 때 사용                            |
| `@modelcontextprotocol/ext-apps/server`     | 서버에서 도구와 리소스 등록(`registerAppTool`, `registerAppResource`) |


```bash
# 서버 (Node.js 20+)
npm install -S @modelcontextprotocol/ext-apps \
  @modelcontextprotocol/client@^2.0.0 @modelcontextprotocol/server@^2.0.0 \
  @modelcontextprotocol/node@^2.0.0 @modelcontextprotocol/express@^2.0.0 zod@^4.2.0
```

- `ext-apps` 1.x와 2.x는 wire protocol이 같으므로 서로 호환됩니다.
- 서버에서 `registerAppTool()`과 `registerAppResource()`를 쓰면 **호스트별 메타데이터가 자동으로 생성**됩니다. App에서 `App.connect()`를 인자 없이 호출하면 SDK가 호스트와 transport를 자동으로 감지합니다. 그래서 **코드 하나로 여러 호스트**를 지원할 수 있습니다.
- 정식 호스트 구현체는 저장소에 없습니다(예제 `examples/basic-host`만 있습니다). MCP-UI client SDK를 쓸 수도 있습니다.

### 12.2 View 기본 API

```typescript
import { App } from "@modelcontextprotocol/ext-apps";
const app = new App();
await app.connect();

app.ontoolresult = (result) => renderChart(result.data);                         // 결과 수신
const res = await app.callServerTool({ name: "fetch_details", arguments: { id: "123" } });
await app.updateModelContext({ content: [{ type: "text", text: "User selected option B" }] });
```

### 12.3 Agent Skills로 개발

Claude Code에서 아래 명령으로 설치합니다.

```
/plugin marketplace add modelcontextprotocol/ext-apps
/plugin install mcp-apps@modelcontextprotocol-ext-apps
```


| Skill               | 기능                               |
| ------------------- | -------------------------------- |
| `create-mcp-app`    | 새 MCP App 스캐폴딩                   |
| `migrate-oai-app`   | OpenAI Apps SDK 앱을 MCP Apps로 전환  |
| `add-app-to-server` | 기존 MCP 서버 도구에 UI 추가              |
| `convert-web-app`   | 기존 웹앱을 웹과 MCP App을 겸하는 하이브리드로 전환 |


### 12.4 예제 서버

Map(CesiumJS 3D 지구본), Three.js, ShaderToy, Sheet Music, Wiki Explorer, Cohort Heatmap, Scenario Modeler, Budget Allocator, Customer Segmentation, System Monitor, Transcript 등이 있습니다. Claude Desktop의 `claude_desktop_config.json`에 다음처럼 추가해 실행합니다.

```json
{ "mcpServers": { "map": { "command": "npx", "args": ["-y", "@modelcontextprotocol/server-map", "--stdio"] } } }
```

오래 쓸 설정이라면 `@modelcontextprotocol/server-map@2.0.1`처럼 버전을 고정하는 것을 권장합니다.

---

## 13. Anthropic Interactive Connectors 확장 (2026-11-10)

원문: *Your apps can now open in Claude, and Claude can work in them* (Product updates / Claude apps 및 MCP, 2026-11-10)

### 13.1 요약

파트너 앱을 **Claude 화면 안에 통째로** 띄울 수 있게 되었습니다. 사용자와 Claude가 탭을 바꾸지 않고 같은 화면, 같은 도구에서 함께 작업합니다.

### 13.2 무엇이 달라졌나


| 구분         | 기존 Interactive Connectors (2026년 초) | 이번 확장                                                               |
| ---------- | ----------------------------------- | ------------------------------------------------------------------- |
| 표시         | 대화 안의 작은 라이브 뷰                      | 대화 옆 **사이드 패널** 또는 **전체 화면**                                        |
| Claude의 역할 | 결과 미리보기                             | 열린 페이지·행·파일을 **직접 수정**                                              |
| 편집 환경      | 제한된 미리보기                            | **내 계정으로 로그인된 실제 에디터**(Confluence, Notion 등)                        |
| 여는 방법      | 프롬프트로 호출                            | **프롬프트 없이 바로 열기**, 사이드바 고정(Pin)                                     |
| 스펙 지원 범위   | 대화 속 인터랙티브 뷰 위주                     | **app tools**, **embedded sites** 등 확장 기능까지 지원해 앱 전체가 Claude 안에서 동작 |


### 13.3 새 기능 4가지

1. **열린 도구 안에서 Claude가 작업**: 페이지·행·파일 수정을 Claude에게 맡길 수 있습니다. 승인은 기존 커넥터 작업과 같은 방식이고, 변경 내용이 실시간으로 도구에 반영됩니다.
2. **도구 전체가 대화 옆에 열림**: 채팅을 숨기거나 전체 화면으로 바꿀 수 있습니다.
3. **프롬프트 없이 바로 열기**: 필요할 때만 같은 화면에서 Claude에게 요청하면 됩니다.
4. **자주 쓰는 도구 고정**: 아티팩트와 함께 배치되고 사이드바와 `+` 메뉴에서 한 번에 엽니다.

### 13.4 제공 범위

- 웹과 Claude 데스크톱 앱, **모든 플랜**에 제공됩니다.
- 채팅과 Cowork를 합친 **"new Claude experience"** 의 일부로 배포됩니다. 계정이 업데이트되거나 조직이 이 기능을 켜야 보입니다.
- Team/Enterprise에서는 Owner 또는 Primary Owner가 `Organization settings > Capabilities`의 **Chat and Cowork unified** 설정으로 제어합니다.

### 13.5 지원 도구


| 구분         | 도구                                                                    |
| ---------- | --------------------------------------------------------------------- |
| 첫 지원(디렉터리) | Asana, Canva, Carta, Dropbox, monday.com, Smartsheet, Vercel, Webflow |
| 개발 중       | Figma, Harvey, Shopify                                                |



| 도구        | 할 수 있는 작업                                             |
| --------- | ----------------------------------------------------- |
| Adobe     | 사진 보정·편집·효과 추가, PDF 하이라이트·편집·민감정보 가리기(redact)         |
| Atlassian | Confluence 페이지를 채팅 옆에 열고 Claude가 작성·편집, 변경 내용을 실시간 검토 |
| Box       | 파일 수정 후 이전·이후 버전을 나란히 비교한 뒤 반영                        |
| HubSpot   | 파이프라인 작업과 후속 조치 우선순위 지정 후 Claude에서 바로 실행              |
| Notion    | 문서나 데이터베이스를 채팅하면서 만들거나 직접 편집                          |


### 13.6 관리자

- 조직이 이미 커넥터의 모든 도구를 허용했다면, 새 기능이 배포되는 대로 **자동으로 노출**됩니다.
- 도구 제한은 기존과 같은 방식으로 합니다.
- 기업 고객은 배포 전에 커넥터별 도구 허용 범위를 다시 확인하는 것을 권장합니다.

### 13.7 개발자

- Interactive Connectors는 **MCP Apps** 위에서 동작합니다. 그래서 Claude 전용이 아니며, 이 확장을 지원하는 모든 AI 앱에서 실행됩니다.
- app tools와 embedded sites 지원은 **개발자 대상 퍼블릭 프리뷰**입니다.

## 14. Claude 구현 상세 — 개발자 관점

### 14.1 Claude에서 MCP App이 할 수 있는 것


| 기능                       | 설명                                  | 조건                                                  |
| ------------------------ | ----------------------------------- | --------------------------------------------------- |
| Side panel / Full screen | 대화 옆이나 전체 화면에 표시                    | 일부 조직부터 제공                                          |
| Entry points             | Artifacts 페이지에서 프롬프트 없이 열기, 사이드바 고정 | 원격 서버, Verified 디렉터리 또는 커스텀 커넥터, unified experience |
| App tools                | Claude가 화면 상태를 바꾸는 도구               | 원격 서버, 일부 조직부터 제공                                   |
| Messages                 | 앱 버튼에서 Claude에게 메시지 보내기             | –                                                   |
| Embedded websites        | 앱 안에 자사 웹사이트(에디터, 대시보드) 임베드         | 원격 서버, Verified 디렉터리 커넥터                            |
| Downloads                | 사용자 기기에 파일 저장                       | –                                                   |


**공통 요구사항**

- app tools, entry points, embedded sites는 **원격 서버 기반 커넥터**에서만 동작합니다. Claude Desktop 설정 파일에 넣은 로컬 서버(예제 서버 포함)로는 테스트할 수 없으므로, 터널로 노출한 뒤 커스텀 커넥터로 등록해 테스트합니다.
- 디렉터리 커넥터는 **Verified listing**이어야 entry points와 embedded sites가 동작합니다. Community listing으로 설치한 사용자에게는 두 기능이 제공되지 않습니다.
- 처음 표시할 때 사용자에게 권한을 묻습니다(Allow once / Always allow). 대화의 권한 설정이 Auto이면 묻지 않고 표시합니다.

### 14.2 `ui.domain` (Claude의 sandbox origin)

Claude에서는 **서버 URL의 SHA-256 hex 앞 32자리 +** `.claudemcpcontent.com` 입니다.

```bash
node -e 'const u="https://example.com/mcp"; console.log(require("crypto").createHash("sha256").update(u).digest("hex").slice(0,32)+".claudemcpcontent.com")'
```

`ui.domain`을 지정하지 않으면 **대화마다 sandbox host가 바뀝니다**. 그러면 OAuth 콜백, CORS, `frame-ancestors`, localStorage와 쿠키 세션이 불안정해집니다.

### 14.3 Display modes (사이드 패널)

- Claude는 대화 안의 View를 `inline`으로, **사이드 패널과 전체 화면을 모두** `fullscreen` 으로 보고합니다.
- `availableDisplayModes` 동작은 다음과 같습니다.
  - 목록이 없으면 두 모드를 모두 지원하는 것으로 봅니다.
  - 빈 목록이거나 `fullscreen`이 없으면 확장 버튼을 보여주지 않고 자동 이동도 하지 않습니다.
  - `fullscreen`만 있으면 대화에는 카드가 놓이고, 클릭하면 사이드 패널(또는 전체 화면)로 열립니다. 도구를 호출할 때 자동으로 사이드 패널에 열려고 시도합니다.
  - SDK에 정의되지 않은 값(예: `sidebar`)을 넣으면 **View가 시작되지 않습니다**.
- **클릭 없이 사이드 패널로 열기**: `resources/read` content item의 `_meta.ui.preferredDisplayMode: "fullscreen"`을 씁니다. 이 필드는 **Claude만의 확장**이라 스펙과 SDK 타입에 없으며 위치가 바뀔 수 있습니다. 시도는 1회만 하며, 전체 화면이 아니라 사이드 패널로 열립니다.
- 앱에서 `requestDisplayMode("fullscreen")`를 요청하려면 **최근 클릭이나 키 입력 핸들러 안에서** 호출해야 합니다. 그렇지 않으면 거부됩니다. `ext-apps` 1.4.0 이상이 필요합니다.

```json
{ "uri": "ui://example/home", "mimeType": "text/html;profile=mcp-app",
  "text": "<!DOCTYPE html>...", "_meta": { "ui": { "preferredDisplayMode": "fullscreen" } } }
```

### 14.4 Entry points (Artifacts 페이지, 사이드바 고정)

- 도구 `_meta`에 `openai/ui` 키(OpenAI MCP extensions에서 온 키)로 global entrypoint를 표시합니다. `ui.resourceUri`도 반드시 함께 둬야 합니다.

```json
{
  "name": "open_studio", "title": "Example Studio",
  "description": "Open Example Studio.", "inputSchema": { "type": "object" },
  "_meta": {
    "ui": { "resourceUri": "ui://example/home" },
    "openai/ui": { "entrypoints": [{ "type": "global" }] }
  }
}
```

- 사용자가 entry point로 열면 요청도 대화도 없는 상태에서 View가 시작됩니다. 그래서 **모든 입력을 optional로 두고, 인자가** `{}`**일 때도 쓸 만한 초기 화면**을 보여줘야 합니다.
- 웹에서는 열 때마다 새 대화가 만들어지고, `fullscreen`을 지원하면 전체 화면으로 표시됩니다.
- `title`은 60자 이하로 쓰고 Claude나 Anthropic 이름은 넣지 않습니다. `name`에는 `__`, `..`, `._`를 쓰면 안 됩니다(목록에는 보이지만 열리지 않습니다).
- 변경이 반영되기까지 최대 약 2시간 걸립니다. 바로 확인하려면 `Customize > Connectors > ⋯ > Refresh tools list`를 실행합니다(본인 계정에만 적용됩니다).

### 14.5 App tools (Claude 제약)


| 항목          | 한도                                              | 넘었을 때                                                |
| ----------- | ----------------------------------------------- | ---------------------------------------------------- |
| View당 도구 수  | 64개                                             | 그 View의 도구를 전부 쓸 수 없습니다                              |
| 도구 정의 전체 크기 | 1 MiB(JSON)                                     | 그 View의 도구를 전부 쓸 수 없습니다                              |
| 도구 이름       | 영문, 숫자, `.`, `_`, `-`만 가능, 128자 이하, View 안에서 고유 | 해당 도구가 전달되지 않습니다                                     |
| 입력 스키마      | 루트는 `type: "object"`, 64 KiB 이하, 중첩 32단계 이하     | 빈 스키마로 전달되고 Always allow가 표시되지 않습니다                  |
| 응답 시간       | 45초(승인 대기 제외)                                   | "응답 없음, 작업이 실행됐을 수 있음" 오류를 받습니다                      |
| 결과 텍스트      | 100,000자                                        | 잘립니다. `structuredContent`가 한도를 넘으면 아예 전달되지 않을 수 있습니다 |


**승인과 설계 가이드**

- 실행 전에 사용자 승인을 받습니다. 승인 창에는 커넥터 이름과 도구 `title`이 표시되고, 사용자는 입력값과 description을 볼 수 있습니다. 사용자가 거부하면 Claude는 오류 결과를 받습니다.
- name, title, description, input schema가 바뀌거나 View를 여는 서버 도구가 바뀌면 다시 승인을 받습니다. 그러므로 **도구 정의는 바꾸지 않고**, 열린 파일명처럼 바뀌는 값은 결과에 담습니다.
- Claude가 도구 목록을 볼 때는 **description의 첫 줄(최대 200자)만** 받습니다. 첫 줄만 읽어도 이해되게 씁니다.
- 45초를 넘길 수 있는 작업은 바로 응답하고, 결과를 조회하는 두 번째 도구를 둡니다.
- View가 `app.callServerTool`로 **자기 서버의 도구**를 부를 때는 승인 창이 뜨지 않습니다.
- **원격 커넥터의 View만** Claude가 호출할 수 있습니다. 웹의 unified experience에서 테스트해야 하며, View가 로드되어 있지 않으면 호출이 실패합니다.
- View가 다시 로드되면 HTML 초기 상태로 돌아갑니다. 유지해야 할 상태는 서버에 저장합니다.
- 첫 View를 연 메시지에서는 도구가 아직 전달되지 않습니다. 앱을 연 뒤 **다음 메시지에서** 조작을 요청해야 합니다.
- 디버깅할 때는 개발자 도구 콘솔을 Verbose 레벨로 두면 SDK의 `console.debug` 프로토콜 로그를 볼 수 있습니다.
- 자주 나는 오류: `app.registerTool is not a function`(SDK 버전), `Client does not support tool capability`, `Schema (vendor: zod) does not implement Standard JSON Schema`(zod 버전)

### 14.6 Embedded sites (앱 안에 자사 웹사이트 임베드)

**어디서 로드되는가**


| 커넥터                | 사이트 로드 조건                                    |
| ------------------ | -------------------------------------------- |
| Verified 디렉터리 커넥터  | listing이 해당 origin을 허용할 때만 로드됩니다             |
| Community 디렉터리 커넥터 | 차단됩니다                                        |
| 커스텀 커넥터(URL로 추가)   | 사용자가 대화에서 origin을 허용한 뒤 로드됩니다. 일부 조직부터 제공됩니다 |
| 로컬 서버              | 차단됩니다(터널 URL로 연결하면 커스텀 커넥터로 취급합니다)           |


차단되어도 Claude는 별도 메시지를 보여주지 않습니다. 그래서 View에 **fallback UI**를 꼭 둬야 합니다.

**설정 절차**

1. View 리소스의 `csp.frameDomains`에 사이트 origin을 선언합니다.
2. `ui.domain`을 지정해 sandbox host를 고정합니다.
3. 사이트 응답 헤더에 두 origin을 **모두** 허용합니다. 정확한 host만 쓰고, wildcard(`*.claudemcpcontent.com`)를 쓰면 다른 커넥터의 앱도 내 사이트를 frame할 수 있으므로 금지합니다.

```
Content-Security-Policy: frame-ancestors https://<hash>.claudemcpcontent.com https://claude.ai
```

**세션과 로그인**

- Claude는 사이트에 세션을 넘겨주지 않으며, `document.requestStorageAccess()`는 거부됩니다.
- 쿠키는 `SameSite=None; Secure; Partitioned`로 설정하고, 다른 탭의 세션에 의존하지 않습니다.
- **권장 로그인 패턴**
  1. 서버에 단기·1회용 토큰을 발급하는 도구를 만들고 `_meta.ui.visibility: ["app"]`으로 둡니다(Claude는 호출할 수 없습니다).
  2. View가 `app.callServerTool()`로 토큰을 받습니다.
  3. View가 사이트의 **정확한 origin**을 target으로 지정해 `postMessage`로 전달합니다.
  4. 사이트가 토큰을 세션으로 교환합니다.
- 로그인 페이지를 frame 안에 띄울 수 있다면 iframe 안에서 바로 로그인 폼을 보여줘도 됩니다.

**디바이스 기능 제한**

- origin이 허용된 View는 `_meta.ui.permissions`로 요청했더라도 camera, microphone, geolocation, clipboard write를 쓸 수 없습니다. 임베드된 사이트도 마찬가지입니다.
- 디렉터리 커넥터는 다른 View도, 커스텀 커넥터는 모든 View가 이 기능을 잃는다고 보고 설계해야 합니다.
- 실제로 허용된 기능은 `app.getHostCapabilities()?.sandbox?.permissions`로 확인합니다.
- `Element.requestFullscreen()`도 실패한다고 가정합니다.

**디버깅 단서**: 콘솔 오류에 `frame-src`가 나오면 View 쪽 CSP 문제, `frame-ancestors`나 `X-Frame-Options`가 나오면 사이트 쪽 헤더 문제입니다.

### 14.7 스펙 표준과 Claude 확장 구분


| 항목                                                | 표준 스펙           | Claude 확장/제약                                   |
| ------------------------------------------------- | --------------- | ---------------------------------------------- |
| `ui://` 리소스, `_meta.ui.resourceUri`, `visibility` | ✅               | –                                              |
| `inline` / `fullscreen` / `pip`                   | ✅               | 사이드 패널도 `fullscreen`으로 보고                      |
| `preferredDisplayMode`                            | ❌               | ✅ Claude 전용                                    |
| `openai/ui.entrypoints`                           | ❌ (OpenAI 확장)   | ✅ Claude가 해석                                   |
| App-provided tools                                | ✅               | 64개, 45초 등의 한도와 승인 UX                          |
| `ui.domain`                                       | ✅ (형식은 호스트가 정함) | `sha256(url)[:32].claudemcpcontent.com`        |
| `frameDomains`                                    | ✅               | Verified listing 또는 사용자 허용 필요, 허용되면 디바이스 권한 박탈 |


## 15. 유사 기술 비교


| 구분        | 일반 MCP Tool | **MCP Apps**  | OpenAI Apps SDK        | MCP-UI                   | Claude 아티팩트    |
| --------- | ----------- | ------------- | ---------------------- | ------------------------ | -------------- |
| 결과 형태     | 텍스트, JSON   | 인터랙티브 HTML UI | 인터랙티브 UI               | HTML, 외부 URL, Remote DOM | Claude가 만든 콘텐츠 |
| 만드는 주체    | MCP 서버      | MCP 서버        | 앱 개발자                  | MCP 서버                   | **Claude(모델)** |
| 실제 시스템 연결 | O           | O             | O                      | O                        | 제한적            |
| 표준 여부     | MCP 코어      | **MCP 공식 확장** | 벤더 SDK                 | 커뮤니티                     | Claude 기능      |
| 이식성       | 높음          | 높음(여러 호스트)    | ChatGPT 중심             | 일부 호스트                   | Claude 전용      |
| 관계        | –           | 둘을 하나로 합친 표준  | `migrate-oai-app`으로 이전 | MCP Apps의 전신             | –              |


Microsoft 365 Copilot 선언적 에이전트도 MCP 플러그인을 통해 MCP Apps(UI 위젯) 응답을 지원합니다.

## 16. AWS AgentCore 관점 적용 포인트

- **Gateway의 리소스 프록시**: AgentCore Gateway는 `resources/list`와 `resources/read`를 하위 MCP 서버 target으로 실시간 프록시합니다. 리소스 URI는 target prefix 없이 그대로 돌려주고, URI가 겹치면 `resourcePriority`가 가장 낮은 target으로 라우팅합니다. 따라서 `ui://` 리소스를 Gateway를 통해 제공하는 구조는 원리상 가능합니다.
- **보안 주의**: AWS 문서에 따르면 `resources/read`의 `uri`는 **검증 없이 하위 target으로 전달**됩니다. SSRF나 `file://` 접근을 막기 위해 `ui://` 같은 허용 스킴 allowlist 검증을 넣어야 합니다(Interceptor Lambda에 적용할 수 있습니다).
- **MCP 프로토콜 버전**: Gateway의 `protocolConfiguration.mcp.supportedVersions`에 클라이언트가 쓰는 버전(예: `2025-11-25`, `2026-07-28`)이 들어 있어야 합니다.
- **인증 분리 설계**
  - MCP 연결 인증은 기존 Cognito 중간 계층 + `CUSTOM_JWT` 구조를 그대로 씁니다([[MCP OAuth 인증]] 참고).
  - embedded site 안의 사용자 세션은 별도로 설계해야 합니다. app 전용 도구로 1회용 토큰을 발급하고 `postMessage`로 전달한 뒤 세션으로 교환하는 방식을 씁니다.
  - `ui.domain`을 고정해야 OAuth 콜백과 `frame-ancestors`를 안정적으로 운영할 수 있습니다.
- **원격 서버 필수**: app tools, entry points, embedded sites는 원격 커넥터에서만 동작합니다. AgentCore Runtime이나 Gateway의 HTTPS MCP 엔드포인트를 커스텀 커넥터로 등록하는 방식이 이 요구사항과 잘 맞습니다.
- **PoC 제안**: AgentCore Runtime에 `registerAppTool`/`registerAppResource`로 만든 MCP App 서버를 배포하고, Gateway target으로 등록한 뒤, Claude 커스텀 커넥터에서 렌더링, app tools, sidebar가 동작하는지 검증합니다.

## 17. 확인하지 못한 부분

- AgentCore Gateway가 도구의 `_meta.ui`(resourceUri, visibility)와 `initialize`의 `extensions["io.modelcontextprotocol/ui"]` capability를 **변형 없이 전달하는지**는 AWS 문서에서 확인하지 못했습니다. 실제 테스트가 필요합니다.
- Claude의 사이드 패널, app tools, embedded sites(커스텀 커넥터)가 "일부 조직부터 제공"이라고만 되어 있어, 전체 공개 일정은 알 수 없습니다.
- `preferredDisplayMode`(Claude)와 `openai/ui.entrypoints`(OpenAI)가 다른 호스트에서도 동작하는지는 보장되지 않습니다.
- Claude Messages(`ui/message`), Downloads, Theming 문서 페이지의 세부 제약(크기 제한 등)은 이번에 자세히 확인하지 못했습니다.
- 모바일 앱에서의 동작은 Claude 문서의 Mobile guidelines를 따로 확인해야 합니다.

## 18. 참고 문서

**MCP Apps 스펙·SDK**

- SEP-1865 스펙: [https://github.com/modelcontextprotocol/ext-apps/blob/main/specification/2026-01-26/apps.mdx](https://github.com/modelcontextprotocol/ext-apps/blob/main/specification/2026-01-26/apps.mdx)
- GitHub 저장소: [https://github.com/modelcontextprotocol/ext-apps](https://github.com/modelcontextprotocol/ext-apps)
- API 문서: [https://apps.extensions.modelcontextprotocol.io/api/](https://apps.extensions.modelcontextprotocol.io/api/)
- Quickstart: [https://apps.extensions.modelcontextprotocol.io/api/documents/quickstart.html](https://apps.extensions.modelcontextprotocol.io/api/documents/quickstart.html)
- MCP 공식 블로그(출시): [https://blog.modelcontextprotocol.io/posts/2026-01-26-mcp-apps/](https://blog.modelcontextprotocol.io/posts/2026-01-26-mcp-apps/)
- 클라이언트 지원 매트릭스: [https://modelcontextprotocol.io/extensions/client-matrix](https://modelcontextprotocol.io/extensions/client-matrix)

**Anthropic / Claude**

- 발표 기사: [https://claude.com/ko/resources/articles/interactive-connectors-full-tool](https://claude.com/ko/resources/articles/interactive-connectors-full-tool)
- Get started: [https://claude.com/docs/connectors/building/mcp-apps/getting-started](https://claude.com/docs/connectors/building/mcp-apps/getting-started)
- Display modes: [https://claude.com/docs/connectors/building/mcp-apps/display-modes](https://claude.com/docs/connectors/building/mcp-apps/display-modes)
- Entry points: [https://claude.com/docs/connectors/building/mcp-apps/entry-points](https://claude.com/docs/connectors/building/mcp-apps/entry-points)
- App tools: [https://claude.com/docs/connectors/building/mcp-apps/app-tools](https://claude.com/docs/connectors/building/mcp-apps/app-tools)
- Embedded sites: [https://claude.com/docs/connectors/building/mcp-apps/embedded-sites](https://claude.com/docs/connectors/building/mcp-apps/embedded-sites)

**AWS**

- AgentCore Gateway resources/read: [https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/gateway-using-mcp-resources-read.html](https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/gateway-using-mcp-resources-read.html)
- AgentCore Gateway resources/list: [https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/gateway-using-mcp-resources-list.html](https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/gateway-using-mcp-resources-list.html)
