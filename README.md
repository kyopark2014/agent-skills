# AWS에서 OpenClaw 안전하게 활용하기

전체적인 Architecture는 아래와 같습니다. OpenClaw는 EC2에 설치합니다. HTTPS로 접속하기 위해 CloudFront - ALB - EC2의 구조를 활용합니다. 여기서 EC2는 VPC의 private subnet에 위치하게 되고, 외부로 나갈때에는 NAT를 이용합니다. OpenClaw는 Dashboard의 chat을 이용해 접속하거나 Telegram을 이용해 활용할 수 있습니다. Telegram은 OpenClaw의 gateway를 이용해 연결됩니다. OpenClaw는 MCP, Skill을 활용할 수 있습니다.

<img width="1100" alt="image" src="https://github.com/user-attachments/assets/96efea94-2640-4d01-85e1-ac5c90557695" />


## AWS에서 OpenClaw 활용하기

### 설치하기

설치에 필요한 파일들은 clone 합니다.

```text
git clone https://github.com/kyopark2014/openclaw
```

아래와 같이 [installer.py](./installer.py)로 설치를 합니다.

```text
python installer.py
```

아래와 설치를 시작합니다.

<img width="500" alt="image" src="https://github.com/user-attachments/assets/d172dd55-df27-4660-b263-6e3f43a99910" />

OpenClaw의 Gateway를 이용하면 Telegram을 이용해 접속할 수 있습니다. 이를 위해 아래와 같이 Telegram의 token을 준비하여 입력합니다. Telegram Token은 아래와 같이 얻을 수 있습니다.

1. Telegram에서 [@BotFather](https://t.me/BotFather)와 대화 시작하거나, https://t.me/BotFather 에 접속합니다.
2. /newbot 명령 입력
3. Bot 이름 입력 (예: OpenClaw Assistant)
4. 이후 BotFather가 제공하는 token을 복사합니다.
   
<img width="600" src="https://github.com/user-attachments/assets/a84125fe-1d2f-4e53-b7a8-d5143bbd78b9" />

설치가 완료되면 아래와 같은 화면이 보입니다.

<img width="500" src="https://github.com/user-attachments/assets/2eb8c672-75b5-4e64-b397-8238d1d607c6" />

CloudFront의 주소로 접속한 후에 [Overview] - [Gateway Access] - [Gateway Token]에서 아래와 같이 gateway token을 입력합니다.

<img width="600" src="https://github.com/user-attachments/assets/20385a1e-0695-4a35-bbd3-2fd4dbde5998" />

Device를 등록하기 위해 SSM으로 접속합니다. 이때 아래와 같이 ec2-user로 전환합니다.

```text
sudo su - ec2-user
```

openclaw의 device 리스트를 확인합니다.

```text
openclaw devices list
```

이때의 결과는 아래와 같습니다.

<img width="900" alt="noname" src="https://github.com/user-attachments/assets/f95424a7-0221-4513-9523-5183b2221288" />

아래와 같이 접속한 디바이스에 권한을 부여합니다.

```text
openclaw devices approve [Device ID]
```

이때 request의 device id를 활용합니다.

<img width="600" alt="noname" src="https://github.com/user-attachments/assets/a2c2945c-339a-4064-b721-e46110f1fe45" />


이후 [Chat]에서 아래와 같이 OpenClaw의 Agent와 대화를 할 수 있습니다.

<img width="800" alt="image" src="https://github.com/user-attachments/assets/84f62e92-0da9-4c1d-9e3a-3838532672c0" />




## 개인 PC에 직접 설치하기

### 설치하기

[Quick setup (CLI)](https://docs.openclaw.ai/start/getting-started)에 따라 아래와 같이 OpenCrew를 설치합니다.

```text
curl -fsSL https://openclaw.ai/install.sh | bash
```

### AWS 환경에서 OpenClaw에서 Bedrock 모델 설정

~/.openclaw/openclaw.json에서 두 가지 설정을 합니다.

아래와 같이 모델을 등록할 수 있습니다. (models.providers)

```java
"models": {
  "providers": {
    "amazon-bedrock": {
      "baseUrl": "https://bedrock-runtime.{region}.amazonaws.com",
      "auth": "aws-sdk",
      "api": "bedrock-converse-stream",
      "models": [
        {
          "id": "global.anthropic.claude-sonnet-4-6",
          "name": "Claude Sonnet 4.6",
          "reasoning": true,
          "input": ["text", "image"],
          "contextWindow": 200000,
          "maxTokens": 8192
        }
      ]
    }
  }
}
```

기본 모델 지정은 아래와 같이 지정할 수 있습니다. (agents.defaults.model)

```java
"agents": {
  "defaults": {
    "model": {
      "primary": "amazon-bedrock/global.anthropic.claude-opus-4-6-v1",
      "fallbacks": ["amazon-bedrock/global.anthropic.claude-sonnet-4-5-20250929-v1:0"]
    }
  }
}
```

- auth: "aws-sdk" → AWS credentials 자동 사용 (환경변수, ~/.aws/credentials, IAM role 등)
- 모델 ID는 {provider}/{model-id} 형식
- Cross-region inference 쓰려면 모델 ID에 global. prefix
- reasoning: true 설정하면 extended thinking 지원


## 유용한 기능들

### Gateway restart

EC2에서 아래 명령어로 gateway를 재시작할 수 있습니다.

```text
sudo systemctl restart openclaw-gateway.service
```

상태 확인은 아래와 같이 수행합니다.

```text
sudo systemctl status openclaw-gateway.service
```

로그 확인이 필요할때에는 아래와 같이 수행합니다.

```text
sudo journalctl -u openclaw-gateway.service -f
```

### SSM으로 EC2에 접속하기

EC2는 보안을 위해 VPC의 private subnet에 있습니다. 따라서 IP로 접속할 수 없으므로 SSM으로 접속한 후에 ec2-user로 변경합니다.

```text
sudo su - ec2-user
```

OpenClew의 버전이나 gateway에 대한 정보는 아래와 같이 확인할 수 있습니다. 

```text
openclaw --version
openclaw config get gateway
```

채널 상태는 아래와 같이 확인합니다.

```text
openclaw channels status
```

Health 체크를 아래와 같이 수행할 수 있습니다.

```text
openclaw health --verbose
```

### Skill

아래와 같이 skill을 추가할 수 있습니다.

#### 방법 1: Workspace에 스킬 추가

```text
# 1. 스킬 디렉토리 생성
mkdir -p ~/.openclaw/workspace/skills/my-skill

# 2. SKILL.md 작성
cat > ~/.openclaw/workspace/skills/my-skill/SKILL.md << 'EOF'
---
name: my_skill
description: 내가 만든 스킬 설명
---

# My Skill

스킬 사용 방법과 지시사항을 여기에 작성...
EOF

# 3. 자동 인식 (watch 켜져 있으면 바로 반영)
```

#### 방법 2: 외부 디렉토리 연결

아래와 같이 ~/.openclaw/openclaw.json 파일에 추가할 수 있습니다.

```java
{
  "skills": {
    "load": {
      "extraDirs": [
        "/home/ec2-user/my-skills",
        "~/Projects/team-skills"
      ],
      "watch": true
    }
  }
}
```

Skill의 enable/disable 설정은 아래와 같이 수행합니다.

```java
{
  "skills": {
    "entries": {
      "weather": {
        "enabled": true
      },
      "some-skill": {
        "enabled": false,
        "env": {
          "API_KEY": "your-key-here"
        }
      }
    }
  }
}
```

#### Skill Command

- 모든 스킬 목록
```text
openclaw skills list
```

- 스킬 상세 정보

```text
openclaw skills info weather
```

- 스킬 요구사항 체크

```text
openclaw skills check
```

### Cron

- 등록된 cron 목록 보기

```text
openclaw cron list
```

- 즉시 테스트 실행

```text
openclaw cron run daily-weather
```

- 비활성화/활성화

```text
openclaw cron disable daily-weather
```

- 삭제

```text
openclaw cron rm daily-weather
```


### Kiro-Cli 설치

SSM으로 접속시 ec2-user로 전환합니다.

```text
sudo su - ec2-user
```

아래와 같이 설치합니다.

```text
curl -fsSL https://cli.kiro.dev/install | bash
```

아래 방식으로 인증을 할 수 있습니다.

```text
$ kiro-cli login --use-device-flow
✔ Select login method · Use for Free with Builder ID

Confirm the following code in the browser
Code: VNCC-PKNS

Open this URL: https://view.awsapps.com/start/#/device?user_code=VNCC-PKNS
Device authorized
Logged in successfully
```

아래와 같이 실행합니다. 모델 설정은 claude-opus-4.6, claude-sonnet-4.6, claude-opus-4.5, claude-sonnet-4.5, claude-sonnet-4, claude-haiku-4.5, deepseek-3.2, minimax-m2.1, qwen3-coder-next 와 같이 선택할 수 있습니다.

```python
kiro-cli chat --model claude-sonnet-4.6
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

## Reference

[OpenClaw — Personal AI Assistant](https://github.com/openclaw/openclaw)

[Getting Started](https://docs.openclaw.ai/start/getting-started)

[openclaw on AWS with Bedrock](https://github.com/aws-samples/sample-OpenClaw-on-AWS-with-Bedrock?tab=readme-ov-file)

