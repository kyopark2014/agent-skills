# drawing-totalroom — 동작 상세

`floor_wall_validated.dxf`의 실명 라벨마다 벽 안쪽 면적을 계산합니다.  
면적 규칙과 문 열림은 `drawing-roomevaluator`(`evaluate_room.py`)와 같고, 이 스킬은 그 모듈을 층 전체 라벨에 적용합니다.  
핵심 구현: `application/skills/drawing-totalroom/scripts/detect_labels.py`

입력 DXF는 수정하지 않습니다. PNG는 결과 DXF를 `render_wall_dxf_png`로 그린 것입니다.

---

## 1. 역할·입출력

| 항목 | 내용 |
|------|------|
| 스킬 경로 | `agent-skills/application/skills/drawing-totalroom/` |
| 선행 | `drawing-llmvalidator` → `floor_wall_validated.dxf` + `_meta.json` |
| 출력 | 같은 폴더의 `floor_label_detected.dxf` / `.png` / `.json` |
| 한 실만 | `drawing-roomevaluator` |

```bash
python3.13 "$SKILLS/drawing-totalroom/scripts/detect_labels.py" \
  --dxf "$ART/floors/5F/floor_wall_validated.dxf" \
  --meta "$ART/floors/5F/floor_wall_validated_meta.json" \
  --door close
```

`--door`를 생략하면 `close`입니다. 각 실의 인접 문을 열어서 계산하라고 하면 `--door open`입니다. 값은 그 층의 모든 실에 같이 적용됩니다.

## 2. 라벨

모델스페이스 `TEXT`/`MTEXT`만 봅니다. 블록 안 글자는 펼치지 않습니다.

실명으로 보는 글자:

- 공백을 없앤 뒤 24자 이하
- `면적`, `천장`, `:`, `=` 이 없음
- 한글이 있거나, 영문 2자 이상에 숫자가 붙음 (`MRI1` 포함)

빠지는 글자: `X1` 같은 축선, `6300` 같은 치수, `UP`/`DN`, 집기 표기(`미니바`, `옷장`, `신발장`, `화분`, `화장대`, `욕조`, `(장애인)`과 그 뒤 번호). `옷방`, `소파룸`처럼 실명인 것은 남깁니다.

같은 실명이 50 mm 안에 있으면 한 곳입니다. 위·아래 줄 간격이 글자 높이의 1.8배 안이고 가로로 겹치면 위 글자부터 이어 한 실명으로 만듭니다. 예: `투시영상` + `검사실7` → `투시영상검사실7`.

판별과 두 줄 붙이기는 `evaluate_room.py`에 있고, 이 스킬은 집기 이름만 추가로 뺍니다.

```87:101:agent-skills/application/skills/drawing-roomevaluator/scripts/evaluate_room.py
def is_room_label(text: str) -> bool:
    """한글 실명, 또는 영문 2자 이상에 숫자가 붙은 이름. 축선·치수는 제외."""
    if _roomish(text):
        return True
    ...
    return len(letters) >= 2 and re.search(r"\d", name) is not None
```

```127:131:agent-skills/application/skills/drawing-roomevaluator/scripts/evaluate_room.py
def _stacked(a, b) -> bool:
    """위·아래 줄 간격이 글자 높이의 1.8배 안이고 가로로 겹치면 한 실명이다."""
    dy = abs(a[1] - b[1])
    height = max(a[3], b[3], 1.0)
    if dy <= height * 0.6 or dy > height * 1.8:
        return False
```

```65:67:agent-skills/application/skills/drawing-totalroom/scripts/detect_labels.py
def collect_labels(msp):
    """drawing-roomevaluator 와 같은 실명. 집기 표기는 뺀다."""
    return [item for item in room.collect_room_labels(msp) if not is_fixture_label(item[2])]
```

## 3. 면적

라벨이 있는 쪽의 **벽 안쪽 면**까지입니다.

| 항목 | 규칙 |
|------|------|
| 경계 | `WALL`·`WINDOW`·`COLUMN`·`DOOR` 선. 문 스윙 호는 경계가 아님 |
| 문 개구 | 같은 벽선에서 2.4 m 이하면 그 벽선으로 이어 실에 포함. 여닫이가 있으면 그 문선에서 멈춤 |
| `--door close` | 문을 모두 닫아 테두리를 잡은 뒤, 테두리 위 `DOOR` 직선과 테두리 450 mm 안의 여닫이만 그 실의 문으로 보고 닫음 |
| `--door open` | 그 문만 염. 테두리 밖 `DOOR`는 닫힌 경계 |
| 기둥 | H-Beam이 벽 안쪽보다 실 안으로 들어온 면적은 `area_m2`에서 제외. 벽 두께 안에만 있는 부분은 빼지 않음 |

서로 다른 실명이 한 면에 있으면 각 레이어에 그 면 전체가 들어갑니다. `shared_with`가 있으면 `area_m2`를 서로 더하지 않습니다. 같은 실명이 서로 다른 면에 있으면 각 면의 `area_m2`를 더합니다.

벽이 닫히지 않은 라벨은 레이어를 만들지 않고 `skipped`에 남깁니다.

## 4. 산출

`floor_label_detected.dxf`는 검증 도면을 복사한 뒤 라벨마다 레이어를 더한 것입니다. 레이어의 HATCH 면적 합이 그 라벨의 `area_m2`입니다. 각 자리에는 실명과 `면적 ㎡` 문자를 둡니다. 좌표는 입력 DXF와 같은 mm입니다.

한 라벨의 면적은 `evaluate_room.shoelace_m2`에서 구멍과 기둥 돌출을 뺀 값입니다.

```130:130:agent-skills/application/skills/drawing-totalroom/scripts/detect_labels.py
    area = room.shoelace_m2(pts) - sum(room.shoelace_m2(hole) for hole in holes)
```

```207:210:agent-skills/application/skills/drawing-totalroom/scripts/detect_labels.py
            hatch = msp.add_hatch(dxfattribs={"layer": name, "color": 256})
            hatch.transparency = float2transparency(0.55)
            hatch.set_solid_fill(color=256, style=0)
            hatch.paths.add_polyline_path(inst["pts"], is_closed=True, flags=1)
```

같은 면은 `_face_key`로 한 번만 더합니다.

```317:320:agent-skills/application/skills/drawing-totalroom/scripts/detect_labels.py
            key = _face_key(inst["pts"])
            if key not in seen_faces:
                seen_faces.add(key)
                unique_area += inst["area_m2"]
```

| JSON | 의미 |
|------|------|
| `rules.door` | 이번 층의 `close` 또는 `open` |
| `labels[].layer` | 더한 DXF 레이어 |
| `labels[].area_m2` | 그 라벨 HATCH 합 (m²) |
| `labels[].instances[]` | 글자 좌표, 그곳의 `area_m2`, `width_m`, `height_m` |
| `instances[].boundary_doors` | 벽 테두리 문. `x`, `y`, `state` |
| `instances[].shared_with` | 같은 면을 가리키는 다른 실명 |
| `unique_face_area_m2` | 서로 다른 면을 한 번씩만 더한 값 |
| `skipped` | 실명으로 봤으나 벽이 닫히지 않은 글자 |
| `drawing_area_m2` | 도면의 `면적` 문구. 계산값과 다르면 계산값을 기준으로 설명 |

PNG는 결과 DXF의 HATCH와 TEXT입니다. `floor_wall_validated.png`를 배경으로 덧그리지 않고, 오른쪽 범례도 붙이지 않습니다.

## 5. 한 줄 요약

**검증된 층의 실명마다 벽·창·기둥·문 선으로 안쪽 면을 잡아, 라벨별 HATCH가 있는 `floor_label_detected`로 저장합니다.**
