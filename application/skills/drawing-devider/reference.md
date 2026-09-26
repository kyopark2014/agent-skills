# drawing-devider — 참고

## 산출물 위치

bash/`execute_code` cwd는 사용자 `artifacts/`이며, Runtime에서는 `$ARTIFACTS_DIR`가 같다.  
**Cursor 로컬 등 env가 비면** SKILL.md [경로 bootstrap]을 먼저 적용한다 — 빈 `$WORKING_DIR`로 `/skills/...`를 만들지 말 것.

```text
$ARTIFACTS_DIR/
  <drawing_id>/
    structure.md / .json
    split_plan.md / .json
    work_log.md
    floors/<F>/
      floor_original.dxf / .png                 # extract_2d (parts 입력 · 미리보기)
      parts/…
```

- `floor_overview.*` / `floor_*_clean.dxf`는 **사용·생성하지 않는다** (제거됨).
- parts 기본 입력은 `floor_original.dxf` (`split_floor --source original`).
- 로컬 개발 시에도 **사용자 artifacts**를 쓴다.

## 기존 폴더 게이트

`$ARTIFACTS_DIR/<drawing_id>/`가 이미 있으면:

1. 경로·기존 파일 요약을 사용자에게 보여 준다
2. **계속 / 중단** (또는 다른 `drawing_id`)을 묻는다
3. 허락 전에는 `extract_2d` / `analyze` / `plan` / `split`을 실행하지 않는다
4. 중단 시 폴더를 임의 삭제하지 않는다

## 60×60 m 규칙

- `plan_split.py`가 \(n_x=\lceil W/\mathrm{step}\rceil\), \(n_y=\lceil H/\mathrm{step}\rceil\) 계산
- strip-only 분할은 한 변이 60 m를 넘을 수 있어 **금지**
- `split_floor.py`가 타일마다 `assert` 후 PNG/DXF 생성

## 층 게이트 · 층별 1개씩

1. `pilot_floor` 한 층만 `split_floor.py`
2. `floor_parts_index.json`의 `approved_for_remaining_floors`는 사용자가 허락하기 전 `false`
3. 허락 후 나머지 층도 **층당 bash 1회** (`extract_2d` → 보고 → `split_floor` → 보고)
4. **금지:** `for FLOOR in 6F 7F …` 일괄 루프, `--floor all`, 한 커맨드에 여러 층
5. 대용량 DXF(수백 MB)에서 일괄 추출은 `TimeoutExpired`를 유발한다

## lib_render 와의 관계

- 치수·렌더 함수는 **같은 스킬** `scripts/lib_render.py`를 import 하여 재사용
- `extract_2d.py` → `floors/<F>/floor_original.dxf` (parts 입력, 기본)
- `split_floor.py` 기본: `--source original` (`floor_overview.*` 미생성)
- `floor_overview.*` / `floor_*_clean.dxf`는 제거됨
- 스킬은 **기존 폴더 확인·층별 1개씩·다층 게이트·60 m 그리드·artifacts 규약**을 강제한다
