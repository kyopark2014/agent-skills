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
drawing-totalroom      floor_label_detected.*  (--fill-unlabeled)
```

## 2. 대상

1. `$ARTIFACTS_DIR/drawing_list.json`의 `source_filename`으로 도면을 고릅니다. 없으면 `drawing_id`, `folder`.
2. 층을 지정하면 그 층만 네 단계를 모두 처리합니다. `1층`은 `1F`, `지하1층`은 `B1F`, `옥상`은 `RF`.
3. 층을 말하지 않으면 `discovered_floors`(도곽이면 `sheet_XX` 포함)를 **범위 전체**로 한 작업으로 처리합니다.
4. 목록에 없고 원본 DXF·DWG만 있으면 `drawing-devider`로 추출한 뒤 그 `drawing_id`를 대상으로 삼습니다.

## 3. 실행 순서

```text
① drawing-devider     extract_2d --floor all(또는 지정 층) 한 번 → analyze_drawing 1회
② drawing-walldetector detect_walls_all 한 번 (한 층이면 detect_walls_floor)
③ drawing-llmvalidator prepare_review_all → view_image_all 각 한 번
                       → 보정은 층마다 bash (한 턴에 물리 CPU 코어 수만큼 병렬)
④ drawing-totalroom   detect_labels_all 최대 3층 배치 (--fill-unlabeled 항상)
⑤ area_sizing.md
```

①②와 Vision(③ 전반)은 범위 전체를 스크립트 한 번씩으로 끝냅니다. 보정만 층마다 bash입니다. 실면적은 한 bash에 최대 3층입니다. 층 사이·단계 사이에 진행 여부를 묻지 않습니다.

| 단계 | 스크립트 | 입력 | 출력 |
|------|----------|------|------|
| 추출 | `extract_2d.py --floor all` | 원본 DXF/DWG | `floor_original.dxf` `.png` |
| 벽 | `detect_walls_all.py` | `floor_original.dxf` | `floor_wall_original.*` |
| 검증 | `prepare_review_all.py` → `view_image_all.py` → 층마다 `correct_walls_floor.py` + `render_wall_diff.py` | `floor_wall_original.*` | `floor_wall_validated.*` |
| 실면적 | `detect_labels_all.py` (≤3층) 또는 `detect_labels.py` | `floor_wall_validated.dxf` `_meta.json` | `floor_label_detected.*` |

이미 있는 산출도 그 단계를 다시 실행해 같은 경로에 덮어씁니다. 도면 폴더를 통째로 지우지는 않습니다. 직전 단계 산출은 그 단계에서 수정하지 않습니다.

`for FLOOR in …`로 층마다 단일 스크립트를 돌리지 않습니다. 추출의 `nohup`·백그라운드는 **devider `SKILL.md`**를 따릅니다. areasizing 본문의 Vision·보정 단계에서는 `nohup`/`&`/`sleep`/`for`를 쓰지 않습니다.

Vision은 생략하지 않습니다. `prepare_review_all`과 `correct_walls_floor`를 한 bash에 넣지 않고, `view_image_all`이 끝난 뒤에 보정합니다. 자세한 호출은 `skill-validator.md`입니다.

면적 조사에서는 **항상** `--fill-unlabeled`를 붙입니다. 실명만 잡고 멈추지 않으며, 닫힌 빈 칸은 `무명_N`으로 채웁니다(열린 open space는 제외).

실패한 층은 그 단계에서 멈추고, 나머지 층은 이어서 처리한 뒤 보고에 적습니다.

## 4. 보고

`$ARTIFACTS_DIR/<drawing_id>/area_sizing.md`에 층별 `floor_original`, `floor_wall_validated`, 라벨 수, `unique_face_area_m2`, 실패 단계를 적습니다.

면적 숫자는 `floor_label_detected.json`의 `labels[].area_m2`, `unique_face_area_m2`를 그대로 씁니다. `shared_with`가 있는 면은 합계에 두 번 넣지 않습니다. 도면 문구 `drawing_area_m2`와 다르면 계산값을 기준으로 말합니다.

## 5. 한 줄 요약

**네 스킬을 devider → walldetector → llmvalidator → totalroom 순으로 범위 단위 `_all` 스크립트로 호출하고, totalroom에는 항상 `--fill-unlabeled`를 붙인 뒤 `area_sizing.md`로 실명 면적을 보고합니다.**
