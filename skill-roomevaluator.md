# drawing-roomevaluator — 동작 상세

`floor_wall_validated.dxf`에서 **실명 하나**의 벽 안쪽 면적을 계산합니다.  
핵심 구현: `application/skills/drawing-roomevaluator/scripts/evaluate_room.py`  
층 전체 라벨은 `drawing-totalroom`이 이 모듈을 그대로 부릅니다.  
면적·영역 제외·집기·설비 토큰은 `scripts/label_info.json`을 씁니다.

입력 DXF·PNG는 수정하지 않습니다. 결과는 DXF 옆 `room_eval/`에만 씁니다.

---

## 1. 역할·입출력

| 항목 | 내용 |
|------|------|
| 스킬 경로 | `agent-skills/application/skills/drawing-roomevaluator/` |
| 선행 | `floor_wall_validated.dxf` / `.png` / `_meta.json` |
| 실명 | `--room`. 공백은 무시. 위·아래로 붙은 글자는 이어서 찾음 |
| 출력 | `room_eval/<실명>.json`, `room_eval/<실명>_overlay.png` |
| 여러 층 | 같은 실명을 여러 층에서 보면 `evaluate_floors.py` 한 번(물리 CPU 코어만큼 동시). `for`로 층마다 `evaluate_room.py`를 돌리지 않음 |

```bash
python3.13 "$SKILLS/drawing-roomevaluator/scripts/evaluate_room.py" \
  --dxf "$ART/sk_yongin_jiwon/floors/5F/floor_wall_validated.dxf" \
  --meta "$ART/sk_yongin_jiwon/floors/5F/floor_wall_validated_meta.json" \
  --png "$ART/sk_yongin_jiwon/floors/5F/floor_wall_validated.png" \
  --room "회의실#1" \
  --door close
```

`--door`를 생략하면 `close`입니다. `--out`으로 다른 경로를 지정하지 않습니다. `room_eval_1`처럼 번호 폴더를 만들지 않고, 같은 경로에 덮어씁니다.

같은 실명이 여러 곳이면 스크립트가 좌표를 알리고 멈춥니다. DXF를 복사하거나 글자를 고치지 않고, 고른 좌표를 `--x` `--y`로 다시 호출합니다.

## 2. 계산 순서

```text
① 실명 TEXT. 붙은 두 줄은 한 실명
② 문을 모두 닫아 벽 테두리를 잡음
     테두리 위 DOOR 직선과, 테두리 450 mm 안의 여닫이가 이 실의 문
③ close 는 그 문을 문선으로 막음. open 은 그 문만 염
     테두리 밖 DOOR, 기둥이 끊은 벽, 벽 끝 300 mm 이하 틈은 어느 쪽이든 닫음
④ 벽선을 polygonize 해서 라벨이 들어 있는 닫힌 면
⑤ 실 안으로 들어온 기둥 돌출부를 제외
⑥ 꼭짓점을 실제 벽·기둥 좌표에 스냅한 뒤 신발끈 면적
```

앞 방안에서 닫히지 않은 라벨만 방안 2(레이캐스트)·방안 3(거리 변환)으로 넘깁니다. 상세 임계는 `SKILL.md` Critical Rules입니다.

| 항목 | 규칙 |
|------|------|
| 경계 | `WALL`·`WINDOW`·`COLUMN`. `close`일 때 그 실의 `DOOR` 직선도 경계. 문 스윙 호·문짝 얇은 사각은 경계가 아님 |
| 문 개구 | 같은 벽선에서 2.4 m 이하면 그 벽선으로 이어 실에 포함. 직교 실간 벽·복도 개구는 잇지 않음. 여닫이가 있으면 그 문선에서 멈춤 |
| 기둥 | 변 0.45–1.5 m 정사각(이중 사각 또는 중심 `_`)이 벽 안쪽보다 실 안으로 들어온 면적을 `area_m2`에서 제외 |
| 제외 라벨 | `label_info.json`의 집기·설비·영역 토큰. `옷장`·`세면대`·`홀-`/`홈-` 등은 면적 실이 아님 |

경계로 읽는 레이어와 문 간격은 상수로 고정되어 있습니다.

```27:32:agent-skills/application/skills/drawing-roomevaluator/scripts/evaluate_room.py
BOUNDARY_LAYERS = (WALL_LAYER, WINDOW_LAYER, COLUMN_LAYER, DOOR_LAYER)
DOOR_GAP_MM = 2400.0
DOOR_TOUCH_MM = 450.0
```

## 3. 출력

| JSON | 의미 |
|------|------|
| `rules.door` | `close` 또는 `open` |
| `boundary_doors` | `kind`는 `leaf` 또는 `swing`. `x`,`y`, `state` |
| `area_m2` | 벽 안쪽 면에서 기둥 돌출부를 뺀 면적 (m²) |
| `column_protrusion_m2` | 뺀 기둥 돌출부 합계 |
| `width_m`, `height_m` | 돌출을 빼기 전 외곽의 가로·세로 |
| `drawing_area_m2` | 실 안 문구 `면적 : N㎡` |
| `overlay_png` | 계측 범위를 반투명으로 덮은 확대 이미지 |
| `enclosed_labels` | 그 면 안의 실명 |

`enclosed_labels`에 두 실명이 함께 있으면 `area_m2`는 그 면 전체이므로 한 번만 말합니다. 서로 다른 면이면 각 `area_m2`를 더합니다. 도면의 `면적` 문구와 다르면 계산값을 기준으로 설명합니다.

## 4. 한 줄 요약

**검증 도면에서 실명 하나의 벽 안쪽 면을 신발끈으로 재고, 기둥 돌출부를 뺀 면적과 오버레이를 `room_eval/`에 저장합니다.**
