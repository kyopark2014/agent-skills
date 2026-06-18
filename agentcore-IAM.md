# AgentCore에서 IAM 인증하기

[agentcore_sigv4_auth.py](./application/agentcore_sigv4_auth.py)와 같이 AgentCoreSigV4Auth을 아래와 같이 정의합니다.

```python
import httpx
import boto3
from botocore.auth import SigV4Auth
from botocore.awsrequest import AWSRequest


class AgentCoreSigV4Auth(httpx.Auth):
    def __init__(self, region: str, service: str = "bedrock-agentcore"):
        self.region = region
        self.service = service

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

LangGraph에서 MCP 정보를 아래와 같이 가져올때에 aws_sigv4에 대한 정보를 가져옵니다. [langgraph_agent.py](./application/langgraph_agent.py)을 참조합니다.

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
                    "env": cfg.get("env", {})
                }
    return server_info
```

[mcp_config.py](./application/mcp_config.py)에서 아래와 같이 IAM 인증을 사용하는 AgentCore Gateway에 대한 auth type을 지정합니다.

```python
if mcp_type == "websearch":
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

def get_agentcore_gateway_mcp_url(gateway_name: str, gateway_region: str) -> str | None:
    client = boto3.client("bedrock-agentcore-control", region_name=gateway_region)
    try:
        response = client.list_gateways()
        for item in response.get("items", []):
            if item.get("name") != gateway_name:
                continue

            gateway_id = item["gatewayId"]
            gateway = client.get_gateway(gatewayIdentifier=gateway_id)
            gateway_url = gateway["gatewayUrl"].rstrip("/")
            return f"{gateway_url}/mcp"
    except Exception as e:
        logger.error(f"Error resolving AgentCore gateway URL for {gateway_name}: {e}")

    return None
```

