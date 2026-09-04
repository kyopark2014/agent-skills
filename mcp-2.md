# MCP Python SDK 2.x 업그레이드

agent-skills의 커스텀 MCP 서버와 LangGraph 클라이언트를 **MCP Python SDK 2.1.1** 기준으로 맞춘 내용입니다.

관련 스펙·가이드:

- [MCP Python SDK v2.1.1](https://github.com/modelcontextprotocol/python-sdk/releases/tag/v2.1.1)
- [What's new in v2](https://py.sdk.modelcontextprotocol.io/whats-new/)
- [Migration Guide: v1 → v2](https://py.sdk.modelcontextprotocol.io/migration/)
- [LangChain MCP (`langchain.mcp`)](https://docs.langchain.com/oss/python/langchain/mcp)

## 좋아지는 점

1. **최신 MCP 스펙 대응**  
   2026-07-28 프로토콜(세션/핸드셰이크 단순화, `server/discover` 등)을 지원하면서, 예전 클라이언트와도 같은 서버로 통신할 수 있습니다.

2. **서버 API가 더 명확**  
   `FastMCP` → `MCPServer`로 이름이 역할에 맞게 바뀌고, host/port 같은 transport 설정은 생성자가 아니라 `run()` 쪽으로 분리됩니다. “서버 정의”와 “어떻게 띄울지”가 나뉩니다.

3. **클라이언트/HTTP 스택 정리**  
   내부 HTTP가 `httpx` → `httpx2`로 바뀌어 SSE·스트림 HTTP와 맞습니다. SigV4 같은 커스텀 auth도 이 기준에 맞춰야 합니다.

4. **LangChain과의 정렬**  
   `langchain-mcp-adapters`(mcp&lt;2 고정) 대신 `langchain.mcp.MCPAdapter`를 쓰게 되어, LangChain 1.4+ / MCP 2.x 방향과 맞습니다. adapters 패키지는 더 이상 2.x를 따라가지 않습니다.

5. **의존성·보안 바닥 상향**  
   pydantic, anyio, sse-starlette 등 최소 버전이 올라가고, OpenTelemetry 연동 등이 SDK에 들어갑니다.

## 바뀐 항목 (이 레포 기준)

| 구분 | 이전 | 이후 |
|------|------|------|
| Python MCP 서버 | `from mcp.server.fastmcp import FastMCP` | `from mcp.server.mcpserver import MCPServer` |
| Agent MCP 클라이언트 | `langchain_mcp_adapters.MultiServerMCPClient` + `get_tools()` | `langchain.mcp.MCPAdapter` + `list_tools()` |
| SigV4 auth | `httpx.Auth` | `httpx2.Auth` |
| Gateway connection | `transport: "streamable_http"` | MCPConfig용 `transport: "http"` (설정 메타 `type: streamable_http`는 그대로) |
| 의존성 | `mcp>=1.28`, `langchain-mcp-adapters` | `mcp>=2.1.1`, `langchain[mcp]>=1.4.0`, `httpx2`, FastAPI `>=0.134` (Starlette 1.x 호환) |

코드 쪽에서는 [mcp_config.py](./application/mcp_config.py)에 연결된 커스텀 서버(`mcp_server_*.py`, outlook 등)의 import/클래스명, [chat.py](./application/chat.py)의 도구 로딩, [langgraph_agent.py](./application/langgraph_agent.py)의 connection 변환, [agentcore_sigv4_auth.py](./application/agentcore_sigv4_auth.py), [requirements.txt](./requirements.txt) / [Dockerfile](./Dockerfile), 관련 문서([README.md](./README.md), [websearch.md](./websearch.md), [agentcore-IAM.md](./agentcore-IAM.md))가 위 표에 맞게 바뀌었습니다.

## 체감에 대한 참고

앱 기능(검색·RAG·memory 등)의 UX가 크게 바뀌는 업그레이드라기보다, **프로토콜/SDK 정합 + 앞으로의 LangChain·Gateway 연동을 유지하기 위한 기반 작업**에 가깝습니다. 도구 이름·stdio/`websearch` 흐름은 그대로 두고, 클라이언트만 서버별로 `MCPAdapter`를 열어 `{server}_` 접두사가 붙지 않게 유지했습니다.

## 주요 코드 패턴

### MCP 서버 (v2)

```python
from mcp.server.mcpserver import MCPServer

mcp = MCPServer(
    name="tavily_tools",
)

@mcp.tool()
async def tavily_web_search(query: str) -> str:
    ...

if __name__ == "__main__":
    mcp.run()  # 기본 stdio
```

### Agent 클라이언트 (v2)

```python
from langchain.mcp import MCPAdapter

server_params = langgraph_agent.load_multiple_mcp_server_parameters(mcp_json)

for server_name, params in server_params.items():
    # 서버를 하나씩 열어 도구 이름에 {server}_ 접두사가 붙지 않게 함
    async with MCPAdapter({"mcpServers": {server_name: params}}) as adapter:
        mcp_tools = await adapter.list_tools()
    tools.extend(mcp_tools)
```

### SigV4 (httpx2)

```python
import httpx2

class AgentCoreSigV4Auth(httpx2.Auth):
    def auth_flow(self, request: httpx2.Request):
        ...
        yield request
```

## 의존성 충돌 주의

MCP 2.x → `sse-starlette>=3` → **Starlette 1.x**가 따라옵니다. FastAPI 0.116 이하는 `APIRouter` 생성 시 `on_startup`을 넘겨 Starlette 1.x와 충돌합니다.

```text
TypeError: Router.__init__() got an unexpected keyword argument 'on_startup'
```

이 프로젝트는 `fastapi>=0.134.0`으로 맞춰 두었습니다. 또한 `langchain.mcp`는 `langchain[mcp]>=1.4.0`과 `fastmcp` 4.x가 필요합니다.

```bash
pip install -r requirements.txt
# 또는
pip install "langchain[mcp]>=1.4.0" "mcp>=2.1.1" "fastapi>=0.134.0" httpx2
```

## 관련 파일

| 파일 | 역할 |
|------|------|
| [application/mcp_config.py](./application/mcp_config.py) | MCP 서버 목록·stdio/Gateway 설정 |
| [application/mcp_server_*.py](./application/) | 커스텀 `MCPServer` 구현 |
| [application/chat.py](./application/chat.py) | `MCPAdapter`로 도구 로드 |
| [application/langgraph_agent.py](./application/langgraph_agent.py) | MCPConfig connection 변환 |
| [application/agentcore_sigv4_auth.py](./application/agentcore_sigv4_auth.py) | Gateway SigV4 (`httpx2`) |
| [requirements.txt](./requirements.txt) / [Dockerfile](./Dockerfile) | `mcp>=2.1.1`, `langchain[mcp]` |
| [websearch.md](./websearch.md) | websearch Gateway + SigV4 흐름 |
