# Agent Skills

Agent는 MCP뿐 아니라 [Skill](https://github.com/anthropics/skills)을 활용하여 다양한 기능을 편리하게 구현할 수 있습니다. 여기에서는 [LangGraph](https://www.langchain.com/langgraph)에서 Agent Skill을 활용하는 방법을 설명합니다.

## 개요

Web UI는 **FastAPI + React**이며, Agent는 **같은 프로세스**의 LangGraph로 실행합니다. (`agentic-work`는 UI와 AgentCore Runtime을 분리하지만, 이 저장소는 UI·Agent를 `application/`에 함께 두어 로컬 테스트에 최적화되어 있습니다.)

| 구분 | 경로 | 역할 |
|------|------|------|
| Web UI | `application/server.py`, `application/web/` | Task·Chat·Skill/MCP 설정, SSE 스트리밍 |
| Agent | `application/chat.py` → `langgraph_agent.py` | LangGraph ReAct + MCP + Skills |
| 설정 | `application/config.json`, `mcp.list`, `skills.list` | 모델·MCP·Skill 기본값 |

```text
Browser (React :8501)
    │  REST + SSE (/api/...)
    ▼
FastAPI (application/server.py)
    │  chat.run_agent(...)
    ▼
LangGraph (langgraph_agent) + MCP + Skills + Bedrock
```

## 로컬 실행

```bash
# 프론트 빌드 후 FastAPI (포트 8501)
./run_local.sh

# 또는
cd application/web && npm install && npm run build && cd ../..
pip install -r requirements.txt
uvicorn application.server:app --host 0.0.0.0 --port 8501
```

브라우저: [http://localhost:8501](http://localhost:8501)

- 최초 접속 시 User ID를 입력하면 쿠키로 세션이 유지됩니다.
- `application/config.json`에 region, S3, Knowledge Base, Memory 등이 필요합니다. (`installer.py`로 생성 가능)
- Agent는 AgentCore Runtime이 아니라 **로컬 LangGraph**로 동작합니다.

프론트만 수정할 때:

```bash
cd application/web && npm run dev   # Vite :5173, /api → :8501 프록시
# 다른 터미널
uvicorn application.server:app --host 0.0.0.0 --port 8501
```

## Operation Architecture

```mermaid
flowchart TB
  subgraph UI["Web UI FastAPI + React"]
    SPA["web/ React SPA"]
    API["server.py / api/*"]
    TS[task_store SQLite]
  end

  subgraph Agent["application/ Agent"]
    RA["chat.run_agent"]
    RLA["run_langgraph_agent"]
    SG["langgraph_agent StateGraph"]
    CM[call_model]
    TN[ToolNode]
  end

  subgraph Skills["Skills"]
    SM[skill.py SkillManager]
    SK["skills/*/SKILL.md"]
    GSI[get_skill_instructions]
  end

  subgraph MCP["MCP"]
    CFG[mcp_config.py]
    SRV["mcp_server_* / remote gateway"]
    CLI[MultiServerMCPClient]
  end

  subgraph AWS["AWS"]
    BR[Bedrock Runtime]
    S3[(S3)]
    KB[Knowledge Base]
    MEM[AgentCore Memory]
  end

  SPA --> API
  API --> TS
  API -->|SSE| RA
  RA --> RLA
  RLA --> SG
  SG --> CM
  CM --> TN
  TN --> CM
  CM --> BR
  RLA --> CLI
  CLI --> SRV
  CFG --> SRV
  RLA --> GSI
  GSI --> SM
  SM --> SK
  TN --> S3
  SRV --> KB
  SRV --> MEM
```

| 기능 | 모듈 | 설명 |
|------|------|------|
| Chat (SSE) | `api/routes_chat.py` → `chat.run_agent` | Task별 스트리밍 대화 |
| Agent | `langgraph_agent` + MCP/Skills | ReAct 루프, checkpoint |
| 이미지 | `build_human_message_with_files` | 멀티모달 HumanMessage로 이미지 전달 |
| RAG 업로드 | `api/routes_rag.py` | S3 업로드 + Knowledge Base sync |
| Memory | Sidebar Memory 토글 + MCP `memory` | AgentCore Memory 저장/조회 |

## Web UI

### 구성

| 레이어 | 스택 |
|--------|------|
| Backend | FastAPI, uvicorn (`application.server:app`, port **8501**) |
| Frontend | React 19 + TypeScript + Vite (`application/web/`) |
| 영속화 | SQLite `application/data/tasks.db` (로컬) |
| Auth | HttpOnly 쿠키 `agent_user_id` |

### 주요 API

| Method | Path | 설명 |
|--------|------|------|
| GET/POST/DELETE | `/api/session` | 사용자 세션 쿠키 |
| GET/PATCH | `/api/config` | 모델·Skill·MCP 목록/기본값 |
| CRUD | `/api/tasks` | Task 생성·수정·삭제 |
| POST | `/api/tasks/{id}/chat` | SSE 채팅 스트림 |
| POST | `/api/files/upload` | 이미지 S3 업로드 |
| POST | `/api/rag/upload` | RAG 문서 업로드·동기화 |
| GET | `/api/health` | 헬스체크 |

### UI에서 설정하는 항목

사이드바 / Config에서 Task마다 다음을 고릅니다.

- **Model** — Bedrock / Mantle 모델 표시명
- **Skills** — `application/skills.list` 기반 체크리스트
- **MCP servers** — `application/mcp.list` 기반 체크리스트
- **Memory** — 켜면 대화 저장 + `memory` MCP 자동 연결
- **Guardrail** — 설정된 경우 Bedrock Guardrail 적용

기본 Skill/MCP는 `config.json`의 `default_skills`, `default_mcp_servers`에 저장되며 Web UI에서 변경할 수 있습니다.

### 디렉터리 (application/)

```text
application/
├── server.py                 # FastAPI 진입점 + SPA 서빙
├── runtime_mode.py           # local: chat.run_agent
├── chat.py                   # LLM, create_agent, run_langgraph_agent
├── langgraph_agent.py        # StateGraph + builtin tools
├── skill.py / skills/        # SKILL.md 기반 스킬
├── mcp_config.py / mcp_server_*.py
├── task_store.py             # tasks.db
├── api/                      # routes_auth, chat, config, files, rag, tasks
├── web/                      # React SPA (src/, dist/)
├── mcp.list / skills.list
└── config.json               # (gitignore) AWS·KB·S3·API keys
```

## Agent Skills

[Agent Skills](https://agentskills.io/specification)은 AI agent에게 특정 작업 수행 방법을 가르치는 재사용 가능한 지침 패키지입니다. discovery → activation → execution 순으로 context를 관리합니다.

### Progressive Disclosure

시스템 프롬프트에는 스킬의 **이름과 설명만** XML로 넣고, 상세 지침은 agent가 `get_skill_instructions`로 **필요할 때만** 로드합니다.

```xml
<available_skills>
  <skill>
    <name>pdf</name>
    <description>PDF 파일 읽기/병합/분할/OCR/폼 처리 등</description>
  </skill>
</available_skills>
```

### 스킬 구조

```text
skills/
├── pdf/
│   ├── SKILL.md
│   └── assets/
├── pptx/
│   └── SKILL.md
└── skill-creator/
    └── SKILL.md
```

| 스킬 예 | 설명 |
|---------|------|
| pdf / docx / xlsx / pptx | 문서 생성·편집 |
| myslide | AWS 테마 프레젠테이션 |
| skill-creator | 새 스킬 설계·패키징 |
| memory-manager | MEMORY.md 기반 워크스페이스 메모리 |
| retrieve | Knowledge Base RAG |
| graphify | 지식 그래프 구축·질의 |

동작은 [skill.py](./application/skill.py)의 `SkillManager`가 담당합니다. Web UI Task에서 고른 skill 목록이 `build_skill_prompt()` → 시스템 프롬프트로 전달됩니다.

## LangGraph Agent

요청 흐름:

1. Web UI `POST /api/tasks/{id}/chat` (SSE)
2. `runtime_mode.run_agent` → `chat.run_agent`
3. 이미지 첨부가 있으면 `build_human_message_with_files`로 멀티모달 `HumanMessage` 구성
4. `create_agent` — builtin tools + 선택 MCP + skill tools
5. `langgraph_agent.buildChatAgentWithHistory` — checkpoint로 Task별 대화 유지
6. `astream(stream_mode="messages")` → notification queue → SSE (`token` / `tool` / `done`)

Builtin tools 예: `execute_code`, `write_file`, `read_file`, `bash`, `upload_file_to_s3`, `get_current_time`, `get_skill_instructions`

## MCP

선택 MCP는 `mcp_config.load_selected_config` → stdio 또는 AgentCore Gateway(`websearch`, SigV4)로 연결됩니다. 목록은 [application/mcp.list](./application/mcp.list)를 참고하세요.

대표적인 MCP:

- [Tavily](https://github.com/kyopark2014/mcp/blob/main/mcp-tavily.md) — 웹 검색
- [RAG / knowledge base](https://github.com/kyopark2014/mcp/blob/main/mcp-rag.md) — Bedrock Knowledge Base
- [web_fetch](https://github.com/kyopark2014/mcp/blob/main/mcp-web-fetch.md) — URL → markdown
- [Notion](https://github.com/kyopark2014/mcp/blob/main/mcp-notion.md) / Slack / drawio / korea_weather / memory 등

Gateway 기반 웹검색은 [websearch.md](./websearch.md)를 참조하세요.

## Memory

장기 기억은 [Amazon Bedrock AgentCore Memory](https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/memory.html)를 사용합니다.

Web UI에서:

1. Task의 **Memory** 토글을 켭니다 → `memory_enabled=true`, 필요 시 MCP `memory` 자동 추가
2. 대화 종료 후 `save_to_memory`로 short-term event 기록 → strategy가 long-term 추출
3. Agent는 MCP `recall_memory`(retrieve / list / get)로 조회만 수행

관련 코드: [mcp_server_memory.py](./application/mcp_server_memory.py), [mcp_memory.py](./application/mcp_memory.py), [agentcore_memory.py](./application/agentcore_memory.py)

워크스페이스 Markdown 메모리(`MEMORY.md`, `memory/*.md`)는 `memory-manager` 스킬과 함께 사용할 수 있습니다.

## 배포하기

### 인프라

```bash
sudo yum install python3 python3-pip git docker -y   # EC2 예
git clone https://github.com/kyopark2014/agent-skills
cd agent-skills && python3 installer.py
```

제거: `python uninstaller.py`

### 애플리케이션

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
./run_local.sh
# Docker
# docker build -t agent-skills . && docker run -p 8501:8501 ...
```

컨테이너는 `uvicorn application.server:app --host 0.0.0.0 --port 8501`로 기동하며, 헬스체크는 `GET /api/health`입니다.

## Telegram / Discord

Web UI와 별도로 봇을 실행할 수 있습니다. 동일하게 `chat.run_langgraph_agent`를 호출합니다.

```bash
cd application
python telegram_bot.py
python discord_bot.py
```

Telegram Token은 [@BotFather](https://t.me/BotFather)에서 발급 후 `installer.py` / Secrets Manager로 등록합니다.

명령 예:

```text
/start
/model Claude 4.6 Sonnet
/mcp
```

## 실행 결과

Skill 생성·실행 예시는 아래와 같습니다.

<img width="700" alt="image" src="https://github.com/user-attachments/assets/f12b214c-b7c2-407a-84b9-db9dae7fee77" />

<img width="700" alt="image" src="https://github.com/user-attachments/assets/68c680a4-833a-4ab3-85b8-204cc1976106" />

<img width="700" alt="image" src="https://github.com/user-attachments/assets/bb0f3034-dea2-4716-a53a-c2916c17308d" />

<img width="700" alt="image" src="https://github.com/user-attachments/assets/bf6e12b0-658a-4360-b30b-82e19a8a034a" />

## Reference

- [anthropics / skills](https://github.com/anthropics/skills)
- [Agent Skills](https://agentskills.io/home)
- [Notion Skills for Claude](https://www.notion.so/notiondevs/Notion-Skills-for-Claude-28da4445d27180c7af1df7d8615723d0)
- [Claude Code Skills](https://support.claude.com/en/articles/12512176-what-are-skills)
- [Agent Skills for Strands Agents SDK](https://github.com/aws-samples/sample-strands-agents-agentskills)
- [Open Agent Skills](https://skills.sh/)
- [agentic-work](https://github.com/kyopark2014/agentic-work) — 상용 배포용 FastAPI UI + AgentCore Runtime
