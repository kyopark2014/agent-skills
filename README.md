# Agent Skills

Agent skill는 폴더로 제공되는 agent에 대한 instructions, scripts, resources입니다. 이를 통해 agent는 적절한 skill을 가져와서 활용할 수 있습니다. Agent는 feedback loop를 통해 질문에 대한 응답의 정확도를 높일 수 있으니 context의 크기가 비약적으로 증가하게 됩니다. Skill을 사용하게 되면 agnet가 필요한 context 만을 불러와서 사용하게 됨으로써 agent가 수행하는 task가 더 많은 일을 할 수 있도록 도울 수 있습니다. [anthropics / skills](https://github.com/anthropics/skills)로 구현되었고, [agentskills](https://github.com/anthropics/skills)로 오픈소스화 되었습니다. 상세한 동작 방식은 [Agent Skills](https://agentskills.io/home)에서 확인할 수 있습니다.

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


## Reference

[anthropics / skills](https://github.com/anthropics/skills)

[agentskills](https://github.com/anthropics/skills)

[Agent Skills](https://agentskills.io/home)

[Notion Skills for Claude](https://www.notion.so/notiondevs/Notion-Skills-for-Claude-28da4445d27180c7af1df7d8615723d0)

[Claude Code Skills](https://support.claude.com/en/articles/12512176-what-are-skills)

[example skills](https://github.com/anthropics/skills)

[Agent Skills for Strands Agents SDK](https://github.com/aws-samples/sample-strands-agents-agentskills)
