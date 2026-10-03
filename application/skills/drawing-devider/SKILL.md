---
name: drawing-devider
description: >-
  CAD/DXF 도면을 층별 floor_original DXF/PNG로 추출하고 구조를 분석합니다.
  도면 추출, extract_2d, 층별 평면도, drawing divider, 평면도 전처리 요청 시 사용합니다.
  (타일 parts 분할은 기본 워크플로에서 제외 — 필요 시 레거시 split 스크립트만 사용)
---

# drawing-devider (층별 도면 추출)

주어진 DXF를 **층별 추출 → 구조 분석 → 산출물 JSON/MD** 순으로 처리한다.  
고해상도 렌더로 **층 하나 = 이미지·DXF 하나**로 충분하므로, **parts 타일 분할은 기본 워크플로에서 하지 않는다.**

## When to Use

- DXF/평면도를 **층 단위** `floor_original.dxf` / `.png`로 뽑을 때
- `extract_2d`, 층별 평면도, 도면 전처리 언급 시
- 다층 도면에서 **층당 1개 스냅샷**이 필요할 때

## Critical Rules

1. **기존 폴더** — `$ARTIFACTS_DIR/<drawing_id>/`가 **이미 있어도 묻지 않는다**. 추출·분석을 바로 진행하고 `floor_original.*`·`structure.*`·`work_log.md`는 **덮어쓴다**. 폴더를 통째로 지우지는 않는다.
2. **구조 파악** — 전 층 추출이 끝나면 “파일 실측 요약”을 `structure.md` / `structure.json`으로 저장한다. 층 추출을 멈추는 조건이 아니다.
3. **층 단위만** — 기본 산출은 `floors/<F>/floor_original.dxf` (+ `.png`). `parts/`·`floor_parts_index.json`·`split_plan.*`를 **만들지 않는다**.
4. **치수** — 층 PNG/DXF 미리보기에 전체·그리드 치수를 포함한다 (`lib_render.py`).
5. **층별 1개씩, 확인 없이** — `extract_2d`는 **한 번의 bash/도구 호출에 층 1개만** 실행한다. `for FLOOR in 6F 7F …` 일괄 루프, `--floor all`, 여러 층을 한 커맨드에 묶어 돌리는 것을 **금지**한다 (대용량 DXF에서 TimeoutExpired 발생). 한 층이 끝나면 **사용자에게 묻지 말고** 바로 다음 층을 같은 방식으로 실행한다.
6. **전 층 연속** — 다층이어도 파일럿 확인을 받지 않는다. 발견된 층을 순서대로 끝까지 추출한 뒤 구조 분석으로 넘어간다.
7. **산출물 경로** — 층 산출은 **사용자 artifacts** (`$ARTIFACTS_DIR/<drawing_id>/`) 아래에만 저장한다. 예외는 프로젝트 도면 목록 `$ARTIFACTS_DIR/drawing_list.json` 하나다. 추출할 때마다 이 파일을 갱신하고, 다른 `drawing_id` 항목은 지우지 않는다. (아래 [Artifacts](#artifacts-layout))
8. **미리보기** — 층 시각 확인은 `floors/<F>/floor_original.png`만 사용. `floor_structure.*` / `floor_overview.*` / `floor_*_2d.png` / `floor_*_geom.json`을 **만들지 않는다**.
9. **스크립트 사용** — 추출·분석은 스킬 `scripts/` 로만 수행한다. ad-hoc 일회성 코드로 대용량 DXF를 우회하지 않는다.
10. **작업 로그** — 전체 작업 내용·파일 목록을 하나의 Markdown으로 남기고 사용자에게 전달한다.
11. 응답은 **한국어**. 경로·JSON 키는 영문/숫자 유지.
12. **타일 분할(레거시)** — 추후 다시 나눌 수 있으나 **현재 기본 경로가 아님**. 사용자가 명시할 때만 `plan_split.py` / `split_floor.py`를 사용한다.

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
| `$SCRIPTS/extract_2d.py` | 원본 → `floors/<F>/floor_original.dxf` (+ `.png` 자동). 건축 레이어가 있으면 벽·창·실명·문만 |
| `$SCRIPTS/lib_structure.py` | 그 필터. 출력 이름은 `floor_original.png` (`floor_structure.*` 금지) |
| `$SCRIPTS/analyze_drawing.py` | 구조 실측 → `structure.md` / `structure.json` |
| `$SCRIPTS/lib_render.py` | 치수·고해상도 렌더 라이브러리 |
| `$SCRIPTS/lib_split.py` | 공통 유틸 (primary 클러스터·bbox 등) |
| `$SCRIPTS/lib_sheet.py` | 도곽·층 제목 층 구분 (XA-S 블록이 없을 때) |
| `$SCRIPTS/plan_split.py` | **(레거시·선택)** 타일 격자 계획 — 기본 워크플로 제외 |
| `$SCRIPTS/split_floor.py` | **(레거시·선택)** parts 자르기 — 기본 워크플로 제외 |

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
⓪ drawing_id 결정
  ↓ (폴더가 이미 있어도 묻지 않고 덮어쓰기)
  ↓
① extract_2d.py --floor <각 층>   ← 층당 bash 1회, 확인 없이 전 층
     → floors/<F>/floor_original.dxf (+ floor_original.png)
     ※ floor_*_clean.dxf / floor_*_2d.png / parts/ 생성 금지
     ※ PNG는 기본 생성 (`--no-png`로 생략)
     ※ 기존 floor_original.* 가 있으면 덮어쓴다
  ↓
② analyze_drawing.py  → structure.md / structure.json
  ↓
③ work_log.md 작성·전달
```

### ⓪ 기존 산출 폴더

`drawing_id` 폴더가 이미 있어도 **묻지 않고** ①부터 진행한다. `floor_original.dxf` / `.png` / `_meta.json`과 `structure.md` / `structure.json` / `work_log.md`는 덮어쓴다. 폴더 전체를 삭제하지 않는다.

### ① 층 추출 (확인 없이 전 층)

```bash
# 위 Script Location bootstrap 후
python3 "$SCRIPTS/extract_2d.py" \
  --dxf "$ARTIFACTS_DIR/<input>.dxf" \
  --floor 12F \
  --out "$ARTIFACTS_DIR" \
  --drawing-id <drawing_id>
```

성공 시 `floors/12F/floor_original.dxf` / `.png` / `_meta.json`이 생긴다. 같은 파일이 있으면 덮어쓴다.  
도곽 안에 건축 레이어(`ARCH`, `*_BG`·`*_CEN` 제외)가 있으면 그 선·실명과 문 스윙만 남긴다. 그 레이어의 창선은 벽과 같이 남긴다. 가구·카세트 배관·등고선은 넣지 않는다. 이 결과도 파일명은 `floor_original.png` 이다.  
다층이면 **멈추지 말고** 다음 층을 바로 추출한다. 사용자 허락을 받지 않는다.

### 층 구분 (블록 이름 → 도곽)

1. **기본:** modelspace INSERT 이름 `XA-S-{N}F 평면` (코어·기둥 포함).
2. **그 형식이 없으면** 추출을 중단하거나 스킬 수정을 묻지 않는다. `lib_sheet.py`가 **도곽(축정렬 테두리)** 과 **층 제목**으로 층을 나눈다. `--layout auto`가 이 순서를 따른다. 제목은 `1층 평면도`뿐 아니라 `1층 냉난방 평면도`처럼 층과 평면도 사이에 용도가 있는 문자열도 인정한다. 같은 제목이 떨어진 도곽에 반복되면 왼쪽부터 `1F`, `1F_2` 로 구분한다. 한 도곽 안에 층 표기가 여럿이고 영역으로 나누지 못하면 그 도곽을 빼지 않는다. 표기 중 하나를 층 이름으로 고르지 않고, 왼쪽·아래부터 `sheet_01`, `sheet_02` 로 둔 뒤 층 이름은 미확정이라고 경고한다. 이미 층이 확정된 도곽과 겹치거나 더 큰 도곽 안에 들어간 테두리는 넣지 않는다. `sheet_XX` 도 확인 없이 추출한다.
3. 도곽 방식이면 먼저 목록만 확인한다.

```bash
python3 "$SCRIPTS/extract_2d.py" \
  --dxf "$ARTIFACTS_DIR/<input>.dxf" \
  --drawing-id <drawing_id> \
  --list-floors
```

4. 목록에 층이 있으면 **확인 없이 전 층**을 추출한다. 원본 DXF는 수정하지 않는다. 기존 `floor_original.*` 는 덮어쓴다.

```bash
python3 "$SCRIPTS/extract_2d.py" \
  --dxf "$ARTIFACTS_DIR/<input>.dxf" \
  --floor 1F \
  --out "$ARTIFACTS_DIR" \
  --drawing-id <drawing_id>
```

나머지 층도 **한 층 = bash 1회**로, 묻지 않고 이어서 진행한다.

**금지 예** (대용량 DXF에서 `TimeoutExpired` 유발):

```bash
# ❌ 여러 층을 한 bash에 for-루프로 일괄 추출
for FLOOR in 6F 7F 8F 9F 10F 11F 12F; do
  python3 "$SCRIPTS/extract_2d.py" --dxf … --floor "$FLOOR" …
done

# ❌ --floor all
python3 "$SCRIPTS/extract_2d.py" --dxf … --floor all …
```

### ② 구조 파악 (파일 실측 요약)

원본 또는 `floor_original.dxf`를 분석한다. MD에는 최소 다음을 포함한다.

| 항목 | 내용 |
|------|------|
| 파일 메타 | 경로, 크기, CAD 버전, 단위(mm) |
| modelspace / 블록 | 엔티티 수, INSERT·층 블록 목록 |
| 레이어 | 상위 레이어, 벽/가구 분리 가능 여부 |
| 층 목록 | `XA-S-{N}F 평면` 또는 도곽·층 제목 (`layout_method`) |
| 층별 대략 span | m 단위 폭×깊이 (가능하면) |
| 이중 클러스터 | 좌우 어긋난 복사본 여부 (12F 사례) |
| 권장 전처리 | `extract_2d.py --floor …` 등 |

```bash
python3 "$SCRIPTS/analyze_drawing.py" \
  --drawing-id <drawing_id> \
  --out "$ART" \
  --raw-dxf "$ARTIFACTS_DIR/<input>.dxf" \
  --floor-dir "$ART/floors"
```

### ③ 작업 로그

모든 단계가 끝나면(또는 1층만 끝난 중간 보고 시) `work_log.md`를 갱신한다.  
사용자에게 **path + 핵심 표**로 전달한다.

### (선택) 타일 분할 — 사용자가 명시한 경우만

고해상도로도 층이 너무 크면 레거시 스크립트를 쓸 수 있다.

```bash
python3 "$SCRIPTS/plan_split.py" --artifacts "$ART" --max-tile-m 60 --overlap-m 1
python3 "$SCRIPTS/split_floor.py" --artifacts "$ART" --floor 12F --source original
```

기본 워크플로·체크리스트에는 포함하지 않는다.

---

## Artifacts Layout

산출물은 **사용자 artifacts** (`$ARTIFACTS_DIR`) 아래로만 모은다.  
bash cwd가 이미 `artifacts/`이므로 상대경로 `<drawing_id>/…` 도 동일하다.

```text
$ARTIFACTS_DIR/
├── drawing_list.json                    # 프로젝트 도면 목록. 분리할 때마다 해당 도면만 갱신
└── <drawing_id>/
    ├── extract_summary.json             # 이 도면의 추출 요약. artifacts 루트에 두지 않는다
    ├── structure.md / .json
    ├── work_log.md
    └── floors/<FLOOR>/
        ├── floor_original.dxf / .png / _meta.json   # 층 스냅샷 · 미리보기 · 후속 스킬 입력
        ├── floor_wall_original.dxf / .png           # (walldetector) 층 전체 벽
        └── floor_meta.json                          # (extract 시)
```

| 산출 | 용도 |
|------|------|
| `drawing_list.json` | 도면 메뉴용 프로젝트 목록. 각 도면에 **원본 DXF 파일명** `source_filename`(및 `source_path`)을 `floors`보다 **앞**에 반드시 기록한다. 그 외 `drawing_id`, 폴더명, 생성·수정 시각, 발견 층, 층별 상태(`pending`/`ready`/`error`)와 DXF·PNG 상대경로. 삭제·재추출의 기준 |
| `floors/<F>/floor_original.dxf` (+ `.png`) | **구조용 층** (벽·창·실명·문 스윙) → **공식 층 단위 입력** · **미리보기**. 건축 레이어의 창선은 벽과 같이 남긴다. 가구·카세트 배관·등고선·중심선은 넣지 않는다 |
| `parts/` · `floor_parts_index.json` · `split_plan.*` | **기본 미생성** (레거시·선택) |
| `floor_structure.*` | **생성 금지** — 같은 내용은 `floor_original.png` |
| `floor_overview.*` | **제거됨** — 생성·사용 금지 |
| `floor_*_clean.dxf` | **제거됨** — 생성·사용 금지 |

`floor_*_2d.png` / `floor_*_geom.json`도 만들지 않는다.

`drawing_list.json` 도면 항목은 `extract_2d.py`가 아래 순서로 쓴다. `floors`가 길어도 원본 DXF 파일명 `source_filename`은 항목 앞에 둔다. 수동으로 고칠 때도 이 필드를 빼지 않는다.

```json
{
  "drawing_id": "<drawing_id>",
  "folder": "<drawing_id>",
  "source_filename": "<원본 DXF 파일명>.dxf",
  "source_path": "<원본 DXF 절대경로>",
  "source_size_bytes": 0,
  "created_at": "",
  "updated_at": "",
  "status": "ready",
  "floors": []
}
```

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
# 도면 추출 작업 로그 — <drawing_id>

## 목차
- [요약](#요약)
- [구조 분석](#구조-분석)
- [실행 결과](#실행-결과)
- [파일 목록](#파일-목록)

## 요약
- 상태: 전체 완료
- 단위: 층당 floor_original (parts 없음, 기존 파일 덮어쓰기)

## 구조 분석
- structure.md 링크

## 실행 결과
| 층 | floor_original | 상태 |
|----|----------------|------|

## 파일 목록
| 구분 | 경로 |
|------|------|
```

---

## Decision Checklist

작업 시작·추출 전에 확인:

- [ ] `$ARTIFACTS_DIR/<drawing_id>/`가 이미 있어도 묻지 않고 덮어썼는가
- [ ] 산출이 `floors/<F>/floor_original.*` 층 단위인가 (`parts/` 기본 생성 안 함)
- [ ] 산출 경로가 `$ARTIFACTS_DIR/<id>/` 인가
- [ ] 다층이면 확인 없이 전 층을 이어서 추출했는가
- [ ] `XA-S-{N}F` 블록이 없으면 도곽·층 제목(`--list-floors`)으로 넘어갔는가 (중단하고 스킬 수정을 묻지 않음)
- [ ] extract를 **층당 bash 1회**로만 돌리는가 (`for` 일괄·`--floor all` 없음, 층 사이 사용자 확인 없음)
- [ ] 미리보기로 `floor_structure.*` / `floor_*_2d.png` / `floor_overview.*`를 쓰지 않는가 (`floor_original.png`만)
- [ ] `drawing_list.json` 각 도면에 원본 DXF `source_filename`을 `floors`보다 앞에 넣었는가

---

## Failure Modes

| 증상 | 대응 |
|------|------|
| 동일 `drawing_id` 폴더 이미 존재 | 묻지 않고 덮어쓴다. 폴더는 삭제하지 않는다 |
| `XA-S-{N}F 평면` 블록 없음 | 중단·스킬 보완 질문 금지. `--list-floors`로 도곽·층 제목을 확인한 뒤 **전 층**을 확인 없이 추출한다 |
| 목록에 `1F`가 없고 `sheet_XX`가 있음 | `--floor 1F`로 재시도하지 않는다. `sheet_01`부터 발견된 이름 그대로 추출한다. 사용자에게 "영역을 확정하지 못했다"고 쓰지 않는다. 미확정은 층 이름만 해당하고 도곽은 이미 있다 |
| `요청한 1F 이름의 도곽은 없습니다` | 실패로 끝내지 않는다. 출력된 발견 목록의 각 이름을 `--floor`에 넣어 이어서 추출한다 |
| 도곽·층 제목도 없음 | 추출하지 않는다. 원본은 그대로 두고, 찾은 테두리·경고를 보고한다 |
| `TimeoutExpired` (extract) | 여러 층 일괄 루프·`--floor all` 금지 → **층당 bash 1회**로 재시도 |
| PNG에 도면이 좌·우 둘 | primary 클러스터(LINE 많은 쪽)만 bbox — `extract_2d` 공통 |
| 치수 없음 | `--with-dims`, PNG 오버레이 + DXF `DIMS` 레이어 |
| 원본 277MB 로드 실패 | 층별 `floor_original.dxf`를 먼저 만든 뒤 후속 스킬 |
| `floor_structure.*` / `floor_*_2d.png` / `floor_overview.*` | 생성 금지 산출물 — `floors/<F>/floor_original.png`만 확인 |
| `/skills/...` 없음 · `WORKING_DIR=` 빈 값 | **env 없이** SKILL.md 옆 `scripts/` 절대경로를 `SCRIPTS`로 지정 (빈 `$WORKING_DIR` 연결 금지) |
| `/sk_yongin_jiwon` Read-only | `ARTIFACTS_DIR` 미설정으로 루트에 mkdir 시도 — artifacts 절대경로 `export` 후 재시도 |

---

## Related

- `scripts/extract_2d.py` — `floor_original.dxf`
- `scripts/lib_render.py` — 치수·고해상도 렌더
- `scripts/plan_split.py` / `split_floor.py` — 레거시 타일 분할 (선택)
- `drawing-walldetector` — `floor_original.dxf`에서 층 전체 벽 검출
