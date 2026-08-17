# Agent Plugins Specification v1.0.0 — 세부 스펙 

> **출처(공식 authoritative):** `github.com/agentplugins/agent-plugins-spec` → `spec/1.0.0.md`
> **상태:** Published (RFC 2119/8174 규범 언어 사용 — MUST/SHOULD/MAY)
> **거버넌스:** Amazon·Cursor·Microsoft·OpenAI·Vercel (Core Maintainer) + Google(8/6 합류)

---

## 1️⃣ 스펙의 목차 구조 (11개 규범 섹션)

| § | 섹션 | 성격 |
|---|---|---|
| 1 | Status and version | 규범 |
| 2 | Conformance language (RFC 2119) | 규범 |
| 3 | Terminology | 규범 |
| 4 | Plugin package model | 규범 |
| 5 | Manifest (`plugin.json`) | 규범 |
| 6 | Component discovery | 규범 |
| 7 | Component types (Skills / MCP) | 규범 |
| 8 | Client extensions | 규범 |
| 9 | Env variables & placeholder expansion | 규범 |
| 10 | Versioning | 규범 |
| 11 | Client conformance | 규범 |
| Appendix A / Design Decisions | 체크리스트·설계근거 | **비규범** |

---

## 2️⃣ 핵심 용어 (§3)

| 용어 | 의미 |
|---|---|
| **Plugin** | 매니페스트 + 선택적 컴포넌트를 담은 **자기완결형 디렉터리** |
| **Plugin root** | 플러그인 패키지의 최상위 디렉터리 |
| **Manifest** | plugin root의 `plugin.json` 파일 |
| **Component** | 스펙이 표준화한 단위 = **skill 또는 MCP server** |
| **Client** | 플러그인을 탐색·설치·로드·실행하는 도구(Kiro, VS Code 등) |
| **Extension namespace** | 클라이언트 전용 데이터용 **역도메인 식별자** (예: `com.example.client`) |

---

## 3️⃣ 패키지 모델 & 표준 레이아웃 (§4)

```text
my-plugin/                       ← Plugin root
├── plugin.json                  ← [필수] 매니페스트
├── skills/                      ← [고정 위치] Agent Skills
│   └── summarize/
│       ├── SKILL.md             ← 스킬 정의(필수 파일명)
│       ├── scripts/analyze.sh
│       └── references/checklist.md
├── mcp.json                     ← [고정 위치] MCP 서버 설정
├── com.example.client/          ← [선택] 클라이언트 확장(역도메인)
│   └── hooks/
├── LICENSE
└── CHANGELOG.md
```

### 🔒 경로 격리(Containment) 규칙 — 보안 관점 (§4.1)
경수님 보안 관심사와 직결되는 핵심 규범입니다:

| 규칙 | 내용 |
|---|---|
| 경로 이탈 금지 | 모든 패키지 경로는 plugin root **밖으로 나가면 안 됨** (`../` 금지) |
| 상대경로 형식 | 스펙 정의 경로는 반드시 `./`로 시작 |
| Symlink | plugin root 내부를 가리키면 OK, 밖이면 **거부(reject)** |
| 실패 경계(failure boundary) | 위반 시 **가장 좁은 범위**만 무효화 (아래 표) |

**실패 경계 계층** (문제 발생 시 어디까지 무효화되나):
```
plugin.json 이탈      → 플러그인 전체 거부 (최상위)
고정 위치 이탈        → 해당 컴포넌트 타입만 invalid
SKILL.md 이탈         → 해당 skill만 skip
command/cwd 이탈      → 해당 server entry만 invalid
기타 경로 이탈        → 해당 경로 접근만 거부 (최하위)
```

---

## 4️⃣ 매니페스트 스펙 (§5) — `plugin.json`

### 📕 Closed Schema (폐쇄형 스키마)
> 허용된 top-level 필드 **10개만** 존재. 그 외 필드는 "report and ignore".

| 필드 | 타입 | 필수 | 설명 |
|---|---|---|---|
| **`$schema`** | string | ✅ MUST | 정규 식별자 `https://agent-plugins.org/schemas/1.0.0/plugin.schema.json` |
| **`name`** | string | ✅ MUST | 사람이 읽는 플러그인 이름 |
| `version` | string | ⬜ | SemVer 권장 (업데이트·캐시 판단용) |
| `description` | string | ⬜ | 짧은 설명 |
| `author` | object | ⬜ | `name`/`email`/`url`만 허용 |
| `homepage` | string | ⬜ | 문서·홈페이지 URL |
| `repository` | string | ⬜ | 소스 저장소 URL |
| `license` | string | ⬜ | SPDX 식별자 권장 |
| `keywords` | string[] | ⬜ | 검색 태그 |
| `extensions` | object | ⬜ | 역도메인 네임스페이스별 클라이언트 데이터 |

### 최소 매니페스트 (딱 이 두 줄이 전부)
```json
{
  "$schema": "https://agent-plugins.org/schemas/1.0.0/plugin.schema.json",
  "name": "minimal-plugin"
}
```

### 📏 `name` 제약 (§5.5)
| 제약 | 규칙 |
|---|---|
| 길이 | 1–64자 |
| 문자셋 | 소문자 `a-z`, `0-9`, `-`, `.` 만 |
| 시작/끝 | 반드시 영숫자 |
| 반복 금지 | `--`, `..` 불가 |

✅ 유효: `my-plugin`, `acme.tools`, `lint3r`, `a`
❌ 무효: `My-Plugin`(대문자), `-start`(선행하이픈), `has--double`, `too.many..dots`

### ⚠️ 검증 실패 처리 (매우 중요한 규범)
- **Unknown top-level 필드** → **비치명적**: report & ignore 후 계속 로드
- **`extensions`가 object 아님** → **비치명적**: ignore 후 계속
- **그 외 스키마 위반** → **치명적(fatal)**: 플러그인 거부, 컴포넌트 실행 금지
- **클라이언트는 로딩 중 원격 스키마를 가져오면 안 됨** (`MUST NOT retrieve`)

---

## 5️⃣ 컴포넌트 탐색 (§6) — 고정 위치

| 컴포넌트 | 고정 위치 | 패턴 |
|---|---|---|
| **Skills** | `skills/` | `SKILL.md`를 포함한 **직속 하위 디렉터리** |
| **MCP servers** | `mcp.json` | JSON 설정 |

- `plugin.json`이 이 위치를 **override 불가**, 인라인 정의 **불가**
- 위치가 **없으면(absent)** → 에러 아님(정상)
- 위치가 있지만 **타입이 틀리면**(예: `mcp.json`이 폴더) → 해당 타입만 invalid, 나머지는 계속

---

## 6️⃣ 컴포넌트 타입 (§7) — v1은 **딱 2개**

### 🔹 7.1 Skills
- **Agent Skills Specification(agentskills.io)** 을 따름 → `SKILL.md` 포맷은 그쪽이 source of truth
- Agent Plugins는 **"어디서 발견하는가"만** 정의 (`skills/`)
- `skills/`의 **직속 자식 디렉터리** 중 `SKILL.md`(정확한 파일명)를 가진 것 1개 = 스킬 1개
- **재귀 탐색 금지** (더 깊은 하위는 안 봄)
- 잘못된 스킬 → 해당 스킬만 skip, 나머지 계속

### 🔹 7.2 MCP servers — `mcp.json`

**top-level 구조** (`$schema` + `mcpServers`만 허용):
```json
{
  "$schema": "https://agent-plugins.org/schemas/1.0.0/mcp.schema.json",
  "mcpServers": { "서버명": { ...server config... } }
}
```

#### 3가지 Transport 변형 (closed union — `type` 필수)

**① stdio (로컬 프로세스)**
| 필드 | 타입 | 필수 | 설명 |
|---|---|---|---|
| `type` | `"stdio"` | ✅ | stdio 트랜스포트 |
| `command` | string | ✅ | **단일 실행 토큰** (셸 문자열 ❌). bare 이름 또는 `./`경로 |
| `args` | string[] | ⬜ | 인자 (변수확장 지원) |
| `env` | object<string> | ⬜ | 환경변수 (변수확장 지원) |
| `cwd` | string | ⬜ | 작업 디렉터리 (변수확장 지원) |

**② streamable-http (원격, 현행)** / **③ sse (레거시 HTTP+SSE)**
| 필드 | 타입 | 필수 | 설명 |
|---|---|---|---|
| `type` | `"streamable-http"` 또는 `"sse"` | ✅ | 원격 트랜스포트 |
| `url` | string | ✅ | 절대 HTTP(S) URL, user-info·fragment 금지. **비-loopback은 HTTPS 필수** |
| `headers` | object<string> | ⬜ | 고정 HTTP 헤더 |

> 🔐 **보안 규범**: `headers`·`env`에 **자격증명/시크릿 embed 금지**(visible package data). URL·헤더에 **변수 확장 금지**. v1은 **OAuth 설정 필드 없음** → 인증은 클라이언트가 관리.

#### Transport 지원 의무 (§7.2.1)
- MCP 지원 클라이언트는 **`stdio` 또는 `streamable-http` 중 최소 1개 MUST**, 둘 다 SHOULD
- `sse` 지원은 **OPTIONAL**
- 선언된 `type`으로 **최초 연결** 시도, fallback은 스펙 밖

#### 로딩 규칙 (§7.2.2) — **부분 실패 격리**
```
mcp.json 자체 무효      → MCP만 비활성, 다른 컴포넌트 계속
개별 server entry 무효   → 그 서버만 skip
transport 미지원         → 그 서버만 skip
서버 시작/연결/인증 실패  → 계속 로드, SHOULD report
```

---

## 7️⃣ 클라이언트 확장 (§8) — 역도메인 네임스페이스

- 클라이언트 전용 데이터는 **`extensions` 내 역도메인 키** 또는 **동명의 top-level 디렉터리**로만 표현
- 예: `com.github.copilot/`, `com.example.client`
- **규범적 이식성 규칙**: *"클라이언트는 자신이 구현하지 않은 네임스페이스 엔트리를 값 검증 없이 무시(MUST ignore)"*
- → 그래서 **Hooks·slash commands·custom agents 등은 v1 포터블 코어가 아님** (클라이언트 확장으로만 가능)

```json
{
  "$schema": ".../plugin.schema.json",
  "name": "example-plugin",
  "extensions": {
    "com.example.client": { "setting": true }
  }
}
```

---

## 8️⃣ 환경변수 & 플레이스홀더 확장 (§9)

### 클라이언트가 subprocess에 **반드시 제공**하는 2개 변수
| 변수 | 의미 | 용도 |
|---|---|---|
| **`PLUGIN_ROOT`** | plugin root 절대경로 | 번들된 스크립트·바이너리·config 참조 |
| **`PLUGIN_DATA`** | 클라이언트 관리 영속 데이터 디렉터리 | node_modules, venv, 캐시, 상태 (업데이트 넘어 유지) |

### 확장 규칙 (`${PLUGIN_ROOT}` / `${PLUGIN_DATA}`)
- 확장 대상: `args`의 모든 문자열, `env`의 모든 **값**, `cwd`
- 확장 **제외**: `env` **키**, `command`, 고정 컴포넌트 위치
- **단일·비재귀** 텍스트 치환 (치환 결과 재스캔 ❌)
- `env`에 `PLUGIN_ROOT`/`PLUGIN_DATA` 키를 직접 넣으면 → 해당 서버 **invalid** (클라이언트가 주입해야 함)

```json
{
  "$schema": "https://agent-plugins.org/schemas/1.0.0/mcp.schema.json",
  "mcpServers": {
    "database": {
      "type": "stdio",
      "command": "npx",
      "args": ["--config", "${PLUGIN_ROOT}/config/db.json"],
      "cwd": "${PLUGIN_ROOT}",
      "env": { "DATA_DIR": "${PLUGIN_DATA}/database" }
    }
  }
}
```

---

## 9️⃣ 버저닝 (§10)

- **스펙·매니페스트 스키마·MCP 스키마는 동일 버전을 공유** (모두 `1.0.0`)
- `mcp.json`의 `$schema` 버전은 `plugin.json`의 버전과 **일치해야 함** (불일치 시 MCP만 invalid)
- 정규 스키마 식별자는 **재할당 금지**(다른 내용에 같은 URL 재사용 ❌)
- 플러그인 `version`은 **SemVer 권장** (Major=breaking / Minor=호환기능 / Patch=수정)

---

## 🔟 클라이언트 적합성 (§11) — 최소 요건

**Conformant client의 최소 8조건:**
1. 디렉터리 경로에서 플러그인 로드 가능
2. `$schema`로 매니페스트 스키마 선택 → closed `plugin.json` 검증
3. 미구현 `extensions`는 값 검증 없이 무시
4. 지원 컴포넌트 타입은 고정 위치에서 탐색
5. MCP 지원 시 `stdio`/`streamable-http` 중 최소 1개
6. subprocess 실행 시 `PLUGIN_ROOT`·`PLUGIN_DATA` 제공 + 변수 확장
7. stdio `command`를 단일 토큰으로 해석, 기본 cwd=plugin root
8. **최소 1개 컴포넌트 타입(skills 또는 MCP) 지원**

### ⭐ 점진적 채택 (§11.2) — 실무 핵심
> **"skills-only 클라이언트도 MCP 미지원인 채로 conformant 가능"**
- "Agent Plugins 호환"이라고 해서 **모든 컴포넌트·트랜스포트를 구현했다는 뜻은 아님**
- 클라이언트는 컴포넌트를 **증분 채택** 가능

---

## 🎨 설계 결정 (Design Decisions) — 왜 이렇게?

| 결정 | 이유 |
|---|---|
| **디렉터리 기반** (zip/registry ❌) | `ls`/`cat`/`git`로 검사·편집·버전관리 가능 |
| **v1은 Skills+MCP만** | 둘은 이미 외부 표준(agentskills.io, MCP)이 있고 크로스클라이언트 채택됨. commands·hooks·agents·rules·LSP는 아직 client-specific |
| **Closed 매니페스트** | 엄격 검증·오타 탐지·키 자동완성. 실험 필드는 `extensions`로 격리 |
| **역도메인 확장** | 중앙 레지스트리 없이 충돌 회피(분산 컨벤션) |
| **명시적 MCP 포맷** | 기존 클라이언트마다 MCP 설정 shape이 달라 transport 추론 방식이 제각각 → 명시적 closed union으로 통일 |
| **컴포넌트 실패 비치명적** | MCP 서버 1개 죽어도 skill은 계속 사용 가능 |

---

## 📊 한눈에 보는 스펙 요약도

```
Agent Plugins v1.0.0
│
├── [필수] plugin.json  ── closed schema (10 fields, $schema+name 필수)
│                          └ name: 1-64자, 소문자+숫자+-.
│
├── skills/            ── Agent Skills spec(agentskills.io) 준수
│   └── <skill>/SKILL.md   (직속 자식만, 재귀 ❌)
│
├── mcp.json           ── {$schema, mcpServers}
│   └── server: stdio | streamable-http | sse(optional)
│         변수: ${PLUGIN_ROOT}, ${PLUGIN_DATA}
│
└── com.<domain>.<client>/  ── 역도메인 클라이언트 확장 (미구현 시 무시)
```

---
