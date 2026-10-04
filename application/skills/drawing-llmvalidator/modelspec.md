# drawing-llmvalidator 보정 모델

`floor_wall_original`을 `floor_wall_validated`로 만드는 흐름입니다. Vision LLM은 타일 이미지를 보고 상자만 넘기고, 선 삭제와 레이어 변경은 `apply_corrections`가 모델스페이스에서 합니다.

대상 코드:

- `scripts/prepare_review.py` — Vision용 크롭
- `scripts/view_image.py` — 타일 PNG를 Vision에 보내고 `review.json` 작성
- `scripts/correct_walls_floor.py` — 보정 진입점, DXF/PNG 저장
- `scripts/lib_llm_correct.py`의 `apply_corrections` (약 4892행) — 모델스페이스 수정

## LLM의 역할

Vision 호출은 `view_image.py` 한 번입니다. 에이전트 도구로 이미지를 보지 않습니다. 스크립트는 타일마다 bbox JSON만 받아 층 mm로 바꿔 `review.json`에 모읍니다. 도면 엔티티는 고치지 않고, 상자를 기하 보정에 넘깁니다.

```
floor_wall_original.png / .dxf          walldetector 산출. 읽기 전용
  ↓
prepare_review.py                       크롭만. LLM 없음
  llm_review/R*C*.png + tiles.json
  ↓
view_image.py                          타일 PNG → review.json (도구 호출 아님)
  ↓
correct_walls_floor.py
  load_review(review.json)
  apply_corrections(doc, review=...)    기하 규칙 + LLM 상자를 함께 적용
  ↓
floor_wall_validated.dxf / .png
llm_review/corrections.json             개수 통계
```

`prepare_review.py`는 `floor_wall_original.png`를 한 변 5000px 이하 조각으로 나눕니다. 겹침은 12%입니다. 5000×5000 이하는 한 장입니다. 층 전체 PNG와 `floor_wall_full.png`는 Vision에 넣지 않습니다. 각 조각의 도면 mm 범위는 `tiles.json`의 `bbox_mm`에 있습니다. 픽셀→mm 변환은 렌더 창(`floor_wall_original_meta.json`의 `bbox_mm`에 여백을 더한 범위) 기준입니다.

LLM이 보는 색은 빨강 = `WALL`, 회색 = `BASE`입니다. SKILL.md의 판정 기준(복도, 계단실, 엘리베이터, H-Beam, 가구, 강당)으로 각 타일을 보고, 기하 규칙이 놓치거나 잘못 잡은 곳만 층 전체 mm 상자로 적습니다.

`llm_review/review.json`:


| 필드               | 의미                                 |
| ---------------- | ---------------------------------- |
| `demote_bboxes`  | 빨강인데 벽이 아닌 곳. 가구, 운동기구, 엘리베이터 카 내부 |
| `promote_bboxes` | 회색인데 벽인 곳. 빠진 칸막이, 복도 경계           |


항목은 `{xmin, ymin, xmax, ymax}`가 필수입니다. `label`, `reason`, `tile`은 사람이 읽는 메모이고 기하 코드는 좌표만 씁니다. `x0/y0/x1/y1`, 중첩 `bbox_mm`, `[xmin,ymin,xmax,ymax]` 배열도 `normalize_bbox`가 받습니다. 키가 없으면 그 항목만 skip합니다.

5F 예 (`floors/5F/llm_review/review.json`): `R0C0.png`의 "대형 엘리베이터 내부 카 좌측선", `R0C2.png`의 "공용홀 벤치", "피트니스 덤벨 랙".

기하 규칙(복도·계단·엘리베이터·가구·강당)은 `review`가 비어 있어도 돕니다. LLM 상자는 그 위에 얹는 입력입니다. `review.json`이 없으면 `load_review`는 `{}`를 반환합니다.

상자가 적용되는 방식은 승격과 강등이 다릅니다.

- **demote** (`demote_in_bboxes`): 상자 안에 중심이 들어오는 `WALL` `LINE`/`LWPOLYLINE`을 삭제 후보에 넣습니다. 복도·계단·엘리베이터·H-Beam 보호보다 우선합니다. 한 변이 25 m를 넘거나 면적이 200 m²를 넘는 상자는 이웃 벽까지 지워지므로 `normalize_bbox_list`에서 버립니다. 가구 한 덩어리(보통 한 변 15 m 이하)만 감싸야 합니다.
- **promote** (`promote_in_bboxes`, `promote_room_row_dividers`): 상자 안의 회색 선을 전부 벽으로 올리지 않습니다. 이미 `WALL`인 선 사이의 갭을 메우는 이중선, 그리고 상자 안의 세로 칸막이만 승격합니다.

## `msp`는 무엇인가

`msp`는 ezdxf `Drawing`의 **모델스페이스(modelspace)**입니다.

- `doc`는 `ezdxf.document.Drawing`입니다. DXF 파일 전체(레이어, 엔티티, 페이퍼스페이스 포함)입니다.
- `doc.modelspace()`는 그 도면의 모델스페이스 레이아웃입니다. CAD에서 종이(페이퍼스페이스)와 구분되는, 실제 도면 기하가 있는 공간입니다.
- `for e in msp`로 그 안의 엔티티를 순회하고, `msp.delete_entity`, `msp.add_line`, `e.dxf.layer` 변경으로 도면을 제자리에서 바꿉니다.

```python
def apply_corrections(doc: Drawing, *, review=None, ...) -> dict:
    """In-place modify doc modelspace WALL layer. Returns stats."""
    msp = doc.modelspace()
    segs = iter_axis_segs(msp, min_len_mm=70.0)
```

레이어 상수:

- `WALL_LAYER = "WALL"`, `WALL_COLOR = 1` (빨강)
- `BASE_LAYER = "BASE"`

## 어떻게 고르나 — `iter_axis_segs`

`iter_axis_segs`가 모델스페이스를 한 번 훑어 축정렬 선분 목록을 만듭니다. 기본 최소 길이는 500 mm이고, `apply_corrections`만 70 mm로 낮춥니다. 엘리베이터 문 어깨(리턴, 약 250 mm)와 짧은 문틀이 후보에서 빠지지 않게 하기 위해서입니다.

필터는 네 단계입니다.

1. 레이어가 `WALL` 또는 `BASE`인 엔티티만 남깁니다. 텍스트와 다른 레이어는 버립니다.
2. 타입이 `LINE`이거나 `LWPOLYLINE`인 것만 남깁니다. 폴리라인은 꼭짓점 사이 선분으로 쪼갭니다. 닫힌 폴리라인은 마지막 점과 첫 점을 잇습니다.
3. 거의 수평·수직인 선만 남깁니다. `_normalize_hv`가 각도 8° 이내를 수평 또는 수직으로 보고, 사선은 버립니다. 길이 1 mm 미만도 버립니다.
4. 길이 `min_len_mm` 이상입니다.

각 결과는 `AxisSeg`입니다. 좌표(`x0,y0,x1,y1`), 길이, 수평/수직, 원본 엔티티 참조(`entity`), 레이어를 같이 듭니다. 이후 판정은 이 목록을 기준으로 하고, 실제 삭제는 `id(entity)`로 원본 엔티티를 찾습니다.

## 어떻게 동작하나

후보를 먼저 모은 뒤, 마지막에 한 번에 반영합니다. demote를 먼저 하면 이웃 `WALL`이 지워져 갭·연속방 승격 근거가 사라집니다.

### 1. 승격(promote) 후보

`BASE`에 있는 선 중 벽으로 볼 것을 고릅니다. 플래그가 켜져 있을 때의 출처:

- 갭 메우기 (`do_gap_promote`)
- 복도 벽, 복도 문 옆 (`do_corridor_promote`)
- 맞댄 칸막이, 동일 선상 실 벽 (항상)
- 계단실 외벽 (`do_stair_promote`)
- 엘리베이터 외벽 (`do_elevator_promote`)
- Vision `review["promote_bboxes"]`. 상자 안의 갭 이중선과 세로 칸막이만 (`promote_in_bboxes`, `promote_room_row_dividers`)

제외:

- 강당·오픈홀 중앙 통로·보이드는 승격하지 않습니다 (`do_open_hall_demote`). 무대 외벽은 따로 승격합니다.
- 엘리베이터 문·후면은 승격하지 않습니다. 측벽만 벽입니다.
- 운동기구, 회의실 내부, 정원·조경은 승격하지 않습니다 (`do_box_demote`).

### 2. 보호(protect)

일반 demote에서 빼 둡니다.

- 복도 벽
- 계단실 외벽
- 엘리베이터 외벽
- H-Beam 기둥 (`do_column_promote`로 `find_hbeam_column_entities`)

휠체어 표식 등 픽토그램은 기둥 protect에 넣지 않고 demote합니다.

### 3. 강등(demote)

지울 `WALL` 엔티티 id 집합입니다.

- 짧은 벽 (`do_short_demote`, 기본값 False)
- 닫힌 가구 박스, 운동기구, 회의실 내부, 조경 (`do_box_demote`)
- 오픈홀 중앙, 계단 디딤판, 엘리베이터 문·후면, 픽토그램

우선순위:

- 보호 집합과 겹치면 보호가 이깁니다 (`demote_ids -= protect_ids`).
- Vision `review["demote_bboxes"]`는 보호보다 우선합니다. 중심이 상자 안인 `WALL`만 지웁니다. 한 변 25 m 초과 또는 면적 200 m² 초과 bbox는 `normalize_bbox_list`에서 이미 버립니다.
- 오픈홀 중앙, 계단 디딤판, 픽토그램, 엘리베이터 문·후면, 가구·운동기구·조경도 보호보다 우선합니다.

### 4. 모델스페이스에 반영

- 강등: `WALL`이면서 demote 집합에 있으면 `msp.delete_entity`를 호출합니다.
- 승격: 같은 좌표는 0.1 mm 단위로 한 번만 반영합니다.
  - 원본이 `BASE`의 `LINE`이면 레이어를 `WALL`로 바꾸고 색을 `WALL_COLOR`로 둡니다. 겹친 `WALL` 선을 추가하지 않습니다.
  - 그 외(폴리라인 일부만 승격하는 경우)는 같은 좌표로 `msp.add_line`을 추가합니다.

### 5. 사후 정리

승격으로 다시 `WALL`이 된 것을 한 번 더 걷어 냅니다.

- 강당·오픈홀 중앙선
- 엘리베이터 문·후면
- 닫힌 가구, 운동기구, 회의실 내부, 조경
- H-Beam 기둥·슬리브는 demote 이후 `BASE`에서 `WALL`로 승격합니다.
- 창틀과 관찰창 유리는 `WINDOW`, 기둥은 `COLUMN`, 문짝은 `DOOR`, 벽은 `WALL` 레이어로 저장합니다. 면적 계산은 `WALL`·`WINDOW`·`COLUMN`을 경계로 읽습니다.
- 다시 빨개진 픽토그램(장애인 표식)을 제거합니다.
- 문짝은 벽이 아니고 개구 양옆은 벽입니다. promote 이후에 `correct_walls_around_doors`로 개구를 끊습니다. 스윙 힌지에 붙은 얇은 문짝은 `demote_swing_hinge_door_leaves`가 보정 맨 마지막에 BASE로 내립니다. 그 문짝은 실 면적 경계가 되지 않습니다. 벽 두께 안의 X자 블록은 `correct_x_block_doors`가 문을 내리고 양옆 벽면만 올립니다. 양 끝 캡으로 닫힌 문짝은 `correct_capped_leaf_doors`가 개구를 내리고 양옆만 올립니다. 옷장 칸의 끝선은 `demote_closet_bay_ends`가 내립니다.

## 반환 통계

도면 파일 저장은 이 함수 밖에서 합니다. 반환 dict의 주요 키:


| 키                               | 의미                           |
| ------------------------------- | ---------------------------- |
| `n_demoted`                     | 삭제한 `WALL` 엔티티 수 (사후 정리 포함)  |
| `n_promoted`                    | 레이어 변경 또는 신규 `LINE`으로 올린 선 수 |
| `n_corridor_protected`          | 일반 demote에서 보호로 빠진 수         |
| `n_open_hall_demoted`           | 오픈홀 중앙 강등                    |
| `n_pictogram_demoted`           | 픽토그램 강등                      |
| `n_stair_promote_candidates`    | 계단실 승격 후보 수                  |
| `n_elevator_promote_candidates` | 엘리베이터 승격 후보 수                |
| `n_elevator_door_demoted`       | 엘리베이터 문·후면 강등                |
| `n_column_promoted`             | H-Beam 기둥·슬리브 승격             |
| `n_wall_square_promoted`        | 벽면 정사각 기둥 승격 (H-Beam 아님)    |
| `n_wall_after`                  | 보정 후 `WALL` 엔티티 수            |
| `n_base`                        | 보정 후 `BASE` 엔티티 수            |
| `review_demote_bboxes`          | 적용한 Vision demote bbox 수     |
| `review_promote_bboxes`         | 적용한 Vision promote bbox 수    |


