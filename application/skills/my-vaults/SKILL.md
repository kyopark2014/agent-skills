---
name: my-vaults
description: ob-docs(Obsidian형 vault)에 저장된 마크다운 노트를 조회·검색·생성·수정합니다. 사용자가 "vault", "내 노트", "ob-docs", "위키링크", "백링크", "노트 찾아줘", "메모 저장", "vault에 적어줘", "지식베이스" 등을 요청할 때 사용합니다.
---

# my-vaults (ob-docs)

standalone **ob-docs** (`https://vault.my-agentic-ai.click`) vault를 API로 읽고 씁니다.  
노트는 `.md`가 Source of Truth입니다. 임의 HTTP를 새로 짜지 말고 아래 스크립트를 실행하세요.

계정(`USER_ID` / email)마다 S3·디스크가 `vault/{userId}/…`로 분리됩니다.  
스크립트가 보내는 userId의 vault만 보이며, 노트 경로는 항상 **그 계정 vault 기준 상대경로**입니다
(예: `Meeting/Note.md` — path에 email을 넣지 마세요).

## When to Use

- vault / ob-docs / 내 노트 / 위키 노트 조회
- 노트 검색, 파일 트리, 백링크·그래프 확인
- 새 노트 작성, 기존 노트 수정·이어쓰기·이름변경·삭제

## Script Location

application working directory 기준 전체 경로를 사용하세요.

| 스크립트 | 용도 |
| --- | --- |
| `skills/my-vaults/scripts/read_vault.py` | 조회 (health / tree / list / read / search / graph / backlinks) |
| `skills/my-vaults/scripts/write_vault.py` | 쓰기 (write / append / mkdir / rename / delete / rebuild) |
| `skills/my-vaults/scripts/lib_vault.py` | HTTP·인증 헬퍼 (직접 실행하지 않음) |

**IMPORTANT**: `scripts/...`로 줄이지 말고 위 전체 경로를 사용하세요.

## Critical Rules

1. **반드시 스크립트로** 조회·수정하세요. `curl`로 ad-hoc API를 새로 작성하지 마세요.
2. 경로는 vault 상대경로입니다. 예: `AI/Ontology.md`, `Meeting/Weekly-Sync.md`
3. 덮어쓰기 전에 `read`로 현재 내용을 확인하세요. 부분 추가는 `append`를 우선 사용하세요.
4. 출력은 JSON입니다. 사용자에게는 한국어로 요약하고, 본문이 길면 핵심만 인용하세요.
5. 인증은 스크립트가 `USER_ID`/`CURRENT_USER_ID`와 vault-agent-token으로 처리합니다. 쿠키를 수동으로 만들지 마세요.
6. **노트 본문 형식**: YAML frontmatter(`---` … `title`/`date`/`tags`/`aliases`/`status` … `---`)를 **넣지 마세요**. 새 노트·덮어쓰기는 상단을 `# 제목` 한 줄로 시작하고 바로 본문을 이어서 쓰세요.
7. **새 노트는 vault 루트에 두지 마세요.** 주제 폴더 아래(`Category/Note.md`)에만 저장합니다. 아래 [Folder Organization](#folder-organization)을 따르세요.
8. **결과는 가능한 하나의 마크다운에 모으세요.** 같은 주제·같은 작업의 산출물을 작은 `.md` 여러 개로 쪼개지 마세요. 섹션(`##`/`###`)으로 구조화하고, 분량이 많으면 제목 바로 아래에 **목차(TOC)** 를 넣으세요. 사용자가 파일 분리를 명시하거나 기존 노트에 이어 쓰는 경우만 예외입니다.

```markdown
# Claude Tag

## 목차
- [개요](#개요)
- [세부](#세부)

## 개요

본문…

## 세부

본문…
```
## Folder Organization

새 노트 작성 시 **반드시** 카테고리 폴더에 넣습니다. 루트에 `Something.md`를 만들지 마세요.

### 절차

1. `tree` 또는 `list`로 기존 폴더를 확인합니다.
2. 노트 주제에 맞는 폴더가 **이미 있으면 그 폴더를 재사용**합니다. (대소문자·이름 변형이 비슷하면 기존 폴더 우선)
3. 없으면 `mkdir`로 새 카테고리 폴더를 만든 뒤 그 안에 노트를 저장합니다.
4. 경로 형식: `<Category>/<NoteTitle>.md` (예: `Meeting/Sprint-Review.md`)

### 카테고리 예시

노트 내용에 맞게 선택·생성하세요. 대표 예시는 다음과 같습니다.

| 폴더 | 넣을 노트 |
| --- | --- |
| `Productivity` | 할 일, 습관, GTD, 워크플로, 생산성 팁 |
| `AI` | 모델, 프롬프트, LLM 개념, AI 도구 |
| `Agent` | 에이전트·스킬·런타임·자동화 로그 |
| `Meeting` | 회의록, 싱크, 액션 아이템 |
| `Project` | 특정 프로젝트 메모·계획·로드맵 |
| `Research` | 조사, 논문, 벤치마크, POC 결과 |
| `Personal` | 개인 메모, 일기성 기록, 목표 |
| `Reference` | 치트시트, 컨벤션, 재사용 참고 |
| `Architecture` | 시스템 설계, 다이어그램, ADR, 의사결정 |
| `DevOps` | CI/CD, 인프라, 배포, 모니터링, IaC |
| `Cloud` | AWS/GCP/Azure 서비스, 계정·리전 메모 |
| `Security` | 위협 모델, 권한·시크릿, 보안 체크리스트 |
| `Product` | 요구사항, 유저 스토리, 우선순위, 스펙 |
| `Design` | UI/UX, 와이어프레임, 디자인 토큰 메모 |
| `Engineering` | 구현 메모, 버그 분석, 리팩터링 기록 |
| `Learning` | 공부 노트, 튜토리얼 정리, 강의 요약 |
| `Ideas` | 아이디어 스케치, 가설, 브레인스토밍 |
| `Writing` | 초안, 블로그·발표 원고, 문서 아웃라인 |
| `Finance` | 예산, 비용, 청구·견적, 재무 메모 |
| `People` | 1:1, 피드백, 팀·조직 관련 메모 |
| `Decision` | 의사결정 로그, 트레이드오프, go/no-go |
| `Runbook` | 운영 절차, 장애 대응, how-to |
| `Template` | 재사용 템플릿(회의록·ADR·체크리스트 뼈대) |
| `Archive` | 완료·폐기된 노트 (사용자가 아카이브 요청 시) |

위 목록에 없으면 짧은 PascalCase/영문 폴더명을 새로 만드세요 (예: `Legal`, `Health`, `Travel`). 한 노트에 주제가 겹치면 **가장 구체적인 하나**만 고릅니다.

### 금지 / 권장

- ❌ `Draft.md`, `Note.md`처럼 vault 루트에 직접 저장
- ❌ 매번 `00-Inbox`에만 쌓기 (사용자가 명시하지 않는 한)
- ❌ 같은 주제 결과를 `Overview.md` / `Details.md` / `Summary.md`처럼 작은 파일 여러 개로 쪼개기
- ✅ 기존 `AI/`가 있으면 `AI/New-Topic.md`
- ✅ 없으면 `mkdir Meeting` 후 `Meeting/Standup-2026-03-17.md`
- ✅ 긴 노트는 하나의 `.md` + `##` 섹션 + (분량 많으면) 목차

## Quick Start

```bash
# 연결 확인 (기본 URL: https://vault.my-agentic-ai.click)
python skills/my-vaults/scripts/read_vault.py health

# 노트 목록
python skills/my-vaults/scripts/read_vault.py list
python skills/my-vaults/scripts/read_vault.py list --prefix Productivity

# 트리 (새 노트 전 폴더 확인용)
python skills/my-vaults/scripts/read_vault.py tree

# 읽기
python skills/my-vaults/scripts/read_vault.py read Productivity/Convention.md
python skills/my-vaults/scripts/read_vault.py read Productivity/Convention.md --raw

# 검색
python skills/my-vaults/scripts/read_vault.py search "온톨로지"

# 그래프 / 백링크
python skills/my-vaults/scripts/read_vault.py graph
python skills/my-vaults/scripts/read_vault.py backlinks Productivity/Convention.md
```

쓰기 (본문은 항상 `# 제목`으로 시작 — frontmatter 금지. **루트 저장 금지**):

```bash
# 새 노트 전: 기존 폴더 확인
python skills/my-vaults/scripts/read_vault.py tree

# 폴더가 없으면 생성 후 저장
python skills/my-vaults/scripts/write_vault.py mkdir Meeting
python skills/my-vaults/scripts/write_vault.py write Meeting/Sprint-Review.md --content "# Sprint Review\n\n본문"

# 기존 폴더가 있으면 그대로 사용
python skills/my-vaults/scripts/write_vault.py write AI/Prompt-Patterns.md --content "# Prompt Patterns\n\n본문"

# 로컬 파일 업로드
python skills/my-vaults/scripts/write_vault.py write Research/Report.md --file "$ARTIFACTS_DIR/report.md"

# 이어쓰기 (stdin)
printf '\n## Update\n- item\n' | python skills/my-vaults/scripts/write_vault.py append Agent/Runtime-Log.md --stdin

# 이동 / 삭제
python skills/my-vaults/scripts/write_vault.py rename Meeting/Draft.md Productivity/Draft.md
python skills/my-vaults/scripts/write_vault.py delete Meeting/Draft.md
```

### Agent usage

```python
import subprocess

READ = "skills/my-vaults/scripts/read_vault.py"
WRITE = "skills/my-vaults/scripts/write_vault.py"

# 새 노트 전 폴더 확인
r = subprocess.run(["python", READ, "tree"], capture_output=True, text=True)
print(r.stdout)

r = subprocess.run(["python", READ, "search", "온톨로지"], capture_output=True, text=True)
print(r.stdout)

# 카테고리 폴더 아래만 쓰기 (루트 금지)
r = subprocess.run(
    ["python", WRITE, "mkdir", "Agent"],
    capture_output=True, text=True,
)
r = subprocess.run(
    ["python", WRITE, "append", "Agent/Runtime-Log.md", "--content", "- done\\n"],
    capture_output=True, text=True,
)
print(r.stdout)
```

## Subcommands

### `read_vault.py`

| 명령 | 설명 |
| --- | --- |
| `health` | 서비스 상태 |
| `session` | 인증된 user_id 확인 |
| `tree` | 폴더 트리 |
| `list [--prefix] [--ext md\|\*]` | 플랫 파일 목록 |
| `read <path> [--raw]` | 노트 본문(+메타/백링크) |
| `search <query> [--limit N]` | 전문/메타 검색 |
| `graph` | 위키링크 그래프 |
| `backlinks <path>` | 해당 노트를 가리키는 링크 |

### `write_vault.py`

| 명령 | 설명 |
| --- | --- |
| `write <path> (--content\|--file\|--stdin)` | 덮어쓰기 저장 |
| `append <path> (--content\|--file\|--stdin)` | 이어쓰기 (`--no-create`로 신규 금지) |
| `mkdir <path>` | 폴더 생성 |
| `rename <from> <to>` | 이동/이름변경 |
| `delete <path>` | 파일·폴더 삭제 |
| `rebuild` | 검색/그래프 인덱스 재생성 |

공통 옵션: `--user-id` (기본은 환경변수 `USER_ID` / `CURRENT_USER_ID`)

API는 ob-docs 사이트 루트의 `/api/...` 입니다 (구 `/vault/api/...` 아님).

## Environment

| 변수 | 기본 | 설명 |
| --- | --- | --- |
| `OB_DOCS_URL` / `VAULT_API_URL` | `https://vault.my-agentic-ai.click` | ob-docs base URL (agentic `sharing_url`/cowork 도메인 사용 금지) |
| `USER_ID` / `CURRENT_USER_ID` | `local-dev` | 세션 user id — **프로덕션에서는 로그인 email** |
| `VAULT_AGENT_TOKEN` | Secrets Manager `agent-skills/vault-agent-token` 또는 `ob-docs/vault-agent-token` | AgentCore용 HMAC (양쪽 시크릿 값이 같아야 함) |
| `OB_DOCS_VAULT_AGENT_SECRET` | (없음) | 토큰 시크릿 이름 강제 지정 |
| `SESSION_SIGNING_KEY` | (로컬/앱 ECS) | 웹 세션 키 — AgentCore에서는 IAM Deny |

로컬 ob-docs:

```bash
export OB_DOCS_URL=http://127.0.0.1:8502
export USER_ID='kyopark2014@gmail.com'   # 계정별 vault
python skills/my-vaults/scripts/read_vault.py list
```

config.json에 `ob_docs_url`을 넣어도 됩니다.

## Response tips

- `list`/`search` 결과는 path 표로 정리하세요.
- `read`는 title·tags·backlinks를 함께 보여준 뒤 본문 요약을 하세요.
- 쓰기 성공 시 path와 `ok: true`만 짧게 확인하면 됩니다.
- 새 노트 작성 시 frontmatter 없이 `# 제목` + 본문만 저장하고, **카테고리 폴더 경로**(`AI/...`, `Meeting/...` 등)를 사용자에게 알려 주세요.
- 사용자가 폴더를 지정하지 않아도 주제에 맞는 폴더를 고르거나 만들고, 기존 폴더가 있으면 재사용하세요.
- 조사·요약·리포트·회의록 등은 **한 파일**에 섹션으로 모으세요. `Part1.md` / `Part2.md`처럼 잘게 나누지 마세요.
- 본문이 길면 (`##` 섹션이 대략 4개 이상이거나 스크롤이 길 때) `# 제목` 다음에 `## 목차`와 앵커 링크 목록을 넣으세요.

## Troubleshooting

### `Vault auth unavailable` / `SESSION_SIGNING_KEY not found`

AgentCore 런타임은 `session-signing-key` 읽기가 **IAM Deny**입니다.  
`agent-skills/vault-agent-token` 또는 `ob-docs/vault-agent-token` + 런타임 정책 Allow가 필요합니다.  
두 시크릿 **문자열이 동일**해야 ob-docs가 검증에 성공합니다.

### `Failed to reach ob-docs`

`OB_DOCS_URL`이 `https://vault.my-agentic-ai.click` (또는 로컬 `http://127.0.0.1:8502`)인지 확인하세요.  
agentic-work CloudFront(`cowork…`)로 치면 실패합니다.

### `HTTP 404` on `/vault/api/...`

구 경로입니다. 이 스킬은 `/api/health`, `/api/files/tree` 등 **사이트 루트 API**만 사용합니다. 스킬을 최신으로 동기화하세요.

### `HTTP 401 unauthorized`

프로덕션: vault-agent-token이 ob-docs·AgentCore 양쪽에 있고 값이 같은지 확인하세요.  
로컬: `OB_DOCS_URL=http://127.0.0.1:8502` + ob-docs `ALLOW_LOCAL_AUTH_BYPASS=1`.

### 빈 tree / `File not found`

`USER_ID`가 실제 Google email인지 확인하세요. `local-dev`는 별도 빈 vault입니다.  
노트는 `vault/{email}/…` 아래에만 있습니다.
