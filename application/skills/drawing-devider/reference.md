# drawing-devider — 참고

## 산출물 위치

bash/`execute_code` cwd는 사용자 `artifacts/`이며, Runtime에서는 `$ARTIFACTS_DIR`가 같다.  
**Cursor 로컬 등 env가 비면** SKILL.md [경로 bootstrap]을 먼저 적용한다 — 빈 `$WORKING_DIR`로 `/skills/...`를 만들지 말 것.

```text
$ARTIFACTS_DIR/
  <drawing_id>/
    structure.md / .json
    work_log.md
    floors/<F>/
      floor_original.dxf / .png / _meta.json   # extract_2d (층 단위 · 미리보기)
```

- `floor_overview.*` / `floor_*_clean.dxf`는 **사용·생성하지 않는다** (제거됨).
- `parts/` · `floor_parts_index.json` · `split_plan.*`는 **기본 워크플로에서 만들지 않는다**.
- 로컬 개발 시에도 **사용자 artifacts**를 쓴다.

## 기존 폴더 게이트

`$ARTIFACTS_DIR/<drawing_id>/`가 이미 있으면:

1. 경로·기존 파일 요약을 사용자에게 보여 준다
2. **계속 / 중단** (또는 다른 `drawing_id`)을 묻는다
3. 허락 전에는 `extract_2d` / `analyze`를 실행하지 않는다
4. 중단 시 폴더를 임의 삭제하지 않는다

## 층 게이트 · 층별 1개씩

1. `pilot_floor` 한 층만 `extract_2d.py`
2. 허락 후 나머지 층도 **층당 bash 1회** (`extract_2d` → 보고)
3. **금지:** `for FLOOR in 6F 7F …` 일괄 루프, `--floor all`, 한 커맨드에 여러 층
4. 대용량 DXF(수백 MB)에서 일괄 추출은 `TimeoutExpired`를 유발한다

## 층 단위 처리 (기본)

고해상도 PNG로 층 전체가 Vision·벽체 인식에 충분하다.  
기본 파이프라인은 **층당 `floor_original` 하나**만 만든다.

## 레거시 타일 분할 (선택)

사용자가 **명시적으로** 타일 분할을 요청할 때만:

- `plan_split.py` — max 60 m · overlap 격자 계획
- `split_floor.py` — `floors/<F>/parts/` 생성

기본 체크리스트·워크플로에는 포함하지 않는다.

## lib_render 와의 관계

- 치수·렌더 함수는 **같은 스킬** `scripts/lib_render.py`를 import 하여 재사용
- `extract_2d.py` → `floors/<F>/floor_original.dxf` (+ `.png`)
- 스킬은 **기존 폴더 확인·층별 1개씩·다층 게이트·artifacts 규약**을 강제한다
