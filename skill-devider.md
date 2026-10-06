# drawing-devider — 동작 상세

원본 CAD/DXF(수백 MB, XREF·블록 중심)에서 **층 하나 = `floor_original.dxf` + `.png`** 를 뽑습니다.  
후속 스킬(`drawing-walldetector` 등)의 공식 입력이 됩니다.  
핵심 구현: `application/skills/drawing-devider/scripts/extract_2d.py`

---

## 1. 역할·입출력

| 항목 | 내용 |
|------|------|
| 스킬 경로 | `agent-skills/application/skills/drawing-devider/` |
| 기본 입력 | `$ARTIFACTS_DIR/<input>.dxf` (또는 `--dxf`) |
| 기본 출력 | `$ARTIFACTS_DIR/<drawing_id>/floors/<F>/floor_original.{dxf,png,_meta.json}` |
| 구조 분석 | `structure.md` / `structure.json` (`analyze_drawing.py`) |
| 도면 목록 | `$ARTIFACTS_DIR/drawing_list.json` (해당 도면만 갱신, `source_filename`을 `floors`보다 앞) |
| 추출 요약 | `$ARTIFACTS_DIR/<drawing_id>/extract_summary.json` |
| 작업 로그 | `work_log.md` |
| 진입 스크립트 | `extract_2d.py` (층 1개, 기본 `--variant original`) |
| 공통 유틸 | `lib_split.py`, `lib_render.py`, `lib_sheet.py`, `lib_structure.py` |

**기본 워크플로에서 만들지 않는 것:** `parts/` 타일, `floor_*_clean.dxf`, `floor_overview.*`, `floor_*_2d.png`.  
타일 분할은 사용자가 명시한 경우에만 `plan_split.py` / `split_floor.py`(레거시).

### 산출 경로

```text
$ARTIFACTS_DIR/<drawing_id>/
├── structure.md / .json          # 구조 실측
├── work_log.md                   # 작업 로그
└── floors/<FLOOR>/
    ├── floor_original.dxf        # 층 스냅샷 (후속 스킬 입력)
    ├── floor_original.png        # 검수·미리보기 (치수 포함)
    └── floor_original_meta.json  # bbox·해상도·엔티티 수
```

---

## 2. 왜 층 추출이 필요한가

지원동 등 원본 DXF는 **modelspace에 도면 기하가 거의 없습니다.**  
대부분이 `INSERT`(층 블록)이고, 실제 LINE/폴리라인은 블록 안에 있습니다.

| 관측 (sample: sk_yongin_jiwon) | 값 |
|--------------------------------|-----|
| 원본 크기 | ~277 MB, AC1032 |
| modelspace 엔티티 | ~3,119 (INSERT 377 + TEXT 위주) |
| 블록 정의 | ~1,108 |
| 층 목록 | 5F–12F (`XA-S-{N}F 평면`) |
| 층 추출 후 (예: 12F) | ~183,000 엔티티, DXF ~35 MB |

modelspace만 순회하면 평면도가 안 보이므로, **해당 층 INSERT만 골라 explode** 한 뒤 새 DXF로 씁니다.

---

## 3. 층 INSERT를 어떻게 고르는가

구현: `find_floor_inserts` / `discover_floors` (`extract_2d.py`)

### 3.1 층 목록

modelspace `INSERT` 이름에서 `XA-S-{N}F 평면` 패턴을 찾아 층 목록을 만듭니다.

```text
XA-S-5F 평면 → 5F
XA-S-12F 평면 → 12F
```

### 3.2 층별 블록 (original 기본)

| 역할 | 이름 패턴 | original | clean(레거시) |
|------|-----------|----------|---------------|
| 평면 | `XA-S-{N}F 평면` | ✓ | ✓ |
| 코어 | `XA-S-{N}F 코어` | ✓ | ✓ |
| 기둥 | `XS-S-{N}F 기둥` | ✓ | ✓ |
| P코어 | `XA-P-{N}F 코어` | ✓ (extra) | — |
| 천장 | `XA-C-{N}F 평면` | ✓ (extra) | — |
| 조경 | `XA-G-조경 ({N}F)` | ✓ (extra) | — |

기본 `--variant original`은 **extra 포함 + 가구 유지**입니다.  
`--variant clean`은 평면/코어/기둥만·가구 제외(비권장·레거시).

```122:133:agent-skills/application/skills/drawing-devider/scripts/extract_2d.py
    n = floor[:-1]  # '5F' → '5'
    core_patterns = [
        re.compile(rf"^XA-S-{n}F\s*평면$"),
        re.compile(rf"^XA-S-{n}F\s*코어$"),
        re.compile(rf"^XS-S-{n}F\s*기둥$"),
    ]
    extra_patterns = [
        re.compile(rf"^XA-P-{n}F\s*코어$"),
        re.compile(rf"^XA-C-{n}F\s*평면$"),
        re.compile(rf"^XA-G-조경\s*\({n}F\)$"),
    ]
```

```755:758:agent-skills/application/skills/drawing-devider/scripts/extract_2d.py
    if skip_furniture is None:
        skip_furniture = variant == "clean"
    if include_extra is None:
        include_extra = variant == "original"
```

### 3.3 도곽·층 제목 (블록 이름이 없을 때)

`XA-S-{N}F 평면`이 없으면 추출을 멈추지 않습니다. `lib_sheet.py`가 다음을 사용합니다.

| 신호 | 예 |
|------|----|
| 도곽 | 한 변이 10m 이상인 축정렬 사각형 (닫힌 폴리라인 또는 네 변의 긴 선) |
| 층 제목 | `1층 평면도`, `1층 냉난방 평면도`, `지하1층`, `B1F`, `12F PLAN`, `옥상 평면도` |

`--layout auto`가 블록 이름을 먼저 보고, 없을 때만 도곽으로 넘깁니다.  
목록은 `--list-floors`로 확인하고, 나온 이름을 층마다 추출합니다.

같은 제목이 떨어진 도곽에 반복되면 왼쪽부터 `1F`, `1F_2`입니다. 한 도곽 안에서 층을 나누지 못하면 `sheet_01`, `sheet_02`로 두고 층 이름만 미확정입니다. `sheet_XX`도 그 이름으로 추출합니다.

도곽 방식이고 `--variant original`이며 도곽 안에 건축 레이어(`ARCH`, `*_BG`·`*_CEN` 제외)가 있으면 `lib_structure.collect_structural_sheet`가 벽·창·실명·문 스윙만 남깁니다. 창선은 벽과 같이 둡니다. 파일명은 그대로 `floor_original.*`입니다. 건축 레이어가 없으면 도곽 안 기하를 그대로 둡니다. 지원동처럼 `XA-S` 블록 도면은 이 필터를 타지 않고, 가구 INSERT를 유지합니다.

```25:34:agent-skills/application/skills/drawing-devider/scripts/lib_structure.py
def is_arch_layer(name: str) -> bool:
    """벽·실명에 쓰는 건축 레이어. 등고(BG)·중심선(CEN)은 제외."""
    upper = name.upper()
    if "ARCH" not in upper:
        return False
    if "ARCH_BG" in upper or upper.endswith("_BG"):
        return False
    if "ARCH_CEN" in upper or upper.endswith("_CEN"):
        return False
    return True
```

```946:950:agent-skills/application/skills/drawing-devider/scripts/extract_2d.py
    if variant == "original":
        packed = collect_structural_sheet(doc, clip)
        if packed is not None:
            entities, info = packed
            structural_mode = True
```

---

## 4. 추출 파이프라인 (상세)

호출 흐름:

```text
extract_2d.py --dxf … --floor 12F --drawing-id …
  → discover_floors / find_floor_inserts
  → explode_insert (재귀, max_depth=12)
  → primary bbox 필터 (이중 클러스터 제거)
  → modelspace TEXT/MTEXT 라벨 병합
  → write_clean_dxf → floors/<F>/floor_original.dxf
  → render_floor_original_preview → .png + _meta.json
```

### 4.1 INSERT explode (`explode_insert`)

1. `insert.virtual_entities()`로 블록 내 엔티티를 펼칩니다.
2. 중첩 `INSERT`는 재귀 진입합니다.
3. (clean만) 블록명에 가구 키워드가 있으면 스킵  
   (`가구`, `DOOR`, `락커`, `회의`, … — `FURNITURE_KEYWORDS`).
4. 펼친 `LINE` / `LWPOLYLINE` / `ARC` / `CIRCLE` / `TEXT` … 를 수집합니다.

원본 INSERT는 그대로 두고, **좌표를 읽어 새 R2010 DXF에 복사**합니다 (`write_clean_dxf`).

```160:166:agent-skills/application/skills/drawing-devider/scripts/extract_2d.py
            if e.dxftype() == "INSERT":
                name = e.dxf.name
                if skip_furniture and is_furniture(name):
                    continue
                walk(e, depth + 1)
            else:
                out.append(e)
```

### 4.2 Primary bbox (좌·우 이중 복사본 대응)

일부 층은 동일 평면이 **좌·우에 두 벌** 있습니다. PNG에 도면이 둘 다 보이면 이 때문입니다.

| 함수 | 용도 |
|------|------|
| `find_primary_line_bbox` | LINE 중심 X 정렬 → **최대 gap ≥ 20 m**이면 둘로 쪼개고, LINE 많은 쪽만 채택 |
| `find_primary_floor_bbox` | 코어 LINE bbox를 핵으로, 같은 대역의 기하를 **최대 80 m**까지 확장 (조경·외곽 포함) |

`original`은 `find_primary_floor_bbox(entities, core_bbox=fresh_core)`를 씁니다.  
필터된 엔티티만 DXF에 들어갑니다.

```164:171:agent-skills/application/skills/drawing-devider/scripts/lib_split.py
    gap, idx = gaps[0]
    if gap < 20_000:
        return min(cxs), max(cxs)
    split = (xs[idx] + xs[idx + 1]) / 2
    left = [x for x in cxs if x < split]
    right = [x for x in cxs if x >= split]
    use = right if len(right) >= len(left) else left
```

간격이 20 m 미만이면 한 도면으로 보고, 그 이상이면 LINE이 많은 쪽만 남깁니다. `find_primary_floor_bbox`는 그 핵에서 같은 대역의 기하를 기본 80 m까지 넓힙니다.

### 4.3 실명 라벨

층 INSERT 밖의 modelspace `TEXT`/`MTEXT` 중 bbox 안 것을 추가로 붙입니다 (`collect_modelspace_labels`).  
실명·면적·천장고 라벨이 PNG/후속 검수에 남습니다.

### 4.4 좌표 정렬

기존 `floor_original_meta.json` / `split_plan.json`의 bbox가 있으면  
`origin = fresh_core - plan_bbox` 로 **이전 산출과 좌표계를 맞춤**니다.  
없으면 기본적으로 절대 좌표(`origin=(0,0)`).

### 4.5 DXF 저장 (`write_clean_dxf`)

- `ezdxf.new("R2010")` 새 문서
- 복사 타입: `LINE`, `LWPOLYLINE`, `CIRCLE`, `ARC`, (+ `TEXT`/`MTEXT`)
- 레이어명은 원본 유지 (`0arch` 등)
- 경로: `$ARTIFACTS_DIR/<drawing_id>/floors/<F>/floor_original.dxf`

```334:350:agent-skills/application/skills/drawing-devider/scripts/extract_2d.py
    doc = ezdxf.new("R2010")
    msp = doc.modelspace()
    ...
        if t == "LINE":
            msp.add_line(sh(e.dxf.start), sh(e.dxf.end), dxfattribs={"layer": e.dxf.layer})
```

`sh`는 `origin_shift`가 있으면 좌표를 빼고, 없으면 `(0, 0)`이라 절대 좌표 그대로입니다.

### 4.6 PNG 미리보기 (`render_floor_original_preview`)

1. plan/primary bbox를 핵으로 외벽까지 Y 확장 (`expand_bbox_include_outer_walls`)
2. matplotlib 고해상도 렌더 (기본 `px_width≈14000`)
3. 전체·그리드 치수 오버레이 (`lib_render` — 상·우 베이 치수는 생략)
4. `floor_original.png` + `floor_original_meta.json`

---

## 5. Sample — sk_yongin_jiwon

로컬 경로:

```bash
SCRIPTS=/Users/ksdyb/Documents/src/agent-skills/application/skills/drawing-devider/scripts
ARTIFACTS_DIR=/Users/ksdyb/Documents/src/agent-skills/application/.session_storage/lge/artifacts
ART="$ARTIFACTS_DIR/sk_yongin_jiwon"
```

### 5.1 층 추출 결과 (요약)

| 층 | 도면 크기 (m) | PNG | 엔티티 | DXF |
|----|---------------|-----|--------|-----|
| 5F | 230.9 × 108.3 | 14200×6712 | 246,391 | ~46 MB |
| 6F | 182.9 × 74.2 | 14200×6977 | 155,762 | ~31 MB |
| 7F | 181.4 × 68.8 | 14200×6571 | 260,564 | ~46 MB |
| 8F | 181.4 × 68.8 | 14200×6508 | 237,494 | ~45 MB |
| 9F | 182.5 × 50.7 | 14200×5057 | 175,490 | ~31 MB |
| 10F | 182.5 × 52.6 | 14200×5057 | 173,799 | ~33 MB |
| 11F | 181.9 × 50.7 | 14200×5053 | 182,607 | ~32 MB |
| 12F | 182.5 × 50.7 | 14200×5050 | 183,643 | ~35 MB |

미리보기: `floors/<F>/floor_original.png`  
(예: `floors/12F/floor_original.png`)

### 5.2 12F meta 예시 (`floor_original_meta.json`)

```json
{
  "floor": "12F",
  "role": "floor_original_preview",
  "size_m": { "width": 184.53, "height": 51.41 },
  "n_entities": 183373,
  "n_labels": 511,
  "png_size": { "width": 14200, "height": 5050 },
  "crop": "plan_expand_outer_walls"
}
```

### 5.3 구조 분석 예시 (`structure.md`)

| 항목 | sample 값 |
|------|-----------|
| 원본 | `SK용인하이닉스_지원동_평면도_241014.dxf` |
| CAD | AC1032 |
| modelspace | INSERT·TEXT 위주 → **층 INSERT explode 필요** |
| 리스크 | `0arch` 단일 레이어 → 벽/가구 레이어 분리 불가 |
| 리스크 | 좌·우 이중 클러스터 → primary bbox |

### 5.4 호출 예

기존 `floor_original.*`가 있어도 같은 경로에 덮어씁니다. 층마다 bash 한 번입니다.

```bash
python3 "$SCRIPTS/extract_2d.py" \
  --dxf "$ARTIFACTS_DIR/SK용인하이닉스_지원동 평면도_241014.dxf" \
  --floor 5F \
  --out "$ARTIFACTS_DIR" \
  --drawing-id sk_yongin_jiwon
```

도곽 도면은 먼저 목록만 봅니다.

```bash
python3 "$SCRIPTS/extract_2d.py" \
  --dxf "$ARTIFACTS_DIR/<input>.dxf" \
  --drawing-id <drawing_id> \
  --list-floors
```

### 5.5 구조 분석

```bash
python3 "$SCRIPTS/analyze_drawing.py" \
  --drawing-id sk_yongin_jiwon \
  --out "$ART" \
  --raw-dxf "…/SK용인하이닉스_지원동_평면도_241014.dxf" \
  --floor-dir "$ART/floors"
```

---

## 6. 워크플로·게이트

```text
DXF 입력
  ↓
① extract_2d --floor <각 층>     층당 bash 1회, 있으면 덮어씀
     → floors/<F>/floor_original.{dxf,png,_meta.json}
     → drawing_list.json / extract_summary.json 갱신
  ↓
② analyze_drawing → structure.md / .json
  ↓
③ work_log.md
```

층을 지정하면 그 층만, 아니면 발견 층(`discovered_floors`, 도곽이면 `sheet_XX` 포함) 전체를 순서대로 처리합니다.

| 호출 | 이유 |
|------|------|
| 층당 bash 1회 | 대용량 DXF에서 `for` 일괄·`--floor all`은 `TimeoutExpired` |
| `parts/` / `floor_overview.*` / `floor_structure.*` | 만들지 않음. 미리보기는 `floor_original.png` |

### 경로 bootstrap (로컬)

`$WORKING_DIR` / `$ARTIFACTS_DIR`가 비면 상대경로가 `/skills/...`로 깨집니다.  
**SKILL.md 옆 `scripts/` 절대경로**와 artifacts 절대경로를 직접 지정합니다 (위 sample 경로).

---

## 7. 스크립트 요약

| 스크립트 | 역할 |
|----------|------|
| `extract_2d.py` | 한 층 `floor_original`, `drawing_list.json` 갱신 |
| `analyze_drawing.py` | 구조 실측 → `structure.md` / `.json` |
| `lib_sheet.py` | `XA-S`가 없을 때 도곽·층 제목 |
| `lib_structure.py` | 도곽 + 건축 레이어일 때 벽·창·실명·문 스윙 |
| `lib_split.py` | primary bbox |
| `lib_render.py` | 치수·고해상도 PNG |
| `plan_split.py` / `split_floor.py` | 타일 분할. 사용자가 명시할 때만 |

---

## 8. 후속 연결

| 다음 스킬 | 입력 |
|-----------|------|
| `drawing-walldetector` | `floors/<F>/floor_original.dxf` → `floor_wall_original.*` |
| `drawing-llmvalidator` | `floor_wall_validated.*` |
| `drawing-areasizing` | 추출 → 벽 → 검증 → 실명 면적을 이 순서로 호출 |

원본 277 MB를 walldetector에 직접 넣지 않습니다. **devider가 만든 층 DXF만** 사용합니다.

---

## 9. 한계

- 블록명이 `XA-S-{N}F 평면`이 아니면 도곽(축정렬 테두리)과 층 제목으로 나눕니다 (`lib_sheet.py`, `--layout auto`). 둘 다 없으면 추출하지 않습니다.
- primary gap 임계(20 m) 밖·안쪽 이상치는 잘리거나, 이중 클러스터가 남을 수 있습니다.
- explode 실패 블록은 warn 후 스킵 → 일부 가구/설비가 빠질 수 있습니다.
- `clean` variant는 가구를 의도적으로 빼므로 후속 벽 검출·라벨 검수에는 `original`을 씁니다.

---

## 10. 한 줄 요약

**원본 DXF의 층 INSERT를 explode해 primary 클러스터만 남긴 `floor_original.dxf`(+치수 PNG)를 층당 하나씩 만들고, 그 결과가 walldetector 등 후속 파이프라인의 입력이 됩니다.**
