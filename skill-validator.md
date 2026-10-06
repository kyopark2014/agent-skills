# drawing-llmvalidator — Vision 벽 검증·보정 상세

`drawing-walldetector`가 만든 **빨간 WALL / 회색 BASE** 층 도면을 Vision LLM이 검수하고,  
기하 휴리스틱 + `review.json` bbox로 **오검출 demote / 미검출 promote** 를 적용해  
`floor_wall_validated.{dxf,png}` 를 만듭니다.  
핵심 구현: `application/skills/drawing-llmvalidator/scripts/lib_llm_correct.py`

입력 `floor_wall_original.*` 은 **덮어쓰지 않습니다**.

---

## 1. 역할·입출력

| 항목 | 내용 |
|------|------|
| 스킬 경로 | `agent-skills/application/skills/drawing-llmvalidator/` |
| 선행 | `drawing-walldetector` → `floors/<F>/floor_wall_original.{dxf,png}` |
| Vision 입력 | `prepare_review.py`가 만든 `llm_review/R*C*.png` (한 변 5000px 이하) |
| 보정 입력 | `floor_wall_original.dxf` + `llm_review/review.json` |
| 기본 출력 | `floor_wall_validated.{dxf,png,_meta.json}` + `diff_original_vs_validated.png` |
| 진입 | `prepare_review.py` → `view_image.py` → `correct_walls_floor.py` → `render_wall_diff.py` |
| 핵심 API | `lib_llm_correct.apply_corrections(doc, review=…)` |

### 산출 경로

```text
$ARTIFACTS_DIR/<drawing_id>/floors/<FLOOR>/
├── floor_wall_original.dxf / .png / _meta.json   # walldetector (불변)
├── floor_wall_validated.dxf / .png / _meta.json  # llmvalidator 출력
├── diff_original_vs_validated.png / .json        # 어두운 빨강=kept, 초록=promote, 파랑=demote
└── llm_review/
    ├── R0C0.png …           # 5000×5000 이하. 범위는 tiles.json
    ├── tiles.json
    ├── review.json          # view_image.py가 쓴 demote/promote bbox
    ├── corrections.json
    └── view_image.log
```

### 스크립트 역할

| 스크립트 | 역할 |
|----------|------|
| `prepare_review.py` | `floor_wall_original.png` → 한 변 5000px 이하 타일 + `tiles.json`. 층 전체 한 장 복사는 하지 않음 |
| `view_image.py` | 타일 PNG를 base64로 `chat.get_chat().invoke`에 보내고 `review.json` 작성 |
| `correct_walls_floor.py` | DXF 로드 → `apply_corrections` → validated 저장 |
| `render_wall_diff.py` | original vs validated diff PNG/JSON |
| `lib_llm_correct.py` | 세그먼트·promote/demote·문·창·기둥 레이어 |

---

## 2. Vision LLM은 “벽을 어떻게 인지”하는가

Vision 모델은 DXF 좌표를 직접 파싱하지 않습니다.  
**벽detector가 칠해 둔 색**으로 구조/비구조를 읽습니다.

| PNG에서 보이는 것 | DXF 의미 | Vision 해석 |
|-------------------|----------|-------------|
| 빨강 선 | `WALL` 레이어 | 이미 벽으로 검출됨 |
| 회색 선 | `BASE` 레이어 | 비벽이거나 **놓친 벽** |
| 파란 텍스트 | TEXT/MTEXT | 실명·UP/DN·강당 등 맥락 |
| ARC(문 스윙) 등 | BASE에 남음 | 개구 힌트 (갭을 메우지 말 것) |

채팅 에이전트는 타일을 열지 않습니다. `view_image.py`가 PNG를 base64로 넣고, UI에서 고른 모델에 판정 기준과 함께 보냅니다. 답은 상자 JSON이고, 스크립트가 타일 0~1 좌표를 층 mm로 바꿔 `review.json`에 모읍니다.

```python
encoded = _encode_png(review_dir / tile["file"])
result = client.invoke(
    [
        chat.HumanMessage(
            content=[
                {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{encoded}"}},
                {"type": "text", "text": prompt},  # SKILL.md 「Vision 판정 기준」
            ]
        )
    ]
)
```

`prompt`의 모델 출력에는 `layer` 필드가 없습니다. `DOOR` / `WINDOW` / `COLUMN`은 보정 마지막에 기하 코드 `mark_doors_and_columns`가 나눕니다.

```json
{
  "demote_bboxes": [
    { "label": "회의실_테이블", "xmin": 12000, "ymin": 45000, "xmax": 16000, "ymax": 48000 }
  ],
  "promote_bboxes": [
    { "label": "연속회의실_칸막이", "xmin": 80000, "ymin": 20000, "xmax": 95000, "ymax": 35000 }
  ]
}
```

- **demote**: bbox 안에 중심이 있는 **WALL 엔티티**를 삭제 후보로 표시  
- **promote**: bbox 안에서 **갭·이중선·연속 실 칸막이** 조건을 통과한 BASE 세그먼트만 승격  
- bbox 좌표 단위는 **mm** (도면 월드 좌표)

즉 Vision의 역할은 “이 구역의 빨강/회색이 이상하다”를 **위치(bbox)로 지시**하는 것이고,  
실제로 어떤 LINE을 올리고 내리는지는 `lib_llm_correct`의 **기하 규칙**이 결정합니다.

### 2.1 Vision용 이미지 준비 (`prepare_review.py`)

```text
floor_wall_original.png
  → 한 변 ≤ 5000px 이면 타일 1장
  → 한 변이 5000px를 넘으면 겹침 12% 격자 (R0C0.png …)
  → tiles.json 에 bbox_px / bbox_mm
```

층 전체 PNG와 `floor_wall_full.png`는 Vision에 넣지 않습니다.  
타일 크롭의 렌더 창은 walldetector와 같은 패딩(`_render_window`)입니다.  
Y는 CAD→이미지에서 뒤집힙니다 (`py = (ymax - y) / …`).

```67:70:agent-skills/application/skills/drawing-llmvalidator/scripts/prepare_review.py
    def mm_to_px(x: float, y: float) -> tuple[float, float]:
        px = (x - xmin) / (xmax - xmin) * W
        py = (ymax - y) / (ymax - ymin) * H
        return px, py
```

### 2.2 Vision이 따르는 최우선 규칙 (SKILL.md 요약)

`view_image.py`가 프롬프트로 넣는 도메인 규칙입니다. 같은 주제의 실행은 `lib_llm_correct.py`가 하고, 모델은 그 규칙에 안 들어가는 자리만 작은 상자로 짚습니다.

| 대상 | 인지 기준 | demote / promote |
|------|-----------|-------------------|
| **복도** | 양옆 긴 H/V 이중선 | 회색이면 promote, demote 금지 |
| **복도 문** | 개구 양옆 짧은 벽 | 한쪽만 빨강이면 반대쪽 promote |
| **계단실** | UP/DN 라벨 + 외곽 이중선 | 외곽 promote·protect, 트레드 다발 demote |
| **엘리베이터** | 샤프트 뱅크·문면 | 잼/어깨/외곽 promote, 전고 문·후면 demote |
| **H-Beam 기둥** | 정사각(0.45–1.5 m) + 중앙 `_` | 기하가 `COLUMN`으로 저장. 밀집 격자·휠체어 표식은 제외 |
| **가구·객석** | 짧은 사각·평행 다발 | demote (작은 bbox만) |
| **강당/오픈홀** | “강당” 라벨 | **중앙** 장축 demote, 외곽은 유지 |

`demote_bboxes`는 **가구 한 덩어리**만 (한 변 ≲ 15–25 m).  
층 절반·복도 전체를 덮으면 이웃 WALL까지 지워 “한 칸만 회색” 이상을 만듭니다 → `normalize_bbox_list`가 큰 박스를 skip합니다.

---

## 3. 코드가 벽을 “다시 보는” 방식 (`AxisSeg`)

보정 파이프라인은 Vision bbox와 별도로 DXF modelspace를 **축정렬 세그먼트**로 재인덱싱합니다.

```133:156:agent-skills/application/skills/drawing-llmvalidator/scripts/lib_llm_correct.py
class AxisSeg:
    x0: float
    y0: float
    x1: float
    y1: float
    length: float
    is_h: bool
    is_v: bool
    entity: Any
    layer: str

    @property
    def ortho(self) -> float:
        return (self.y0 + self.y1) * 0.5 if self.is_h else (self.x0 + self.x1) * 0.5

    @property
    def along0(self) -> float:
        return min(self.x0, self.x1) if self.is_h else min(self.y0, self.y1)
```

| 개념 | 의미 |
|------|------|
| `layer` | `WALL` 또는 `BASE` — Vision이 본 빨강/회색과 동일 |
| `is_h` / `is_v` | 각도 ±8° 이내만 후보 (사선 제외) |
| `ortho` | H면 Y, V면 X — “어느 줄에 있나” |
| `along0/1` | 그 줄 위에서 구간 시작·끝 |
| `_bucket(ortho, 50)` | 50 mm 양자화로 같은 벽선 묶기 |

`iter_axis_segs`는 `LINE` / `LWPOLYLINE`만 펼칩니다.  
`apply_corrections`에서는 **최소 길이 70 mm**로 읽습니다 (엘리베이터 문 어깨 등 짧은 조각 포함).

### 3.1 이중선 = 구조 벽 후보

보정 쪽 `_has_parallel_pair`의 기본 간격은 50–420 mm이고, overlap은 400 mm 또는 짧은 쪽의 25%입니다. 1차 검출(`wall_conditions.json`)의 후보 간격은 30–420 mm입니다.

```272:290:agent-skills/application/skills/drawing-llmvalidator/scripts/lib_llm_correct.py
def _has_parallel_pair(
    cand: AxisSeg,
    base: list[AxisSeg],
    *,
    thick_min: float = 50.0,
    thick_max: float = 420.0,
) -> bool:
    """BASE 이중선(벽 두께) 짝이 있으면 구조 벽 후보로 본다."""
    ...
        if ov >= max(400.0, 0.25 * min(cand.length, o.length)):
            return True
```

가구·해칭은 보통 이 조건을 오래 만족하지 않거나, 아래 demote 규칙에 걸립니다.

---

## 4. 전체 보정 파이프라인

```text
correct_walls_floor.py
  → ezdxf.readfile(floor_wall_original.dxf)
  → load_review(llm_review/review.json)   # 없으면 {}
  → apply_corrections(doc, review=…)
  → save floor_wall_validated.dxf
  → render_wall_dxf_png → .png + _meta.json + corrections.json
```

`apply_corrections` 내부 순서 (중요):

```text
1) iter_axis_segs (min_len=70)
2) H-Beam 기둥 id 수집
3) ★ promote 후보를 demote 전에 전부 확정
     - 갭 승격, 복도/문플랭크, 연속방, 계단, 엘리베이터
     - review promote_bboxes / room_row_dividers
     - 오픈홀·엘리베이터문·가구 필터로 promote에서 제거
4) demote 집합 구성
     - (옵션) short-demote, pack, dense cluster, 가구/피트니스/조경
     - review demote_bboxes
5) protect(복도·계단·엘리베이터·H-Beam)로 자동 demote에서 제외
     단 review demote_bboxes · 오픈홀 중앙 · 엘리베이터 문후면 · 가구는
     protect보다 우선 demote
6) WALL 엔티티 삭제 (demote)
7) BASE→WALL 또는 add_line (promote)
8) post-pass: 승격으로 다시 올라온 홀중앙/문후면/가구 제거
9) H-Beam·슬리브 승격, 픽토그램 재제거
10) 문 개구를 끊고 양옆을 올린 뒤 mark_doors_and_columns
      문짝·스윙=DOOR, 창=WINDOW, 기둥=COLUMN, 벽=WALL
```

세그먼트는 70 mm부터 읽고, 승격 후보를 삭제보다 먼저 모읍니다.

```19768:19783:agent-skills/application/skills/drawing-llmvalidator/scripts/lib_llm_correct.py
    segs = iter_axis_segs(msp, min_len_mm=70.0)
    ...
    # promote 후보를 demote 전에 확정
    promote: list[AxisSeg] = []
    if do_gap_promote:
        promote.extend(find_promote_segments(segs))
```

문·창·기둥 레이어는 보정 끝에서 호 반지름과 스윕으로 나눕니다. Vision JSON에는 이 이름이 없습니다.

```21102:21129:agent-skills/application/skills/drawing-llmvalidator/scripts/lib_llm_correct.py
def mark_doors_and_columns(msp) -> dict[str, Any]:
    """문짝·스윙은 DOOR, 창은 WINDOW, 기둥은 COLUMN이다."""
    ...
        sweep = (end_angle - start_angle) % 360.0
        if not (400.0 <= radius <= 1400.0 and 50.0 <= sweep <= 130.0):
            continue
```

promote를 demote보다 먼저 고르는 이유:  
넓은 `demote_bboxes`가 이웃 WALL을 지운 뒤면 갭/연속방 맥락이 사라져 **한 칸만 회색**으로 남는 문제가 생깁니다.

---

## 5. 문제점 탐지·수정 — Promote (BASE → WALL)

### 5.1 긴 벽 런 사이 “진짜 갭” (`find_promote_segments`)

이미 빨간 WALL 런이 같은 축에 두 덩어리 있고, 그 **사이 갭(≲ 4.5 m)** 을 메우는 회색 이중선만 승격합니다.

```241:263:agent-skills/application/skills/drawing-llmvalidator/scripts/lib_llm_correct.py
def _fills_true_gap(...):
    """True only if segment overlaps a gap *between* two existing WALL runs."""
    ...
        if gap <= 80 or gap > gap_max:
            continue
        ov = min(a1, right0) - max(a0, left1)
        if ov >= min(gap * 0.5, a1 - a0) and ov >= 400:
            return True
```

조건 요약:

1. BASE 길이 ≥ 1.2 m  
2. 같은 ortho 근처 WALL interval이 있고, 이미 55% 이상 덮이지 않음  
3. `_fills_true_gap` — 양쪽 WALL 사이 갭을 실제로 채움  
4. `_has_parallel_pair` — 이중선

→ “외벽에 구멍처럼 빠진 구간” 자동 복구.

### 5.2 복도 장축 (`promote_corridor_walls`)

갭 조건 **없이** 길이 ≥ 6 m + 이중선 BASE를 승격.  
가구·계단 해칭은 보통 이 길이의 단일 이중선이 아닙니다.

### 5.3 복도 문 양옆 (`promote_corridor_door_flanks`)

같은 벽선에서 WALL–WALL 사이 또는 WALL 끝↔BASE가 **문 크기 갭(0.7–2.2 m)** 이면,  
이미 한쪽이 빨강인 개구의 **반대쪽 BASE 플랭크**를 승격합니다.  
그 플랭크의 두 면이 서로의 길이 대부분을 덮는 평행 이중선일 때만입니다. 긴 선이 짧은 사각 변을 덮는 것은 이중선이 아닙니다. 개구 한쪽에 벽이 있다는 이유만으로는 올리지 않습니다.

문 개구에 맞닿은 평행 이중선(`promote_door_side_doubles`)은 길이와 관계없이 WALL입니다. 두 면이 서로의 길이 대부분을 덮고, 간격이 문 두께와 같으며, 겹친 구간이 문 밖에 있고 끝이 개구에 닿을 때만 올립니다.

### 5.4 연속 방 열 (`promote_collinear_room_walls`)

갭 승격은 “양쪽 WALL 사이”만 메웁니다.  
**끝단·한쪽 이웃만** 빨강인 칸막이는 여기서: 동일 축에 abut(≲ 0.6 m)하는 WALL이 있으면 BASE 이중선 승격.

### 5.5 Vision bbox 연동

| 함수 | 동작 |
|------|------|
| `promote_in_bboxes` | bbox 안 갭 승격(+ 수직 칸막이·이웃 WALL ≥2) |
| `promote_room_row_dividers` | bbox 안·이웃 칸막이 ≥2인 긴 이중선 BASE |

bbox만으로 아무 회색선이나 올리지 않습니다. **이중선 + 구조 맥락**이 있어야 합니다.

```2773:2780:agent-skills/application/skills/drawing-llmvalidator/scripts/lib_llm_correct.py
def promote_in_bboxes(segs, bboxes, *, min_len_mm: float = 1000.0, gap_max_mm: float = 6000.0):
    """Vision bbox 안에서도 'WALL 런 사이 갭' + 이중선만 승격."""
```

### 5.6 계단 / 엘리베이터 / 기둥

| 함수 | Vision·텍스트 힌트 | 승격 대상 |
|------|-------------------|-----------|
| `promote_stair_enclosure_walls` | UP/DN 라벨 → 코어 bbox | 외곽 변 근처 이중선만 (중심 난간 제외) |
| `promote_elevator_enclosure_walls` | 샤프트/뱅크 기하 | 잼·문어깨·복도꺾임·문사이·외곽 (전고 문 개구 제외) |
| `promote_hbeam_columns` | 정사각+중앙 dash | 해당 엔티티 레이어를 WALL로 변경 |

엘리베이터는 `min_len` 70 mm 세그먼트까지 후보에 넣어 **짧은 문틀**을 놓치지 않습니다.

### 5.7 실제 승격 적용

```19891:19908:agent-skills/application/skills/drawing-llmvalidator/scripts/lib_llm_correct.py
        # BASE LINE 은 레이어를 바꿔 회색이 남지 않게 한다
        if (... LINE ... BASE_LAYER):
            e.dxf.layer = WALL_LAYER
            e.dxf.color = WALL_COLOR
        else:
            msp.add_line(
                (s.x0, s.y0), (s.x1, s.y1),
                dxfattribs={"layer": WALL_LAYER, "color": WALL_COLOR},
            )
```

- BASE **LINE** → 레이어만 WALL로 (이중 선 방지)  
- **LWPOLYLINE** 일부만 승격 → 해당 변만 빨간 LINE 추가

---

## 6. 문제점 탐지·수정 — Demote (WALL 삭제)

### 6.1 Vision bbox (`demote_in_bboxes`)

WALL 엔티티 **중심점**이 bbox 안이면 삭제 후보.

```2741:2768:agent-skills/application/skills/drawing-llmvalidator/scripts/lib_llm_correct.py
def demote_in_bboxes(msp, bboxes) -> set[int]:
    """review.json demote_bboxes 안의 WALL 엔티티."""
    ...
        if e.dxf.layer != WALL_LAYER:
            continue
        mx = sum(p[0] for p in pts) / len(pts)
        my = sum(p[1] for p in pts) / len(pts)
        for b in boxes:
            if b["xmin"] <= mx <= b["xmax"] and b["ymin"] <= my <= b["ymax"]:
                result.add(id(e))
```

로드 시 `normalize_bbox_list`가 한 변 > 25 m 또는 면적 > 200 m² demote를 **skip + 경고**합니다.

### 6.2 기하 자동 demote

| 함수 | 잡는 오검출 |
|------|-------------|
| `demote_closed_furniture_boxes` | 닫힌 소·중형 폴리 + 장변에 붙은 WALL LINE |
| `demote_fitness_equipment` | 피트니스 라벨 근처 기구 윤곽 |
| `demote_landscape_walls` | 조경 물결/바위 클러스터 내부 선 |
| `demote_open_hall_center_walls` | “강당” 라벨 기준 **홀 중앙** 장축 |
| `demote_elevator_door_back_faces` | 엘리베이터 전고 문·후면·스파인·카 측면 |
| `find_demote_wall_entities` | 긴 런에 안 붙은 짧은 WALL (`--short-demote`일 때만) |

### 6.3 Protect — demote에서 빼는 벽

```2834:2858:agent-skills/application/skills/drawing-llmvalidator/scripts/lib_llm_correct.py
def protect_corridor_wall_entities(..., long_min_mm: float = 6000.0, mid_min_mm: float = 2500.0):
    """복도·긴 이중선 벽 WALL 엔티티는 demote 금지."""
    ...
        if s.length >= long_min_mm:
            protect.add(eid)
        if s.length >= mid_min_mm and _has_parallel_pair(s, wall):
            protect.add(eid)
```

추가로 계단 외곽·엘리베이터 측벽·H-Beam id를 protect에 합칩니다.  
그 뒤:

```19857:19875:agent-skills/application/skills/drawing-llmvalidator/scripts/lib_llm_correct.py
    review_demote = demote_in_bboxes(msp, review.get("demote_bboxes") or [])
    demote_ids -= protect_ids
    demote_ids |= review_demote
    demote_ids |= open_hall_demote
    demote_ids |= elev_door_demote
    demote_ids |= furniture_box_demote
```

→ 자동 demote만 복도 보호에 걸린다. Vision `demote_bboxes`·강당 중앙 오검출·엘리베이터 문면·가구는 **보호를 뚫고** 제거한다.

삭제:

```19877:19881:agent-skills/application/skills/drawing-llmvalidator/scripts/lib_llm_correct.py
    for e in list(msp):
        if e.dxf.layer == WALL_LAYER and id(e) in demote_ids:
            msp.delete_entity(e)
            n_demoted += 1
```

BASE로 “되돌리는” 것이 아니라 WALL 복사본을 지웁니다. 원본 회색 기하(BASE)는 그대로 남습니다.

---

## 7. Vision과 기하의 역할 분담

```text
view_image.py                         lib_llm_correct.apply_corrections
  타일 PNG → chat.get_chat().invoke     review.json 이 없어도 기하 규칙은 실행
  demote_bboxes / promote_bboxes   ──▶  상자는 관심 구역
                                        선 삭제·추가·레이어는 여기만
```

| 구분 | Vision (`view_image.py`) | 기하 (`apply_corrections`) |
|------|---------------------------|----------------------------|
| 입력 | 타일 PNG의 빨강·회색 | DXF의 `WALL` / `BASE` 세그먼트 |
| 출력 | 상자 JSON. 레이어 필드 없음 | 엔티티 삭제·승격, 마지막에 `DOOR`·`WINDOW`·`COLUMN` |
| 쓰는 경우 | 수치 규칙에 안 들어가는 국소 오검출·미검출 | 복도·문·계단·엘리베이터·기둥·강당·정형 가구 |
| demote 상자 | 빨강인데 벽이 아닌 한 덩어리 | 중심이 상자 안인 `WALL`만 삭제. protect보다 우선. 한 변 25 m 또는 200 m² 초과는 버림 |
| promote 상자 | 회색인데 벽인 한 덩어리 | 상자 안 회색을 전부 올리지 않음. WALL 사이 갭 이중선과 세로 칸막이만 |

`review.json`이 없어도 `correct_walls_floor.py`는 동작합니다.  
Vision은 **휴리스틱이 애매한 구역**을 bbox로 보강하는 층입니다.

---

## 8. 실행·검증

```bash
SCRIPTS=.../drawing-llmvalidator/scripts
ART=$ARTIFACTS_DIR/<drawing_id>

python3 "$SCRIPTS/prepare_review.py" --artifacts "$ART" --floor 5F
python3.13 "$SCRIPTS/view_image.py" --artifacts "$ART" --floor 5F
python3 "$SCRIPTS/correct_walls_floor.py" --artifacts "$ART" --floor 5F
python3 "$SCRIPTS/render_wall_diff.py" --artifacts "$ART" --floor 5F
```

주요 CLI 플래그 (`correct_walls_floor.py`):

| 플래그 | 기본 | 의미 |
|--------|------|------|
| `--review` | `llm_review/review.json` | Vision bbox 경로 |
| `--no-gap-promote` | off | 갭 승격 끄기 |
| `--no-corridor-promote` | off | 복도 장축/문플랭크 끄기 |
| `--no-pack-demote` | off | 평행 다발 demote 끄기 |
| `--short-demote` | **off** | 짧은 고립 WALL demote (복도 조각 손상 가능) |
| `--no-png` | off | validated PNG 생략 |

`corrections.json` / meta의 `llm_validate`에 `n_demoted`, `n_promoted`, `n_corridor_protected`, 계단·엘리베이터·기둥 통계가 남습니다.

---

## 9. Decision Checklist

- [ ] `floor_wall_original.dxf`가 있는가
- [ ] `view_image.py`가 `review.json`을 쓴 뒤에 `correct_walls_floor.py`를 실행했는가
- [ ] 출력이 `floor_wall_validated.*`이고 original은 그대로인가
- [ ] 문·창·기둥이 `DOOR` / `WINDOW` / `COLUMN`으로 나뉘었는가

---

## Related

- `drawing-walldetector` — 이중선 1차 검출 (`skill-walldetector.md`)
- `drawing-devider` — `floor_original` 선행 (`skill-devider.md`)
- `drawing-totalroom` — 검증 DXF의 실명 면적 (`skill-totalroom.md`)
- `drawing-areasizing` — 추출부터 실명 면적까지 (`skill-areasizing.md`)
- 스킬 요약·에이전트 규칙: `application/skills/drawing-llmvalidator/SKILL.md`
