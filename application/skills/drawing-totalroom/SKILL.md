---
name: drawing-totalroom
description: >-
  floor_wall_validated.dxf 모델스페이스의 TEXT/MTEXT 실명 라벨을 모두 모아,
  drawing-roomevaluator와 같은 벽 안쪽 면적으로 실마다 공간을 잡습니다.
  같은 폴더에 floor_label_detected.dxf, floor_label_detected.png,
  floor_label_detected.json을 씁니다. DXF는 검증 도면에 라벨별 레이어를
  더한 것이고, 각 레이어에 면적 HATCH와 이름·면적 문자가 있습니다.
  PNG는 그 층의 라벨 면적을 한 장에 보여 줍니다.
  각 실의 벽 테두리에 닿는 문은 `--door close`(기본) 또는 `--door open`으로 고릅니다.
  한 층의 전체 실 면적, 라벨별 면적, floor_label_detected 요청 시 사용합니다.
  대상 파일은 artifacts/drawing_list.json으로 찾습니다.
---

# drawing-totalroom (층 전체 실명 면적)

`floor_wall_validated.dxf` 의 실명 라벨마다, 그 글자가 들어 있는 **벽 안쪽 면**을 계산한다.
면적 규칙과 문 열림은 `drawing-roomevaluator` 와 같다. 입력 DXF·PNG 는 수정하지 않는다.

## When to Use

- 한 층의 **모든 실명**에 대해 면적을 구할 때
- 라벨별 레이어로 면적을 확인하고, 층 전체 색면 PNG 가 필요할 때
- 실이 하나뿐이면 `drawing-roomevaluator` 를 쓴다

## 대상 도면 찾기

건물·층 이름이 있으면 폴더를 만들지 말고 `$ARTIFACTS_DIR/drawing_list.json`에서 고른다.

1. `$ARTIFACTS_DIR/drawing_list.json`이 있으면 그 파일. 없으면 `/Users/ksdyb/Documents/src/agent-skills/application/.session_storage/lge/artifacts/drawing_list.json`.
2. 사용자가 말한 건물·프로젝트 이름을 `drawings[].source_filename`에 맞춘다. 공백·대소문자는 무시하고, 그 말이 파일명에 들어 있으면 그 도면이다. `source_filename`에 없으면 `drawing_id`, `folder`를 같은 방식으로 본다. 없거나 둘 이상이면 `source_filename`을 보여주고 고르게 한다.
3. 그 도면의 `floors[].floor`가 요청 층과 같으면 그 항목이다. `1층`은 `1F`, `지하1층`은 `B1F`, `옥상`은 `RF`로 본다. `floor`가 없고 `name_confirmed`가 false이면 `title`에 그 층 표기가 있는 항목이 후보다. 후보가 둘 이상이면 `floor`와 `title`을 보여주고 고르게 한다. 없으면 `discovered_floors`를 알리고 없는 층 폴더는 만들지 않는다.
4. 입력은 `$ARTIFACTS_DIR/<folder>/floors/<floor>/floor_wall_validated.dxf`, 같은 폴더의 `floor_wall_validated_meta.json`, `floor_wall_validated.png`이다. validated 파일이 없으면 그 층은 벽 검증 전이라고 알린다.

## Critical Rules

1. **라벨** — 모델스페이스의 `TEXT`/`MTEXT`만 본다. 블록 안 글자는 펼치지 않는다. 공백을 없앤 뒤 24자 이하이며 `면적`, `천장`, `:`, `=` 이 없는 글자 중, 한글이 있거나 영문 2자 이상에 숫자가 붙은 이름만 실명이다. `X1` 같은 축선, `6300`·`6,300` 같은 치수, `UP`/`DN` 은 빠진다. `MRI1` 은 포함한다. 집기 표기는 실명이 아니다. `미니바`, `미비바`, `옷장`, `신발장`, `화분`, `화장대`, `월풀욕조`, `욕조`, `(장애인)`과, 그 뒤에 번호만 붙은 글자(`옷장1`, `화분#2`)는 뺀다. `옷방`, `소파룸`처럼 집기 이름이 아닌 실명은 남긴다. 같은 실명이 50 mm 안에 있으면 한 곳이다. 위·아래 줄 간격이 글자 높이의 1.8배 안이고 가로로 겹치면 위 글자부터 이어 한 실명으로 만든다. 예: `투시영상` 과 `검사실7` → `투시영상검사실7`.
2. **면적** — 라벨이 있는 쪽의 **벽 안쪽 면**까지. `WALL`·`WINDOW`·`COLUMN`·`DOOR` 선을 모두 경계로 읽는다. 문 스윙 호는 경계가 아니다. 같은 벽선의 문 개구(2.4 m 이하)는 그 벽선으로 이어 실에 포함한다. 여닫이 문이 있는 개구는 그 문선에서 멈춘다.
   `--door` 기본값은 `close`다. 요청에 열림·오픈이 없으면 `close`로 실행한다. 값은 그 층의 모든 실에 같이 적용된다.
   각 실은 문을 모두 닫아 벽 테두리를 잡은 뒤, 그 테두리 위의 `DOOR` 직선과 테두리 450 mm 안의 여닫이만 그 실의 문으로 본다.
   `open`은 실마다 그 문만 연다. 테두리 밖 `DOOR`는 `open`이어도 닫힌 경계로 둔다.
3. **기둥 돌출부는 항상 뺀다.** H-Beam이 벽 안쪽 면보다 실 안으로 들어온 면적은 `area_m2`에서 제외한다. 벽 두께 안에만 있는 부분은 빼지 않는다.
4. **레이어** — `floor_label_detected.dxf` 는 `floor_wall_validated.dxf` 를 복사한 뒤 라벨 레이어를 더한 도면이다. 라벨 하나당 레이어 하나다. 같은 실명이 여러 곳이면 그 레이어에 HATCH 가 여러 개다. 레이어의 HATCH 면적 합이 그 라벨의 `area_m2`다. 각 자리에는 PNG 와 같이 실명과 `면적 ㎡` 문자를 흰 판 위에 둔다. 좌표는 입력 DXF 와 같은 mm 이다.
5. **같은 면** — 서로 다른 실명이 한 면에 있으면 각 레이어에 그 면 전체가 들어간다. `shared_with` 가 있으면 `area_m2`를 서로 더하지 않는다. 같은 실명이 서로 다른 면에 있으면 각 면의 `area_m2`를 더한다.
6. **원본 보존** — validated DXF/PNG 는 읽기 전용이다. 결과 DXF 는 그 도면의 사본에 라벨 레이어를 더한 것이고, 검증 파일을 덮어쓰지 않는다. 결과는 그 DXF 와 같은 폴더의 `floor_label_detected.dxf`, `floor_label_detected.png`, `floor_label_detected.json` 만 덮어쓴다. 번호 붙은 파일은 만들지 않는다.
7. **스크립트 절대경로** — 이 SKILL.md 옆 `scripts/detect_labels.py`.
8. 응답은 **한국어**. 경로·JSON 키·레이어 이름은 산출 그대로 쓴다.

## Script Location

```bash
SCRIPTS=/Users/ksdyb/Documents/src/agent-skills/application/skills/drawing-totalroom/scripts
# Runtime: SCRIPTS="$WORKING_DIR/skills/drawing-totalroom/scripts"

ART="${ARTIFACTS_DIR:-/Users/ksdyb/Documents/src/agent-skills/application/.session_storage/lge/artifacts}"
python3.13 "$SCRIPTS/detect_labels.py" \
  --dxf "$ART/<folder>/floors/<floor>/floor_wall_validated.dxf" \
  --meta "$ART/<folder>/floors/<floor>/floor_wall_validated_meta.json" \
  --png "$ART/<folder>/floors/<floor>/floor_wall_validated.png" \
  --door close
```

`--door`를 생략하면 `close`다. 사용자가 각 실의 인접 문을 열어서 계산하라고 하면 `--door open`을 붙인다.

| 인자 | 의미 |
| --- | --- |
| `--dxf` | `floor_wall_validated.dxf` |
| `--meta` | `floor_wall_validated_meta.json` (PNG 좌표 변환) |
| `--png` | `floor_wall_validated.png` (면적 색의 배경) |
| `--door` | `close`(기본) 또는 `open`. 실마다 그 실의 벽 테두리에 닿는 문만 연다 |

## Workflow

```
drawing_list.json 에서 건물 · 층 결정
  ↓
floor_wall_validated.dxf 의 TEXT/MTEXT 실명 목록
  ↓
라벨마다 WALL·WINDOW·COLUMN·DOOR 안쪽 면 (drawing-roomevaluator 와 같은 규칙)
  `--door open` 이면 그 실의 벽 테두리 문만 연다. 생략하면 close
  ↓
floor_label_detected.dxf    # 검증 도면 + 라벨별 레이어, HATCH, 이름·면적 문자
floor_label_detected.png    # 층 전체, 라벨별 색과 면적
floor_label_detected.json   # 라벨·인스턴스·빠진 라벨
```

## 출력

- `labels[].layer` — 검증 도면 위에 더한 DXF 레이어 이름. 그 레이어에 면적, 실명, 면적 문자가 있다.
- `rules.door` — 이번 층의 `close` 또는 `open`
- `instances[].boundary_doors` — 그 자리의 벽 테두리 문. `x`,`y`와 `state`
- `labels[].area_m2` — 그 라벨의 HATCH 면적 합 (m²). 같은 실명이 여러 면이면 더한 값이다.
- `labels[].instances[]` — 글자 좌표, 그곳의 `area_m2`, `width_m`, `height_m`.
- `instances[].shared_with` — 같은 면을 가리키는 다른 실명. 있으면 그 면은 한 번만 말한다.
- `unique_face_area_m2` — 서로 다른 면을 한 번씩만 더한 값.
- `skipped` — 실명으로 봤으나 벽이 닫히지 않은 글자.
- `png` — 배경 평면도 위에 라벨별 색면과 그 자리의 면적만 그린다. 오른쪽 범례는 붙이지 않는다. 자리 글자는 그 시트에 이미 있는 실명과 같은 높이로 그린다. 그 높이보다 작아지지 않는다. 짧은 변이 1.5 m 이상인 실이 없으면 시트 높이의 1.1%를 바닥으로 둔다.

벽이 닫히지 않은 라벨은 레이어를 만들지 않는다. 도면의 `면적` 문구는 `drawing_area_m2` 참고값이다. 계산값과 다르면 계산값을 기준으로 설명한다.

## Related

- `drawing-llmvalidator` — `floor_wall_validated` 선행
- `drawing-areasizing` — 추출 → 벽 → 검증 다음에 이 스킬로 라벨 실 면적을 낸다
