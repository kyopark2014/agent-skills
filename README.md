# Agent Skills

여기에서는 LangGraph에서 Agent skill을 활용하는 방법에 대해 설명합니다. Agent skill는 폴더로 제공되는 agent에 대한 instructions, scripts, resources입니다. 이를 통해 agent는 적절한 skill을 가져와서 활용할 수 있습니다. Agent는 feedback loop를 통해 질문에 대한 응답의 정확도를 높일 수 있으니 context의 크기가 비약적으로 증가하게 됩니다. Skill을 사용하게 되면 agent가 필요한 context 만을 불러와서 사용하게 됨으로써 agent가 수행하는 task가 더 많은 일을 할 수 있도록 도울 수 있습니다. [anthropics / skills](https://github.com/anthropics/skills)로 구현되었고, [agentskills](https://github.com/anthropics/skills)로 오픈소스화 되었습니다. 상세한 동작 방식은 [Agent Skills](https://agentskills.io/home)에서 확인할 수 있습니다.

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

OpenClaw의 [skill-creator](./application/skills/skill-creator/SKILL.md)를 활용하여 skill을 생성할 수 있도록 하였습니다. 

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

Skill 생성 동작은 아래 영상을 참조하세요.

https://raw.githubusercontent.com/kyopark2014/agent-skills/main/contents/skill_creator.mp4





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
