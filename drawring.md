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

## 도면 자르기

CAD/DXF 평면도는 층·블록 단위로도 Vision·벽체 인식에 너무 큰 경우가 많아, **분석 가능한 타일(≤ 60×60 m)** 로 나눈다.

### 스킬: `drawing-devider`

| 항목 | 내용 |
|------|------|
| 경로 | `agent-skills/application/skills/drawing-devider/` |
| 트리거 | 도면 분할, drawing divider, floor tile, `extract_2d`, 평면도 자르기 |
| 미리보기 | `floors/<F>/floor_original.png`만 사용 (`floor_overview.*` / `floor_*_2d.png` **생성·사용 금지**) |

#### 워크플로

1. **기존 폴더 게이트** — `$ARTIFACTS_DIR/<drawing_id>/`가 있으면 계속/중단 확인 (허락 전 스크립트 금지)
2. **전처리** — `extract_2d.py` → `floors/<F>/floor_original.dxf` (층당 bash 1회)
3. **구조 파악** — `structure.md` / `structure.json`
4. **분할 계획** — `split_plan.md` / `split_plan.json` (타일 ≤ 60×60 m)
5. **파일럿 1층** — `split_floor.py` → parts (`--source original`), **사용자 허락**
6. **나머지 층** — 층당 bash 1회 (일괄 `for` / `--floor all` 금지)
7. **작업 로그** — `work_log.md` 전달

#### 스크립트

| 스크립트 | 역할 |
|----------|------|
| `scripts/analyze_drawing.py` | 원본/`floor_original` 실측 |
| `scripts/plan_split.py` | max 60 m · overlap 반영 격자 계획 |
| `scripts/split_floor.py` | parts(floor_original) |
| `scripts/extract_2d.py` | 원본 → `floors/<F>/floor_original.dxf` (+ `.png`) |
| `scripts/lib_render.py` | 치수·고해상도 렌더 라이브러리 (`split_floor`가 import) |
| `scripts/lib_split.py` | 공통 유틸 (primary 클러스터 등) |

#### 산출물 (중요)

| 산출 | 상태 |
|------|------|
| `floors/<F>/floor_original.dxf` (+ `.png`) | **공식 층 스냅샷** — parts 입력 · 미리보기 (modelspace 실명 TEXT 포함) |
| `floor_overview.*` | **제거됨** — 더 이상 생성·사용하지 않음 |
| `floor_*_clean.dxf` | **제거됨** — 더 이상 생성·사용하지 않음 |

#### 산출물 경로 (사용자 `$ARTIFACTS_DIR`)

```text
$ARTIFACTS_DIR/<drawing_id>/
  ├── structure.md / structure.json
  ├── split_plan.md / split_plan.json
  ├── work_log.md
  └── floors/<FLOOR>/
      ├── floor_original.dxf / .png / _meta.json
      ├── floor_wall_original.dxf / .png / _meta.json
      ├── floor_meta.json
      ├── floor_parts_index.json
      └── parts/R0C0.png|.dxf|_meta.json|_geom.json …
```

#### 제약

- 타일 한 변 **60 m 이내** (overlap 포함 시에도). strip-only로 60 m 초과 금지.
- overlap 기본 1 m → 유효 step ≤ 28 m 기준으로 nx/ny 산정.
- 잘린 도면에 **치수(전체·그리드)** 필수.
- 다층이면 **파일럿 1층 → 승인 → 나머지**, 모두 **층당 bash 1회**.
- 이중 레이아웃(좌·우 복사) → `find_primary_line_bbox`로 LINE 많은 쪽만.

#### 스모크 (지원동 5F–12F)

| 항목 | 내용 |
|------|------|
| drawing_id | `sk_yongin_jiwon` |
| 원본 | SK용인하이닉스_지원동 평면도_241014.dxf (277 MB) |
| 경로 | `$ARTIFACTS_DIR/sk_yongin_jiwon/` |
| max_tile_m | **60**, overlap 1 m |
| 상태 | **5F–12F 분할 완료** — 총 **107 타일** (`work_log.md`) |

| 층 | 타일 | 층 | 타일 |
|----|------|----|------|
| 5F | 21 | 9F | 10 |
| 6F | 14 | 10F | 12 |
| 7F | 14 | 11F | 10 |
| 8F | 14 | 12F | 12 |

---

## 벽 검출

`drawing-devider`가 만든 타일·층 `floor_original` DXF에서 벽을 찾아 **빨간색(`WALL` 레이어, ACI 1)** 으로 표시한 DXF/PNG를 만든다.

### 스킬: `drawing-walldetector`

| 항목 | 내용 |
|------|------|
| 경로 | `agent-skills/application/skills/drawing-walldetector/` |
| 입력 | `floors/<F>/floor_original.dxf` + `parts/*.dxf` (`floor_parts_index.json`) |
| 출력 | `floors/<F>/floor_wall_original.*` + `floors/<F>/walls/R*C*_walls.*` |
| 구현 | `scripts/lib_walls.py` (평행 이중선 휴리스틱) |
| 게이트 | 기존 `walls/` 확인 → 파일럿 1층 → 컨펌 → **층당** `detect_walls_floor` |

#### 스크립트

| 스크립트 | 역할 |
|----------|------|
| `scripts/detect_walls_floor.py` | **한 층** 타일 + `floor_wall_original` 검출 |
| `scripts/detect_walls_tile.py` | 단일 타일 |
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
6. **제외** — ARC/CIRCLE, 닫힌 사각 ≤ 3.5 m(기둥·가구), 짧은 다변 폴리(조경·해칭), 평행선 ≥4(계단).
7. **출력** — `BASE`(회색 8) + `WALL`(빨강 1). PNG 동일 색 구분.

요약하면, 

- DXF의 LINE과 LWPOLYLINE에서 수평/수직 선분만 뽑아, 
- 두 선이 50~420mm 간격으로 나란히 붙어 있으면 벽으로 판정합니다. 
- 기둥·가구(≤3.5 m 닫힌 사각), 조경/해칭(짧은 다변), 계단 해칭, 문/설비(ARC·CIRCLE)는 자동으로 제외됩니다.

#### 파라미터

| 이름 | 기본 | 의미 |
|------|------|------|
| `min_len_mm` | 500 | 후보 세그먼트 최소 길이 |
| `thick_min_mm` | 50 | 이중선 최소 간격 |
| `thick_max_mm` | 420 | 이중선 최대 간격 |
| `entity_wall_ratio` | 0.75 | 폴리라인 통째 벽 승격 비율 |
| `furniture_box_max_mm` | 3500 | 이 이하 닫힌 박스 = 비가중(가구·기둥) |
| `short_pair_max_mm` | 2800 | 양쪽 < 이 값인 이중선 쌍 = 가구 변 제외 |

#### 워크플로

1. `floors/<F>/walls/` 존재 시 계속/중단 확인
2. 층 `floor_original.png`로 구조·타일 위치 파악
3. 파일럿: `detect_walls_floor.py --floor <PILOT>`
4. `*_walls.png`에서 빨강=벽 검수 후 컨펌
5. 나머지 층도 **층당** `detect_walls_floor` (기본으로 `detect_walls_all` 금지)

#### 산출물

```text
$ARTIFACTS_DIR/<drawing_id>/
  floors/<F>/
    floor_wall_original.dxf / .png / _meta.json
    walls/
      walls_index.json
      R0C0_walls.dxf / .png / _meta.json
  walls_all_index.json          # 선택(다층 요약)
```

#### 한계

- 단일선으로만 그린 벽·비스듬한 벽은 약함
- 이중선 간격이 두께 대역 밖이면 놓침
- ≤3.5 m 닫힌 박스로 그린 **작은 실(화장실 칸 등)** 도 가구와 함께 제외될 수 있음
- 레이어 표준화가 되면 휴리스틱보다 레이어/블록 규칙을 우선하는 편이 낫다

#### 스모크 (지원동)

- devider 분할: **완료** (107 타일)
- walldetector: **미실행** — `floors/*/walls/` 없음. 파일럿 층부터 `detect_walls_floor` 진행 예정

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

