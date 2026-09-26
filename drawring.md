# 도면 분석 - SK용인하이닉스_지원동_평면도_241014.dxf

CAD/DXF 도면 파이프라인 정리.  

## 공통 경로 규약

| 항목 | 값 |
|------|-----|
| 스킬 루트 | `agent-skills/application/skills/` |
| 스크립트 | **스킬 폴더 `scripts/` 절대경로** (`SCRIPTS`). Runtime만 `$WORKING_DIR/skills/<skill>/scripts` |
| 산출물 | `$ARTIFACTS_DIR/<drawing_id>/` (비어 있으면 cwd·세션 artifacts 절대경로) |
| cwd | bash/`execute_code`는 `artifacts/` — 상대 `skills/`·`scripts/` **금지** |

**주의:** `$WORKING_DIR`·`$ARTIFACTS_DIR`가 비면 `"$WORKING_DIR/skills/..."` → `/skills/...` 로 깨진다. 빈 env를 경로에 붙이지 말 것.

로컬 예:

```bash
SCRIPTS=/Users/ksdyb/Documents/src/agent-skills/application/skills/drawing-devider/scripts
ARTIFACTS_DIR=/Users/ksdyb/Documents/src/agent-skills/application/.session_storage/lge/artifacts
ART=$ARTIFACTS_DIR/sk_yongin_jiwon
```

---

## 도면 추출 (층 단위)

CAD/DXF 평면도는 **층별 `floor_original` DXF/PNG** 로 추출한다.  
고해상도 렌더로 층 하나면 Vision·벽체 인식이 가능하므로, **parts 타일 분할은 기본 워크플로에서 하지 않는다** (추후 필요 시 레거시 스크립트만).

### 스킬: `drawing-devider`

| 항목 | 내용 |
|------|------|
| 경로 | `agent-skills/application/skills/drawing-devider/` |
| 트리거 | 도면 추출, extract_2d, 층별 평면도, drawing divider |
| 미리보기 | `floors/<F>/floor_original.png`만 사용 (`floor_overview.*` / `floor_*_2d.png` **생성·사용 금지**) |

#### 워크플로

1. **기존 폴더 게이트** — `$ARTIFACTS_DIR/<drawing_id>/`가 있으면 계속/중단 확인 (허락 전 스크립트 금지)
2. **파일럿 1층** — `extract_2d.py` → `floors/<F>/floor_original.dxf` (+ `.png`), **사용자 허락**
3. **나머지 층** — 층당 bash 1회 (일괄 `for` / `--floor all` 금지)
4. **구조 파악** — `structure.md` / `structure.json`
5. **작업 로그** — `work_log.md` 전달

#### 스크립트

| 스크립트 | 역할 |
|----------|------|
| `scripts/extract_2d.py` | 원본 → `floors/<F>/floor_original.dxf` (+ `.png`) |
| `scripts/analyze_drawing.py` | 원본/`floor_original` 실측 |
| `scripts/lib_render.py` | 치수·고해상도 렌더 라이브러리 |
| `scripts/lib_split.py` | 공통 유틸 (primary 클러스터 등) |
| `scripts/plan_split.py` | **(레거시·선택)** 타일 격자 계획 |
| `scripts/split_floor.py` | **(레거시·선택)** parts 자르기 |

#### 산출물 (중요)

| 산출 | 상태 |
|------|------|
| `floors/<F>/floor_original.dxf` (+ `.png`) | **공식 층 스냅샷** · 미리보기 · 후속 스킬 입력 |
| `parts/` · `floor_parts_index.json` · `split_plan.*` | **기본 미생성** (레거시·선택) |
| `floor_overview.*` | **제거됨** — 더 이상 생성·사용하지 않음 |
| `floor_*_clean.dxf` | **제거됨** — 더 이상 생성·사용하지 않음 |

#### 산출물 경로 (사용자 `$ARTIFACTS_DIR`)

```text
$ARTIFACTS_DIR/<drawing_id>/
  ├── structure.md / structure.json
  ├── work_log.md
  └── floors/<FLOOR>/
      ├── floor_original.dxf / .png / _meta.json
      ├── floor_wall_original.dxf / .png / _meta.json
      └── floor_meta.json
```

#### 제약

- 다층이면 **파일럿 1층 → 승인 → 나머지**, 모두 **층당 bash 1회**.
- 이중 레이아웃(좌·우 복사) → `find_primary_line_bbox`로 LINE 많은 쪽만.

#### 스모크 (지원동 5F–12F)

| 항목 | 내용 |
|------|------|
| drawing_id | `sk_yongin_jiwon` |
| 원본 | SK용인하이닉스_지원동 평면도_241014.dxf (277 MB) |
| 경로 | `$ARTIFACTS_DIR/sk_yongin_jiwon/` |
| 상태 | **층 단위 `floor_original`** (parts 타일 분할은 기본 제외) |

---

## 벽 검출

`drawing-devider`가 만든 층 `floor_original` DXF에서 벽을 찾아 **빨간색(`WALL` 레이어, ACI 1)** 으로 표시한 DXF/PNG를 만든다.

### 스킬: `drawing-walldetector`

| 항목 | 내용 |
|------|------|
| 경로 | `agent-skills/application/skills/drawing-walldetector/` |
| 입력 | `floors/<F>/floor_original.dxf` |
| 출력 | `floors/<F>/floor_wall_original.*` |
| 구현 | `scripts/lib_walls.py` (평행 이중선 휴리스틱) |
| 게이트 | 기존 `floor_wall_original` 확인 → 파일럿 1층 → 컨펌 → **층당** `detect_walls_floor` |

#### 스크립트

| 스크립트 | 역할 |
|----------|------|
| `scripts/detect_walls_floor.py` | **한 층** `floor_wall_original` 검출 |
| `scripts/detect_walls_tile.py` | 단일 DXF (레거시·선택) |
| `scripts/detect_walls_all.py` | 다층 일괄 (**사용자 명시 시에만**) |
| `scripts/lib_walls.py` | 분류·DXF/PNG |

호출 예:

```bash
SCRIPTS="$WORKING_DIR/skills/drawing-walldetector/scripts"
ART="$ARTIFACTS_DIR/sk_yongin_jiwon"
python3 "$SCRIPTS/detect_walls_floor.py" --artifacts "$ART" --floor 12F
```

#### 왜 이 방식인가

지원동 클린 DXF는 벽·가구·설비가 거의 전부 **`0arch` 한 레이어**에 있어 레이어 이름으로는 벽을 나눌 수 없다.  
Vision 모델 대신, CAD 평면도의 관례인 **이중선(벽 양면)** 기하로 판정한다.

#### 정보는 어디서 오는가

**① DXF에서 읽는 것 (기하·엔티티 타입)**

| 타입 | DXF에서 얻는 값 | 용도 |
|------|-----------------|------|
| `LINE` | 시작·끝점 | 벽 후보 세그먼트 |
| `LWPOLYLINE` | 꼭짓점 목록 → 변마다 세그먼트 | 벽 후보 |
| `ARC` / `CIRCLE` | 중심·반지름·각도 | 벽에서 **제외** (문 스윙·설비) |

**② DXF에 없고 코드에 둔 것 (판정 규칙)**

| 규칙 | 근거 |
|------|------|
| 이중선 ≈ 벽 | CAD 평면도 관례 |
| 간격 50–420 mm | `thick_min/max_mm` 기본값 |
| 길이 ≥ 500 mm | `min_len_mm` |
| `ARC` ≠ 벽 | 문 스윙 관례 |
| 닫힌 사각 ≤ 3.5 m ≈ 기둥·가구 | `furniture_box_max_mm` (책상·캐비닛 이중윤곽 제외) |
| 짧은 이중선 쌍(< 2.8 m) 제외 | 가구·설비 변 (긴 벽 런만 유지) |
| 짧은 다변 폴리라인 ≈ 조경·해칭 | 변 ≥10 · 평균 길이 < 1.5 m |
| 평행선 ≥4 ≈ 계단 | 트레드·해칭 제외 |
| 폴리라인 통째 승격 ≥ 75% | `entity_wall_ratio` (부분 매칭 전체도색 방지) |

#### 검출 방법 (평행 이중선)

```text
LINE / LWPOLYLINE
  → 세그먼트 분해
  → 축정렬(H/V) · 길이 ≥ 500 mm 후보
  → 직교 간격이 벽 두께(50–420 mm)인 평행 쌍 + 길이 overlap
  → 벽으로 마킹 → WALL(빨강) / BASE(회색) DXF
```

1. **세그먼트 추출** — `LINE`, `LWPOLYLINE`만. 폴리라인은 변마다 한 세그먼트.
2. **축정렬 필터** — 수평·수직(±8°)만. 사선 제외.
3. **최소 길이** — 기본 `min_len_mm=500`.
4. **평행 이중선 매칭** — H선은 Y간격, V선은 X간격이 **50–420 mm**이고 overlap이 충분하면 벽.
5. **엔티티 승격** — 폴리라인은 벽 비율 ≥ **75%**일 때만 통째 WALL. 미만은 빨간 세그먼트만.
6. **제외** — ARC/CIRCLE, 닫힌 사각 ≤ 3.5 m(가구만; **H-Beam 기둥은 WALL**), 짧은 다변 폴리(조경·해칭), 평행선 ≥4(계단).
7. **출력** — `BASE`(회색 8) + `WALL`(빨강 1). PNG 동일 색 구분.

요약하면, 

- DXF의 LINE과 LWPOLYLINE에서 수평/수직 선분만 뽑아, 
- 두 선이 50~420mm 간격으로 나란히 붙어 있으면 벽으로 판정합니다. 
- 가구(≤3.5 m 닫힌 사각), 조경/해칭(짧은 다변), 계단 해칭, 문/설비(ARC·CIRCLE)는 자동으로 제외됩니다. **H-Beam 기둥(중첩 정사각)은 WALL.**

#### 파라미터

| 이름 | 기본 | 의미 |
|------|------|------|
| `min_len_mm` | 500 | 후보 세그먼트 최소 길이 |
| `thick_min_mm` | 50 | 이중선 최소 간격 |
| `thick_max_mm` | 420 | 이중선 최대 간격 |
| `entity_wall_ratio` | 0.75 | 폴리라인 통째 벽 승격 비율 |
| `furniture_box_max_mm` | 3500 | 이 이하 닫힌 박스 = 비가중(가구; H-Beam 기둥은 예외로 WALL) |
| `short_pair_max_mm` | 2800 | 양쪽 < 이 값인 이중선 쌍 = 가구 변 제외 |

#### 워크플로

1. `floors/<F>/floor_wall_original.*` 존재 시 계속/중단 확인
2. 층 `floor_original.png`로 구조 파악
3. 파일럿: `detect_walls_floor.py --floor <PILOT>`
4. `floor_wall_original.png`에서 빨강=벽 검수 후 컨펌
5. 나머지 층도 **층당** `detect_walls_floor` (기본으로 `detect_walls_all` 금지)

#### 산출물

```text
$ARTIFACTS_DIR/<drawing_id>/
  floors/<F>/
    floor_wall_original.dxf / .png / _meta.json
    floor_wall_index.json
  walls_all_index.json          # 선택(다층 요약)
```

#### 한계

- 단일선으로만 그린 벽·비스듬한 벽은 약함
- 이중선 간격이 두께 대역 밖이면 놓침
- ≤3.5 m 닫힌 박스로 그린 **작은 실(화장실 칸 등)** 도 가구와 함께 제외될 수 있음
- 레이어 표준화가 되면 휴리스틱보다 레이어/블록 규칙을 우선하는 편이 낫다

#### 스모크 (지원동)

- devider: **층 단위 `floor_original`** (parts 타일 분할 기본 제외)
- walldetector: **층 단위 `floor_wall_original`** — 파일럿 층부터 `detect_walls_floor` 진행

---

## Validation

`drawing-walldetector`의 빨간 WALL을 Vision(+기하)으로 검수해 가구 오검출을 줄이고,
미검출 구조 벽을 승격한 뒤 **`floor_wall_validated.*`** 로 저장한다.  
입력 `floor_wall_original.*` 은 덮어쓰지 않는다.

### 스킬: `drawing-llmvalidator`

| 항목 | 내용 |
|------|------|
| 경로 | `agent-skills/application/skills/drawing-llmvalidator/` |
| 입력 | `floors/<F>/floor_wall_original.{dxf,png}` + `llm_review/review.json` |
| 출력 | `floors/<F>/floor_wall_validated.{dxf,png,_meta.json}` |
| 구현 | `scripts/lib_llm_correct.py` (demote/promote) · `correct_walls_floor.py` |
| 게이트 | 파일럿 1층 → 컨펌 → **층당** 1회 (`--short-demote` 기본 OFF) |

#### 스크립트

| 스크립트 | 역할 |
|----------|------|
| `scripts/prepare_review.py` | Vision용 크롭/`floor_wall_full.png` |
| `scripts/correct_walls_floor.py` | demote/promote → `floor_wall_validated.*` |
| `scripts/lib_llm_correct.py` | 갭·복도·연속방 승격, demote 보호 |

```bash
SCRIPTS="$WORKING_DIR/skills/drawing-llmvalidator/scripts"
ART="$ARTIFACTS_DIR/sk_yongin_jiwon"
python3 "$SCRIPTS/prepare_review.py" --artifacts "$ART" --floor 12F
# Vision → llm_review/review.json 작성 후
python3 "$SCRIPTS/correct_walls_floor.py" --artifacts "$ART" --floor 12F
```

#### 워크플로

1. `floor_wall_original.png` (또는 크롭)로 Vision 검수
2. `llm_review/review.json` — `demote_bboxes` / `promote_bboxes` (mm)
3. `correct_walls_floor.py` — **promote 후보를 demote 전에 확정** 후 적용
4. `floor_wall_validated.png` 검수 → 다음 층

#### Vision 핵심 규칙

- **복도 양측 장축선은 기본적으로 벽** — demote 금지, 회색이면 promote
- **복도 인접 벽의 문 개구는 양옆(좌·우) 모두 벽** — 한쪽만 빨강이면 반대쪽 promote
- **계단실(UP/DN)은 문 제외 외곽이 벽** — 트레드·중심 난간은 비벽, 외곽 이중선은 promote·protect
- **엘리베이터 입구 잼·뱅크 주위는 벽** — 로비 향 문면 잼, 전고 문/후면/중앙은 비벽
- **H-Beam 기둥(정사각+`_`)은 벽** — 심볼만 WALL. 외부 연결·직사각 슬리브는 승격하지 않음. 밀집 격자는 제외
- **연속된 동일 방 열**은 벽 처리가 같아야 함 — 한 칸만 회색이면 promote
- **강당·오픈홀 중앙에는 벽이 없음** — 객석 통로·보이드(X)·「강당」라벨을 관통하는 장축은 demote
- `demote_bboxes`는 **가구 한 덩어리**만 (한 변 ≤ 25 m). 층 절반·복도 전체 금지
- `--short-demote`는 문 사이 복도 조각을 지울 수 있어 **기본 OFF**

#### 산출물

```text
$ARTIFACTS_DIR/<drawing_id>/floors/<F>/
  floor_wall_original.*          # walldetector (불변)
  floor_wall_validated.*         # llmvalidator 출력
  diff_original_vs_validated.png # 초록=promote · 파랑=demote · 어두운빨강=kept
  llm_review/
    review.json / corrections.json
    floor_wall_full.png / crop_*.png …
```

### 예외·실패 사례와 대응 (지원동 12F)

walldetector 휴리스틱 + Vision bbox가 겹치면 “구조적으로 이상해 보이는” 결과가 난다.  
아래에 **언제 생기는지**와 **어떻게 막았는지**를 정리한다.

#### 1) 복도 양측 벽이 지워짐

| | 내용 |
|--|------|
| **증상** | 복도를 따라가는 긴 수평/수직 경계가 회색이 되거나, validated에서 끊김 |
| **원인** | `--short-demote`가 문 개구로 잘린 짧은 WALL 조각을 가구로 보고 일괄 삭제. Vision이 복도 경계를 demote에 넣는 경우도 있음 |
| **대응** | (1) SKILL에 「복도 양측 = 벽」 명시 (2) `protect_corridor_wall_entities` — 긴·이중선 WALL은 demote 제외 (3) `promote_corridor_walls` — 긴 이중선 BASE 자동 승격 (4) `--short-demote` 기본 OFF |

#### 2) 연속된 방 중 한 칸만 벽이 다름

| | 내용 |
|--|------|
| **증상** | 같은 줄·같은 형태 방인데 한 방만 외곽/칸막이가 회색(BASE), 나머지는 빨강(WALL) |
| **원인 A (walldetector)** | 방 외곽이 닫힌 `LWPOLYLINE`인데, 내부 책상 이중선이 많아 해당 변만 평행쌍 매칭 실패 → `entity_wall_ratio < 0.75`로 폴리 전체가 BASE, 변 세그먼트도 WALL LINE 미생성. 이웃 방은 LINE으로 잡혀 WALL |
| **원인 B (llmvalidator)** | `review.json`의 `demote_bboxes`가 **층 절반**처럼 과도하게 큼 → bbox demote가 이웃 WALL까지 삭제 → 이후 promote가 “양쪽 WALL 사이 갭” 맥락을 잃어 한 칸만 회색으로 고착 |
| **대응** | (1) **promote를 demote 전에 계산**해 이웃 WALL이 지워지기 전에 후보 확정 (2) `promote_collinear_room_walls` — 한쪽만 맞닿은 연속 방 칸도 승격 (3) `demote_bboxes` 한 변 > 25 m 또는 면적 과대면 **skip + 경고** (4) SKILL에 연속 방 일관성·bbox 크기 제한 명시 |

#### 3) `review.json` bbox 키 오류 (`KeyError: xmin`)

| | 내용 |
|--|------|
| **증상** | `correct_walls_floor` 실행 중 `demote_in_bboxes`에서 `KeyError: 'xmin'` |
| **원인** | Vision이 `label`만 있거나 `x0/y0`, 중첩 `bbox_mm` 등 다른 키로 bbox를 씀 |
| **대응** | `normalize_bbox` / `normalize_bbox_list` — 여러 형식 허용, 키 없으면 해당 항목만 skip |

#### 4) 강당 한가운데에 벽이 생김

| | 내용 |
|--|------|
| **증상** | 「중강당」등 오픈홀 중앙 통로·보이드(X)를 세로/가로 빨간 WALL이 관통 |
| **원인** | walldetector가 긴 단일선을 벽으로 잡고, `protect_corridor`가 길이≥6 m라 demote 보호. corridor/collinear promote가 홀 내부 BASE도 승격 |
| **대응** | (1) TEXT「강당」라벨로 홀 bbox 추정 (2) `demote_open_hall_center_walls` — 가장자리 제외 내부 장축 demote, protect보다 우선 (3) `filter_promote_away_from_open_halls` — 홀 내부 promote 차단 (4) AHU·조정실 등 설비 라벨은 제외 |

#### 5) 계단실 외곽이 회색으로 남음

| | 내용 |
|--|------|
| **증상** | UP/DN 계단 코어의 좌·우·상·하 경계가 BASE(회색)이고, 문만 비어야 할 외곽이 불완전 |
| **원인** | 계단 트레드 평행다발 demote·짧은 이중선이 복도 promote min_len(6 m)에 못 미침 |
| **대응** | (1) SKILL에 「계단 = 문 제외 외곽 벽」명시 (2) `promote_stair_enclosure_walls` — UP/DN 클러스터 외곽 이중선 승격 (3) `protect_stair_enclosure_entities` — 외곽 WALL demote 금지 (4) 중심 난간·트레드는 승격하지 않음 |

#### 6) 엘리베이터 입구 측면 잼이 빠짐

| | 내용 |
|--|------|
| **증상** | 로비를 향한 문 개구 위·아래 짧은 수직(화살표)이 회색 |
| **원인** | 문 방향을 뱅크 바깥/중앙으로 오인하거나 모서리·외곽만 승격 |
| **대응** | (1) 로비 간격(5–9.5 m) 열 쌍으로 문 방향 결정 (2) `door_jamb` 짧은 수직 promote (3) 주위 외곽 유지, 전고 문/후면/중앙 demote |

#### 7) H-Beam 기둥이 회색이거나, 설비 격자가 빨강

| | 내용 |
|--|------|
| **증상** | 진짜 기둥이 회색이거나, 작은 정사각 밀집 격자가 WALL(빨강) |
| **원인** | `_` 없는 사각 오인; 외부 연결선까지 승격하면 가구 이중선이 WALL 됨 |
| **대응** | (1) 정사각+`_` 심볼만 `promote_hbeam_columns` (2) 외부 연결 승격 없음 (3) 소형 밀집 제외 (4) 가구 demote에서 기둥 exclude |

#### 처리 순서 (현재 `apply_corrections`)

```text
floor_wall_original
  → promote 후보 확정 (갭 / 복도 / 연속방 / 계단실 / 엘리베이터 / review promote_bboxes)
       ※ 강당·오픈홀 내부 장축은 promote 후보에서 제외
  → demote (short·pack·dense·box·review demote_bboxes
            · open_hall center)
       ※ 복도·긴 이중선·계단실·엘리베이터·H-Beam 기둥은 protect 로 demote 제외
       ※ 오픈홀 중앙 오검출은 protect보다 우선 demote
       ※ 과대 demote_bbox 는 load 시 skip
  → promote LINE 추가
  → H-Beam 기둥 BASE→WALL
  → floor_wall_validated
```

#### 스모크 (12F)

| 항목 | 값 |
|------|-----|
| 과도 demote_bbox 제거 후 | demoted↓, wall_after↑ |
| 연속방 이상 구간 예 | `x≈86900–91800 @ y≈887966` → validated에 WALL LINE 승격 확인 |

---

## 가구·물체 블록 (지원동)

원본 `SK용인하이닉스_지원동_평면도_241014.dxf`에서 가구·의자·설비 등은 **레이어가 아니라 블록(INSERT)** 으로 표현된다.  
층 평면 블록(`XA-S-5F 평면` 등) 안에 `$0$` 접미사로 중첩되며, 기하가 거의 전부 **`0arch`** 한 레이어에 몰려 있어 레이어명으로는 벽을 나눌 수 없다.

| 항목 | 값 |
|------|-----|
| 표현 방식 | `INSERT` → 블록 정의 (중첩) |
| 식별 단서 | 블록명 키워드 (`지원동_가구(...)`, `chair_...`, `화)...` 등) |
| 필터 위치 | `extract_2d.py` / `lib_render.py` 의 `FURNITURE_KEYWORDS` |
| 평면 블록 안 중첩 INSERT | 가구 키워드 매칭 약 **6,200+** (고유 short name ~84) |

### `FURNITURE_KEYWORDS` (파이프라인)

벽체 오염 완화용. **가구만이 아니라** 문·입면·RAIN·업다운 등 비구조도 포함.

```text
가구, chair, 피트니스, 화), DOOR, DOR_, 도어, 자동문,
락커, 라커, 샤워, 신발, 러닝, 파우더, 큐비클, 대변기,
소변, 객석, 좌석, 모바일, 회의, 업다운, 절취, RAIN, rain,
입면, 슬라이딩, 접견
```

### 블록명 목록 (short name = `$0$` 뒤)

#### 사무·좌석 가구

| 블록명 | 비고 |
|--------|------|
| `지원동_가구(업무좌석)` / `(업무좌석END)` / `(업무좌석TL)` | 업무용 책상+의자 (사용 빈도 높음) |
| `지원동_가구(모바일)` | 모바일/가변 좌석 |
| `지원동_가구(리더1)` / `(리더2)` | 리더 가구 |
| `지원동_가구(임원1)` / `(임원2)` | 임원 가구 |
| `chair_190820` | 의자 (5F·6F) |

#### 회의·접견 가구

| 블록명 | 비고 |
|--------|------|
| `지원동_가구(6인회의)` / `(8인회의)` / `(중회의)` / `(대회의)` / `(리더대회의)` | 회의 테이블·의자 |
| `접견가구 TYPE2` / `접견실 가구 TYPE3` / `접견홀 가구` | 접견실 (5F) |

#### 소파·OA·기타 가구

| 블록명 | 비고 |
|--------|------|
| `지원동_가구(소파1)` | 소파 |
| `지원동_가구(OA1)` | OA 가구 |
| `지원동_가구(칠판)` | 칠판 |
| `지원동_설비통합(가구1~4)` | 설비 통합형 가구 (11F) |

#### 피트니스·샤워·락커 (주로 5F)

| 블록명 | 비고 |
|--------|------|
| `지원동_피트니스(의자)` / `(러닝머신)` / `(라커)` / `(신발장)` / `(파우더)` | 피트니스 기구·부속 |
| `지원동_피트니스(가구2/4/5/6/7)` | 기타 피트니스 가구 |
| `지원동_피트니스(5F)` | 피트니스 영역 묶음 |
| `지원동_가구(기준층락커)` | 락커 — 중첩 INSERT 최다 수준 |
| `지원동_5F(샤워부스)` / `지원동_남자샤워실(5F)` | 샤워 |

#### 위생기구 (화장실)

| 블록명 | 비고 |
|--------|------|
| `화)대변기` / `화)소변기` / `화)세면기` | 위생기구 |
| `화)큐비클01` / `화)큐비클1(왼쪽 끝)` / `(오른쪽끝)` | 대변 큐비클 |
| `화)소변큐비클` / `…900` / `(왼쪽끝)` / `(오른쪽끝)` | 소변 큐비클 |
| `대변기_B` | 5F |

#### 객석·식당 좌석

| 블록명 | 비고 |
|--------|------|
| `소강당 객석_21석` / `_21석(장애인추가)` / `_23석` | 소강당 (6F) |
| `장애인좌석` | 5F·6F |
| `지원동_좌석배치(6F식당)` | 식당 |
| `지원동_7층 좌측/우측좌석배치(억조)` / `지원동_7층 좌석배치(억조)` | 7F·8F |

#### 문·도어 (키워드 매칭, 가구 아님)

`DOOR_*`, `DOR_*`, `자동문`, `지원동_자동문(...)`, `지원동_방화자동문`, `슬라이딩 도어`, `편개형 도어 1290` 등.

#### 기타 (키워드 매칭, 가구 아님)

`입면라인`, `지원동_입면(돌출바)`, `RAINPLUS` / `rainplus`, `업다운 시작/종점`, `절취선 (에스컬레이터 …)`.

#### 키워드 밖이지만 물체성 있는 블록

| 블록명 | 비고 |
|--------|------|
| `지원동_주방(6F식당)` / `지원동_주방장비배치(20F)` / `지원동_주방용 외조기#2(7F-8F)` | 주방 |
| `지원동_7층 …장비배치(억조)` / `지원동_8층 중앙배치(억조)` | 장비 배치 |
| `원형벤치` (조경 블록 안) | 조경 벤치 |

### 사용 시 주의

- **순수 가구 인벤토리**가 필요하면 `가구`·`chair`·`좌석`·`회의`·`접견`·`피트니스`·`화)` 등만 쓰고, 문·입면·RAIN·업다운·절취는 제외한다.
- `FURNITURE_KEYWORDS`는 clean 변형(레거시)에서 가구를 **건너뛸 때** 쓰던 필터이며, 현재 기본 `original`은 가구를 유지한다.

