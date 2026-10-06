# drawing-walldetector — 동작 상세

`drawing-devider`가 만든 층 단위 `floor_original.dxf`에서 벽을 검출하고, **빨간색 `WALL` 레이어**로 표시한 DXF/PNG를 만듭니다.  
핵심 구현: `application/skills/drawing-walldetector/scripts/lib_walls.py`

---

## 1. 역할·입출력

| 항목 | 내용 |
|------|------|
| 스킬 경로 | `agent-skills/application/skills/drawing-walldetector/` |
| 선행 | `drawing-devider` → `floors/<F>/floor_original.dxf` |
| 기본 입력 | `$ARTIFACTS_DIR/<drawing_id>/floors/<F>/floor_original.dxf` |
| 기본 출력 | `floor_wall_original.*` (common + 프로젝트 + 샘플)와 `floor_wall_common.png` (common만) |
| 도면별 조건 | `wall_conditions.json` + `floors/<F>/wall_samples/wall_conditions.json` |
| 진입 스크립트 | `detect_walls_floor.py` (층 1개). 샘플 파일이 없으면 그 전에 `sample_wall_conditions.py --vision` |
| 분류·저장 | `lib_walls.classify_entities` → `write_walls_dxf` / `render_walls_png` |

원본 277MB DXF를 직접 돌리지 않습니다. `parts/` 타일 분할은 기본 워크플로에서 쓰지 않습니다 (`--with-tiles`는 레거시·선택).

### 산출 경로

```text
$ARTIFACTS_DIR/<drawing_id>/
├── floors/<FLOOR>/
│   ├── floor_original.dxf / .png          # (devider) 입력
│   ├── floor_wall_original.dxf / .png / _meta.json  # common+프로젝트+샘플
│   ├── floor_wall_common.png / _meta.json           # common만
│   ├── floor_wall_index.json
│   └── wall_samples/                      # 샘플 2장·observations·wall_conditions.json
└── walls_all_index.json                   # (선택) 다층 요약
```

---

## 2. DXF에서 무엇을 읽는가 (벽 선택에 쓰는 정보)

지원동 등 클린 DXF는 벽·가구·설비가 거의 전부 **`0arch` 단일 레이어**에 있습니다.  
레이어 이름·블록명으로는 벽을 나눌 수 없어, **엔티티 타입 + 좌표 기하**만으로 판정합니다.

### 2.1 읽는 엔티티·필드

| DXF 타입 | 읽는 값 | 벽 판정에서의 역할 |
|----------|---------|-------------------|
| `LINE` | `start`, `end` (x,y) | 세그먼트 1개 → 이중선 후보 |
| `LWPOLYLINE` | `get_points("xy")`, `closed` | 꼭짓점 간 변마다 세그먼트. 닫힌 사각·다변 제외 규칙에도 사용 |
| `ARC` | center, radius, start/end angle | **벽 아님** (문 스윙 등). BASE에만 복사 |
| `CIRCLE` | center, radius | **벽 아님** (설비). BASE에만 복사 |
| `TEXT` / `MTEXT` | insert, text, height | 벽 판정 제외. BASE 복사·PNG 라벨용 |
| `DIMENSION` | — | `load_tile_entities`에서 **로드 자체를 스킵** |

`POLYLINE`(구형)·`ELLIPSE`·`SPLINE` 등은 기하 타입으로 BASE에 포함될 수 있으나, 벽 승격 대상은 사실상 **`LINE` / `LWPOLYLINE`** 입니다.

### 2.2 DXF에 없고 코드에 둔 규칙

| 규칙 | 기본값 | 의미 |
|------|--------|------|
| 축정렬 허용각 | ±8° | H/V만 후보 (사선 벽 약함) |
| `min_len_mm` | 500 | 후보 세그먼트 최소 길이 |
| `thick_min_mm` / `thick_max_mm` | 30 / 420 | 후보 간격. 외벽 마감선 30–45 mm 포함. 대역에 들어갔다고 벽이 되지는 않음 |
| 후보 겹침 | 400 mm, 그리고 짧은 쪽의 25% | `wall_conditions.json` `common[0]` |
| `wall_pack_gap_mm` | 200 | 이웃 ≥3이고 인접 간격 중앙값이 이보다 크면 계단 해칭 |
| `entity_wall_ratio` | 0.75 | 폴리라인 변의 ≥75%가 벽일 때만 통째 승격 |
| `furniture_box_max_mm` | 3500 | 닫힌 사각 max변 ≤ 이 값 → 가구. H-Beam 정사각은 예외로 `COLUMN` |

레이어명(`0arch` 등)·색상·선종은 **벽 선택에 사용하지 않습니다**.

---

## 3. 벽 선택 파이프라인 (상세)

호출 흐름:

```text
detect_walls_floor.py
  → load_tile_entities(floor_original.dxf)   # modelspace, DIMENSION 제외
  → classify_entities(entities, …)
  → write_walls_dxf / render_walls_png
```

### 3.1 세그먼트 추출 (`extract_segments`)

1. `LINE` → `(x0,y0)–(x1,y1)` 세그먼트 1개 (`entity_idx`, `seg_idx=0`)
2. `LWPOLYLINE` → 연속 꼭짓점마다 1세그먼트. `closed`이면 마지막→첫 점도 추가합니다.
3. 각 세그먼트에 대해:
   - 길이 `length`
   - 각도 → `is_h` / `is_v` (±8°)
   - H면 x 오름차순, V면 y 오름차순으로 끝점 정규화 (overlap 계산용)

### 3.2 평행 이중선 매칭 (`detect_wall_keys`)

CAD 평면도에서 벽은 **양면 이중선**으로 그려진다는 관례를 이용합니다.

```text
축정렬(H/V) · 길이 ≥ 500 mm · 간격 30–420 mm · 겹침 충분
  → 아래 셋 중 하나일 때만 WALL
     1. 간벽: 간격 120–180 mm, 같은 직선 런 2.2 m 이상
     2. X문 개구에 맞닿은 간벽 (X 획 자체는 벽 아님)
     3. 연속 실 테두리: 짧은 쪽 ≥ 2.2 m, 겹침 ≥ 80%
        (같은 조건의 평행선이 8개 이상이면 이 조건으로 올리지 않음)
```

숫자의 원본은 `wall_conditions.json`의 `common[0]`입니다. `projects.hynix`·`hotel`·`hospital`·`house`는 그 도면에서 common과 다른 키만 뒤에 붙입니다. `floors/<F>/wall_samples/wall_conditions.json`이 있으면 `floor_wall_original`에만 더합니다. `floor_wall_common`에는 넣지 않습니다.

```4:20:agent-skills/application/skills/drawing-walldetector/wall_conditions.json
      "candidate": {
        "angle_tolerance_deg": 8,
        "min_length_mm": 500,
        "gap_mm": { "min": 30, "max": 420 },
        "overlap_mm_min": 400,
        "overlap_ratio_of_shorter": 0.25
      },
      "broken_partition": {
        "enabled": true,
        "gap_mm": { "min": 120, "max": 180 },
        "run_min_length_mm": 2200
      },
```

후보로 고른 뒤, 간격이 두께 대역 안이고 겹침이 충분할 때만 이웃으로 둡니다.

```812:819:agent-skills/application/skills/drawing-walldetector/scripts/lib_walls.py
                d = mb - ma
                if d > thick_max:
                    break
                if d < thick_min:
                    continue
                ov = _pair_overlap(a, b, along_x=along_x)
                need = max(min_overlap, overlap_ratio * min(a.length, b.length))
                if ov >= need:
                    neighbors.append((d, b))
```

여러 겹은 이웃이 3개 이상일 때 인접 간격 중앙값을 봅니다. 200 mm보다 크면 계단 해칭으로 빼고, 200 mm 이하면 2.5 m 미만인 짧은 겹만 뺍니다. 촘촘하고 2.8 m 이상이라는 이유만으로 외벽으로 두지 않습니다.

### 3.3 엔티티 단위 분류 (`classify_entities`)

세그먼트 키가 나온 뒤, 엔티티를 WALL / 제외 / BASE-only로 나눕니다.

| 순서 | 조건 | 결과 |
|------|------|------|
| ① | H-Beam 기둥 (`find_hbeam_column_idxs`) | **`COLUMN`** (파랑, ACI 5) |
| ② | 닫힌 사각 `is_non_wall_closed_box` (max변 ≤ 3.5 m, min변 ≥ 150) | skip (가구·일반 기둥 윤곽) |
| ③ | 짧은 다변 폴리 (`변 ≥10` · 평균 길이 `< 1.5 m`) | skip (조경·해칭) |
| ④ | `ARC` / `CIRCLE` / `TEXT` / `MTEXT` / `DIMENSION` | 벽 승격 안 함 |
| ⑤ | `LINE`이고 키가 `wall_keys`에 있음 | WALL 엔티티 |
| ⑥ | `LWPOLYLINE`이고 벽 변 비율 ≥ 75% | 통째 WALL |
| ⑦ | 폴리라인 비율 < 75% | 엔티티는 WALL 아님. 해당 **세그먼트만** 빨간 LINE으로 보강 |

**H-Beam 기둥** 휴리스틱 (요약):

- 닫힌 대략 정사각(변 450–1500 mm, 가로·세로 차이 22% 이내) + 중심 근처 짧은 H/V dash LINE
- 밀집 격자(반경 내 후보 ≥4)는 제외 → 외부 연결·슬리브 오인 완화
- 사각 안에 ARC가 둘 이상이거나 `_` 후보가 6개 이상이면 장애인 표식 등으로 보고 제외
- 가구 박스로 빠지지 않도록 **`COLUMN`으로 저장**합니다. 창틀은 `WINDOW`, 여닫이 문짝·X자 문은 `DOOR`입니다.

분류 결과 dict (요지):

- `wall_entity_idxs` — 통째 WALL로 승격된 엔티티 인덱스
- `wall_keys` / `wall_segs` — 이중선으로 잡힌 세그먼트
- `skip_column_idxs` — 가구·해칭으로 스킵한 인덱스
- 통계: `n_wall_entities`, `n_wall_segs`, `n_furniture_skipped`, …

---

## 4. 선택한 벽을 DXF에 어떻게 저장하는가

원본 `floor_original.dxf`를 **수정하지 않습니다**.  
`ezdxf.new("R2010")`으로 **새 문서**를 만들고 `floor_wall_original.dxf`로 저장합니다.

구현: `write_walls_dxf(entities, classification, out_path)`

### 4.1 레이어·색

| 레이어 | ACI 색 | 의미 |
|--------|--------|------|
| `BASE` | 8 (회색) | 벽·문·창·기둥으로 올라가지 않은 기하 |
| `WALL` | 1 (빨강) | 벽 |
| `DOOR` | 3 (초록) | 문짝·X자 문 |
| `WINDOW` | 4 (청록) | 같은 개구의 얇은 창틀 |
| `COLUMN` | 5 (파랑) | H-Beam 등 기둥 |

```1291:1300:agent-skills/application/skills/drawing-walldetector/scripts/lib_walls.py
    def _stored_layer(ei: int) -> tuple[str, int] | None:
        if ei in column_idxs:
            return COLUMN_LAYER, COLUMN_COLOR
        if ei in window_idxs:
            return WINDOW_LAYER, WINDOW_COLOR
        if ei in door_idxs:
            return DOOR_LAYER, DOOR_COLOR
        if ei in wall_idxs and ei not in skip:
            return WALL_LAYER, WALL_COLOR
        return None
```

기둥·창·문·벽 순으로 보고, 어디에도 없으면 BASE입니다.

### 4.2 저장 순서

```text
1) 새 Drawing (R2010) + BASE / WALL / DOOR / WINDOW / COLUMN
2) 문·창·기둥·벽으로 분류되지 않은 기하와 TEXT 는 BASE
3) column → window → door → wall 순으로 레이어를 나눠 복사
     (skip_column_idxs 는 WALL에서 제외)
4) 통째 승격되지 않은 wall_segs 만 빨간 LINE 으로 보강
5) doc.saveas(floor_wall_original.dxf)
   같은 분류를 common 조건만으로 다시 돌려 floor_wall_common.png
```

### 4.3 엔티티 복사 (`_copy_entity`)

원본 엔티티를 그대로 옮기지 않고, **좌표를 읽어 새 엔티티를 생성**합니다.

| 원본 타입 | 출력 |
|-----------|------|
| `LINE` | `msp.add_line(start, end, layer, color)` |
| `LWPOLYLINE` | `add_lwpolyline(points "xyb")` + `closed` 유지 |
| `CIRCLE` / `ARC` | 동일 중심·반지름·각도 |
| `TEXT` / `MTEXT` | insert·높이·문자열 복사 |

블록(`INSERT`)·복잡한 속성은 다루지 않습니다. 벽 파이프라인 입력은 이미 devider가 풀어 둔 2D 기하 위주입니다.

### 4.4 “부분만 벽”인 폴리라인

폴리라인 변의 일부만 이중선에 매칭되면:

- 전체 엔티티는 `wall_entity_idxs`에 **안 들어갑니다** (≤75% 규칙)
- BASE에는 회색 폴리라인 전체가 남고
- 매칭된 변만 `wall_segs` → **독립 `LINE`(WALL, 빨강)** 으로 추가합니다.

통째 승격된 엔티티의 세그먼트는 중복 LINE을 넣지 않습니다 (`entity_idx in wall_idxs`면 skip).

### 4.5 저장 카운트 (`dxf_counts`)

`_meta.json` / `floor_wall_index.json`에 기록합니다:

| 키 | 의미 |
|----|------|
| `base` | BASE에 복사한 엔티티 수 |
| `wall` | WALL로 통째 복사한 엔티티 수 |
| `wall_seg_lines` | 부분 벽 보강용 빨간 LINE 수 |

### 4.6 PNG (검수)

`render_walls_png`는 동일 classification으로:

- BASE ≈ 진회색 `#555555`
- WALL ≈ 빨강 `#e74c3c` (선 더 굵게)
- ARC/CIRCLE 회색, TEXT/MTEXT 파란 라벨
- `bbox_mm`이 있으면 `floor_original.png`와 같은 크롭·여백

DXF의 레이어 구조와 시각적으로 대응합니다.

---

## 5. 스크립트·워크플로

| 스크립트 | 역할 |
|----------|------|
| `sample_wall_conditions.py` | 가운데 샘플 2장 → `wall_samples/wall_conditions.json`. 파일이 있으면 다시 만들지 않음 |
| `detect_walls_floor.py` | 한 층 `floor_wall_original` + `floor_wall_common` |
| `lib_walls.py` | 분류·DXF/PNG. 조건은 `wall_conditions.json` |
| `lib_wall_samples.py` | 샘플 창·측정 |
| `detect_walls_tile.py` | 단일 DXF. 사용자가 타일을 명시할 때 |
| `detect_walls_all.py` | 다층 일괄. 사용자가 일괄을 명시할 때 |

이미 `floor_wall_original.*`가 있어도 같은 경로에 덮어씁니다. 호출은 층당 bash 1회입니다. `for` 일괄과 `detect_walls_all`은 대용량에서 `TimeoutExpired`가 납니다.

### 호출 예

```bash
SCRIPTS="$WORKING_DIR/skills/drawing-walldetector/scripts"
ART="$ARTIFACTS_DIR/<drawing_id>"

python3 "$SCRIPTS/detect_walls_floor.py" --artifacts "$ART" --floor 12F
```

로컬:

```bash
SCRIPTS=/Users/ksdyb/Documents/src/agent-skills/application/skills/drawing-walldetector/scripts
ART=/Users/ksdyb/Documents/src/agent-skills/application/.session_storage/lge/artifacts/sk_yongin_jiwon
```

---

## 6. 파라미터 조정

과검출·미검출 시 `detect_walls_floor.py` CLI를 조정합니다:

| 인자 | 기본 | 조정 방향 |
|------|------|-----------|
| `--min-len-mm` | 500 | 올리기 → 짧은 가구 변↓ / 내리기 → 짧은 벽↑ |
| `--thick-min-mm` | 30 | 얇은 칸막이·마감선에 맞춤 |
| `--thick-max-mm` | 420 | 두꺼운 코어 벽이 빠지면 올리기 |
| `--no-png` | off | DXF만 빠르게 |
| `--floor-px-width` | 4000 | PNG 가로 (meta에 png_size 있으면 자동 맞춤) |

---

## 7. 한계

- **단일선**으로만 그린 벽, **비스듬한(사선) 벽**은 거의 잡지 못합니다.
- 이중선이 확정 조건(간벽 120–180 mm, 연속 면 2.2 m·겹침 80% 등) 밖이면 미검출됩니다.
- ≤3.5 m 닫힌 박스로 그린 **작은 실**(화장실 칸 등)이 가구와 함께 제외될 수 있습니다.
- 레이어/블록이 표준화된 DXF면 휴리스틱보다 레이어 규칙이 낫습니다.

---

## 8. 한 줄 요약

**`LINE`/`LWPOLYLINE`의 축정렬 이중선을 `wall_conditions.json` 조건으로 확정하고, 원본은 그대로 둔 채 `WALL`·`DOOR`·`WINDOW`·`COLUMN`·`BASE`로 나눈 `floor_wall_original`을 저장합니다. common만 적용한 검수용이 `floor_wall_common.png`입니다.**
