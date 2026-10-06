# drawing-areasizing — 동작 상세

CAD/DXF를 **층 추출 → 벽 검출 → Vision 검증 → 실명 면적**까지 한 번에 수행합니다.  
자체 스크립트는 없습니다. 각 단계의 구현은 자식 스킬 `scripts/`이고, 이 스킬은 **순서와 파일 연결**만 정합니다.

판정·인자·덮어쓰기의 원본은 각 `SKILL.md`입니다. 이 문서와 자식 문서가 다르면 자식 스킬을 따릅니다.

---

## 1. 역할·입출력

| 항목 | 내용 |
|------|------|
| 스킬 경로 | `agent-skills/application/skills/drawing-areasizing/` |
| 언제 | 추출부터 라벨 실 면적까지 한 번에. "도면 분석", "면적 조사", "실명 면적" |
| 한 단계만 | 그 스킬만. 실이 하나면 `drawing-roomevaluator` |
| 보고 | `$ARTIFACTS_DIR/<drawing_id>/area_sizing.md` |

```text
drawing-devider        floors/<F>/floor_original.dxf / .png
        ↓
drawing-walldetector   floor_wall_original.*
        ↓
drawing-llmvalidator   floor_wall_validated.*
        ↓
drawing-totalroom      floor_label_detected.dxf / .png / .json
```

## 2. 대상

1. `$ARTIFACTS_DIR/drawing_list.json`의 `source_filename`으로 도면을 고릅니다. 없으면 `drawing_id`, `folder`.

```496:506:agent-skills/application/skills/drawing-devider/scripts/extract_2d.py
_DRAWING_KEY_ORDER = (
    "drawing_id",
    "folder",
    "source_filename",
    "source_path",
    ...
    "discovered_floors",
)
```

`source_filename`을 `floors`보다 앞에 두어, 목록이 길어져도 원본 파일 이름으로 도면을 찾습니다.
2. 층을 지정하면 그 층만 네 단계를 모두 처리합니다. `1층`은 `1F`, `지하1층`은 `B1F`, `옥상`은 `RF`.
3. 층을 말하지 않으면 `discovered_floors`(도곽이면 `sheet_XX` 포함)를 순서대로 처리합니다.
4. 목록에 없고 원본 DXF만 있으면 `drawing-devider`로 추출한 뒤 그 `drawing_id`를 대상으로 삼습니다.

## 3. 실행 순서

```text
① drawing-devider     범위 안 전 층 추출 → analyze_drawing 1회
② 층마다:
     walldetector → llmvalidator (review.json 필수) → totalroom
③ area_sizing.md
```

①을 범위 안 전 층에 대해 끝낸 다음 ②로 갑니다. ②는 **층 하나 안에서** 벽 검출, Vision 검증, 실 면적을 끝내고 다음 층으로 갑니다. 이 스킬의 Python 파일은 없고, 아래 산출 파일 이름이 단계 사이의 계약입니다.

```755:758:agent-skills/application/skills/drawing-devider/scripts/extract_2d.py
    if skip_furniture is None:
        skip_furniture = variant == "clean"
    if include_extra is None:
        include_extra = variant == "original"
```

첫 단계는 기본 `original`이라 가구 INSERT를 유지하고, 조경·천장·P코어도 넣습니다. 마지막 단계는 `detect_labels.py`가 `evaluate_room`의 면적 함수를 라벨마다 부릅니다.

| 단계 | 스크립트 | 입력 | 출력 |
|------|----------|------|------|
| 추출 | `drawing-devider/scripts/extract_2d.py` | 원본 DXF | `floor_original.dxf` `.png` |
| 벽 | `drawing-walldetector/scripts/detect_walls_floor.py` | `floor_original.dxf` | `floor_wall_original.*` |
| 검증 | `prepare_review.py` → `view_image.py` → `correct_walls_floor.py` → `render_wall_diff.py` | `floor_wall_original.*` | `floor_wall_validated.*` |
| 실면적 | `drawing-totalroom/scripts/detect_labels.py` | `floor_wall_validated.dxf` `_meta.json` | `floor_label_detected.*` |

이미 있는 산출도 그 단계를 다시 실행해 같은 경로에 덮어씁니다. 도면 폴더를 통째로 지우지는 않습니다. 직전 단계 산출은 그 단계에서 수정하지 않습니다.

`extract_2d`, `detect_walls_floor`, `prepare_review`, `view_image`, `correct_walls_floor`, `render_wall_diff`, `detect_labels`는 호출 하나당 층 하나입니다. `for FLOOR in …`, `--floor all`, `detect_walls_all`은 쓰지 않습니다.

Vision은 생략하지 않습니다. `prepare_review.py`와 `correct_walls_floor.py`를 한 bash에 넣지 않고, 로그에 `review.json` 경로가 나온 뒤에 보정합니다. 자세한 호출은 `skill-validator.md`입니다.

실패한 층은 그 단계에서 멈추고, 나머지 층은 이어서 처리한 뒤 보고에 적습니다.

## 4. 보고

`$ARTIFACTS_DIR/<drawing_id>/area_sizing.md`에 층별 `floor_original`, `floor_wall_validated`, 라벨 수, `unique_face_area_m2`, 실패 단계를 적습니다.

면적 숫자는 `floor_label_detected.json`의 `labels[].area_m2`, `unique_face_area_m2`를 그대로 씁니다. `shared_with`가 있는 면은 합계에 두 번 넣지 않습니다. 도면 문구 `drawing_area_m2`와 다르면 계산값을 기준으로 말합니다.

## 5. 한 줄 요약

**네 스킬을 devider → walldetector → llmvalidator → totalroom 순으로 호출하고, 전 층이 끝난 뒤 `area_sizing.md`로 실명 면적을 보고합니다.**
