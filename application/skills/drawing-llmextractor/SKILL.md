---
name: drawing-llmextractor
description: >-
  사용자가 지정한 주제와 이미지 경로를 받아, UI에서 선택한 Vision LLM으로
  도면·이미지 속 객체를 찾고 원본 위에 빨간색으로 표시합니다.
  객체 추출, 주제별 마킹, 빨간 박스, drawing llm extractor, 도면에서 기둥/문/설비
  찾기 요청 시 사용합니다.
---

# drawing-llmextractor (주제 객체 추출·빨간 표시)

이미지 파일과 **추출 주제**를 받아, `chat.py`의 `extract_text`와 같은 방식
(base64 PNG + 프롬프트 → `get_chat().invoke`)으로 주제에 맞는 객체를 찾는다.
찾은 영역은 **원본 이미지를 덮어쓰지 않고**, 복사본 위에 **빨간색 박스**로 표시해 저장한다.

LLM은 **UI에서 사용자가 선택한 모델**이다. 에이전트가 모델을 임의로 고르지 않는다.

## When to Use

- 도면·스캔·평면도 PNG에서 특정 대상(기둥, 문, 엘리베이터, 계단, 가구 등)을 찾아 표시할 때
- “이 이미지에서 ○○만 빨간색으로 추출/표시” 요청 시
- 이미지 경로와 주제가 함께 주어졌을 때

## Critical Rules

1. **입력** — 사용자가 준 **주제**와 **이미지 파일 경로**만 사용한다. 경로를 추측해 다른 파일을 열지 않는다. Load files로 고른 이미지는 대화의 `절대 경로:` 가 그 경로다. 이미지가 보여도 그 줄이 없으면 실행하지 말고 경로를 요청한다.
2. **원본 보존** — 입력 이미지는 수정하지 않는다. 표시 결과는 별도 PNG다.
3. **모델** — `--model`을 붙이지 않는다. 스크립트가 환경변수 `UI_MODEL_NAME`으로 UI 선택 모델을 쓴다. 사용자가 다른 모델을 **명시**한 경우에만 `--model "UI에 있는 표시 이름"`을 넘긴다.
4. **스크립트 절대경로** — `$WORKING_DIR/skills/drawing-llmextractor/scripts/` 또는 이 SKILL.md 옆 `scripts/` 절대경로. cwd 상대 `skills/...` 금지.
5. **Python** — 비대화형 bash의 `python3`는 Xcode 3.9인 경우가 있다. 그 환경에는 `langchain_community`가 없어 `import chat`이 실패한다. **`python3.13`으로 실행**한다. 스크립트도 패키지가 없으면 python3.13으로 다시 실행한다.
6. **한 장씩** — 이미지 여러 장이면 파일당 bash 1회. 한 명령에 루프로 몰지 않는다.
7. **bash 300초** — `bash` 도구는 300초가 지나면 `TimeoutExpired`로 자식 프로세스를 죽인다. 층 도면은 조각마다 Vision 호출이라 5분을 넘긴다. **포그라운드로 기다리지 말고** `nohup`으로 로그 파일에 남긴 뒤, 짧은 `tail`로 진행만 확인한다. `2>&1 | tail`은 프로세스가 끝나야 출력이 나오므로 쓰지 않는다.
8. 응답은 **한국어**. 경로·JSON 키는 영문 유지.

## Script Location

```bash
# --- bootstrap ---
if [ -n "${WORKING_DIR:-}" ] && [ -f "$WORKING_DIR/skills/drawing-llmextractor/scripts/extract_objects.py" ]; then
  SCRIPTS="$WORKING_DIR/skills/drawing-llmextractor/scripts"
else
  SCRIPTS="/Users/ksdyb/Documents/src/agent-skills/application/skills/drawing-llmextractor/scripts"
fi
test -f "$SCRIPTS/extract_objects.py" || { echo "missing $SCRIPTS/extract_objects.py" >&2; exit 2; }

if command -v python3.13 >/dev/null 2>&1; then PY=python3.13; else PY=python3; fi
LOG="/absolute/path/to/drawing.wall.log"
nohup "$PY" "$SCRIPTS/extract_objects.py" \
  --image "/absolute/path/to/drawing.png" \
  --topic "기둥" > "$LOG" 2>&1 &
echo "started $!"
# 진행 확인은 짧게. sleep 240 처럼 300초에 가까운 대기는 TimeoutExpired가 난다.
tail -n 20 "$LOG"
```

| 인자 | 의미 |
| --- | --- |
| `--image` | 입력 이미지 절대경로 (png/jpg/webp/gif/bmp/tif) |
| `--topic` | 추출 주제. 사용자의 말을 번역하지 않고 그대로 전달 |
| `--output` | 선택. 빨간 표시 PNG 경로. 생략 시 원본과 같은 폴더의 `{stem}.{주제}.png` |
| `--model` | 선택. **기본 생략.** UI 선택 모델(`UI_MODEL_NAME`) |
| `--max-tiles` | 선택. 조각 수에 영향 없음. 나누기는 아래 5000px 규칙을 따른다. 여백 조각은 건너뜀 |
| `--workers` | 선택. 동시에 Vision 호출할 조각 수. 기본 4 |

## Workflow

```
사용자: 주제 + 이미지 경로
  ↓
extract_objects.py
  1. UI_MODEL_NAME → chat.update → get_chat()  (UI에서 고른 모델)
  2. 겹침을 넣은 조각의 가로 또는 세로가 5000px를 넘으면 그 방향으로 더 나눈다.
     5000×5000 이하면 그대로 둔다. 200만 픽셀로 축소하지 않는다.
     거의 흰 여백 조각은 건너뜀
  3. 잉크가 있는 조각을 `--workers`(기본 4)개씩 동시에 base64 PNG + 주제 프롬프트로 get_chat().invoke
     응답은 <result> JSON {objects:[{label, bbox}]}
  4. 조각 좌표를 원본 픽셀로 되돌리고, 조각 전체를 덮는 박스는 버림
  5. 겹치는 검출은 더 작은 박스를 남기고 합침
  6. 원본 복사본에 빨간색 채움 + 테두리로 표시
  ↓
{원본이름}.{주제}.png
{원본이름}.{주제}.json
```

큰 도면은 200만 픽셀로 줄여 보내지 않는다. 겹침 12%를 포함한 조각이 5000×5000을 넘지 않을 때까지 나눈 뒤, 검출을 원본 좌표로 모은다. 조각 전체를 덮는 박스는 버린다.

## 출력

stdout 예:

```text
model: Claude 5.0 Sonnet
model_id: us.anthropic.claude-sonnet-5
topic: 기둥
objects: 12
marked: /path/floor.기둥.png
json: /path/floor.기둥.json
```

JSON `objects[]` 항목:

- `label` — 객체 이름
- `bbox_px` — 원본 이미지 픽셀 `[x0, y0, x1, y1]` (왼쪽 위 원점)
- `bbox_norm` — 원본 대비 0~1 좌표

`objects: 0`이면 주제에 맞는 객체가 없다는 뜻이다. 입력 파일을 다시 돌리거나 주제를 바꾸라고 안내한다.

사용자에게 **표시된 PNG 경로**, **객체 개수**, **사용한 모델 이름**을 알린다. 이미지 미리보기가 가능하면 marked PNG를 보여 준다.

## Decision Checklist

- [ ] 주제와 이미지 경로가 사용자 입력에서 왔는가
- [ ] `--model` 없이 UI 선택 모델을 썼는가 (사용자가 모델을 지정한 경우만 예외)
- [ ] 원본 이미지는 그대로이고, 빨간 표시는 별도 PNG인가
- [ ] 결과 JSON의 `model`이 UI에서 고른 이름과 같은가
