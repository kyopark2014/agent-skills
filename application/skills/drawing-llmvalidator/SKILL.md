---
name: drawing-llmvalidator
description: >-
  drawing-walldetector 산출(WALL 빨강)을 Vision LLM으로 검수해 가구 오검출을
  제거하고, 연속 회의실 등 구조적으로 빠져 있는 벽을 승격합니다. wall LLM
  validate, wall 오분류 수정, floor_wall_validated 요청 시 사용합니다.
---

# drawing-llmvalidator (Vision 벽 검증·보정)

`drawing-walldetector`가 만든 **빨간 WALL** 결과를 Vision(에이전트)이 타일·줌 크롭으로
검수한 뒤, 가구 오검출 demote / 구조 벽 미검출 promote 를 적용해
**`floor_wall_validated.dxf`(+`.png`)** 로 저장한다.
입력 `floor_wall_original.*` 은 덮어쓰지 않는다.

## When to Use

- walldetector 후 가구·좌석·데스크가 빨강으로 남아 있을 때
- 연속 회의실/접견실에서 칸막이 일부만 회색(미검출)일 때
- `floor_wall_validated` / wall LLM validate / 오분류 수정 요청 시

## Critical Rules

1. **선행** — `floors/<F>/floor_wall_original.dxf` (walldetector)가 있어야 한다.
2. **입력 미리보기** — Vision에는 `floor_wall_original.png` (또는 `prepare_review.py` 산출)를 쓴다.
   레거시 parts가 있으면 타일 크롭도 만든다.
3. **출력** — `floors/<F>/floor_wall_validated.{dxf,png,_meta.json}` 만 생성·갱신.
   `floor_wall_original.*` 은 읽기 전용(덮어쓰기 금지).
4. **층당 1회** — 한 bash에 전층 일괄 금지. 파일럿 층 보정 → 컨펌 → 다음 층.
5. **스크립트 절대경로** — `$WORKING_DIR/skills/drawing-llmvalidator/scripts/` 또는
   이 SKILL.md 옆 `scripts/` 절대경로. cwd 상대 `skills/...` 금지.
6. 응답은 **한국어**. 경로·JSON 키는 영문/숫자 유지.

## Script Location

```bash
SCRIPTS=/Users/ksdyb/Documents/src/agent-skills/application/skills/drawing-llmvalidator/scripts
# Runtime: SCRIPTS="$WORKING_DIR/skills/drawing-llmvalidator/scripts"
ART="$ARTIFACTS_DIR/<drawing_id>"

# 1) Vision용 크롭 (입력: floor_wall_original.png)
python3 "$SCRIPTS/prepare_review.py" --artifacts "$ART" --floor 5F

# 2) Vision 검수 후 → floor_wall_validated.*
python3 "$SCRIPTS/correct_walls_floor.py" --artifacts "$ART" --floor 5F

# 3) (선택) original vs validated 개선 diff
python3 "$SCRIPTS/render_wall_diff.py" --artifacts "$ART" --floor 5F
# → floors/5F/diff_original_vs_validated.png
```

| 스크립트 | 역할 |
| --- | --- |
| `prepare_review.py` | `floor_wall_original.png` → `llm_review/` (층 전체 또는 레거시 타일 크롭) |
| `correct_walls_floor.py` | demote/promote → `floor_wall_validated.*` + `llm_review/corrections.json` |
| `render_wall_diff.py` | original vs validated → `diff_original_vs_validated.png` (초록=promote, 파랑=demote) |
| `lib_llm_correct.py` | 갭 승격·가구 강등·WALL DXF PNG 재렌더 |

## Workflow

```
walldetector 산출
  floors/<F>/floor_wall_original.{dxf,png}   ← 입력(불변)
  ↓
① prepare_review.py → llm_review/ (층 전체 또는 레거시 타일 크롭)
  ↓
② Vision: demote / promote 판정
  ↓
③ correct_walls_floor.py
     - 기하: WALL 런 사이 진짜 갭 + 이중선 promote
     - 기하: 복도·연속방·긴 이중선 promote
     - 기하: 강당·오픈홀 중앙 통로 demote (`protect`보다 우선)
     - 기하: 짧은 평행다발 pack demote
     - review.json demote/promote_bboxes
     - `--short-demote` 옵션
  ↓
④ floor_wall_validated.dxf / .png / _meta.json 저장
  ↓
⑤ 사용자 컨펌 → 다음 층
```

## Vision 판정 기준

빨강 = WALL, 회색 = BASE(비벽 또는 미검출). PNG·DXF를 보고 `review.json`을 작성한다.

### 최우선 규칙 — 복도 벽

- **복도(통로) 양옆을 따라가는 긴 수평·수직 선은 기본적으로 벽이다.**
- 문 개구부·문 스윙(ARC)이 있어도, 복도 양측 경계선 자체는 **WALL로 유지·승격**한다.
- 복도 벽을 `demote_bboxes`에 넣지 않는다. 회색이면 `promote_bboxes`에 넣는다.
- 가구·좌석이 복도 옆에 있어도, **복도 경계선과 가구 윤곽을 혼동하지 않는다.**

### 최우선 규칙 — 계단실

- **계단(UP/DN)은 문 개구를 제외하고 외곽이 벽으로 둘러싸인다.**
- 계단실 외곽 이중선이 회색이면 `promote` / 기하 승격 대상. 이미 빨강이면 demote 금지.
- **트레드·중심 난간 해칭**은 벽이 아니다 (짧은 평행 다발 → demote 유지).
- 문 스윙(ARC) 위치의 개구 갭은 메우지 않는다.

### 최우선 규칙 — 엘리베이터

- **입구 측면 잼(문 개구 위·아래 짧은 수직)이 벽** — 로비를 향한 문면의 화살표 위치.
- **뱅크 주위 외곽도 벽.** 전고 문 개구·후면·중앙 스파인·카 내부 측면은 비벽.
- 문 방향: 인접 열 사이 로비(약 5–9.5 m)를 향함 (윙 중앙 복도가 아님).

### 최우선 규칙 — H-Beam 기둥

- **중첩 정사각(+ 내부 짧은 대시) H-Beam 기둥은 모두 WALL**이다.
- 책상·캐비닛 등 큰 가구 사각과 구분: 기둥은 보통 변 ≤ 1.2 m 동심 이중 사각.
- BASE면 WALL로 승격, demote(가구 박스) 대상에서 제외.

### Demote (WALL → 제거) — 복도·계단·엘리베이터·기둥 제외

- 책상·의자·회의 테이블·안내데스크·피트니스 기구 윤곽
- 객석/계단 트레드처럼 **짧은 평행선 다발**
- 도면 주석·타이틀 박스 (`5층평면도` 등)
- 실 내부 단독 짧은 사각(캐비닛·랙) — **단 H-Beam 기둥 제외**
- **강당·오픈홀 중앙**을 가로지르는 긴 선 (객석 통로·보이드 X·실명 라벨을 관통)
  — `protect_corridor`보다 우선 demote. 외곽 장축은 유지.
- **금지:** 복도 양측 장축 경계, 외벽, 코어·**계단실** 외곽, **엘리베이터 측벽·사이드**, **H-Beam 기둥**, 긴 이중선
- **엘리베이터 문·후면**은 demote 대상 (protect 제외)

### Promote (BASE → WALL)

- **복도 양측**이 회색(미검출)인 긴 이중선
- **계단실 외곽** 이중선 (문 개구 제외)
- **엘리베이터 도어면 잼·문사이 칸막이** (카 상하·전고 문/후면·샤프트 X 제외)
- **H-Beam 기둥** (중첩 정사각)
- **연속된 동일 방 열**에서 이웃만 빨강인 회색 칸막이/외곽 (한 칸만 다르게 기입된 경우)
- 연속 회의실/접견실 칸막이 중 이웃만 빨강인 회색 칸
- 같은 축 긴 벽 런 사이 짧은 갭
- 외벽·코어 이중선이 부분만 빨강인 경우
- **금지:** 강당·오픈홀 **내부**를 가로지르는 장축 BASE (중앙 통로는 승격하지 않음)

### 유지

- 문 스윙(ARC), 이미 회색인 가구, 계단 발판·엘리베이터 샤프트 X 해칭
- 이미 빨간 **복도·외벽·코어·계단실·엘리베이터 측벽·H-Beam 기둥**
- 강당·오픈홀의 **외곽** 장축 (중앙 통로만 제거)

### `--short-demote` 주의

문 사이 짧은 WALL 조각을 지울 때 **복도 벽 런도 깨질 수 있다.**  
기본은 **끄고** 실행한다. 켤 경우에도 기하 보호가 복도·긴 이중선 WALL을 demote에서 제외한다.

## review.json bbox 형식

`llm_review/review.json`의 `demote_bboxes` / `promote_bboxes` 각 항목은 **mm 좌표**로:

```json
{
  "label": "가구_좌측",
  "xmin": 0,
  "ymin": 0,
  "xmax": 10000,
  "ymax": 5000
}
```

`x0/y0/x1/y1`, `bbox_mm` 중첩, `[xmin,ymin,xmax,ymax]` 배열도 허용한다.  
필수 키가 없으면 해당 항목만 skip 하고 경고를 남긴다 (`KeyError` 없음).

### demote_bboxes 크기 제한 (중요)

- **가구·캐비닛 한 덩어리만** 감싸는 작은 박스여야 한다 (보통 한 변 ≤ 15 m).
- 층 절반·복도 전체·연속 방 열을 덮는 bbox는 **금지** — 이웃 WALL까지 지워
  “같은 줄 방 하나만 회색” 같은 이상을 만든다.
- 연속된 동일 형태 방의 벽은 동일하게 처리한다. 한 칸만 회색이면 `promote` 대상.

## Artifacts

```text
$ARTIFACTS_DIR/<drawing_id>/floors/<F>/
  floor_wall_original.dxf / .png / _meta.json   # walldetector 입력 (불변)
  floor_wall_validated.dxf / .png / _meta.json  # llmvalidator 출력
  diff_original_vs_validated.png                # promote(초록)/demote(파랑) diff
  llm_review/
    R0C0_wall_crop.png …
    review.json
    corrections.json
```

## Decision Checklist

- [ ] walldetector `floor_wall_original.*` 존재하는가
- [ ] 출력이 `floor_wall_validated.*` 인가 (original 덮어쓰기 금지)
- [ ] `prepare_review` 크롭으로 Vision 검수했는가
- [ ] 보정은 층당 1회인가

## Related

- `drawing-walldetector` — 이중선 휴리스틱 1차 검출
- `drawing-devider` — 타일·`floor_original` 선행
