# drawing-devider — 참고

## 산출물 위치

bash/`execute_code` cwd는 사용자 `artifacts/`이며, Runtime에서는 `$ARTIFACTS_DIR`가 같다.  
**Cursor 로컬 등 env가 비면** SKILL.md [경로 bootstrap]을 먼저 적용한다 — 빈 `$WORKING_DIR`로 `/skills/...`를 만들지 말 것.

```text
$ARTIFACTS_DIR/
  drawing_list.json                            # 프로젝트 도면 목록. 각 항목 floors 앞에 source_filename(원본 DXF 파일명)
  <drawing_id>/
    extract_summary.json                       # 이 도면만. artifacts 루트에 두지 않는다
    structure.md / .json
    work_log.md
    floors/<F>/
      floor_original.dxf / .png / _meta.json   # extract_2d (층 단위 · 미리보기)
```

- `floor_structure.*` / `floor_overview.*` / `floor_*_clean.dxf`는 **사용·생성하지 않는다**. 구조용으로 걸러 낸 도면도 `floor_original.png` 이다.
- `parts/` · `floor_parts_index.json` · `split_plan.*`는 **기본 워크플로에서 만들지 않는다**.
- 로컬 개발 시에도 **사용자 artifacts**를 쓴다.

## 기존 폴더

`$ARTIFACTS_DIR/<drawing_id>/`가 이미 있어도 묻지 않고 추출·분석을 진행한다. `floor_original.*`, `structure.md`, `structure.json`, `work_log.md`는 덮어쓴다. 폴더를 통째로 삭제하지 않는다.

## 층 구분

| 순서 | 방식 | 조건 |
|------|------|------|
| 1 | `XA-S-{N}F 평면` INSERT | 블록 이름이 있을 때 |
| 2 | 도곽 + 층 제목 (`lib_sheet.py`) | 1이 비어 있을 때. `--layout auto` |

2번에서도 **확인 없이 전 층**을 추출한다. 형식이 없다고 추출을 멈추거나 스킬 변경을 묻지 않는다. 목록은 `--list-floors`.

제목으로 인정하는 예: `1층 평면도`, `1층 냉난방 평면도`, `제2층`, `지하1층`, `B1F`, `12F PLAN`, `옥상 평면도`.  
층과 `평면도` 사이의 용도 단어는 허용한다. 치수·실명·`2층 참조` 같은 문구는 층으로 세지 않는다.  
같은 제목이 떨어진 도곽에 여러 번 있으면 왼쪽부터 `1F`, `1F_2` 로 구분한다.  
한 도곽 안에 층 표기가 여럿이고 서로 떨어져 있지 않아 나누지 못하면, 그 도곽을 건너뛰지 않는다. 표기로 층 이름을 정하지 않고 왼쪽·아래부터 `sheet_01`, `sheet_02` 로 남긴다. 경고에 층 이름이 미확정임을 적는다. 층이 이미 확정된 도곽과 겹치거나, 더 큰 도곽에 포함된 테두리는 제외한다. `sheet_XX` 도 확인 없이 추출한다.

## 여러 층은 한 번에 · 확인 없음

1. 발견된 층은 `extract_2d.py --floor all` 한 번으로 추출한다. 고른 층만이면 `--floor 1F,3F`. 동시에 도는 층 수는 물리 CPU 코어 수다
2. 같은 경로의 `floor_original.*` 가 있으면 덮어쓴다
3. **금지:** `for FLOOR in …` 로 extract 를 반복 호출, 층 사이에 사용자 확인
4. 한 층만 필요하면 `--floor <F>` 한 번이다

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
- 스킬은 **기존 파일 덮어쓰기·층별 1개씩 연속 실행·artifacts 규약**을 강제한다. 층 사이·기존 폴더에서 사용자 확인을 받지 않는다
