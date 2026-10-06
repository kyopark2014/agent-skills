# drawing-llmextractor — 동작 상세

이미지와 **추출 주제**를 받아, UI에서 고른 Vision 모델로 객체를 찾고 원본 복사본 위에 박스로 표시합니다.  
면적 파이프라인(`drawing-areasizing`)과는 별개입니다.  
핵심 구현: `application/skills/drawing-llmextractor/scripts/extract_objects.py`

원본 이미지는 수정하지 않습니다. 모델은 `--model`을 붙이지 않으면 환경변수 `UI_MODEL_NAME`입니다.

---

## 1. 역할·입출력

| 항목 | 내용 |
|------|------|
| 스킬 경로 | `agent-skills/application/skills/drawing-llmextractor/` |
| 입력 | 주제 + 이미지. 건물·층만 있으면 `drawing_list.json`의 그 층 `floor_original.png` |
| 벽 도면을 말하면 | `floor_wall_validated.png`, 없으면 `floor_wall_original.png` |
| 출력 | `{원본이름}.{주제}.png` / `.json`. 기본색은 빨강. 벽이 이미 빨강이면 `--color 연두` |

```bash
python3.13 "$SCRIPTS/extract_objects.py" \
  --image "/absolute/path/to/floor_original.png" \
  --topic "기둥"
```

층 도면은 조각마다 Vision 호출이라 bash 300초를 넘깁니다. `nohup`으로 로그에 남기고, 포그라운드로 끝까지 기다리지 않습니다. 이미지가 여러 장이면 파일당 bash 1회입니다.

| 인자 | 의미 |
|------|------|
| `--image` | png/jpg/webp/gif/bmp/tif 절대경로 |
| `--topic` | 사용자 말을 번역하지 않고 그대로 |
| `--output` | 생략 시 `{stem}.{주제}.png` |
| `--model` | 생략. 사용자가 모델을 명시한 경우에만 |
| `--workers` | 동시 Vision 호출. 기본 4 |
| `--color` | 기본 빨강. `연두`, `#RRGGBB` |

## 2. 호출

`chat.py`의 `extract_text`와 같이 base64 PNG와 프롬프트를 `get_chat().invoke`에 보냅니다.

```68:69:agent-skills/application/skills/drawing-llmextractor/scripts/extract_objects.py
MAX_TILE_SIDE = 5000
OVERLAP = 0.12
```

```430:445:agent-skills/application/skills/drawing-llmextractor/scripts/extract_objects.py
def _invoke_tile(chat, tile_png: bytes, prompt: str) -> str:
    img_base64 = encode_vision_png(Image.open(BytesIO(tile_png)))
    messages = [
        chat.HumanMessage(
            content=[
                {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{img_base64}"}},
                {"type": "text", "text": prompt},
            ]
        )
    ]
    result = _thread_llm(chat).invoke(messages)
    return chat._content_to_text(result.content)
```

모델이 돌려준 0~1 상자는 그 조각의 픽셀 창에 다시 얹습니다.

```407:416:agent-skills/application/skills/drawing-llmextractor/scripts/extract_objects.py
def _tile_to_original(norm, tile):
    left, top, right, bottom = tile
    tw = max(1, right - left)
    th = max(1, bottom - top)
    x0 = int(round(left + norm[0] * tw))
    y0 = int(round(top + norm[1] * th))
    x1 = int(round(left + norm[2] * tw))
    y1 = int(round(top + norm[3] * th))
    return [x0, y0, x1, y1]
```

```text
1. UI_MODEL_NAME → chat.get_chat()
2. 겹침 12%를 넣은 조각의 한 변이 5000px를 넘으면 더 나눔
   5000×5000 이하면 그대로. 200만 픽셀로 축소하지 않음
   거의 흰 여백 조각은 건너뜀
3. 잉크가 있는 조각을 workers개씩 동시에 호출
   응답은 JSON {objects:[{label, bbox}]}
4. 조각 좌표를 원본 픽셀로 되돌림. 조각 전체를 덮는 박스는 버림
5. 겹치는 검출은 더 작은 박스를 남김
6. 원본 복사본에 채움과 테두리만 그림. 객체 이름은 이미지에 적지 않음
```

주제가 벽이면 창틀·관찰창도 벽으로 표시합니다. 주제가 창이면 기호 하나씩 표시합니다.

## 3. 출력

```text
model: <UI에서 고른 이름>
model_id: ...
topic: 기둥
objects: 12
marked: /path/floor.기둥.png
json: /path/floor.기둥.json
```

JSON `objects[]`는 `label`, 원본 픽셀 `bbox_px` `[x0,y0,x1,y1]`, 0~1 `bbox_norm`입니다. `objects: 0`이면 그 주제에 맞는 객체가 없다는 뜻입니다.

## 4. 한 줄 요약

**UI에서 고른 모델에 타일 PNG를 보내 주제 객체의 상자를 받고, 원본은 그대로 둔 채 `{이름}.{주제}.png`에 표시합니다.**
