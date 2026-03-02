# Agent Skills

Agent는 MCP뿐 아니라 [skill](https://github.com/anthropics/skills)을 활용하여 다양한 기능을 편리하게 구현할 수 있습니다. 여기에서는 LangGraph에서 Agent skill을 활용하는 방법에 대해 설명합니다. 아래는 전체적인 architecture는 아래와 같습니다. CloudFront - ALB - EC2로 streamlit을 안전하게 제공하고, LangGraph Agent에 MCP와 Skills 기능을 구현합니다. 여기서 Skill은 skill creator를 이용해 tavily-search를 생성하고, pdf skill을 이용해 tavily-search로 검색한 결과를 pdf로 제공할 수 있습니다. MCP를 이용해 aws docuement를 조회하거나 RAG를 검색할 수 있습니다.

<img width="900" alt="image" src="https://github.com/user-attachments/assets/6687f969-9596-4018-9ed7-541b8a6ee9af" />


## Skills

[What are skills?](https://agentskills.io/what-are-skills)와 같이 skills folder에는 SKILL.md, scripts, reference, assets 등을 포함합니다. SKILL.md는 name, description과 같은 metadata를 포함하고 있어서 agent가 수행할 task를 정의할 수 있습니다. 

```text
my-skill/
├── SKILL.md          # Required: instructions + metadata
├── scripts/          # Optional: executable code
├── references/       # Optional: documentation
└── assets/           # Optional: templates, resources
```

Agent skills는 효과적으로 context를 관리하기 위하여 discovery, activation, execution의 과정을 거칩니다. 정리하면 agent가 관련된 skill의 name과 description을 읽는 discovery를 수행한 후에, SKILL.md에 포함된 instruction을 읽는 activation을 수행합니다. Agent는 instruction을 수행하는데 필요하다면 관련된 파일(referenced file)을 읽거나 포함된 코드(bundled code)를 실행합니다.


## LangGraph에서 Skill의 구현

[chat.py](./application/chat.py)의 run_langgraph_agent는 사용자의 요청(query)를 Agent를 이용해 수행합니다. 여기서는 [app.py](./application/app.py)에서 선택한 MCP 서버의 리스트에서 mcp.json을 생성하여 server_params을 추출하고, MCP tool과 built-in tool을 추출하여 agent를 생성합니다. built-in tool에는 skill을 위한 get_skill_instructions과 execute_code, write_file, read_file 들이 있습니다. 

```python
async def run_langgraph_agent(query, mcp_servers):
    mcp_json = mcp_config.load_selected_config(mcp_servers)
    server_params = langgraph_agent.load_multiple_mcp_server_parameters(mcp_json)

    client = MultiServerMCPClient(server_params)        
    tools = await client.get_tools()

    builtin_tools = langgraph_agent.get_builtin_tools()
    tools = tools + builtin_tools
        
    app = langgraph_agent.buildChatAgent(tools)
    config = {
        "recursion_limit": 50,
        "configurable": {"thread_id": user_id},
        "tools": tools,
        "system_prompt": None
    }            
    inputs = {
        "messages": [HumanMessage(content=query)]
    }
            
    result = ""
    async for stream in app.astream(inputs, config, stream_mode="messages"):
        message = stream[0]    
        for content_item in message.content:
            if content_item.get('type') == 'text':
                text_content = content_item.get('text', '')
                result += text_content
                                
    return result
```

[langgraph_agent.py](./application/langgraph_agent.py)의 get_builtin_tools은 skill과 관련된 tool 들의 리스트를 리턴합니다. 이 tool중에 get_skill_instructions은 등록된 skill에 대한 정보를 리턴합니다.

```python
def get_builtin_tools():
    """Return the list of built-in tools for the skill-aware agent."""
    return [execute_code, write_file, read_file, upload_file_to_s3, get_skill_instructions]

@tool
def get_skill_instructions(skill_name: str) -> str:
    """Load the full instructions for a specific skill by name.

    Use this when you need detailed instructions for a task that matches
    one of the available skills listed in the system prompt.

    Args:
        skill_name: The name of the skill to load (e.g. 'pdf').

    Returns:
        The full skill instructions, or an error message if not found.
    """
    instructions = skill_manager.get_skill_instructions(skill_name)
    if instructions:
        return instructions
    available = ", ".join(skill_manager.registry.keys())
    return f"Skill '{skill_name}'을 찾을 수 없습니다. 사용 가능한 skill: {available}"
```

[langgraph_agent.py](./application/langgraph_agent.py)에서는 Skill을 관리하기 위한 SkillManager를 정의합니다. SkillManager가 initiate될 때에 _discover()는 skill directory에 있는 skill 정보를 가져와서 registry에 등록합니다. 등록된 skill 정보는  available_skills_xml를 통해 prompt에서 활용합니다. 

```python
@dataclass
class Skill:
    name: str
    description: str
    instructions: str
    path: str

class SkillManager:
    """Discovers, loads and selects Agent Skills following the Anthropic spec."""

    def __init__(self, skills_dir: str = SKILLS_DIR):
        self.registry: dict[str, Skill] = {}
        self._discover()

    def _discover(self):
        """Scan skills directory and load metadata (frontmatter only)."""
        for entry in os.listdir(self.skills_dir):
            skill_md = os.path.join(self.skills_dir, entry, "SKILL.md")
            if os.path.isfile(skill_md):
                meta, instructions = self._parse_skill_md(skill_md)
                skill = Skill(
                    name=meta.get("name", entry),
                    description=meta.get("description", ""),
                    instructions=instructions,
                    path=os.path.join(self.skills_dir, entry),
                )
                self.registry[skill.name] = skill

    # ---- prompt generation (progressive disclosure) ----
    def available_skills_xml(self) -> str:
        """Generate <available_skills> XML for the system prompt (metadata only)."""
        if not self.registry:
            return ""
        lines = ["<available_skills>"]
        for s in self.registry.values():
            lines.append("  <skill>")
            lines.append(f"    <name>{s.name}</name>")
            lines.append(f"    <description>{s.description}</description>")
            lines.append("  </skill>")
        lines.append("</available_skills>")
        return "\n".join(lines)

    def get_skill_instructions(self, name: str) -> Optional[str]:
        """Return full instructions for a skill (loaded on demand)."""
        skill = self.registry.get(name)
        return skill.instructions if skill else None

skill_manager = SkillManager()
```

LangGraph의 agent는 아래와 같이 구현합니다. 여기서 build_system_prompt은 SKILL에 대한 정보인 skills_xml과 SKILL_USAGE_GUIDE를 아래와 같이 포함합니다.

```python
async def call_model(state: State, config):
    last_message = state['messages'][-1]

    tools = config.get("configurable", {}).get("tools", None)
    custom_prompt = config.get("configurable", {}).get("system_prompt", None)

    system = build_system_prompt(custom_prompt)

    chatModel = chat.get_chat()
    model = chatModel.bind_tools(tools)

    prompt = ChatPromptTemplate.from_messages([
        ("system", system),
        MessagesPlaceholder(variable_name="messages"),
    ])
    chain = prompt | model
    response = await chain.ainvoke(messages)
    return {"messages": [response], "image_url": image_url}

SKILL_USAGE_GUIDE = (
    "\n## Skill 사용 가이드\n"
    "위의 <available_skills>에 나열된 skill이 사용자의 요청과 관련될 때:\n"
    "1. 먼저 get_skill_instructions 도구로 해당 skill의 상세 지침을 로드하세요.\n"
    "2. 지침에 포함된 코드 패턴을 execute_code 도구로 실행하세요.\n"
    "3. 생성된 파일은 upload_file_to_s3로 업로드하고 URL을 사용자에게 전달하세요.\n"
    "4. skill 지침이 없는 일반 질문은 직접 답변하세요.\n"
)
def build_system_prompt(custom_prompt: Optional[str] = None) -> str:
    """Assemble the full system prompt with available skills metadata."""
    if custom_prompt:
        base = custom_prompt
    else:
        base = BASE_SYSTEM_PROMPT

    skills_xml = skill_manager.available_skills_xml()
    if skills_xml:
        return f"{base}\n\n{skills_xml}\n{SKILL_USAGE_GUIDE}"
    return base
```


### Skill의 생성

OpenClaw의 [skill-creator](./application/skills/skill-creator/SKILL.md)를 참조하여 skill을 생성할 수 있도록 하였습니다.

```text
├── SKILL.md (must required)
│   ├── YAML frontmatter metadata (required)
│   │   ├── name: (required)
│   │   └── description: (required)
│   └── Markdown instructions (required)
└── Bundled Resources (optional)
    ├── scripts/          - Executable code (Python/Bash/etc.)
    ├── references/       - Documentation intended to be loaded into context as needed
    └── assets/           - Files used in output (templates, icons, fonts, etc.)
```




## 배포하기

### EC2로 배포하기

AWS console의 EC2로 접속하여 [Launch an instance](https://us-west-2.console.aws.amazon.com/ec2/home?region=us-west-2#Instances:)를 선택합니다. [Launch instance]를 선택한 후에 적당한 Name을 입력합니다. (예: es) key pair은 "Proceed without key pair"을 선택하고 넘어갑니다. 

<img width="700" alt="ec2이름입력" src="https://github.com/user-attachments/assets/c551f4f3-186d-4256-8a7e-55b1a0a71a01" />


Instance가 준비되면 [Connet] - [EC2 Instance Connect]를 선택하여 아래처럼 접속합니다. 

<img width="700" alt="image" src="https://github.com/user-attachments/assets/e8a72859-4ac7-46af-b7ae-8546ea19e7a6" />

이후 아래와 같이 python, pip, git, boto3를 설치합니다.

```text
sudo yum install python3 python3-pip git docker -y
pip install boto3
```

Workshop의 경우에 아래 형태로 된 Credential을 복사하여 EC2 터미널에 입력합니다.

<img width="700" alt="credential" src="https://github.com/user-attachments/assets/261a24c4-8a02-46cb-892a-02fb4eec4551" />

아래와 같이 git source를 가져옵니다.

```python
git clone https://github.com/kyopark2014/agent-skills
```

아래와 같이 installer.py를 이용해 설치를 시작합니다.

```python
cd agent-skills && python3 installer.py
```

API 구현에 필요한 credential은 secret으로 관리합니다. 따라서 설치시 필요한 credential 입력이 필요한데 아래와 같은 방식을 활용하여 미리 credential을 준비합니다. 

- 일반 인터넷 검색: [Tavily Search](https://app.tavily.com/sign-in)에 접속하여 가입 후 API Key를 발급합니다. 이것은 tvly-로 시작합니다.  
- 날씨 검색: [openweathermap](https://home.openweathermap.org/api_keys)에 접속하여 API Key를 발급합니다. 이때 price plan은 "Free"를 선택합니다.

설치가 완료되면 CloudFront로 접속하여 Agent를 실행합니다.

<img width="500" alt="cloudfront_address" src="https://github.com/user-attachments/assets/7ab1a699-eefb-4b55-b214-23cbeeeb7249" />

인프라가 더이상 필요없을 때에는 uninstaller.py를 이용해 제거합니다.

```text
python uninstaller.py
```


### 배포된 Application 업데이트 하기

AWS console의 EC2로 접속하여 [Launch an instance](https://us-west-2.console.aws.amazon.com/ec2/home?region=us-west-2#Instances:)를 선택하여 아래와 같이 아래와 같이 "app-for-agent-skills"라는 이름을 가지는 instance id를 선택합니다.

[connect]를 선택한 후에 Session Manager를 선택하여 접속합니다. 

<img width="700" alt="image" src="https://github.com/user-attachments/assets/d1119cd6-08fb-4d3e-b1c2-77f2d7c1216a" />

이후 아래와 같이 업데이트한 후에 다시 브라우저에서 확인합니다.

```text
cd ~/agent-skills/ && sudo ./update.sh
```

### 실행 로그 확인

[EC2 console](https://us-west-2.console.aws.amazon.com/ec2/home?region=us-west-2#Instances:)에서 "app-for-agent-skills"라는 이름을 가지는 instance id를 선택 한 후에, EC2의 Session Manager를 이용해 접속합니다. 

먼저 아래와 같이 현재 docker container ID를 확인합니다.

```text
sudo docker ps
```

이후 아래와 같이 container ID를 이용해 로그를 확인합니다.

```text
sudo docker logs [container ID]
```

실제 실행시 결과는 아래와 같습니다.

<img width="600" src="https://github.com/user-attachments/assets/2ca72116-0077-48a0-94be-3ab15334e4dd" />

### Local에서 실행하기

AWS 환경을 잘 활용하기 위해서는 [AWS CLI를 설치](https://docs.aws.amazon.com/ko_kr/cli/v1/userguide/cli-chap-install.html)하여야 합니다. EC2에서 배포하는 경우에는 별도로 설치가 필요하지 않습니다. Local에 설치시는 아래 명령어를 참조합니다.

```text
curl "https://awscli.amazonaws.com/awscli-exe-linux-x86_64.zip" -o "awscliv2.zip" 
unzip awscliv2.zip
sudo ./aws/install
```

AWS credential을 아래와 같이 AWS CLI를 이용해 등록합니다.

```text
aws configure
```

설치하다가 발생하는 각종 문제는 [Kiro-cli](https://aws.amazon.com/ko/blogs/korea/kiro-general-availability/)를 이용해 빠르게 수정합니다. 아래와 같이 설치할 수 있지만, Windows에서는 [Kiro 설치](https://kiro.dev/downloads/)에서 다운로드 설치합니다. 실행시는 셀에서 "kiro-cli"라고 입력합니다. 

```python
curl -fsSL https://cli.kiro.dev/install | bash
```

venv로 환경을 구성하면 편리하게 패키지를 관리합니다. 아래와 같이 환경을 설정합니다.

```text
python -m venv .venv
source .venv/bin/activate
```

이후 다운로드 받은 github 폴더로 이동한 후에 아래와 같이 필요한 패키지를 추가로 설치 합니다.

```text
pip install -r requirements.txt
```

이후 아래와 같은 명령어로 streamlit을 실행합니다. 

```text
streamlit run application/app.py
```


### Gmail 등록 

GOG CLI를 설치합니다. 

```text
brew install steipete/tap/gogcli
```

먼저 json 형태의 credential을 다운받아야 합니다. 아래와 같은 작업을 수행합니다.

1. [Google Cloud Console](https://console.cloud.google.com)에서 OAuth 클라이언트를 만들기 위해 새 프로젝트 생성 (또는 기존 프로젝트 선택)을 수행합니다.

2. API 활성화를 위해 Gmail API, Google Calendar API, Google Drive API 을 설정합니다.


3. OAuth 동의 화면 구성은 "User Type: External"로 하고, 테스트 사용자에 본인 이메일 추가합니다.
   
4. OAuth 클라이언트 ID를 생성합니다. 이때 사용할 gmail을 테스트 사용자로 등록하여야 합니다.

- Application type: Desktop app

- 이름: "OpenClaw"

5. client_secret_xxx.json를 다운로드합니다.

브라우저가 있으면 아래와 같이 수행합니다.

```text
gog auth credentials /path/to/client_secret_xxx.json
```

이후 아래와 같이 메 일주소를 등록합니다. service를 지정하지 않으면 appscript, calendar, chat, classroom, contacts, docs, drive, forms, gmail, people, sheets, slides, tasks가 등록됩니다.

```text
gog auth add your-email@gmail.com
```

지정을 하면 아래와 같이 일부만 허용할 수 있습니다.

```text
gog auth add your-email@gmail.com --services gmail,calendar,drive,contacts
```

인증된 정보는 아래 명령어로 확인할 수 있습니다.

```text
gog auth list 
```

EC2와 같이 브라우저가 없는 경우에 dashboard에 접속해서 chat에서 "gmail을 등록해주세요"라고 입력후 주어진 가이드에 따라 수행합니다. "gog auth add"를 수행시 localhost로 수행되는 url을 받아서 client에서 수행하여야 하므로 dashboard의 chat에서 수행하여야 합니다.

### Web Fetch

"mcp-server-fetch-typescript"는 playwright 기반의 URL 내용 추출에 용이합니다. HTML을 Markdown/Text 변환하므로 빠르고 편리하나, 단순 HTTP GET만 수행하고 JS로 동적 생성되는 콘텐츠는 못 가져옵니다. 이를 위해 [mcp_config.py](./application/mcp_config.py)에 아래의 MCP 조건을 추가합니다.

```java
{
   "mcpServers": {
       "web_fetch": {
           "command": "npx",
           "args": ["-y", "mcp-server-fetch-typescript"]
       }
   }
}
```

또한, [Dockerfile](./Dockerfile)에도 아래 내용을 추가합니다.

```text
RUN npx -y mcp-server-fetch-typescript --version 2>/dev/null || true && \
    npx playwright install --with-deps chromium
```

MAC같은 local 환경에서 실행시 아래와 같이 수동으로 설치합니다.

```text
npx playwright install --with-deps chromium
```

Web Fetch로 아래와 같이 html을 markdown으로 변환하여 활용합니다.

<img width="700" alt="image" src="https://github.com/user-attachments/assets/56dc77ea-4cb8-4317-af00-6a21ce5be9d0" />


### Telegram과 연동

[python-telegram-bot](https://github.com/python-telegram-bot/python-telegram-bot)을 활용하여, polling 방식으로 Telegram 서버에 주기적으로 새 메시지를 확인하고, 메시지가 오면 chat.run_langgraph_agent를 호출해 Agent 응답을 생성한 뒤 다시 Telegram으로 보내줍니다. 상세한 코드는 [telegram_bot.py](./application/telegram_bot.py)을 참조합니다.

Telegram Token을 아래와 같이 생성합니다. 

1. Telegram에서 [@BotFather](https://t.me/BotFather)와 대화 시작하거나, https://t.me/BotFather 에 접속합니다.
2. /newbot 명령 입력
3. Bot 이름 입력 (예: OpenClaw Assistant)
4. 이후 BotFather가 제공하는 token을 복사합니다.

생성된 token은 아래와 같이 installer.py를 이용해 secret으로 저장합니다.

```text
python installer.py
```

<img width="766" height="27" alt="noname" src="https://github.com/user-attachments/assets/6ce85514-d637-40b1-a6ad-20932ea27a85" />


아래와 같이 python-telegram-bot을 설치합니다.

```text
pip install python-telegram-bot
```

Streamlit과 별개로 아래 명령어를 telegram bot을 준비합니다.

```text
python telegram_bot.py
```

이제 telegram에서 메시지를 보내면 동작을 확인할 수 있습니다. 또한, 아래 명령어를 telegram에서 활용할 수 있습니다. 

```text
/start - 안내 메시지
/model <모델명> - AI 모델 변경 (예: /model Claude 4.5 Sonnet)
/mcp - 현재 MCP 서버 목록 확인
```

이때의 결과는 아래와 같습니다. 

<img width="500" alt="image" src="https://github.com/user-attachments/assets/8d579ef4-f7d5-4938-a864-f5a3ae4ab41f" />



## 실행 결과

아래와 같이 SKILL 생성을 요청합니다.

<img width="700" alt="image" src="https://github.com/user-attachments/assets/f12b214c-b7c2-407a-84b9-db9dae7fee77" />

skill-creater가 아래와 같이 tavily-search라는 skill을 생성합니다.

<img width="700" alt="image" src="https://github.com/user-attachments/assets/68c680a4-833a-4ab3-85b8-204cc1976106" />

아래와 같이 skill이 생성되었습니다.

<img width="700" alt="image" src="https://github.com/user-attachments/assets/bb0f3034-dea2-4716-a53a-c2916c17308d" />

이제 아래와 같이 tavily-search를 이용해 실행할 수 있습니다.

<img width="700" alt="image" src="https://github.com/user-attachments/assets/bf6e12b0-658a-4360-b30b-82e19a8a034a" />





## Reference

[anthropics / skills](https://github.com/anthropics/skills)

[Agent Skills](https://agentskills.io/home)

[Notion Skills for Claude](https://www.notion.so/notiondevs/Notion-Skills-for-Claude-28da4445d27180c7af1df7d8615723d0)

[Claude Code Skills](https://support.claude.com/en/articles/12512176-what-are-skills)

[example skills](https://github.com/anthropics/skills)

[Agent Skills for Strands Agents SDK](https://github.com/aws-samples/sample-strands-agents-agentskills)

[Claude Code Plugins: Orchestration and Automation](https://github.com/wshobson/agents/tree/main)

[Deep Agents CLI](https://github.com/langchain-ai/deepagents/tree/master/libs/cli)

[Using skills with Deep Agents CLI](https://www.youtube.com/watch?v=Yl_mdp2IiW4)
