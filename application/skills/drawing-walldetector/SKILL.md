---
name: drawing-walldetector
description: >-
  drawing-devider가 만든 층 floor_original DXF에서 벽을 검출하고 빨간색 WALL
  레이어 DXF/PNG로 저장합니다. 벽 검출, wall detect, wall DXF, 평면도 벽체,
  floor_wall_original, "하이닉스 5F 벽"처럼 건물·층으로 지정하는 요청 시 사용합니다.
  대상 파일은 artifacts/drawing_list.json으로 찾습니다.
---

# drawing-walldetector (벽 검출)

`drawing-devider`가 만든 **층 단위** `floor_original.dxf`에서 벽을 찾아 **빨간색**으로
표시한 DXF(및 검수용 PNG)를 생성한다. 기본 산출은 **`floor_wall_original.*`** 이고,
프로젝트 조건과 샘플에서 모은 도면별 조건을 빼면 **`floor_wall_common.png`** 다.

## When to Use

- `floors/<F>/floor_original.dxf`에서 층 전체 벽을 뽑을 때
- 벽체 DXF / 빨간 벽 오버레이 / wall detect / `floor_wall_original` 요청 시
- "하이닉스 5F 벽"처럼 **건물·층**만 있고 경로가 없을 때

## 대상 도면 찾기

건물·층 이름이 있으면 폴더를 만들지 말고 `$ARTIFACTS_DIR/drawing_list.json`에서 고른다.

1. `$ARTIFACTS_DIR/drawing_list.json`이 있으면 그 파일. 없으면 `/Users/ksdyb/Documents/src/agent-skills/application/.session_storage/lge/artifacts/drawing_list.json`.
2. 사용자가 말한 건물·프로젝트 이름을 `drawings[].source_filename`에 맞춘다. 공백·대소문자는 무시하고, 그 말이 파일명에 들어 있으면 그 도면이다. 예: `하이닉스` → `SK용인하이닉스_지원동 평면도_241014.dxf` → `folder` `sk_yongin_jiwon`. `source_filename`에 없으면 `drawing_id`, `folder`를 같은 방식으로 본다. 없거나 둘 이상이면 `source_filename`을 보여주고 고르게 한다.
3. 그 도면의 `floors[].floor`가 요청 층과 같으면 그 항목이다. `1층`은 `1F`, `지하1층`은 `B1F`, `옥상`은 `RF`로 본다. `floor`가 없고 `name_confirmed`가 false이면 `title`에 그 층 표기가 있는 항목이 후보다. 후보가 둘 이상이면 `floor`와 `title`을 보여주고 고르게 한다. 없으면 `discovered_floors`를 알리고 없는 층 폴더는 만들지 않는다.
4. 이 스킬의 입력은 그 층의 `dxf`·`png`이다. artifacts 루트 기준 상대경로이며 파일은 `floor_original.dxf` / `floor_original.png`이다. 절대경로는 `$ARTIFACTS_DIR/<folder>/floors/<floor>/floor_original.dxf`이다. 사용자가 층을 하나만 말하면 그 층만 검출한다. 층을 말하지 않으면 `discovered_floors`를 순서대로 검출한다.

## Critical Rules

1. **입력은 층 floor_original** — 위 절차로 고른 `$ARTIFACTS_DIR/<folder>/floors/<F>/floor_original.dxf`만 사용한다. 원본 277MB DXF를 직접 돌리지 않는다. `parts/`·`floor_parts_index.json`은 **필수가 아니다**.
2. **입력 미리보기** — 검출 입력은 `$ARTIFACTS_DIR/<drawing_id>/floors/<F>/floor_original.png` 이다. 사용자에게 진행 여부를 묻지 않는다.
3. **기존 산출** — `floors/<F>/floor_wall_original.*`가 **이미 있어도 묻지 않는다**. 바로 검출하고 **덮어쓴다**. 폴더를 통째로 지우지는 않는다.
4. **범위** — 사용자가 층을 지정하면 그 층만 검출한다. 층을 말하지 않으면 `discovered_floors`를 순서대로 끝까지 검출한다. 파일럿 확인은 받지 않는다.
5. **여러 층은 한 번에** — 층이 둘 이상이면 `detect_walls_all.py` 한 번. 물리 CPU 코어 수만큼 층을 동시에 검출한다. 한 층이면 `detect_walls_floor.py`. `for FLOOR in …` 는 쓰지 않는다. 층 사이에 사용자 확인은 없다.
6. **벽은 빨간색** — 출력 DXF의 `WALL` 레이어(ACI 1). 베이스 기하는 `BASE`(회색).
7. **산출 경로** — `$ARTIFACTS_DIR/<drawing_id>/floors/<F>/floor_wall_original.*` (프로젝트 조건과 샘플 조건 포함). 같은 폴더의 `floor_wall_common.png`는 `common`만 적용한 검수용이다. 레거시 타일은 `walls/` (선택).
8. **도면별 벽 치수** — 벽 두께·길이는 도면마다 다르다. `wall_conditions.json` 의 `common` 은 공통값이고, 그 도면에서 빠진 벽은 샘플 2장을 읽어 모은다. `floors/<F>/wall_samples/wall_conditions.json` 이 없으면 검출 전에 만들고, 이미 있으면 묻지 않고 그 파일을 쓴다. 사용자가 다시 뽑으라고 할 때만 샘플부터 다시 한다.
9. **스크립트 사용** — `$WORKING_DIR/skills/drawing-walldetector/scripts/` 로만 수행. ad-hoc 대용량 파싱 금지.
10. 응답은 **한국어**. 경로·JSON 키는 영문/숫자 유지.
11. **타일 검출(레거시)** — `parts/`가 있고 사용자가 명시한 때만 `--with-tiles`.

## Script Location

`bash` / `execute_code`의 cwd는 `artifacts/`이다. 스킬 스크립트는 `$WORKING_DIR/skills/...`로 호출하세요.
(`WORKING_DIR`는 bash 도구가 주입하는 환경변수이며, Runtime에서는 `/app`이다.)

| 스크립트 | 용도 |
| --- | --- |
| `$WORKING_DIR/skills/drawing-walldetector/scripts/sample_wall_conditions.py` | 가운데 샘플 2장 → 도면별 벽 두께·길이 조건 |
| `$WORKING_DIR/skills/drawing-walldetector/scripts/detect_walls_floor.py` | **한 층** `floor_wall_original` (기본). `wall_samples/wall_conditions.json` 이 있으면 붙인다 |
| `$WORKING_DIR/skills/drawing-walldetector/scripts/detect_walls_tile.py` | 단일 DXF (레거시·선택) |
| `$WORKING_DIR/skills/drawing-walldetector/scripts/detect_walls_all.py` | 층이 둘 이상. 물리 CPU 코어 수만큼 동시 검출 |
| `$WORKING_DIR/skills/drawing-walldetector/scripts/lib_walls.py` | 평행 이중선 기반 벽 분류·DXF/PNG |

**IMPORTANT**: `skills/...` 또는 `scripts/...` 상대경로를 쓰지 마세요. cwd가 `artifacts/`라 실패합니다.  
구경로 `cde-pilot/.../skills/...` 도 쓰지 마세요.

```bash
SCRIPTS="$WORKING_DIR/skills/drawing-walldetector/scripts"
ART="$ARTIFACTS_DIR/<drawing_id>"

# 한 층
python3 "$SCRIPTS/detect_walls_floor.py" --artifacts "$ART" --floor 12F

# 여러 층. workers 생략 시 물리 CPU 코어 수
python3 "$SCRIPTS/detect_walls_all.py" --artifacts "$ART"
```

로컬 개발(비 Runtime) 예:

```bash
SCRIPTS=/Users/ksdyb/Documents/src/agent-skills/application/skills/drawing-walldetector/scripts
ART=/path/to/user/artifacts/<drawing_id>
```

---

## Workflow (필수 순서)

```
drawing_list.json 에서 건물(source_filename) · 층(floor) 결정
  ↓
drawing-devider 산출물
  $ARTIFACTS_DIR/<folder>/floors/<F>/floor_original.{dxf,png}
  ↓
⓪ floor_original.dxf 확인 (없으면 devider 먼저. 목록의 sheet_XX 포함)
  ↓ (floor_wall_original.* 가 있어도 묻지 않고 덮어쓰기)
① 샘플 2장으로 도면별 벽 조건   ← wall_samples/wall_conditions.json 이 없을 때만
     sample_wall_conditions.py --vision
     → floors/<F>/wall_samples/wall_conditions.json
  ↓
② 층이 하나면 detect_walls_floor.py
   층이 둘 이상이면 detect_walls_all.py  ← 물리 CPU 코어 수만큼 동시
     → floors/<F>/floor_wall_original.*   ← common + project + 샘플 조건
     → floors/<F>/floor_wall_common.png   ← common 조건만, project·샘플 미적용
  ↓
③ walls_all_index.json / work_log 갱신 (선택)
```

### ⓪ 기존 산출

`floor_wall_original.dxf` / `.png` / `_meta.json` / `floor_wall_index.json`이 이미 있어도 **묻지 않고 덮어쓴다**. 폴더 전체를 삭제하지 않는다.

### ① 샘플에서 벽 두께·길이 모으기

`common` 숫자만으로는 도면마다 다른 짧은 벽·얇은 벽이 빠진다. 검출 전에 그 층의 한가운데 샘플 **2장**을 읽고, LLM이 벽이라고 한 이중선의 간격·길이를 DXF에서 재서 `wall_conditions.json` 과 같은 조건으로 저장한다. `common`이 이미 잡는 쌍은 조건에 넣지 않는다.

`floors/<F>/wall_samples/wall_conditions.json` 이 **이미 있으면 이 단계를 건너뛴다**. 사용자가 다시 뽑으라고 하면 `wall_samples/` 를 지우고 처음부터 한다.

```bash
if command -v python3.13 >/dev/null 2>&1; then PY=python3.13; else PY=python3; fi
"$PY" "$SCRIPTS/sample_wall_conditions.py" \
  --artifacts "$ART" --floor "$FLOOR" --vision
```

스크립트가 샘플 PNG 두 장을 만들고, Vision으로 `observations.json`을 쓴 뒤 `wall_conditions.json`까지 만든다. `view_image` 도구는 없다. `read_file`은 픽셀을 돌려주지 않는다. 샘플을 보려고 `upload_file_to_s3`를 호출하지 않는다. `floor_original.png` 원본은 넣지 않는다.

샘플마다 벽 bbox를 나눈다. 벽이 없으면 `walls`는 `[]`다.

```json
{
  "samples": [
    {"id": "sample_01", "walls": [{"bbox": [0.10, 0.20, 0.14, 0.80]}]},
    {"id": "sample_02", "walls": []}
  ]
}
```

bbox 는 그 샘플 이미지 기준이다. 왼쪽 위가 `(0, 0)`, 오른쪽 아래가 `(1, 1)` 이다.

`wall_samples/wall_conditions.json` 의 `conditions` 가 검출에 붙는다. `measurements` 는 잰 간격·길이고, `covered_by_common` 이 true 인 쌍은 조건으로 만들지 않는다. 벽이 하나도 없어도 파일은 만든다. 그 경우 검출은 `common` 만 쓴다.

### 검출 개요

레이어가 `0arch` 단일인 경우가 많아, **축정렬 세그먼트의 평행 이중선(벽 두께 대역)** 으로 벽을 판정한다.

- 두께 기본: 30–420 mm (마감선이 30–45 mm로 떨어진 외벽 포함)
- 최소 세그먼트 길이: 500 mm
- 같은 대역에서 간격이 성긴 다수 평행선(계단 해칭) · ARC/CIRCLE(문·설비) 제외
- 벽 두께 안에 평행선이 여러 겹이고 간격이 촘촘해도, 그 이유만으로 2.8 m 이상 긴 선을 외벽으로 두지 않는다
- 양쪽이 1.7 m 이상이고 간격이 250 mm 이하라는 이유만으로 개구 조각 벽 쌍으로 두지 않는다
- **X자 문은 벽이 아니다.** 교차하는 대각선(LINE 쌍 또는 X 폴리라인)은 WALL에서 뺀다. 문 궤적선이 이중선 사이에 있으면 그 궤적은 제외하고 바깥 면만 벽으로 둔다.
- **창은 벽 경계이고 레이어는 WINDOW다.** 같은 개구에 나란히 겹친 얇은 창틀(두께 15–90 mm, 길이 0.4–1.6 m)은 `WINDOW`로 저장한다. 객실 이중벽 한가운데, 양 끝 작은 사각 캡과 그 사이 0.9–1.2 m 선은 창이다. 202호 옷장과 안쪽 사이, 206호 아래 화살표가 그 예다. 그 선과 캡, 캡 사이 양면은 벽처럼 올리되 레이어는 `WINDOW`다. 캡 밖으로 이어진 면과 끝의 짧은 막이선은 `WALL`로 올린다. 같은 형태는 층 전체에서 같이 처리한다. 객실 위쪽의 두 단 미서기창(206호 화살표)은 약 1 m 선이 좌우로 어긋나 개구 약 1.9 m를 이룬다. 창선은 벽처럼 올리되 레이어는 `WINDOW`다. 개구 양끝에서 이어진 인접 면과 끝의 짧은 막이 사각은 `WALL`로 올린다. 돌출창 윤곽은 `WALL`이다. 한 폴리선에서 45° 볼살, 축에 나란한 바깥면, 반대 45° 볼살이 이어지고 돌출이 0.25–1.0 m이면 그 윤곽을 `WALL`로 올린다. 창틀 모서리의 짧은 사각만 벽이 아니다.
- **기둥은 COLUMN, 문짝은 DOOR이다.** H-Beam 정사각은 `COLUMN`으로 저장한다. X자 문과 여닫이 문짝은 `DOOR`로 저장한다. 나머지 벽은 `WALL`이다.
- **문짝 너머의 양옆 벽은 벽이다.** 가장 가까운 평행선이 벽이 아니면, 그다음 긴 평행선을 간격만으로 벽 짝에 넣지 않는다. 작은 사각 두 개와 짧은 스윙으로 그린 여닫이문도 같다. 문 표시와 벽 두께 안의 문선은 내리고, 양옆 면은 올린다. 1/4 스윙의 문짝이 여러 줄이면 그 한가운데에 WALL 한 줄을 둔다. 힌지에 붙은 문짝이 한 줄이면 그 줄을 WALL로 둔다. 스윙 호는 벽이 아니다.
- **여닫이문 잎은 벽이 아니다.** 두께 20–80 mm, 폭 0.65–1.45 m 인 문짝만 WALL에서 뺀다. 그 잎 옆에 가깝다는 이유만으로 다른 도형을 문으로 넣지 않는다. 문끝에 붙어 있고 기존 벽과 80 mm 이내로 이어진 짧은 벽(문선·벽 끝)은 WALL로 올린다.
- **개구로 잘린 간벽은 벽이다.** 옷장에 붙은 이중선처럼 조각이 0.6–1.3 m여도, 같은 직선에서 맞닿은 런이 2.2 m 이상이고 간격이 120–180 mm(간벽)이면 WALL로 유지한다. 세로 간벽은 조각이 1 m 안팎이어도, 같은 두 면 위에서 X 문 개구에 맞닿아 있으면 벽이다.
- **연속된 실 테두리는 벽이다.** 가장 가까운 평행선 간격이 30–420 mm이고, 짧은 쪽이 2.2 m 이상이며, 겹침이 짧은 쪽의 80% 이상이면 WALL이다. 그 두 면과 좌표가 8 mm 이내이면, 짧은 쪽이 2.5 m 이상인 조각도 WALL이다. 접견실#3처럼 간격 200 mm이고 조각이 하나인 7.9 m 이중선과, 1.3 m 개구 너머의 2.8 m가 이 경우다. 2.2 m 이상으로 80% 이상 겹치는 평행선이 8개 이상이면 이 조건으로 올리지 않는다. 끝의 짧은 문짝은 그 개수에 넣지 않는다.
- **H-Beam 기둥**(중첩 정사각, 변 ≤ 1.2 m, 가로·세로 차이 22% 이내)은 `COLUMN`으로 저장한다. 가구 사각은 제외한다. 사각 안에 호가 둘 이상이거나 짧은 선이 많은 표식(휠체어)은 기둥이 아니다.

---

## Artifacts Layout

산출물은 **사용자 artifacts** (`$ARTIFACTS_DIR/<drawing_id>/`) 아래에만 둔다.

```text
$ARTIFACTS_DIR/<drawing_id>/
├── floors/<FLOOR>/
│   ├── floor_original.dxf / .png          # (devider) 입력 · 미리보기
│   ├── floor_wall_original.dxf / .png / _meta.json  # 층 전체 벽 (프로젝트·샘플 조건 포함)
│   ├── floor_wall_common.png / _meta.json           # common 조건만
│   ├── floor_wall_index.json              # 층 요약
│   └── wall_samples/
│       ├── sample_01.png / sample_02.png  # 가운데 샘플
│       ├── samples.json                   # 샘플 mm 창 · prompt
│       ├── observations.json              # LLM 벽 bbox
│       └── wall_conditions.json           # 모은 두께·길이 조건
└── walls_all_index.json                   # (선택) 다층 요약
```

레거시 타일(`--with-tiles`) 시만 `walls/R*C*_walls.*` · `walls/walls_index.json` 생성.

---

## Decision Checklist

- [ ] 건물·층은 `drawing_list.json`의 `source_filename`·`floor`로 골랐는가
- [ ] `$ARTIFACTS_DIR/<folder>/floors/<F>/floor_original.dxf`가 있는가
- [ ] `wall_samples/wall_conditions.json` 이 없으면 `sample_wall_conditions.py --vision` 으로 조건을 모았는가
- [ ] `floor_wall_original.*`가 이미 있어도 묻지 않고 덮어썼는가
- [ ] 스크립트를 `$WORKING_DIR/skills/drawing-walldetector/scripts/...`로 호출하는가
- [ ] 다층이면 확인 없이 전 층을 이어서 검출했는가
- [ ] 여러 층은 `detect_walls_all.py` 한 번인가 (`for` 루프 없음)

---

## Failure Modes

| 증상 | 대응 |
|------|------|
| `floor_original.dxf` 없음 | 먼저 `drawing-devider` `extract_2d.py --floor <F>`. `<F>`는 `--list-floors`에 있는 이름만 쓴다 |
| 요청 층이 `1F`인데 목록은 `sheet_XX` | `--floor 1F`로 끝내지 않는다. `sheet_XX`를 추출한 뒤 그 폴더로 벽 검출을 이어 간다. "여러 층 표기가 혼재해 영역을 확정하지 못했다"고 보고하지 않는다. 층 이름만 미확정이다 |
| `skills/...` / `cde-pilot/...` 경로 실패 | `$WORKING_DIR/skills/drawing-walldetector/scripts/...` 사용 |
| `floor_wall_original.*` 이미 존재 | 묻지 않고 덮어쓴다. 폴더는 삭제하지 않는다 |
| Timeout / 전층 일괄 실패 | `detect_walls_all` 을 유지한다. 동시에 너무 많으면 `--workers` 를 줄인다 |
| 과검출·미검출 | 샘플 조건이 오래됐으면 `wall_samples/` 를 지우고 다시 모은다. 그래도 남으면 `min_len_mm` / `thick_min_mm` / `thick_max_mm` 조정 (reference.md) |
| 샘플에서 벽을 못 찾음 | `observations.json` 의 `walls` 를 `[]` 로 두고 조건 파일을 만든다. 검출은 common 만 쓴다 |

---

## 사용자에게 전달할 내용

- 층별 `$ARTIFACTS_DIR/<drawing_id>/floors/<F>/floor_wall_original.*` 경로
- `floor_wall_original.png` (벽=빨강, 프로젝트 조건 포함)
- `floor_wall_common.png` (벽=빨강, common 조건만)
- 완료 시: 층별 벽 통계. 중간 층에서 진행 여부를 묻지 않는다
- `sheet_XX`는 추출·검출이 끝난 도곽이다. 지상 1층 미완료로 적지 않고, 층 이름이 미확정인 도곽으로 적는다

## Related

- `drawing-devider` — `floor_original` 층 추출 선행 스킬
- `drawing-areasizing` — 추출부터 실명 면적까지 이 스킬을 포함해 순서대로 수행
- `scripts/lib_walls.py` — 벽 분류·렌더 구현
