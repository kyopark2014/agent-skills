---
name: drawing-areasizing
description: >-
  CAD/DXF 도면을 층별 추출, 벽 검출, Vision LLM 벽 검증, 라벨 실 면적 조사까지
  한 번에 수행합니다. 도면 분석, 면적 조사, 실명 면적, 전체 실 면적, area sizing,
  "이 도면 면적 뽑아" 요청 시 이 스킬을 사용합니다. 내부에서 drawing-devider,
  drawing-walldetector, drawing-llmvalidator, drawing-totalroom 을 순서대로
  실행합니다. 추출만·벽만·검증만·실 하나만이면 그 개별 스킬을 씁니다.
---

# drawing-areasizing (도면 면적 조사)

도면 분석은 아래 네 단계를 **이 순서**로 끝낸다.

```
drawing-devider        층별 floor_original
        ↓
drawing-walldetector   floor_wall_original
        ↓
drawing-llmvalidator   floor_wall_validated
        ↓
drawing-totalroom      floor_label_detected
```

검출·검증·면적 규칙의 원본은 각 스킬의 `SKILL.md`다. 이 스킬은 **순서, 대상 범위, 단계 사이 파일 연결**만 정한다. 규칙을 여기에 다시 적거나, 스크립트를 새로 만들지 않는다.

## When to Use

- DXF·평면도에서 **추출부터 라벨 실 면적까지** 한 번에 필요할 때
- "도면 분석", "면적 조사", "실명 면적", "전체 실 면적"
- 건물 이름만 있고 네 단계를 이어서 달라고 할 때

한 단계만 말하면 그 스킬만 쓴다. 실이 하나뿐이면 `drawing-roomevaluator`.

## 단계마다 그 스킬을 로드

각 단계를 시작하기 **직전**에 그 스킬 지침만 로드하고, 그 단계가 끝나면 다음 스킬을 로드한다. 네 스킬을 한꺼번에 펼치지 않는다.

```
get_skill_instructions(plugin_name="base", skill_name="drawing-devider")
get_skill_instructions(plugin_name="base", skill_name="drawing-walldetector")
get_skill_instructions(plugin_name="base", skill_name="drawing-llmvalidator")
get_skill_instructions(plugin_name="base", skill_name="drawing-totalroom")
```

도구가 없으면 각 `SKILL.md`를 읽는다. 경로는 아래 `SKILLS` 기준이다.

로드한 스킬의 **Critical Rules, Workflow, Script Location, Failure Modes**를 그 단계에 적용한다. 스크립트 인자·판정 기준·덮어쓰기 규칙은 그 파일이 최신이다. 이 문서의 명령 예와 다르면 **그 스킬 문서를 따른다.**

## Critical Rules

1. **기존 스킬만 실행** — 추출·벽·보정·실면적 코드를 새로 작성하지 않는다. ad-hoc DXF 파싱도 하지 않는다.
2. **층당 bash 1회** — `extract_2d`, `detect_walls_floor`, `prepare_review`, `correct_walls_floor`, `render_wall_diff`, `detect_labels`는 호출 하나당 층 하나. `for FLOOR in …` 일괄, `--floor all`, `detect_walls_all` 은 쓰지 않는다.
3. **확인 없이 범위 끝까지** — 층 사이·단계 사이에 진행 여부를 묻지 않는다. 그 사이에 채팅 문장도 쓰지 않고, 도구 결과 뒤에 바로 다음 도구를 호출한다. 사용자에게 보이는 글은 전 층이 끝난 뒤 `area_sizing.md`와 요약 한 번뿐이다.
4. **Vision은 생략하지 않는다** — `prepare_review.py` 다음에 `view_image.py`를 그 층에 1회 실행한다. `view_image` 도구는 없다. 판정은 채팅에 쓰지 않는다. 로그에 `review.json` 경로가 나온 뒤에 `correct_walls_floor.py`를 실행한다. prepare와 correct를 한 bash에 넣지 않는다. `floor_wall_original.png` 원본은 Vision에 넣지 않는다. `read_file`과 S3 업로드로 이미지를 보지 않는다.
5. **기존 파일은 skip 하지 않는다** — `floor_original.*`, `floor_wall_original.*`, `floor_wall_validated.*`, `llm_review/`, `floor_label_detected.*`, `structure.*`, `area_sizing.md`가 이미 있어도 그 층·그 단계를 건너뛰거나 묻지 않는다. 같은 경로에 다시 써서 **덮어쓴다**. 도면 폴더를 통째로 지우지는 않는다. 원본 DXF와 각 단계의 읽기 전용 입력(직전 단계 산출)은 그 단계에서 수정하지 않는다.
6. **실패 층** — 그 층은 실패한 단계에서 멈춘다. 나머지 층은 이어서 처리하고, 마지막 보고에 실패 층을 적는다.
7. 응답은 **한국어**. 경로·JSON 키·층 이름은 산출 그대로 쓴다.

## 경로

```bash
if [ -n "${WORKING_DIR:-}" ] && [ -f "$WORKING_DIR/skills/drawing-devider/scripts/extract_2d.py" ]; then
  SKILLS="$WORKING_DIR/skills"
else
  SKILLS="/Users/ksdyb/Documents/src/agent-skills/application/skills"
fi

if [ -z "${ARTIFACTS_DIR:-}" ] || [ ! -d "$ARTIFACTS_DIR" ]; then
  ARTIFACTS_DIR="/Users/ksdyb/Documents/src/agent-skills/application/.session_storage/lge/artifacts"
fi

test -f "$SKILLS/drawing-devider/scripts/extract_2d.py"
test -f "$SKILLS/drawing-walldetector/scripts/detect_walls_floor.py"
test -f "$SKILLS/drawing-llmvalidator/scripts/prepare_review.py"
test -f "$SKILLS/drawing-totalroom/scripts/detect_labels.py"
test -d "$ARTIFACTS_DIR"
```

`skills/...` 상대경로로 호출하지 않는다. cwd가 `artifacts/`이면 실패한다.

## 대상

1. `$ARTIFACTS_DIR/drawing_list.json`이 있으면 그 파일. 없으면 위 기본 artifacts의 `drawing_list.json`.
2. 건물·프로젝트 이름은 `drawings[].source_filename`에 맞춘다. 공백·대소문자는 무시하고, 그 말이 파일명에 들어 있으면 그 도면이다. 없으면 `drawing_id`, `folder`를 같은 방식으로 본다. 없거나 둘 이상이면 `source_filename`을 보여주고 고르게 한다.
3. 사용자가 층을 지정하면 그 층만 네 단계 모두 처리한다. `1층`은 `1F`, `지하1층`은 `B1F`, `옥상`은 `RF`. `floor`가 없고 `name_confirmed`가 false이면 `title`에 그 층 표기가 있는 항목이 후보다. 후보가 둘 이상이면 `floor`와 `title`을 보여주고 고른다.
4. 층을 말하지 않으면, ①에서 발견된 층(`discovered_floors`, 도곽이면 `sheet_XX` 포함)을 순서대로 모두 처리한다. `sheet_XX`는 층 이름이 미확정인 도곽이지, 빼 두는 층이 아니다.
5. 원본 DXF만 있고 목록에 없으면 `drawing-devider`로 추출한 뒤 그 `drawing_id`를 대상으로 삼는다. 원본 DXF는 수정하지 않는다.

## Workflow

```
대상 도면 · 층 범위 결정
  ↓
① drawing-devider     범위 안 전 층 추출 → analyze_drawing 1회
  ↓                     (floor_original.* 가 있어도 skip 하지 않고 덮어쓰기)
② 층마다, 확인 없이, 채팅 문장 없이:
     drawing-walldetector
     drawing-llmvalidator    ← Vision review.json 필수
     drawing-totalroom
  ↓                     (각 산출이 있어도 skip 하지 않고 덮어쓰기)
③ $ARTIFACTS_DIR/<drawing_id>/area_sizing.md  ← 여기서만 사용자에게 보고
```

산출물이 이미 있어도 그 단계를 생략하지 않는다. 범위 안 전 층·전 단계를 다시 실행하고 같은 경로에 덮어쓴다.

①을 범위 안 전 층에 대해 끝낸 다음 ②로 간다. ②는 **층 하나 안에서** 벽 검출 → LLM 검증 → 실 면적을 끝내고 다음 층으로 간다. Vision 조각이 여러 층에 쌓이지 않게 한다. 스크립트가 끝나면 채팅에 쓰지 않고 바로 다음 도구를 호출한다.

### ① drawing-devider

`get_skill_instructions(..., "drawing-devider")` 후 그 Workflow를 따른다.

- 도곽 도면이면 먼저 `--list-floors`. 나온 이름만 `--floor`에 넣는다.
- 층마다 bash 1회:

```bash
python3 "$SKILLS/drawing-devider/scripts/extract_2d.py" \
  --dxf "$ARTIFACTS_DIR/<input>.dxf" \
  --floor <F> \
  --out "$ARTIFACTS_DIR" \
  --drawing-id <drawing_id>
```

- 범위 안 추출이 끝나면 `analyze_drawing.py` 1회 → `structure.md` / `structure.json`.
- `floor_original.*`가 이미 있어도 그 층 추출을 skip 하지 않는다. 같은 경로에 덮어쓴다.
- 다음 단계 입력: `floors/<F>/floor_original.dxf` 와 `floor_original.png`.

### ②-a drawing-walldetector

`get_skill_instructions(..., "drawing-walldetector")` 후 그 층의 벽만 검출한다.

```bash
python3 "$SKILLS/drawing-walldetector/scripts/detect_walls_floor.py" \
  --artifacts "$ARTIFACTS_DIR/<drawing_id>" \
  --floor <F>
```

`floor_wall_original.*`가 이미 있어도 skip 하지 않고 덮어쓴다. 다음 단계 입력: `floors/<F>/floor_wall_original.dxf` 와 `.png`. original은 이후 단계에서 읽기 전용이다.

### ②-b drawing-llmvalidator

`get_skill_instructions(..., "drawing-llmvalidator")` 의 판정 기준으로 그 층만 검증한다.

```bash
python3 "$SKILLS/drawing-llmvalidator/scripts/prepare_review.py" \
  --artifacts "$ARTIFACTS_DIR/<drawing_id>" \
  --floor <F>
```

`floor_wall_validated.*`와 `llm_review/`가 이미 있어도 skip 하지 않는다. `view_image` 도구는 호출하지 않는다. `view_image.py`가 타일을 보고 `llm_review/review.json`을 쓴다. 판정은 채팅에 쓰지 않는다. 기준은 그 스킬의 Vision 판정(문·기둥·창·복도·계단·엘리베이터)을 따른다.

```bash
if command -v python3.13 >/dev/null 2>&1; then PY=python3.13; else PY=python3; fi
LOG="$ARTIFACTS_DIR/<drawing_id>/floors/<F>/llm_review/view_image.log"
nohup "$PY" "$SKILLS/drawing-llmvalidator/scripts/view_image.py" \
  --artifacts "$ARTIFACTS_DIR/<drawing_id>" \
  --floor <F> > "$LOG" 2>&1 &
echo "started $!"
tail -n 20 "$LOG"
```

로그에 `review.json` 경로가 나온 뒤에 correct를 실행한다.

```bash
python3 "$SKILLS/drawing-llmvalidator/scripts/correct_walls_floor.py" \
  --artifacts "$ARTIFACTS_DIR/<drawing_id>" \
  --floor <F>

python3 "$SKILLS/drawing-llmvalidator/scripts/render_wall_diff.py" \
  --artifacts "$ARTIFACTS_DIR/<drawing_id>" \
  --floor <F>
```

다음 단계 입력: `floor_wall_validated.dxf`, `floor_wall_validated_meta.json`.

### ②-c drawing-totalroom

`get_skill_instructions(..., "drawing-totalroom")` 후 그 층의 라벨 실을 계산한다. 인터프리터는 그 스킬과 같이 `python3.13`이다.

```bash
python3.13 "$SKILLS/drawing-totalroom/scripts/detect_labels.py" \
  --dxf "$ARTIFACTS_DIR/<drawing_id>/floors/<F>/floor_wall_validated.dxf" \
  --meta "$ARTIFACTS_DIR/<drawing_id>/floors/<F>/floor_wall_validated_meta.json" \
  --door close
```

`--door`를 생략하면 `close`다. 사용자가 각 실의 인접 문을 열라고 하면 `--door open`을 붙인다.

`floor_label_detected.*`가 이미 있어도 skip 하지 않고 같은 폴더에 덮어쓴다. 산출: `floor_label_detected.dxf`, `.png`, `.json`.

### ③ 보고

`$ARTIFACTS_DIR/<drawing_id>/area_sizing.md`를 쓰고 사용자에게 경로와 요약을 전달한다.

```markdown
# 도면 면적 조사 — <drawing_id>

## 요약
- 원본: <source_filename>
- 범위: <층 목록 또는 지정 층>
- 상태: 완료 | 일부 실패

## 층별 결과
| 층 | floor_original | floor_wall_validated | 라벨 수 | unique_face_area_m2 | 상태 |
|----|----------------|----------------------|---------|---------------------|------|

## 실패
| 층 | 단계 | 이유 |
|----|------|------|

## 파일
| 층 | floor_label_detected.json | floor_label_detected.png |
|----|---------------------------|--------------------------|
```

면적 숫자는 `floor_label_detected.json`의 `labels[].area_m2`, `unique_face_area_m2`를 그대로 쓴다. `shared_with`가 있는 면은 합계에 두 번 넣지 않는다. 도면의 `면적` 문구(`drawing_area_m2`)와 계산값이 다르면 계산값을 기준으로 말한다. `skipped`는 벽이 닫히지 않은 라벨로 적는다.

## 단계 연결

| 단계 | 스킬 | 입력 | 출력 |
|------|------|------|------|
| 추출 | `drawing-devider` | 원본 DXF | `floors/<F>/floor_original.dxf` `.png` |
| 벽 | `drawing-walldetector` | `floor_original.dxf` | `floor_wall_original.dxf` `.png` |
| 검증 | `drawing-llmvalidator` | `floor_wall_original.*` | `floor_wall_validated.dxf` `.png` `_meta.json` |
| 실면적 | `drawing-totalroom` | `floor_wall_validated.dxf` `_meta.json` | `floor_label_detected.dxf` `.png` `.json` |

## Decision Checklist

- [ ] 네 단계를 devider → walldetector → llmvalidator → totalroom 순으로 실행했는가
- [ ] 각 단계 전에 그 스킬 지침을 로드하고, 스크립트는 그 스킬 `scripts/`만 호출했는가
- [ ] 층당 bash 1회인가
- [ ] LLM 단계에서 `review.json`을 Vision으로 쓴 뒤에 correct를 실행했는가
- [ ] 지정 층만, 또는 미지정이면 발견 층 전체를 처리했는가
- [ ] 기존 산출이 있어도 단계를 skip 하지 않고 같은 경로에 덮어썼는가
- [ ] 단계 사이에 채팅 문장을 쓰지 않았는가
- [ ] 전 층이 끝난 뒤에만 `area_sizing.md`와 층별 `floor_label_detected.*` 경로를 보고했는가

## Failure Modes

| 증상 | 대응 |
|------|------|
| 산출 파일이 이미 있음 | skip 하지 않는다. 그 단계를 다시 실행해 같은 경로에 덮어쓴다 |
| `floor_original.dxf` 없음 | ①을 그 층에 대해 다시 실행한 뒤 ②로 간다 |
| `floor_wall_original.dxf` 없음 | walldetector를 그 층에 대해 실행한 뒤 검증으로 간다 |
| `floor_wall_validated.dxf` 없음 | totalroom으로 가지 않는다. llmvalidator를 끝낸 뒤 진행한다 |
| `TimeoutExpired` | 그 층의 해당 스크립트만 다시 실행한다. 여러 층을 한 명령에 묶지 않는다 |
| `sheet_XX`만 발견됨 | `1F`로 바꾸지 않는다. 발견된 이름으로 네 단계를 진행한다 |
| 자식 스킬 명령이 이 문서와 다름 | 자식 `SKILL.md`를 따른다 |

## Related

- `drawing-devider` — 층 추출
- `drawing-walldetector` — 벽 검출
- `drawing-llmvalidator` — Vision 벽 검증
- `drawing-totalroom` — 라벨 실 면적
- `drawing-roomevaluator` — 실 하나
