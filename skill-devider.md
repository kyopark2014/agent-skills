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
| 작업 로그 | `work_log.md` |
| 진입 스크립트 | `extract_2d.py` (층 1개) |
| 공통 유틸 | `lib_split.py` (primary bbox), `lib_render.py` (치수·PNG) |

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

### 4.2 Primary bbox (좌·우 이중 복사본 대응)

일부 층은 동일 평면이 **좌·우에 두 벌** 있습니다. PNG에 도면이 둘 다 보이면 이 때문입니다.

| 함수 | 용도 |
|------|------|
| `find_primary_line_bbox` | LINE 중심 X 정렬 → **최대 gap ≥ 20 m**이면 둘로 쪼개고, LINE 많은 쪽만 채택 |
| `find_primary_floor_bbox` | 코어 LINE bbox를 핵으로, 같은 대역의 기하를 **최대 80 m**까지 확장 (조경·외곽 포함) |

`original`은 `find_primary_floor_bbox(entities, core_bbox=fresh_core)`를 씁니다.  
필터된 엔티티만 DXF에 들어갑니다.

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

### 5.4 호출 예 (파일럿 1층)

```bash
# ⓪ 기존 폴더 확인 — 있으면 계속/중단을 사용자에게 물은 뒤 진행
ART="$ARTIFACTS_DIR/sk_yongin_jiwon"
ls -la "$ART" 2>/dev/null | head

# ① 파일럿 층만
python3 "$SCRIPTS/extract_2d.py" \
  --dxf "$ARTIFACTS_DIR/../upload/SK용인하이닉스_지원동_평면도_241014.dxf" \
  --floor 12F \
  --out "$ARTIFACTS_DIR" \
  --drawing-id sk_yongin_jiwon

# 미리보기: $ART/floors/12F/floor_original.png
# → 여기서 멈추고 승인 후, 다음 층도 bash 1회씩
```

승인 후 다른 층:

```bash
python3 "$SCRIPTS/extract_2d.py" \
  --dxf "…/SK용인하이닉스_지원동_평면도_241014.dxf" \
  --floor 5F \
  --out "$ARTIFACTS_DIR" \
  --drawing-id sk_yongin_jiwon
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
⓪ drawing_id 폴더 존재? → 있으면 계속/중단 확인 (허락 전 실행 금지)
  ↓
① extract_2d --floor <파일럿>
     → floors/<F>/floor_original.{dxf,png,_meta.json}
  ↓
② PNG·크기 보고 → 나머지 층 허락
  ↓
③ 허락 시 다음 층만 extract  (층당 bash 1회)
  ↓
④ analyze_drawing → structure.md / .json
  ↓
⑤ work_log.md 작성·전달
```

### 금지

| 금지 | 이유 |
|------|------|
| `for FLOOR in 5F 6F …` 일괄 | 대용량 DXF에서 `TimeoutExpired` |
| `--floor all` | 동일 |
| 기존 `$ART` 있을 때 묻지 않고 덮어쓰기 | 산출 손실 |
| `parts/` / `floor_overview.*` 기본 생성 | 워크플로에서 제외 |

### 경로 bootstrap (로컬)

`$WORKING_DIR` / `$ARTIFACTS_DIR`가 비면 상대경로가 `/skills/...`로 깨집니다.  
**SKILL.md 옆 `scripts/` 절대경로**와 artifacts 절대경로를 직접 지정합니다 (위 sample 경로).

---

## 7. 스크립트 요약

| 스크립트 | 역할 |
|----------|------|
| `extract_2d.py` | **기본** — 한 층 `floor_original` |
| `analyze_drawing.py` | 구조 실측 → `structure.md` / `.json` |
| `lib_split.py` | primary bbox·타일 격자 유틸 |
| `lib_render.py` | 치수·고해상도 PNG |
| `plan_split.py` / `split_floor.py` | **레거시·선택** 타일 분할 |

---

## 8. 후속 연결

| 다음 스킬 | 입력 |
|-----------|------|
| `drawing-walldetector` | `floors/<F>/floor_original.dxf` → `floor_wall_original.*` |
| `drawing-llmvalidator` | 벽 검출 결과 보정 |

원본 277 MB를 walldetector에 직접 넣지 않습니다. **devider가 만든 층 DXF만** 사용합니다.

---

## 9. 한계

- 블록명 규약이 `XA-S-{N}F 평면` 형태가 아니면 `discover_floors` / `find_floor_inserts`를 맞춰야 합니다.
- primary gap 임계(20 m) 밖·안쪽 이상치는 잘리거나, 이중 클러스터가 남을 수 있습니다.
- explode 실패 블록은 warn 후 스킵 → 일부 가구/설비가 빠질 수 있습니다.
- `clean` variant는 가구를 의도적으로 빼므로 후속 벽 검출·라벨 검수에는 `original`을 씁니다.

---

## 10. 한 줄 요약

**원본 DXF의 층 INSERT를 explode해 primary 클러스터만 남긴 `floor_original.dxf`(+치수 PNG)를 층당 하나씩 만들고, 그 결과가 walldetector 등 후속 파이프라인의 입력이 됩니다.**
