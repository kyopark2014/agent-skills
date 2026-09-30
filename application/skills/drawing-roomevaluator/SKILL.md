---
name: drawing-roomevaluator
description: >-
  drawing-llmvalidator 산출(floor_wall_validated DXF/PNG)에서 실명으로
  방을 찾고, 빨간 WALL로 둘러싸인 안쪽 면적을 계산합니다. 문 개구는 벽선으로
  잇고, 실 안으로 나온 기둥 돌출부는 항상 뺍니다. 반투명 오버레이 PNG로
  계측 범위를 확인합니다. 실 면적, room area, 접견실 면적 요청 시 사용합니다.
---

# drawing-roomevaluator (실명 기준 벽체 안쪽 면적)

`floor_wall_validated.dxf` 의 **빨간 WALL** 이 실명을 둘러싼 안쪽 면적을 계산한다.
입력 DXF·PNG 는 수정하지 않는다.

## When to Use

- 검증된 평면도에서 `접견실#3` 처럼 **실명으로** 면적을 구할 때
- 빨간 벽으로 닫힌 실의 계측 범위를 반투명으로 확인하고 싶을 때

## Critical Rules

1. **입력** — `floor_wall_validated.dxf` + 같은 폴더의 `_meta.json` + `.png`.
   벽은 레이어 `WALL`(빨강). 실명은 `TEXT`/`MTEXT`.
2. **면적** — 라벨이 있는 쪽의 **벽 안쪽 면**까지. 같은 벽선의 문 개구(2.4 m 이하)는
   그 벽선으로 이어 실에 포함한다. 벽 두께 한가운데나 바깥면이 아니다.
3. **기둥 돌출부는 항상 뺀다.** H-Beam(변 0.45–1.5 m 정사각, 이중 사각 또는 중심 `_`)이
   벽 안쪽 면보다 실 안으로 들어온 면적은 예외 없이 `area_m2`에서 제외한다.
   기둥이 끊은 벽선은 다시 이어 안쪽 면을 잡고, 그 면 안의 기둥만 뺀다.
   벽 두께 안에만 있는 부분은 실 밖이므로 빼지 않는다. 돌출이 없으면 뺄 면적은 0이다.
4. **원본 보존** — validated DXF/PNG 는 읽기 전용. 결과는 `room_eval/` 에만 쓴다.
   같은 실명이 여러 곳이면 스크립트가 좌표를 알리고 멈춘다. DXF를 복사하거나 글자를 고치지 말고, 고른 좌표를 `--x` `--y`로 다시 호출한다.
5. **스크립트 절대경로** — 이 SKILL.md 옆 `scripts/evaluate_room.py`.
6. 응답은 **한국어**. 경로·JSON 키는 영문.

## Script Location

```bash
SCRIPTS=/Users/ksdyb/Documents/src/agent-skills/application/skills/drawing-roomevaluator/scripts
# Runtime: SCRIPTS="$WORKING_DIR/skills/drawing-roomevaluator/scripts"

python3.13 "$SCRIPTS/evaluate_room.py" \
  --dxf "$ART/floors/5F/floor_wall_validated.dxf" \
  --meta "$ART/floors/5F/floor_wall_validated_meta.json" \
  --png "$ART/floors/5F/floor_wall_validated.png" \
  --room "접견실#3"
```

| 인자 | 의미 |
| --- | --- |
| `--dxf` | `floor_wall_validated.dxf` |
| `--meta` | `floor_wall_validated_meta.json` (PNG 좌표 변환) |
| `--png` | `floor_wall_validated.png` (오버레이 배경) |
| `--room` | 실명. 공백은 무시하고 TEXT 와 정확히 맞춘다 |
| `--x`, `--y` | 선택. 같은 실명이 여러 곳일 때 고를 라벨 좌표 (mm) |
| `--out` | 선택. 기본은 DXF 옆 `room_eval/` |

## Workflow

```
floor_wall_validated.dxf / .png / _meta.json
  ↓
① 실명 TEXT 위치
  ↓
② WALL 선분. H-Beam 정사각은 벽선 연결용으로만 쓰고 실 경계로 두지 않는다
  ↓
③ 같은 벽선의 2.4 m 이하 틈(문·기둥)을 잇고, 벽 끝의 300 mm 이하 틈은 벽 두께로 막는다
  ↓
④ 벽선을 polygonize 해서 라벨이 들어 있는 닫힌 면
  ↓
⑤ 실 안으로 들어온 기둥 돌출부를 항상 제외
  ↓
⑥ 꼭짓점을 실제 벽·기둥 좌표에 스냅 후 신발끈 면적
  ↓
room_eval/<실명>_overlay.png
room_eval/<실명>.json
```

## 출력

- `area_m2` — 벽 안쪽 면에서 기둥 돌출부를 뺀 면적 (m²)
- `column_protrusion_m2` — 뺀 기둥 돌출부 합계. 없으면 0
- `width_m`, `height_m` — 벽 안쪽 면의 가로·세로 (돌출을 빼기 전 외곽)
- `drawing_area_m2` — 실 안 도면 문구 `면적 : N㎡` (있으면)
- `overlay_png` — 계측 범위를 반투명으로 덮은 확대 이미지
- `enclosed_labels` — 그 면 안에 있는 실명 라벨

같은 면의 `enclosed_labels`에 두 실명이 함께 있으면 `area_m2`는 그 면 전체이므로 한 번만 말한다. 서로 다른 면이면 각 `area_m2`를 더한다.

도면의 `면적` 문구는 참고값이다. 계산값과 다르면 계산값을 기준으로 설명한다.
