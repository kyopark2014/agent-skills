---
name: drawing-devider
description: >-
  CAD/DXF 도면을 분석 가능한 크기(기본 60×60 m 이내)로 분할하고 치수를 포함한
  타일(PNG/DXF/JSON)과 작업 로그를 산출합니다. 도면 분할, drawing divider,
  floor tile, extract_2d, 평면도 자르기 요청 시 사용합니다.
---

# drawing-devider (도면 분할)

주어진 DXF/클린 도면을 **분석 → 구조 MD → 분할 계획 → (층별) 자르기 → 산출물 JSON/MD** 순으로 처리한다.

## When to Use

- DXF/평면도/층별 도면을 **Vision·벽체 인식이 가능한 크기**로 자를 때
- `extract_2d`, 도면 분할, tile, 30m 그리드 언급 시
- 다층 도면에서 **층별 N개 타일 + 치수** 산출이 필요할 때

## Critical Rules

1. **기존 폴더 게이트** — `$ARTIFACTS_DIR/<drawing_id>/`가 **이미 있으면** 분석·자르기를 **시작하지 않는다**. 경로·기존 산출물 요약을 보여 주고 **계속(덮어쓰기/이어하기) / 중단**을 사용자에게 물은 뒤, 허락이 있을 때만 진행한다.
2. **먼저 구조 파악** — “파일 실측 요약” 형태로 분석한 뒤 MD로 저장한다. 분석 없이 자르지 않는다.
3. **분할 계획 MD/JSON**을 구조 MD를 근거로 작성한다. 타일 한 변은 **60 m 이내** (기본 `max_tile_m=60`).
4. **치수 필수** — 잘린 도면(PNG/DXF)에 전체·그리드 치수를 포함한다 (`lib_render.py`와 동일 계열).
5. **층별 1개씩** — `extract_2d`·`split_floor`는 **한 번의 bash/도구 호출에 층 1개만** 실행한다. `for FLOOR in 6F 7F …` 일괄 루프, `--floor all`, 여러 층을 한 커맨드에 묶어 돌리는 것을 **금지**한다 (대용량 DXF에서 TimeoutExpired 발생). 한 층이 끝나면 결과를 보고한 뒤 다음 층으로 넘어간다.
6. **층 게이트** — 다층이면 **파일럿 1개 층만** 자른 뒤 사용자 허락을 받고, 나머지 층도 **층당 1회씩** 진행한다.
7. **산출물 경로** — **사용자 artifacts** (`$ARTIFACTS_DIR/<drawing_id>/`) 아래에만 저장한다. (아래 [Artifacts](#artifacts-layout))
8. **미리보기** — 층 시각 확인은 `floors/<F>/floor_original.png`만 사용. `floor_overview.*` / `floor_*_2d.png` / `floor_*_geom.json`을 **만들지 않는다**.
9. **스크립트 사용** — 자르기는 스킬 `scripts/` 로만 수행한다. ad-hoc 일회성 코드로 대용량 DXF를 우회하지 않는다.
10. **작업 로그** — 전체 작업 내용·파일 목록을 하나의 Markdown으로 남기고 사용자에게 전달한다.
11. 응답은 **한국어**. 경로·JSON 키는 영문/숫자 유지.

## Script Location

### 경로 bootstrap (필수 — 매 bash 전)

Runtime은 `$WORKING_DIR`·`$ARTIFACTS_DIR`를 주입하지만, **Cursor 로컬 등에서는 둘 다 비어 있을 수 있다.**  
빈 값으로 `"$WORKING_DIR/skills/..."`를 쓰면 **`/skills/...`**, `"$ARTIFACTS_DIR/id"`는 **`/id`** 로 펼쳐져 실패한다 (`can't open file`, `Read-only file system`).

**규칙:**

1. `$WORKING_DIR` / `$ARTIFACTS_DIR`가 **비어 있거나**, 아래 파일이 없으면 **env를 쓰지 않는다**.
2. **SCRIPTS 기본값** = **이 SKILL.md와 같은 스킬 폴더의 `scripts/` 절대경로**  
   (예: `…/skills/drawing-devider/SKILL.md` → `…/skills/drawing-devider/scripts`).
3. **ARTIFACTS_DIR 기본값** = bash cwd가 artifacts이면 그 경로, 아니면 사용자가 연 세션 artifacts  
   (로컬 예: `…/application/.session_storage/lge/artifacts`).
4. `skills/...`·`scripts/...` **상대경로 금지** (cwd=`artifacts/`라 실패).

```bash
# --- 매 호출 전 bootstrap ---
SKILL_SCRIPTS="$(cd "$(dirname "$0")" 2>/dev/null && pwd)"  # 직접 실행 시
# 에이전트: SKILL.md 절대경로를 알면 아래로 고정
# SCRIPTS="<SKILL.md 디렉터리>/scripts"

if [ -n "${WORKING_DIR:-}" ] && [ -f "$WORKING_DIR/skills/drawing-devider/scripts/extract_2d.py" ]; then
  SCRIPTS="$WORKING_DIR/skills/drawing-devider/scripts"
elif [ -f "${SCRIPTS:-}/extract_2d.py" ]; then
  : # 이미 설정됨
else
  echo "FATAL: SCRIPTS를 SKILL.md 옆 scripts/ 절대경로로 지정하세요" >&2
  exit 2
fi

if [ -z "${ARTIFACTS_DIR:-}" ] || [ ! -d "$ARTIFACTS_DIR" ]; then
  # cwd가 artifacts인 경우 (Runtime bash)
  if [ -d "$PWD" ] && ls "$PWD"/*.dxf >/dev/null 2>&1 || [ -d "$PWD/sk_yongin_jiwon" ]; then
    ARTIFACTS_DIR="$PWD"
  else
    echo "FATAL: ARTIFACTS_DIR 미설정 — 사용자 artifacts 절대경로를 export 하세요" >&2
    exit 2
  fi
fi

ART="$ARTIFACTS_DIR/<drawing_id>"
# 검증 (실패 시 추측 경로로 mkdir 하지 말 것)
test -f "$SCRIPTS/extract_2d.py" || { echo "missing $SCRIPTS/extract_2d.py" >&2; exit 2; }
test -d "$ARTIFACTS_DIR" || { echo "missing ARTIFACTS_DIR=$ARTIFACTS_DIR" >&2; exit 2; }
```

로컬(이 워크스페이스) 고정 예:

```bash
SCRIPTS=/Users/ksdyb/Documents/src/agent-skills/application/skills/drawing-devider/scripts
ARTIFACTS_DIR=/Users/ksdyb/Documents/src/agent-skills/application/.session_storage/lge/artifacts
ART="$ARTIFACTS_DIR/sk_yongin_jiwon"
```

| 스크립트 | 용도 |
| --- | --- |
| `$SCRIPTS/analyze_drawing.py` | 구조 실측 → `structure.md` / `structure.json` |
| `$SCRIPTS/plan_split.py` | 60×60 m 그리드 계획 → `split_plan.md` / `split_plan.json` |
| `$SCRIPTS/split_floor.py` | 한 층 타일 자르기(+치수) → `floors/<F>/parts/` (기본: floor_original) |
| `$SCRIPTS/extract_2d.py` | 원본 → `floors/<F>/floor_original.dxf` (+ `.png` 자동) |
| `$SCRIPTS/lib_render.py` | 치수·고해상도 렌더 라이브러리 (`split_floor`가 import) |

```bash
# bootstrap 후
python3 "$SCRIPTS/analyze_drawing.py" \
  --drawing-id <drawing_id> \
  --out "$ART" \
  --raw-dxf "$ARTIFACTS_DIR/<input>.dxf" \
  --floor-dir "$ART/floors"
```

---

## Workflow (필수 순서)

```
DXF 입력 (artifacts/ 또는 --dxf)
  ↓
⓪ drawing_id 결정 → $ARTIFACTS_DIR/<drawing_id>/ 존재 여부 확인
  ↓ (이미 있으면 사용자에게 계속/중단 확인 — 허락 전 작업 금지)
  ↓
① extract_2d.py → floors/<F>/floor_original.dxf (+ floor_original.png)
     ※ floor_*_clean.dxf / floor_*_2d.png 생성 금지
     ※ PNG는 기본 생성 (`--no-png`로 생략)
  ↓
② analyze_drawing.py  → structure.md / structure.json
  ↓
③ plan_split.py       → split_plan.md / split_plan.json  (타일 ≤ 60×60 m)
  ↓
④ 사용자에게 계획 요약 제시
  ↓
⑤ split_floor.py --floor <FIRST>
     → parts는 **floor_original.dxf** 기준 (--source original)
     → floor_overview.* 는 **생성·사용하지 않음** (레거시 있으면 삭제)
  ↓
⑥ 사용자 허락 요청 (나머지 층 진행 여부)
  ↓
⑦ 허락 시 다음 층만 extract(필요 시) → split  ← 층당 1회, 일괄 루프 금지
  ↓ (층마다 완료 보고 후 다음 층)
⑧ work_log.md 작성·전달
```

### ⓪ 기존 산출 폴더 확인 (필수)

`drawing_id`를 정한 직후, **어떤 스크립트도 실행하기 전에** 대상 폴더를 확인한다.

```bash
ART="$ARTIFACTS_DIR/<drawing_id>"
if [ -d "$ART" ]; then
  echo "EXISTING: $ART"
  ls -la "$ART" 2>/dev/null | head -40
fi
```

이미 존재하면 **여기서 멈추고** 사용자에게 한국어로 묻는다.

| 안내할 내용 | 예시 |
|-------------|------|
| 경로 | `$ARTIFACTS_DIR/sk_yongin_jiwon/` |
| 기존 산출 | `structure.md`, `floors/5F/`, `work_log.md` 등 요약 |
| 선택지 | **계속** (덮어쓰기·이어하기) / **중단** / (선택) **다른 drawing_id** |

규칙:

- 사용자가 **계속**하기 전까지 `extract_2d` / `analyze` / `plan` / `split`을 **실행하지 않는다**.
- **중단**이면 작업을 끝낸다. 폴더를 임의로 삭제하지 않는다.
- **계속**이면 기존 파일을 덮어쓸 수 있음을 한 줄로 알리고 워크플로를 이어간다.
- 폴더가 없으면 곧바로 ①부터 진행한다.

### ① 구조 파악 (파일 실측 요약)

원본 또는 `floor_original.dxf`를 분석한다. MD에는 최소 다음을 포함한다.

| 항목 | 내용 |
|------|------|
| 파일 메타 | 경로, 크기, CAD 버전, 단위(mm) |
| modelspace / 블록 | 엔티티 수, INSERT·층 블록 목록 |
| 레이어 | 상위 레이어, 벽/가구 분리 가능 여부 |
| 층 목록 | `XA-S-{N}F 평면` 등 |
| 층별 대략 span | m 단위 폭×깊이 (가능하면) |
| 이중 클러스터 | 좌우 어긋난 복사본 여부 (12F 사례) |
| 권장 전처리 | `extract_2d.py --floor …` 등 |

층별 `floor_original.dxf`가 없으면 **해당 층만** 추출한다 (`--floor`에 층 하나).

```bash
# 위 Script Location bootstrap 후
python3 "$SCRIPTS/analyze_drawing.py" \
  --drawing-id <drawing_id> \
  --out "$ART" \
  --raw-dxf "$ARTIFACTS_DIR/<input>.dxf" \
  --floor-dir "$ART/floors"
```

```bash
python3 "$SCRIPTS/extract_2d.py" \
  --dxf "$ARTIFACTS_DIR/<input>.dxf" \
  --floor 12F \
  --out "$ARTIFACTS_DIR" \
  --drawing-id <drawing_id>
```

**금지 예** (대용량 DXF에서 `TimeoutExpired` 유발):

```bash
# ❌ 여러 층을 한 bash에 for-루프로 일괄 추출
for FLOOR in 6F 7F 8F 9F 10F 11F 12F; do
  python3 "$SCRIPTS/extract_2d.py" --dxf … --floor "$FLOOR" …
done

# ❌ --floor all
python3 "$SCRIPTS/extract_2d.py" --dxf … --floor all …
```

나머지 층이 필요하면 **층마다 별도 bash 호출**로 1개씩 돌리고, 완료·실패를 보고한 뒤 다음 층으로 간다.

### ② 분할 계획

`structure.json`의 층별 bbox(m)로 타일 수를 계산한다.

\[
n_x = \lceil W / (30 - 2\cdot overlap) \rceil,\quad
n_y = \lceil H / (30 - 2\cdot overlap) \rceil,\quad
N = n_x \times n_y
\]

- 기본 `max_tile_m=60`, `overlap_m=1` → 유효 step ≤ 58 m (겹침 포함 시에도 ≤ 60 m)
- 장축만 나누는 strip 모드는 **한 변이 60 m를 넘으면 금지** → 반드시 2D 그리드
- 계획 MD에 층별 `N`, `n_x`, `n_y`, 타일 ID 규칙(`R{r}C{c}`)을 명시

### ③ 자르기 (층 1개 → 승인 → 나머지 층도 1개씩)

```bash
# bootstrap 후 (SCRIPTS / ART 필수)
python3 "$SCRIPTS/split_floor.py" \
  --artifacts "$ART" \
  --floor 12F \
  --source original \
  --max-tile-m 60 --overlap-m 1 \
  --dpi 300 --px-width 6000
```

성공 시 해당 층 `floors/12F/floor_parts_index.json`이 갱신된다.  
**여기서 멈추고** 사용자에게 PNG 샘플·타일 수·경로를 보여 준 뒤 나머지 층 진행 허락을 받는다.

허락 후 나머지 층도 **한 층 = bash 1회**로만 진행한다.

1. (필요 시) `extract_2d.py --floor <NEXT>` 한 층만  
2. 완료 보고 (`floor_original.dxf` 크기 등)  
3. `split_floor.py --floor <NEXT>` 한 층만  
4. 타일 수·경로 보고 → 다음 층  

`for … in 6F 7F …` 로 extract/split을 한 번에 묶지 않는다.

### ④ 작업 로그

모든 단계가 끝나면(또는 1층만 끝난 중간 보고 시) `work_log.md`를 갱신한다.  
사용자에게 **path + 핵심 표**로 전달한다.

---

## Artifacts Layout

산출물은 **사용자 artifacts** (`$ARTIFACTS_DIR`) 아래로만 모은다.  
bash cwd가 이미 `artifacts/`이므로 상대경로 `<drawing_id>/…` 도 동일하다.

```text
$ARTIFACTS_DIR/<drawing_id>/
├── structure.md / .json
├── split_plan.md / .json
├── work_log.md
└── floors/<FLOOR>/
    ├── floor_original.dxf / .png / _meta.json   # 층 스냅샷 → parts 입력 · 미리보기
    ├── floor_wall_original.dxf / .png           # (walldetector) 층 전체 벽
    ├── floor_meta.json
    ├── floor_parts_index.json
    └── parts/R0C0.png|.dxf|_meta.json|_geom.json …
```

| 산출 | 용도 |
|------|------|
| `floors/<F>/floor_original.dxf` (+ `.png`) | **원본에 가까운 층** (조경·가구·실명 라벨) → **parts 기준** · **미리보기** |
| `floor_overview.*` | **제거됨** — 생성·사용 금지 |
| `floor_*_clean.dxf` | **제거됨** — 생성·사용 금지 |

`floor_*_2d.png` / `floor_*_geom.json`도 만들지 않는다.

### `floor_parts_index.json` (층 단위)

```json
{
  "floor": "12F",
  "drawing_id": "sk_yongin_jiwon",
  "max_tile_m": 60,
  "overlap_m": 1,
  "grid": { "nx": 8, "ny": 3, "tile_w_m": 18.91, "tile_h_m": 15.84 },
  "source": {
    "kind": "original",
    "dxf": "…/floors/12F/floor_original.dxf",
    "original_dxf": "…/floors/12F/floor_original.dxf"
  },
  "bbox_mm": { "xmin": 0, "ymin": 0, "xmax": 0, "ymax": 0 },
  "parts": [
    {
      "id": "R0C0",
      "row": 0,
      "col": 0,
      "bbox_mm": {},
      "size_m": { "width": 18.91, "height": 15.84 },
      "files": { "png": "…", "dxf": "…", "meta": "…", "geom": "…" },
      "dimensions_overall": { "width_m": 18.91, "height_m": 15.84 }
    }
  ],
  "status": "completed",
  "approved_for_remaining_floors": false
}
```

각 타일 `*_meta.json`에는 bbox(mm/m), 치수 요약, 렌더 설정, 상대 파일 경로를 넣는다.

---

## Markdown Templates

### structure.md 골격

```markdown
# 도면 구조 — <drawing_id>

## 파일 메타
| 항목 | 내용 |
|------|------|
| 경로 | |
| 크기 | |
| 단위 | mm |

## 파일 실측 요약
| 항목 | 값 |
|------|-----|
| modelspace 엔티티 | |
| 블록 수 | |
| 층 목록 | |
| 기하 레이어 | |

## 층별 span (추정)
| 층 | 폭(m) | 깊이(m) | 비고 |
|----|-------|---------|------|

## 전처리 / 리스크
- …
```

### work_log.md 골격

```markdown
# 도면 분할 작업 로그 — <drawing_id>

## 목차
- [요약](#요약)
- [구조 분석](#구조-분석)
- [분할 계획](#분할-계획)
- [실행 결과](#실행-결과)
- [파일 목록](#파일-목록)

## 요약
- 상태: 1층 완료 / 전체 완료
- 파일럿 층: 12F (N=… 타일)

## 구조 분석
- structure.md 링크

## 분할 계획
- split_plan.md 링크, max_tile_m=60

## 실행 결과
| 층 | 타일 수 | 상태 | index |
|----|---------|------|-------|

## 파일 목록
| 구분 | 경로 |
|------|------|
```

---

## Decision Checklist

작업 시작·자르기 전에 확인:

- [ ] `$ARTIFACTS_DIR/<drawing_id>/`가 이미 있으면 **계속/중단**을 사용자에게 물었는가 (허락 전 스크립트 미실행)
- [ ] `structure.md`에 실측 요약이 있는가
- [ ] 모든 타일 `width_m ≤ 30` and `height_m ≤ 30` 인가
- [ ] 치수(`with-dims`)가 켜져 있는가
- [ ] 산출 경로가 `$ARTIFACTS_DIR/<id>/` 인가
- [ ] 첫 층만 돌렸고 사용자 허락을 기다리는가 (다층인 경우)
- [ ] extract/split을 **층당 bash 1회**로만 돌리는가 (`for` 일괄·`--floor all` 없음)
- [ ] 미리보기로 `floor_*_2d.png` / `floor_overview.*`를 쓰지 않는가 (`floor_original.png`만)

---

## Failure Modes

| 증상 | 대응 |
|------|------|
| 동일 `drawing_id` 폴더 이미 존재 | 사용자에게 계속/중단 확인 — 허락 전 덮어쓰기 금지 |
| `TimeoutExpired` (extract/split) | 여러 층 일괄 루프·`--floor all` 금지 → **층당 bash 1회**로 재시도 |
| PNG에 도면이 좌·우 둘 | primary 클러스터(LINE 많은 쪽)만 bbox — `extract_2d`/`split_floor` 공통 |
| 타일이 60 m 초과 | `plan_split` 재계산, strip 모드 금지 |
| 치수 없음 | `--with-dims`, PNG 오버레이 + DXF `DIMS` 레이어 |
| 원본 277MB 로드 실패 | 층별 `floor_original.dxf`를 먼저 만든 뒤 split |
| 가구로 벽 왜곡 | walldetector는 이중선 휴리스틱; original에도 가구 포함됨 |
| `floor_*_2d.png` / `floor_overview.*` | 생성 금지 산출물 — `floors/<F>/floor_original.png`만 확인 |
| `/skills/...` 없음 · `WORKING_DIR=` 빈 값 | **env 없이** SKILL.md 옆 `scripts/` 절대경로를 `SCRIPTS`로 지정 (빈 `$WORKING_DIR` 연결 금지) |
| `/sk_yongin_jiwon` Read-only | `ARTIFACTS_DIR` 미설정으로 루트에 mkdir 시도 — artifacts 절대경로 `export` 후 재시도 |

---

## Related

- `scripts/extract_2d.py` — `floor_original.dxf`
- `scripts/lib_render.py` — 치수·고해상도 렌더 (`split_floor`가 재사용)
- `scripts/split_floor.py` — parts(floor_original)
- `drawing-walldetector` — 타일 DXF에서 벽 검출
