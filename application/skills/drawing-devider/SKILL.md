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

1. **기존 폴더 게이트** — `$ARTIFACTS_DIR/<drawing_id>/`가 **이미 있으면** 분석·추출을 **시작하지 않는다**. 경로·기존 산출물 요약을 보여 주고 **계속(덮어쓰기/이어하기) / 중단**을 사용자에게 물은 뒤, 허락이 있을 때만 진행한다.
2. **먼저 구조 파악** — “파일 실측 요약” 형태로 분석한 뒤 MD로 저장한다. 분석 없이 다층 추출만 밀어붙이지 않는다.
3. **층 단위만** — 기본 산출은 `floors/<F>/floor_original.dxf` (+ `.png`). `parts/`·`floor_parts_index.json`·`split_plan.*`를 **만들지 않는다**.
4. **치수** — 층 PNG/DXF 미리보기에 전체·그리드 치수를 포함한다 (`lib_render.py`).
5. **층별 1개씩** — `extract_2d`는 **한 번의 bash/도구 호출에 층 1개만** 실행한다. `for FLOOR in 6F 7F …` 일괄 루프, `--floor all`, 여러 층을 한 커맨드에 묶어 돌리는 것을 **금지**한다 (대용량 DXF에서 TimeoutExpired 발생). 한 층이 끝나면 결과를 보고한 뒤 다음 층으로 넘어간다.
6. **층 게이트** — 다층이면 **파일럿 1개 층만** 추출한 뒤 사용자 허락을 받고, 나머지 층도 **층당 1회씩** 진행한다.
7. **산출물 경로** — **사용자 artifacts** (`$ARTIFACTS_DIR/<drawing_id>/`) 아래에만 저장한다. (아래 [Artifacts](#artifacts-layout))
8. **미리보기** — 층 시각 확인은 `floors/<F>/floor_original.png`만 사용. `floor_overview.*` / `floor_*_2d.png` / `floor_*_geom.json`을 **만들지 않는다**.
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
| `$SCRIPTS/extract_2d.py` | 원본 → `floors/<F>/floor_original.dxf` (+ `.png` 자동) |
| `$SCRIPTS/analyze_drawing.py` | 구조 실측 → `structure.md` / `structure.json` |
| `$SCRIPTS/lib_render.py` | 치수·고해상도 렌더 라이브러리 |
| `$SCRIPTS/lib_split.py` | 공통 유틸 (primary 클러스터·bbox 등) |
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
⓪ drawing_id 결정 → $ARTIFACTS_DIR/<drawing_id>/ 존재 여부 확인
  ↓ (이미 있으면 사용자에게 계속/중단 확인 — 허락 전 작업 금지)
  ↓
① extract_2d.py --floor <FIRST>
     → floors/<F>/floor_original.dxf (+ floor_original.png)
     ※ floor_*_clean.dxf / floor_*_2d.png / parts/ 생성 금지
     ※ PNG는 기본 생성 (`--no-png`로 생략)
  ↓
② 사용자 허락 요청 (나머지 층 진행 여부)
  ↓
③ 허락 시 다음 층만 extract  ← 층당 1회, 일괄 루프 금지
  ↓ (층마다 완료 보고 후 다음 층)
④ analyze_drawing.py  → structure.md / structure.json
  ↓
⑤ work_log.md 작성·전달
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

- 사용자가 **계속**하기 전까지 `extract_2d` / `analyze`를 **실행하지 않는다**.
- **중단**이면 작업을 끝낸다. 폴더를 임의로 삭제하지 않는다.
- **계속**이면 기존 파일을 덮어쓸 수 있음을 한 줄로 알리고 워크플로를 이어간다.
- 폴더가 없으면 곧바로 ①부터 진행한다.

### ① 층 추출 (파일럿 → 승인 → 나머지)

```bash
# 위 Script Location bootstrap 후
python3 "$SCRIPTS/extract_2d.py" \
  --dxf "$ARTIFACTS_DIR/<input>.dxf" \
  --floor 12F \
  --out "$ARTIFACTS_DIR" \
  --drawing-id <drawing_id>
```

성공 시 `floors/12F/floor_original.dxf` / `.png` / `_meta.json`이 생긴다.  
**여기서 멈추고** 사용자에게 PNG·경로·파일 크기를 보여 준 뒤 나머지 층 진행 허락을 받는다.

허락 후 나머지 층도 **한 층 = bash 1회**로만 진행한다.

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
| 층 목록 | `XA-S-{N}F 평면` 등 |
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
$ARTIFACTS_DIR/<drawing_id>/
├── structure.md / .json
├── work_log.md
└── floors/<FLOOR>/
    ├── floor_original.dxf / .png / _meta.json   # 층 스냅샷 · 미리보기 · 후속 스킬 입력
    ├── floor_wall_original.dxf / .png           # (walldetector) 층 전체 벽
    └── floor_meta.json                          # (extract 시)
```

| 산출 | 용도 |
|------|------|
| `floors/<F>/floor_original.dxf` (+ `.png`) | **원본에 가까운 층** (조경·가구·실명 라벨) → **공식 층 단위 입력** · **미리보기** |
| `parts/` · `floor_parts_index.json` · `split_plan.*` | **기본 미생성** (레거시·선택) |
| `floor_overview.*` | **제거됨** — 생성·사용 금지 |
| `floor_*_clean.dxf` | **제거됨** — 생성·사용 금지 |

`floor_*_2d.png` / `floor_*_geom.json`도 만들지 않는다.

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
- 상태: 1층 완료 / 전체 완료
- 파일럿 층: 12F
- 단위: 층당 floor_original (parts 없음)

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

- [ ] `$ARTIFACTS_DIR/<drawing_id>/`가 이미 있으면 **계속/중단**을 사용자에게 물었는가 (허락 전 스크립트 미실행)
- [ ] 산출이 `floors/<F>/floor_original.*` 층 단위인가 (`parts/` 기본 생성 안 함)
- [ ] 산출 경로가 `$ARTIFACTS_DIR/<id>/` 인가
- [ ] 첫 층만 돌렸고 사용자 허락을 기다리는가 (다층인 경우)
- [ ] extract를 **층당 bash 1회**로만 돌리는가 (`for` 일괄·`--floor all` 없음)
- [ ] 미리보기로 `floor_*_2d.png` / `floor_overview.*`를 쓰지 않는가 (`floor_original.png`만)

---

## Failure Modes

| 증상 | 대응 |
|------|------|
| 동일 `drawing_id` 폴더 이미 존재 | 사용자에게 계속/중단 확인 — 허락 전 덮어쓰기 금지 |
| `TimeoutExpired` (extract) | 여러 층 일괄 루프·`--floor all` 금지 → **층당 bash 1회**로 재시도 |
| PNG에 도면이 좌·우 둘 | primary 클러스터(LINE 많은 쪽)만 bbox — `extract_2d` 공통 |
| 치수 없음 | `--with-dims`, PNG 오버레이 + DXF `DIMS` 레이어 |
| 원본 277MB 로드 실패 | 층별 `floor_original.dxf`를 먼저 만든 뒤 후속 스킬 |
| `floor_*_2d.png` / `floor_overview.*` | 생성 금지 산출물 — `floors/<F>/floor_original.png`만 확인 |
| `/skills/...` 없음 · `WORKING_DIR=` 빈 값 | **env 없이** SKILL.md 옆 `scripts/` 절대경로를 `SCRIPTS`로 지정 (빈 `$WORKING_DIR` 연결 금지) |
| `/sk_yongin_jiwon` Read-only | `ARTIFACTS_DIR` 미설정으로 루트에 mkdir 시도 — artifacts 절대경로 `export` 후 재시도 |

---

## Related

- `scripts/extract_2d.py` — `floor_original.dxf`
- `scripts/lib_render.py` — 치수·고해상도 렌더
- `scripts/plan_split.py` / `split_floor.py` — 레거시 타일 분할 (선택)
- `drawing-walldetector` — `floor_original.dxf`에서 층 전체 벽 검출
