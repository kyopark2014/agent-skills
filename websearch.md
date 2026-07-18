# AgentCore Web Search (websearch) 구현

agent-skills는 **Amazon Bedrock AgentCore Gateway**의 관리형 Web Search 커넥터(`gateway-websearch`)를 MCP 도구로 연결하여, Tavily·`web_fetch`·Knowledge Base와 함께 LangGraph Agent 및 Agent Skills 워크플로에서 인터넷 검색을 수행할 수 있습니다. Gateway 인프라는 [installer.py](./installer.py)로 자동 생성하거나, 이미 존재하는 Gateway를 재사용할 수 있습니다. Application 레이어([mcp_config.py](./application/mcp_config.py), [langgraph_agent.py](./application/langgraph_agent.py))는 Gateway URL을 런타임에 조회해 SigV4로 MCP에 연결합니다.

SigV4 인증 구현의 공통 패턴은 [agentcore-IAM.md](./agentcore-IAM.md)도 참조하세요.

## Infrastructure 배포 (installer.py)

[installer.py](./installer.py)는 OpenSearch·Knowledge Base·S3·CloudFront·IAM과 함께 **AgentCore Web Search Gateway**를 `us-east-1`에 프로비저닝합니다. Gateway는 애플리케이션 리전(`us-west-2`)과 분리된 **고정 리전**에 생성됩니다.

```mermaid
flowchart TB
  INS["installer.py"]

  subgraph Step3["[3/9] IAM + AgentCore (us-east-1)"]
    MEM["role-agentcore-memory-for-agent-skills-us-west-2"]
    ROLE["role-agentcore-gateway-websearch-for-agent-skills"]
    GW["Gateway\ngateway-websearch\nprotocol: MCP, authorizer: AWS_IAM"]
    TGT["Target\nwebsearch\nconnector: web-search"]
  end

  CFG["application/config.json\nagentcore_websearch_gateway_*"]
  RUN["로컬 실행\n./run_local.sh (uvicorn :8501)"]

  INS --> MEM
  INS --> ROLE
  ROLE --> GW
  GW --> TGT
  TGT --> CFG
  CFG --> RUN
```

### installer가 생성하는 websearch 리소스

| 리소스 | 이름/값 | 리전 | 설명 |
|--------|---------|------|------|
| IAM 역할 | `role-agentcore-gateway-websearch-for-agent-skills` | 글로벌 | Gateway 서비스 역할 (`bedrock-agentcore.amazonaws.com`) |
| Gateway | `gateway-websearch` | `us-east-1` | MCP 프로토콜, `AWS_IAM` 인증 |
| Gateway Target | `websearch` | `us-east-1` | 관리형 커넥터 `web-search` (`GATEWAY_IAM_ROLE`) |
| IAM 정책 | `agentcore-gateway-websearch-policy-for-agent-skills` | — | `InvokeGateway`, `InvokeWebSearch` |

Gateway IAM 역할 정책은 다음을 허용합니다.

- `bedrock-agentcore:InvokeGateway` — 계정 내 Gateway MCP 엔드포인트
- `bedrock-agentcore:InvokeWebSearch` — `arn:aws:bedrock-agentcore:us-east-1:aws:tool/web-search.v1`

로컬 실행 환경의 AWS credential(프로파일 등)이 Gateway 호출 권한을 가져야 합니다. `bedrock-agentcore:*` 정책이 있는 IAM 사용자·역할을 사용하세요.

### installer 실행 순서 (websearch 관련)

전체 installer는 9단계이며, websearch는 **3단계**에서 AgentCore Memory 역할·Knowledge Base IAM 역할과 함께 생성됩니다. 자세한 배포 순서는 [installer.md](./installer.md)를 참조하세요.

| 단계 | 작업 | websearch 관련 |
|------|------|----------------|
| 2/9 | S3 버킷 | — |
| **3/9** | IAM 역할 + AgentCore Gateway | Memory 역할, Gateway 역할 → `gateway-websearch` → `websearch` target |
| 4/9 | OpenSearch Serverless | — |
| 5/9 | Knowledge Base | — |
| 4~6 | OpenSearch, Knowledge Base, CloudFront(S3) | `build_app_environment()`로 config 반영 (`sharing_url` 포함) |

```bash
cd agent-skills
python3 installer.py
```

성공 시 로그에 Gateway ID·URL·역할 ARN이 출력되고, `application/config.json`에 아래 필드가 기록됩니다.

| config.json 필드 | 예시 | 용도 |
|------------------|------|------|
| `agentcore_websearch_gateway_name` | `gateway-websearch` | Gateway 이름 |
| `agentcore_websearch_gateway_region` | `us-east-1` | Gateway 리전 |
| `agentcore_websearch_gateway_id` | `gw-...` | Gateway 식별자 |
| `agentcore_websearch_gateway_url` | `https://...` | MCP 엔드포인트 URL |
| `agentcore_websearch_gateway_role` | `arn:aws:iam::...:role/...` | Gateway 서비스 역할 ARN |

> **참고:** Application([mcp_config.py](./application/mcp_config.py))은 `config.json`의 URL을 직접 읽지 않고, `bedrock-agentcore-control` API로 `gateway-websearch`를 조회합니다. config 필드는 운영·디버깅용 참조입니다.

### 멱등성 (재실행)

installer는 이미 존재하는 리소스를 건너뜁니다.

- `gateway-websearch` Gateway가 있으면 **재생성하지 않고** 기존 Gateway 사용
- `websearch` target이 없으면 추가 생성 후 `synchronize_gateway_targets` 호출
- IAM 역할이 있으면 기존 역할 ARN 반환

동일 AWS 계정에서 여러 프로젝트 installer를 실행해도 Gateway 이름(`gateway-websearch`)이 같으면 **하나의 Gateway를 공유**합니다.

### installer 핵심 코드

Gateway는 `bedrock-agentcore-control` 클라이언트(`us-east-1`)로 생성합니다. Target은 관리형 `web-search` 커넥터를 MCP 타깃으로 등록합니다.

```python
# installer.py — get_or_create_agentcore_websearch_gateway() 요약
response = agentcore_control_client.create_gateway(
    name="gateway-websearch",
    description="AgentCore Web Search gateway for agent-skills",
    roleArn=gateway_service_role_arn,
    protocolType="MCP",
    authorizerType="AWS_IAM",
    tags={"project": "agent-skills"},
)
gateway_id = response["gatewayId"]
wait_for_agentcore_gateway_ready(gateway_id)

response = agentcore_control_client.create_gateway_target(
    gatewayIdentifier=gateway_id,
    name="websearch",
    targetConfiguration={
        "mcp": {
            "connector": {
                "source": {"connectorId": "web-search"},
                "configurations": [{"name": "WebSearch", "parameterValues": {}}],
            }
        }
    },
    credentialProviderConfigurations=[
        {"credentialProviderType": "GATEWAY_IAM_ROLE"}
    ],
)
agentcore_control_client.synchronize_gateway_targets(
    gatewayIdentifier=gateway_id,
    targetIdList=[response["targetId"]],
)
```

### Gateway 삭제 (uninstaller.py)

[uninstaller.py](./uninstaller.py)는 Gateway 삭제를 **기본적으로 묻고, 기본 답은 no**입니다. Gateway를 유지하면 IAM 역할(`role-agentcore-gateway-websearch-for-agent-skills`)도 함께 보존됩니다.

```bash
# Gateway 삭제 확인 프롬프트 표시 (기본: no)
python3 uninstaller.py --yes

# Gateway 삭제 확인 없이 함께 삭제
python3 uninstaller.py --yes --delete-agentcore-gateway
```

삭제 순서: Knowledge Base → OpenSearch → **AgentCore Gateway**(target 먼저) → IAM 역할 → `config.json`의 `agentcore_websearch_gateway_*` 필드 제거

## Operation Architecture

```mermaid
flowchart TB
  UI["FastAPI + React (server.py)"]

  subgraph Skills["Agent Skills · skill.py"]
    SK[SkillManager / get_skill_instructions]
  end

  subgraph MCP["MCP · mcp_config.py"]
    LC[load_config websearch]
    GURL[get_agentcore_gateway_mcp_url]
    LSC[load_selected_config]
  end

  subgraph LG["LangGraph · langgraph_agent.py"]
    LMS[load_multiple_mcp_server_parameters]
    AUTH[AgentCoreSigV4Auth]
    MSC[MultiServerMCPClient]
    TN[ToolNode]
  end

  GW["AgentCore Gateway\ngateway-websearch\nus-east-1"]
  WS["Web Search connector\nbedrock-agentcore:InvokeWebSearch"]

  UI -->|mcp_servers에 websearch| LSC
  UI --> SK
  LSC --> LC
  LC --> GURL
  GURL -->|bedrock-agentcore-control| GW

  LSC --> LMS
  LMS -->|streamable_http + SigV4| AUTH
  AUTH --> MSC
  MSC -->|MCP over HTTP| GW
  GW --> WS
  MSC --> TN
  SK --> TN
```

| 구성요소 | 파일 | 설명 |
|----------|------|------|
| UI 선택 | [server.py](./application/server.py) / Web UI | Task 설정의 MCP에 `websearch` 포함 |
| MCP 설정 | [mcp_config.py](./application/mcp_config.py) | Gateway URL 조회 및 `streamable_http` + SigV4 메타데이터 반환 |
| MCP 클라이언트 | [langgraph_agent.py](./application/langgraph_agent.py) | `MultiServerMCPClient`용 connection dict 생성, SigV4 auth 주입 |
| SigV4 인증 | [agentcore_sigv4_auth.py](./application/agentcore_sigv4_auth.py) | httpx 요청에 `bedrock-agentcore` 서비스 SigV4 서명 |
| Agent 실행 | [chat.py](./application/chat.py) | `run_langgraph_agent()` → `create_agent()` 경로로 MCP·Skill 통합 |

| MCP | transport | 인증 | 비고 |
|-----|-----------|------|------|
| **websearch** | `streamable_http` | AWS SigV4 (`us-east-1`) | AgentCore 관리형 Web Search |
| tavily | stdio | Tavily API Key | Secrets Manager (`tavilyapikey`) |
| web_fetch | stdio | 없음 | URL 본문 fetch (npx) |
| knowledge base | stdio | IAM | OpenSearch Serverless RAG |

## Agent에서의 사용 흐름

Web UI Task 설정에서 MCP 목록에 `"websearch"`가 포함되면, AgentCore Runtime의 `create_agent()` 경로로 전달됩니다. Agent 생성 시 `load_selected_config()`로 MCP 설정을 병합하고, `MultiServerMCPClient`가 도구 목록을 LangGraph `ToolNode`에 등록합니다. Skill과 함께 선택하면 `websearch`로 수집한 정보를 Skill 워크플로(예: `graphify`, `myslide`)와 함께 활용할 수 있습니다.

기본 MCP 선택은 `config.json`의 `default_mcp_servers`와 `application/mcp.list`를 따릅니다.

```python
mcp_options = [
    "use-aws",
    "websearch",
    "tavily",
    "knowledge base",
    "aws_documentation",
    "trade_info",
    "web_fetch",
    "drawio",
    "text_extraction",
    "slack",
    "notion",
    # ...
]
default_selections = ["web_fetch", "slack", "notion", "korea_weather", "websearch"]
```

[telegram_bot.py](./application/telegram_bot.py)도 Agent 모드에서 동일한 MCP 설정을 사용할 수 있습니다.

## MCP 구현

[mcp_config.py](./application/mcp_config.py)에서 `websearch` 타입은 stdio 서버가 아니라 **AgentCore Gateway MCP 엔드포인트**를 가리킵니다. Gateway URL은 `bedrock-agentcore-control` API로 런타임에 조회합니다.

```python
def get_agentcore_gateway_mcp_url(gateway_name: str, gateway_region: str) -> str | None:
    client = boto3.client("bedrock-agentcore-control", region_name=gateway_region)
    try:
        response = client.list_gateways()
        for item in response.get("items", []):
            if item.get("name") != gateway_name:
                continue
            gateway_id = item["gatewayId"]
            gateway = client.get_gateway(gatewayIdentifier=gateway_id)
            return gateway["gatewayUrl"].rstrip("/")
    except Exception as e:
        logger.error(f"Error resolving AgentCore gateway URL for {gateway_name}: {e}")
    return None
```

`load_config("websearch")`는 `gateway-websearch`가 **us-east-1**에 존재할 때만 설정을 반환합니다. 없으면 빈 dict를 반환하고 해당 MCP는 건너뜁니다.

```python
elif mcp_type == "websearch":
    gateway_url = get_agentcore_gateway_mcp_url("gateway-websearch", "us-east-1")
    if not gateway_url:
        logger.info(
            "AgentCore gateway websearch MCP skipped: "
            "gateway-websearch not found in us-east-1."
        )
        return {}
    return {
        "mcpServers": {
            "gateway-websearch": {
                "type": "streamable_http",
                "url": gateway_url,
                "auth_type": "aws_sigv4",
                "auth_region": "us-east-1",
                "auth_service": "bedrock-agentcore",
            }
        }
    }
```

| 필드 | 값 | 의미 |
|------|-----|------|
| `type` | `streamable_http` | MCP Streamable HTTP transport |
| `url` | Gateway MCP URL | `get_gateway()`의 `gatewayUrl` |
| `auth_type` | `aws_sigv4` | IAM 자격 증명으로 요청 서명 |
| `auth_region` | `us-east-1` | Gateway 리전 (고정) |
| `auth_service` | `bedrock-agentcore` | SigV4 서비스 이름 |

## LangGraph MCP 클라이언트 연동

[langgraph_agent.py](./application/langgraph_agent.py)의 `load_multiple_mcp_server_parameters()`는 `mcp_config` 출력을 [langchain-mcp-adapters](https://reference.langchain.com/python/langchain-mcp-adapters/client/MultiServerMCPClient) 형식으로 변환합니다. `streamable_http` 타입이면서 `auth_type == "aws_sigv4"`인 경우 SigV4 auth 객체를 connection에 붙입니다.

```python
def load_multiple_mcp_server_parameters(mcp_json: dict):
    mcpServers = mcp_json.get("mcpServers")
    server_info = {}
    if mcpServers is not None:
        for server_name, cfg in mcpServers.items():
            if cfg.get("type") in ("streamable_http", "http"):
                connection = {
                    "transport": "streamable_http",
                    "url": cfg.get("url"),
                    "headers": cfg.get("headers", {})
                }
                if cfg.get("auth_type") == "aws_sigv4":
                    connection["auth"] = agentcore_sigv4_auth.AgentCoreSigV4Auth(
                        region=cfg.get("auth_region", "us-east-1"),
                        service=cfg.get("auth_service", "bedrock-agentcore"),
                    )
                server_info[server_name] = connection
            else:
                server_info[server_name] = {
                    "transport": "stdio",
                    "command": cfg.get("command", ""),
                    "args": cfg.get("args", []),
                    "env": cfg.get("env", {}),
                }
    return server_info
```

[chat.py](./application/chat.py)의 `create_agent()`는 이 connection dict로 `MultiServerMCPClient`를 만들고 `get_tools()`로 LangGraph 도구를 등록합니다.

```python
mcp_json = mcp_config.load_selected_config(mcp_servers)
server_params = langgraph_agent.load_multiple_mcp_server_parameters(mcp_json)
client = MultiServerMCPClient(server_params)
mcp_tools = await client.get_tools()
tools.append(mcp_tools)
```

## SigV4 인증

websearch MCP는 API Key나 Bearer 토큰 대신 **실행 환경의 IAM identity로 HTTP 요청 자체를 SigV4 서명**합니다. [agentcore_sigv4_auth.py](./application/agentcore_sigv4_auth.py)는 httpx `Auth` 구현체이며, boto3 세션의 현재 IAM 자격 증명으로 Gateway MCP URL에 대한 요청을 `bedrock-agentcore` 서비스 SigV4로 서명합니다. Gateway IAM 정책·역할 설정은 [agentcore-IAM.md](./agentcore-IAM.md)를 참조하세요.

### 인증 흐름

```mermaid
sequenceDiagram
    participant Config as mcp_config
    participant Loader as load_multiple_mcp_server_parameters
    participant Client as MultiServerMCPClient
    participant Auth as AgentCoreSigV4Auth
    participant Boto3 as boto3.Session
    participant Gateway as AgentCore Gateway (HTTP)

    Config->>Loader: auth_type=aws_sigv4 설정
    Loader->>Client: connection["auth"] = AgentCoreSigV4Auth(...)
    Client->>Gateway: httpx HTTP 요청
    Auth->>Boto3: get_credentials()
    Boto3-->>Auth: access_key, secret_key, token
    Auth->>Auth: SigV4Auth로 Authorization 헤더 생성
    Auth->>Gateway: 서명된 요청 전송
```

| 단계 | 구성요소 | 역할 |
|------|----------|------|
| 1. 설정 | [mcp_config.py](./application/mcp_config.py) | `auth_type`, `auth_region`, `auth_service` 메타데이터 반환 |
| 2. 연결 | [langgraph_agent.py](./application/langgraph_agent.py) | `auth_type == "aws_sigv4"`일 때 `AgentCoreSigV4Auth` 인스턴스를 connection에 연결 |
| 3. 클라이언트 | `MultiServerMCPClient` | langchain-mcp-adapters가 내부 httpx 클라이언트에 `auth` 객체 연결 |
| 4. 서명 | [agentcore_sigv4_auth.py](./application/agentcore_sigv4_auth.py) | 요청마다 boto3 credential으로 SigV4 서명 후 헤더 주입 |
| 5. 검증 | AgentCore Gateway | IAM으로 `Authorization` 헤더 검증 (`authorizerType: AWS_IAM`) |

### 1단계: connection에 auth 객체 연결

[langgraph_agent.py](./application/langgraph_agent.py)의 `load_multiple_mcp_server_parameters()`는 MCP 서버가 `streamable_http`/`http` 타입이고 `auth_type: "aws_sigv4"`인 경우에만 `connection` 딕셔너리에 `auth` 키를 추가합니다.

```python
if cfg.get("auth_type") == "aws_sigv4":
    connection["auth"] = agentcore_sigv4_auth.AgentCoreSigV4Auth(
        region=cfg.get("auth_region", "us-east-1"),
        service=cfg.get("auth_service", "bedrock-agentcore"),
    )
```

- `region`: SigV4 서명에 쓰는 AWS 리전 (websearch는 `us-east-1` 고정)
- `service`: SigV4 서비스 이름 (`bedrock-agentcore`)

이 단계는 credential을 직접 다루지 않고, **“이 MCP 서버는 IAM SigV4로 인증한다”**는 플래그를 connection 객체에 달아주는 설정입니다.

### 2단계: AWS Credential 획득 및 SigV4 서명

MCP 클라이언트가 Gateway URL로 HTTP 요청을 보낼 때마다 httpx가 `AgentCoreSigV4Auth.auth_flow()`를 호출합니다.

```python
class AgentCoreSigV4Auth(httpx.Auth):
    def auth_flow(self, request: httpx.Request):
        credentials = boto3.Session().get_credentials().get_frozen_credentials()
        headers = dict(request.headers)
        body = request.content

        aws_request = AWSRequest(
            method=request.method,
            url=str(request.url),
            data=body,
            headers=headers,
        )
        SigV4Auth(credentials, self.service, self.region).add_auth(aws_request)
        prepared = aws_request.prepare()

        for key, value in prepared.headers.items():
            request.headers[key] = value

        yield request
```

#### Credential 소스 (boto3 기본 chain)

코드에 access key를 하드코딩하지 않습니다. `boto3.Session().get_credentials()`가 실행 환경에서 자격 증명을 자동으로 찾습니다.

| 우선순위 | 소스 |
|---------|------|
| 1 | 환경 변수 (`AWS_ACCESS_KEY_ID`, `AWS_SECRET_ACCESS_KEY`, `AWS_SESSION_TOKEN`) |
| 2 | `~/.aws/credentials` |
| 3 | EC2/ECS/Lambda IAM Role (instance metadata 등) |
| 4 | `AWS_PROFILE`로 지정한 프로파일 |

`.get_frozen_credentials()`는 요청 시점의 credential을 불변 스냅샷으로 고정합니다. STS 임시 credential인 경우 `token`(session token)도 포함됩니다.

#### SigV4 서명 과정

1. httpx가 Gateway로 보낼 HTTP 요청(method, URL, body, headers)을 `botocore.awsrequest.AWSRequest`로 감쌉니다.
2. `botocore.auth.SigV4Auth(credentials, "bedrock-agentcore", "us-east-1").add_auth()`가 AWS Signature Version 4 서명을 계산합니다.
3. 서명 결과가 `Authorization`, `X-Amz-Date`, `X-Amz-Security-Token`(임시 credential인 경우) 등의 헤더로 요청에 추가됩니다.
4. 서명된 요청이 AgentCore Gateway(`authorizerType: AWS_IAM`)로 전송되고, Gateway가 IAM으로 서명을 검증합니다.

### 런타임 요구사항

로컬 개발 환경에서 **실행 환경의 AWS credential**(프로파일 등)이 Gateway 호출 권한을 가져야 합니다. 최소 `bedrock-agentcore:InvokeGateway`, `bedrock-agentcore:InvokeWebSearch`가 허용된 IAM 사용자·역할이어야 합니다.

## 사전 준비

### 권장: installer로 Gateway 생성

```bash
cd agent-skills
python3 installer.py
```

installer 실행 주체(IAM 사용자·역할)에는 최소 다음 권한이 필요합니다.

| API | 용도 |
|-----|------|
| `bedrock-agentcore-control:CreateGateway`, `CreateGatewayTarget`, `ListGateways`, `GetGateway`, `SynchronizeGatewayTargets` | Gateway·Target 생성 (installer, `us-east-1`) |
| `iam:CreateRole`, `PutRolePolicy`, `AttachRolePolicy` | Gateway 서비스 역할 |
| `bedrock-agentcore:InvokeGateway`, `bedrock-agentcore:InvokeWebSearch` | Agent 런타임에서 MCP 호출 |
| `bedrock-agentcore-control:ListGateways`, `GetGateway` | URL 조회 ([mcp_config.py](./application/mcp_config.py)) |

### 대안: 기존 Gateway 재사용

다른 프로젝트 installer로 이미 `gateway-websearch`(`us-east-1`)가 있으면 agent-skills installer를 실행하지 않아도 됩니다. Application은 API로 Gateway URL을 조회하므로 config에 websearch 필드가 없어도 동작합니다.

수동 생성이 필요한 경우 AWS 콘솔 또는 `bedrock-agentcore-control` API로 Gateway + `web-search` 커넥터 target을 `us-east-1`에 구성합니다.

`aws configure`로 credential을 설정한 뒤 Agent를 실행합니다.

## Tavily / web_fetch 와의 차이

| 항목 | websearch (AgentCore) | tavily | web_fetch |
|------|------------------------|--------|-----------|
| 백엔드 | Bedrock AgentCore Web Search | Tavily Search API | URL 직접 fetch |
| API Key | 불필요 (IAM) | `TAVILY_API_KEY` 필요 | 불필요 |
| Gateway | `us-east-1` 필수 | 없음 | 없음 |
| transport | streamable HTTP + SigV4 | stdio | stdio |

Skill 생성·리서치·슬라이드 작성처럼 **최신 웹 검색**이 필요할 때 `websearch`를 기본 MCP로 두고, 특정 URL 본문이 필요하면 `web_fetch`, Tavily 전용 검색이 필요하면 `tavily`를 함께 선택할 수 있습니다.

## 실행

[README.md](./README.md)의 설치·실행 절차와 동일합니다. Gateway가 없으면 `python3 installer.py`로 생성하거나, 로그에 `gateway-websearch not found in us-east-1`이 출력되며 websearch MCP만 제외됩니다.

```bash
./run_local.sh
# 또는
uvicorn application.server:app --host 0.0.0.0 --port 8501
```

**Agent** 또는 **Agent (Chat)** 모드에서 sidebar **MCP Config** → **websearch** 체크 후, 예를 들어 아래와 같이 질의합니다.

```text
최근 AWS AgentCore 발표 내용을 websearch로 조사하고, myslide 스킬로 요약 슬라이드를 만들어 주세요.
```

## 관련 파일

| 파일 | 역할 |
|------|------|
| [installer.py](./installer.py) | Gateway IAM 역할·`gateway-websearch`·`websearch` target 생성, `config.json` 기록 |
| [uninstaller.py](./uninstaller.py) | Gateway·target 삭제 (`--delete-agentcore-gateway`) |
| [installer.md](./installer.md) | 전체 인프라 배포 순서·리소스 명세 |
| [agentcore-IAM.md](./agentcore-IAM.md) | SigV4 인증 및 Gateway IAM 정책 상세 |
| [application/mcp_config.py](./application/mcp_config.py) | websearch MCP 정의, Gateway URL 조회 |
| [application/langgraph_agent.py](./application/langgraph_agent.py) | SigV4 connection 변환 |
| [application/agentcore_sigv4_auth.py](./application/agentcore_sigv4_auth.py) | httpx SigV4 auth |
| [application/server.py](./application/server.py) | FastAPI Web UI 진입점 |
| [application/web/](./application/web/) | React SPA |
| [application/chat.py](./application/chat.py) | LangGraph Agent 생성 및 MCP 도구 등록 |

[Announcing Web Search on Amazon Bedrock AgentCore: Ground your AI agents in current, accurate web knowledge](https://aws.amazon.com/ko/blogs/aws/announcing-web-search-on-amazon-bedrock-agentcore-ground-your-ai-agents-in-current-accurate-web-knowledge/)
