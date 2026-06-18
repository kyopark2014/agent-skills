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

## Web Search Gateway MCP 설정

Web Search는 AgentCore가 제공하는 관리형 connector(`web-search`)입니다. 별도 검색 API나 API 키 없이 Gateway를 통해 `WebSearch` MCP tool을 사용할 수 있습니다. Gateway는 `us-east-1`에 생성하며, 애플리케이션은 IAM SigV4로 Gateway MCP endpoint에 접속합니다.

### 1. Gateway IAM 역할 생성

[installer.py](./installer.py)에서 Gateway 서비스 역할을 생성합니다. 역할 이름은 `role-agentcore-gateway-websearch-for-{project_name}`이며, AgentCore 서비스가 assume할 수 있도록 trust policy를 설정합니다. 인라인 정책에는 Gateway 호출(`InvokeGateway`)과 Web Search tool 호출(`InvokeWebSearch`) 권한이 포함됩니다.

```python
def create_agentcore_websearch_gateway_role() -> str:
    role_name = f"role-agentcore-gateway-websearch-for-{project_name}"

    assume_role_policy = {
        "Version": "2012-10-17",
        "Statement": [
            {
                "Sid": "GatewayAssumeRolePolicy",
                "Effect": "Allow",
                "Principal": {"Service": "bedrock-agentcore.amazonaws.com"},
                "Action": "sts:AssumeRole",
                "Condition": {
                    "StringEquals": {"aws:SourceAccount": account_id},
                    "ArnLike": {
                        "aws:SourceArn": (
                            f"arn:aws:bedrock-agentcore:{AGENTCORE_GATEWAY_REGION}:"
                            f"{account_id}:gateway/{AGENTCORE_WEBSEARCH_GATEWAY_NAME}-*"
                        )
                    },
                },
            }
        ],
    }
    role_arn = create_iam_role(role_name, assume_role_policy)

    gateway_policy = {
        "Version": "2012-10-17",
        "Statement": [
            {
                "Sid": "InvokeGateway",
                "Effect": "Allow",
                "Action": ["bedrock-agentcore:InvokeGateway"],
                "Resource": [
                    (
                        f"arn:aws:bedrock-agentcore:{AGENTCORE_GATEWAY_REGION}:"
                        f"{account_id}:gateway/*"
                    )
                ],
            },
            {
                "Sid": "InvokeWebSearchTool",
                "Effect": "Allow",
                "Action": ["bedrock-agentcore:InvokeWebSearch"],
                "Resource": [
                    (
                        f"arn:aws:bedrock-agentcore:{AGENTCORE_GATEWAY_REGION}:"
                        "aws:tool/web-search.v1"
                    )
                ],
            },
        ],
    }
    attach_inline_policy(
        role_name,
        f"agentcore-gateway-websearch-policy-for-{project_name}",
        gateway_policy,
    )
    return role_arn
```

### 2. Gateway 및 Web Search target 생성

Gateway 이름은 `gateway-websearch`, target 이름은 `websearch`입니다. Gateway는 `protocolType="MCP"`, `authorizerType="AWS_IAM"`으로 생성하고, target에는 managed connector `web-search`를 등록합니다. connector target 생성 시 `configurations`와 `credentialProviderConfigurations`가 필요합니다.

```python
def get_or_create_agentcore_websearch_gateway(gateway_service_role_arn: str) -> Dict[str, str]:
    gateway_id = None
    for gateway in _list_all_agentcore_gateways():
        if gateway.get("name") == AGENTCORE_WEBSEARCH_GATEWAY_NAME:
            gateway_id = gateway["gatewayId"]
            break

    if not gateway_id:
        response = agentcore_control_client.create_gateway(
            name=AGENTCORE_WEBSEARCH_GATEWAY_NAME,
            description=f"AgentCore Web Search gateway for {project_name}",
            roleArn=gateway_service_role_arn,
            protocolType="MCP",
            authorizerType="AWS_IAM",
            tags={"project": project_name},
        )
        gateway_id = response["gatewayId"]
        wait_for_agentcore_gateway_ready(gateway_id)

    gateway = wait_for_agentcore_gateway_ready(gateway_id)
    target_id = _ensure_websearch_gateway_target(gateway_id)
    gateway_url = gateway.get("gatewayUrl", "").rstrip("/")

    return {
        "gateway_id": gateway_id,
        "gateway_name": AGENTCORE_WEBSEARCH_GATEWAY_NAME,
        "gateway_region": AGENTCORE_GATEWAY_REGION,
        "gateway_url": gateway_url,
        "gateway_arn": gateway.get("gatewayArn", ""),
        "gateway_service_role_arn": gateway_service_role_arn,
        "target_id": target_id,
    }


def _ensure_websearch_gateway_target(gateway_id: str) -> str:
    response = agentcore_control_client.create_gateway_target(
        gatewayIdentifier=gateway_id,
        name=AGENTCORE_WEBSEARCH_TARGET_NAME,
        description=f"Managed Web Search connector for {project_name}",
        targetConfiguration={
            "mcp": {
                "connector": {
                    "source": {
                        "connectorId": "web-search",
                    },
                    "configurations": [
                        {
                            "name": "WebSearch",
                            "parameterValues": {},
                        }
                    ],
                }
            }
        },
        credentialProviderConfigurations=[
            {"credentialProviderType": "GATEWAY_IAM_ROLE"}
        ],
    )
    target_id = response["targetId"]

    agentcore_control_client.synchronize_gateway_targets(
        gatewayIdentifier=gateway_id,
        targetIdList=[target_id],
    )
    return target_id
```

installer 실행 시 위 Gateway 정보는 `application/config.json`에 저장됩니다.

```json
{
  "agentcore_websearch_gateway_name": "gateway-websearch",
  "agentcore_websearch_gateway_region": "us-east-1",
  "agentcore_websearch_gateway_id": "<gateway-id>",
  "agentcore_websearch_gateway_url": "https://<gateway-id>.gateway.bedrock-agentcore.us-east-1.amazonaws.com/mcp",
  "agentcore_websearch_gateway_role": "arn:aws:iam::<account-id>:role/role-agentcore-gateway-websearch-for-agent-skills"
}
```

### 3. 애플리케이션 MCP 설정

[mcp_config.py](./application/mcp_config.py)에서 `websearch` 타입을 선택하면 `us-east-1`의 `gateway-websearch` URL을 조회하고, IAM SigV4 인증으로 MCP 서버를 등록합니다. Gateway가 없으면 websearch MCP는 건너뜁니다.

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

[app.py](./application/app.py)에서 `websearch`를 MCP 선택 목록에 포함하면 위 설정이 LangGraph agent에 전달됩니다. agent는 `tools/list`로 `WebSearch` tool을 발견하고, `tools/call`로 웹 검색을 수행합니다.

### 4. 삭제

[uninstaller.py](./uninstaller.py)는 `gateway-websearch`와 연결된 target을 삭제합니다. 기본적으로 별도 확인 프롬프트를 표시하며, `--delete-agentcore-gateway` 옵션으로 확인 없이 삭제할 수 있습니다. Gateway 삭제를 거부하면 IAM 역할 `role-agentcore-gateway-websearch-for-{project_name}`은 유지됩니다.
