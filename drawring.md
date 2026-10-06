# 도면 분석 파이프라인

CAD/DXF를 **층 추출 → 벽 검출 → Vision 검증 → 실명 면적** 순으로 처리합니다.  
한 번에 끝까지 돌릴 때는 `drawing-areasizing`이 네 스킬을 이 순서로 호출합니다. 한 단계만 말하면 그 스킬만 씁니다.

예시 도면은 SK용인하이닉스 지원동 (`sk_yongin_jiwon`, 원본 `SK용인하이닉스_지원동 평면도_241014.dxf`)입니다. 같은 파이프라인은 호텔·병원·주택 도면에도 씁니다. 건물·층 이름은 `$ARTIFACTS_DIR/drawing_list.json`의 `source_filename`·`floors[].floor`로 고릅니다.

판정 문장·방 이름별 예외의 원본은 각 스킬 `SKILL.md`입니다. 이 문서는 스크립트와 산출물이 실제로 하는 일을 적습니다.

## 공통 경로


| 항목    | 값                                                                         |
| ----- | ------------------------------------------------------------------------- |
| 스킬 루트 | `agent-skills/application/skills/`                                        |
| 스크립트  | 스킬 폴더 `scripts/` **절대경로**. Runtime만 `$WORKING_DIR/skills/<skill>/scripts` |
| 산출물   | `$ARTIFACTS_DIR/<drawing_id>/`                                            |
| 도면 목록 | `$ARTIFACTS_DIR/drawing_list.json`                                        |
| cwd   | bash는 `artifacts/` — 상대 `skills/`·`scripts/` 금지                           |


`$WORKING_DIR`·`$ARTIFACTS_DIR`가 비면 `"$WORKING_DIR/skills/..."`가 `/skills/...`로 깨집니다. 파일이 없으면 env를 붙이지 말고 스킬 `scripts/` 절대경로를 씁니다.

로컬 예:

```bash
SKILLS=/Users/ksdyb/Documents/src/agent-skills/application/skills
ARTIFACTS_DIR=/Users/ksdyb/Documents/src/agent-skills/application/.session_storage/lge/artifacts
ART=$ARTIFACTS_DIR/sk_yongin_jiwon
```



## 전체 순서

```text
drawing-devider        floors/<F>/floor_original.dxf / .png
        ↓
drawing-walldetector   floor_wall_original.*  (+ floor_wall_common.png)
        ↓
drawing-llmvalidator   floor_wall_validated.*
        ↓
drawing-totalroom      floor_label_detected.dxf / .png / .json
```


| 스킬                      | 역할                                             |
| ----------------------- | ---------------------------------------------- |
| `drawing-areasizing`    | 위 네 단계를 순서대로 실행. 스크립트는 없습니다                    |
| `drawing-roomevaluator` | 실이 **하나**일 때 `floor_wall_validated`에서 그 실만 면적  |
| `drawing-llmextractor`  | 주제(기둥·문 등)를 Vision으로 찾아 별도 PNG에 표시. 면적 파이프라인 밖 |


공통 실행 규칙:

- 기존 `floor_original.*`·`floor_wall_original.*`·`floor_wall_validated.*`·`llm_review/`·`floor_label_detected.*`가 있으면 **같은 경로에 덮어씁니다**. 도면 폴더는 유지합니다.
- 층을 지정하면 그 층만, 아니면 발견 층 전체를 순서대로 처리합니다.
- `extract_2d`·`detect_walls_floor`·`prepare_review`·`view_image`·`correct_walls_floor`·`detect_labels`는 **층당 bash 1회**. `for FLOOR in …`, `--floor all`, `detect_walls_all`은 기본으로 쓰지 않습니다.

---



## 1. 층 추출 — `drawing-devider`

경로: `application/skills/drawing-devider/`

원본 DXF를 층별 `floors/<F>/floor_original.dxf`(+ `.png`)로 뽑습니다. 층 하나가 후속 Vision·벽 인식의 입력입니다.

### 층 구분 (`--layout auto`)

1. modelspace INSERT `XA-S-{N}F 평면`이 있으면 **block** 방식. 지원동이 이 경우입니다.
2. 그 형식이 없으면 `lib_sheet.py`가 도곽(축정렬 테두리)과 층 제목으로 나눕니다. `1층 평면도`뿐 아니라 `1층 냉난방 평면도`처럼 사이에 용도가 있어도 인정합니다. 같은 제목이 떨어진 도곽에 반복되면 왼쪽부터 `1F`, `1F_2`. 한 도곽 안에서 층을 나누지 못하면 `sheet_01`, `sheet_02`로 두고 층 이름만 미확정입니다. `sheet_XX`도 그 이름으로 추출합니다.
3. 도곽 도면은 먼저 `--list-floors`로 목록만 보고, 나온 이름만 `--floor`에 넣습니다.



### 스크립트


| 스크립트                                       | 역할                                                                       |
| ------------------------------------------ | ------------------------------------------------------------------------ |
| `scripts/extract_2d.py`                    | 원본 → `floors/<F>/floor_original.dxf` (+ `.png`). 기본 `--variant original` |
| `scripts/lib_sheet.py`                     | XA-S가 없을 때 도곽·층 제목                                                       |
| `scripts/lib_structure.py`                 | 도곽 + 건축 레이어일 때 벽·창·실명·문 스윙만                                              |
| `scripts/analyze_drawing.py`               | `structure.md` / `structure.json`                                        |
| `scripts/lib_render.py`                    | 치수·고해상도 렌더                                                               |
| `scripts/lib_split.py`                     | primary 클러스터·bbox                                                        |
| `scripts/plan_split.py` / `split_floor.py` | **레거시.** 사용자가 타일을 명시할 때만                                                 |


```bash
SCRIPTS="$SKILLS/drawing-devider/scripts"
python3 "$SCRIPTS/extract_2d.py" \
  --dxf "$ARTIFACTS_DIR/<input>.dxf" \
  --floor 5F \
  --out "$ARTIFACTS_DIR" \
  --drawing-id sk_yongin_jiwon
```



### `floor_original`에 무엇이 들어가는가


| 레이아웃                                                | 내용                                                                                                                                       |
| --------------------------------------------------- | ---------------------------------------------------------------------------------------------------------------------------------------- |
| **block** (지원동 `XA-S-{N}F`)                         | `--variant original`이 기본. 가구 INSERT를 **유지**합니다. 좌·우 복사본이면 LINE이 많은 primary 클러스터만. `--variant clean`은 레거시이며 `FURNITURE_KEYWORDS`로 가구를 뺍니다 |
| **sheet** + 도곽 안 건축 레이어 (`ARCH`, `*_BG`·`*_CEN` 제외) | `collect_structural_sheet`. 벽·창·실명·문 스윙만. 창선은 벽과 같이 남깁니다. 가구·카세트 배관·등고선은 넣지 않습니다. 파일명은 그대로 `floor_original.`*                            |
| **sheet** + 건축 레이어 없음                               | 도곽 안 기하를 그대로 둡니다                                                                                                                         |


미리보기는 `floor_original.png`만 씁니다. `floor_structure.*` / `floor_overview.*` / `floor_*_2d.png` / `floor_*_geom.json`은 만들지 않습니다 (`--geom-json`은 디버그용). PNG는 기본 생성 (`--no-png`로 생략).

### 워크플로

1. `drawing_id` 폴더가 있어도 추출합니다. `floor_original.*`·`structure.*`·`work_log.md`는 덮어씁니다.
2. 층마다 `extract_2d.py --floor <F>` 한 번. 한 층이 끝나면 다음 층을 처리합니다.
3. 범위 안 추출이 끝나면 `analyze_drawing.py` 1회.
4. `work_log.md`를 남깁니다.

매번 `$ARTIFACTS_DIR/drawing_list.json`의 해당 도면만 갱신합니다. 다른 `drawing_id`는 지우지 않습니다. 항목 앞에 원본 파일명 `source_filename`·`source_path`를 둡니다. 이 도면 요약은 `$ARTIFACTS_DIR/<drawing_id>/extract_summary.json`입니다. artifacts 루트에 두지 않습니다.

### 산출물

```text
$ARTIFACTS_DIR/
├── drawing_list.json
└── <drawing_id>/
    ├── extract_summary.json
    ├── structure.md / structure.json
    ├── work_log.md
    └── floors/<F>/
        ├── floor_original.dxf / .png
        └── floor_original_meta.json
```

`parts/` · `floor_parts_index.json` · `split_plan.*` · `floor_*_clean.dxf`는 기본 미생성입니다.

---



## 2. 벽 검출 — `drawing-walldetector`

경로: `application/skills/drawing-walldetector/`

입력은 `floors/<F>/floor_original.dxf`뿐입니다. 원본 대용량 DXF를 직접 돌리지 않습니다.

지원동처럼 벽·가구·설비가 거의 전부 `0arch` 한 레이어이면 레이어 이름으로 벽을 나눌 수 없습니다. `LINE` / `LWPOLYLINE`을 세그먼트로 쪼개 **축정렬 평행 이중선**으로 판정합니다. 숫자의 원본은 `wall_conditions.json`의 `common[0]`입니다.

### 후보와 확정

간격이 두께 대역에 들어갔다고 바로 벽이 되지는 않습니다.


| 단계       | 값                                                                                                                              |
| -------- | ------------------------------------------------------------------------------------------------------------------------------ |
| 방향       | 수평·수직, 허용 ±8°                                                                                                                  |
| 후보 최소 길이 | 500 mm (`min_len_mm`)                                                                                                          |
| 후보 간격    | 30–420 mm (`thick_min_mm`–`thick_max_mm`). 외벽 마감선 30–45 mm 포함                                                                  |
| 후보 겹침    | 400 mm 이상, 그리고 짧은 쪽의 25% 이상                                                                                                    |
| 여러 겹     | 이웃 ≥3이고 인접 간격 중앙값 > 200 mm면 계단 해칭으로 제외. 중앙값 ≤ 200 mm면 2.5 m 미만 짧은 겹만 빼고 긴 겹은 확정 조건으로 넘깁니다. 촘촘하고 2.8 m 이상이라는 이유만으로 외벽으로 두지 않습니다 |


가장 가까운 평행선이 아래 중 하나일 때 `WALL`로 확정합니다. 간벽·X문 조건은 더 먼 평행선에도 적용합니다. 연속 면은 가장 가까운 선에만 적용합니다.


| 확정            | 조건                                                                                                                    |
| ------------- | --------------------------------------------------------------------------------------------------------------------- |
| 개구로 잘린 간벽     | 간격 120–180 mm, 같은 직선 런이 두 면 모두 2.2 m 이상 (직교 8 mm, 이음 40 mm)                                                           |
| X문 개구에 맞닿은 간벽 | 간격 120–180 mm, X 폴리라인 두 면과 25 mm 이내, 끝이 개구와 50 mm 이내                                                                  |
| 연속된 실 테두리     | 간격 30–420 mm, 짧은 쪽 ≥ 2.2 m, 겹침 ≥ 짧은 쪽의 80%. 같은 조건의 평행선이 8개 이상이면 이 조건으로 올리지 않습니다. 이미 벽이 된 두 면과 8 mm 이내인 2.5 m 이상 조각도 벽 |


그 외 제외·분리:


| 대상               | 처리                                                                                                 |
| ---------------- | -------------------------------------------------------------------------------------------------- |
| `ARC` / `CIRCLE` | 벽 아님 (문 스윙·설비)                                                                                     |
| 닫힌 사각 ≤ 3.5 m    | 가구로 제외. H-Beam 중첩 정사각은 예외                                                                          |
| 짧은 다변 폴리라인       | 조경·해칭 제외                                                                                           |
| 폴리라인 벽 비율 ≥ 75%  | 통째 승격 (`entity_wall_ratio`). 미만이면 매칭된 세그먼트만                                                        |
| X자 문             | 교차 대각선은 벽이 아님. 이중선 사이 궤적은 빼고 바깥 면만 벽                                                               |
| 여닫이 문짝           | 두께 20–80 mm, 폭 0.65–1.45 m는 `DOOR`                                                                 |
| 창틀               | 같은 개구의 얇은 창틀(두께 15–90 mm, 길이 0.4–1.6 m)은 `WINDOW`. 돌출창 윤곽(45° 볼살–바깥면–반대 볼살, 돌출 0.25–1.0 m)은 `WALL` |
| H-Beam 기둥        | 중첩 정사각, 변 ≤ 1.2 m, 가로·세로 차이 22% 이내 → `COLUMN`. 사각 안 호가 둘 이상이거나 짧은 선이 많은 표식(휠체어)은 기둥 아님             |


저장 레이어 (ACI):


| 레이어      | 색   | ACI |
| -------- | --- | --- |
| `WALL`   | 빨강  | 1   |
| `DOOR`   | 초록  | 3   |
| `WINDOW` | 청록  | 4   |
| `COLUMN` | 파랑  | 5   |
| `BASE`   | 회색  | 8   |




### 도면별 조건

`wall_conditions.json`의 `common`은 공통값입니다. `projects`는 키만 다른 조건을 뒤에 붙입니다. 현재 키는 `hynix`(지원동), `hotel`, `hospital`, `house`입니다.

`floors/<F>/wall_samples/wall_conditions.json`이 없으면 검출 전에 `sample_wall_conditions.py --vision`으로 층 한가운데 샘플 2장을 읽어, common이 놓친 이중선의 간격·길이를 모읍니다. 이미 있으면 그 파일을 씁니다. 샘플 조건은 `floor_wall_original`에만 붙고 `floor_wall_common`에는 넣지 않습니다.


| 산출                      | 적용 조건              |
| ----------------------- | ------------------ |
| `floor_wall_original.*` | common + 프로젝트 + 샘플 |
| `floor_wall_common.png` | common만            |




### 스크립트


| 스크립트                                | 역할                                                  |
| ----------------------------------- | --------------------------------------------------- |
| `scripts/sample_wall_conditions.py` | 샘플 2장 → `wall_samples/wall_conditions.json`         |
| `scripts/detect_walls_floor.py`     | **한 층** `floor_wall_original` + `floor_wall_common` |
| `scripts/lib_walls.py`              | 분류·DXF/PNG                                          |
| `scripts/lib_wall_samples.py`       | 샘플 창·조건 수집                                          |
| `scripts/detect_walls_tile.py`      | 단일 DXF (레거시)                                        |
| `scripts/detect_walls_all.py`       | 다층 일괄. 사용자가 명시할 때만                                  |


```bash
SCRIPTS="$SKILLS/drawing-walldetector/scripts"
python3 "$SCRIPTS/detect_walls_floor.py" --artifacts "$ART" --floor 5F
```

`wall_samples/wall_conditions.json`이 없을 때만, 그 전에:

```bash
python3.13 "$SCRIPTS/sample_wall_conditions.py" --artifacts "$ART" --floor 5F --vision
```



### 파라미터 (`wall_conditions.json` `common[0]`, CLI로 덮어쓸 수 있음)


| 이름                     | 기본   | 의미                 |
| ---------------------- | ---- | ------------------ |
| `min_len_mm`           | 500  | 후보 세그먼트 최소 길이      |
| `thick_min_mm`         | 30   | 이중선 최소 간격          |
| `thick_max_mm`         | 420  | 이중선 최대 간격          |
| `wall_pack_gap_mm`     | 200  | 이보다 성긴 여러 겹은 계단 해칭 |
| `entity_wall_ratio`    | 0.75 | 폴리라인 통째 승격         |
| `furniture_box_max_mm` | 3500 | 이하 닫힌 박스 = 가구      |




### 산출물

```text
$ARTIFACTS_DIR/<drawing_id>/floors/<F>/
  floor_wall_original.dxf / .png / _meta.json
  floor_wall_common.png / _meta.json
  floor_wall_index.json
  wall_samples/
    sample_01.png / sample_02.png
    samples.json / observations.json
    wall_conditions.json
walls_all_index.json          # 선택
```

`--with-tiles`일 때만 `walls/R*C*_walls.*`.

### 한계

- 단일선 벽·사선 벽은 약합니다.
- 이중선 간격이 확정 조건 밖이면 놓칩니다.
- ≤ 3.5 m 닫힌 박스로 그린 작은 실(화장실 칸 등)도 가구와 함께 빠질 수 있습니다.
- 레이어가 표준화되면 휴리스틱보다 레이어·블록 규칙을 먼저 쓰는 편이 낫습니다.

---



## 3. 벽 검증 — `drawing-llmvalidator`

경로: `skills/drawing-llmvalidator/`

`floor_wall_original`의 빨간 WALL을 `floor_wall_validated.*`로 고칩니다. 입력 `floor_wall_original.*`은 덮어쓰지 않습니다.

선을 지우고 레이어를 바꾸는 것은 항상 `lib_llm_correct.py`의 `apply_corrections`입니다. Vision은 도면 엔티티를 건드리지 않습니다. `view_image.py`가 타일 PNG만 보고 `llm_review/review.json`에 **상자**를 씁니다. `review.json`이 없어도 기하 규칙은 그대로 돕니다. `load_review`는 없으면 `{}`를 반환합니다.

### 기하를 쓰는 경우

DXF에서 길이·간격·각도·블록 형태로 재는 구조입니다. 층마다 반복되고, 숫자로 일반화할 수 있는 경우입니다. `WINDOW`·`COLUMN`은 `WALL`로 처리합니다.


| 경우           | 기하가 하는 일                                                                       |
| ------------ | ------------------------------------------------------------------------------ |
| 복도 양측        | 6 m 이상, 또는 2.5 m 이상이면서 벽 두께 이중선인 WALL은 demote에서 보호. 회색 장축 이중선은 promote         |
| 같은 줄의 방      | 이웃만 빨강인 칸막이·맞댄 벽·실 모서리·WALL 런 사이 갭을 promote                                    |
| 문            | ARC 스윙(반지름·호 각도), X자, 문짝 두께·폭으로 개구를 찾고, 문짝은 내리고 양옆 벽면은 올림. 저장은 `DOOR`          |
| 창            | 개구에 겹친 창틀·관찰창 치수로 `WINDOW`. 면적 경계로는 남깁니다                                       |
| 기둥           | 정사각+`_`, H형, 벽이 변에서 끊긴 정사각을 `COLUMN`으로 승격. 휠체어 표식·밀집 격자는 제외                    |
| 계단           | `UP`/`DN` 근처 외곽 이중선은 promote·protect. 짧은 평행 다발(트레드)은 demote                    |
| 엘리베이터        | 열 간격 약 5–9.5 m인 로비 쪽 잼·어깨·외곽은 벽. 전고 문·후면은 demote                               |
| 강당·오픈홀       | TEXT `강당`/`AUDITORIUM`으로 홀 범위를 잡고, 중앙 장축은 protect보다 우선해 demote. 외곽·무대 3면은 유지   |
| 가구·기구의 정형 패턴 | 닫힌 사각, 「피트니스」 주변 짧은 선, 배식대·회의실 내부, 조경 윤곽 안을 demote                             |
| 레이어 분리       | 보정 맨 끝에 문·창·기둥을 `DOOR` / `WINDOW` / `COLUMN`으로 다시 칠합니다. Vision은 레이어를 지정하지 않습니다 |


방 이름·문 형태별 수치(지원동·병원 도면에서 맞춘 값)도 이 기하 함수 안에 있습니다. 목록은 `drawing-llmvalidator/SKILL.md`의 판정 기준과 같고, 실행은 `lib_llm_correct.py`입니다.

### Vision을 쓰는 경우

이미지를 Vision LLM으로 확인하여, 수치 규칙에 **안 들어가는 오검출·미검출을 수행합니다** . 모델은 `SKILL.md`의 「Vision 판정 기준」을 프롬프트을 따라 타일 한 장마다 수행합니다.


| 상자               | 이미지에서 보는 것                                                 | 기하가 상자를 적용하는 방식                                                                            |
| ---------------- | ---------------------------------------------------------- | ------------------------------------------------------------------------------------------ |
| `demote_bboxes`  | **빨강인데 벽이 아닌** 한 덩어리. 책상·기구·벤치·엘리베이터 카 내부처럼 정형 가구 규칙이 놓친 것 | 상자 안에 **중심**이 들어오는 `WALL` `LINE`/`LWPOLYLINE`만 삭제. 복도·계단·엘리베이터·기둥 protect보다 **우선**         |
| `promote_bboxes` | **회색인데 벽인** 한 덩어리. 빠진 칸막이·복도 경계처럼 길이·이중선 규칙이 못 올린 것        | 상자 안 회색 선을 전부 올리지 **않습니다**. 이미 WALL인 선 사이의 갭 이중선, 그리고 양옆에 WALL 이웃이 있는 세로 칸막이(2.5 m 이상)만 승격 |


Vision이 하지 않는 일:

- 엔티티 삭제, 선 추가, 레이어·색 변경. 
- 복도 전체·층 절반·연속 방 열. demote 상자는 한 변 25 m 초과 또는 면적 200 m² 초과면 버립니다. 보통 한 변 15 m 이하가 가구·개구 한 덩어리입니다.
- Vision으로 문·창·기둥 레이어를 생성하지 않습니다. 

기하가 놓친 자리를 작은 상자로 짚습니다.

타일을 보는 주체는 채팅 에이전트가 아닙니다. `view_image.py`가 `llm_review/`의 PNG를 읽어 base64로 만들고, UI에서 고른 모델(`chat.get_chat()`)에 이미지와 판정 기준을 함께 보냅니다. 모델 답은 상자 JSON이고, 스크립트가 타일 좌표를 층 mm로 바꿔 `review.json`에 모읍니다.

```python
# view_image.py — 타일 한 장
encoded = _encode_png(review_dir / tile["file"])  # PNG → base64
chat, client = _chat()  # chat.get_chat(), UI_MODEL_NAME
result = client.invoke(
    [
        chat.HumanMessage(
            content=[
                {
                    "type": "image_url",
                    "image_url": {"url": f"data:image/png;base64,{encoded}"},
                },
                {"type": "text", "text": prompt},  # SKILL.md 「Vision 판정 기준」
            ]
        )
    ]
)
data = _parse_json(chat._content_to_text(result.content))
# data["demote_bboxes"] / data["promote_bboxes"]
# 타일 0~1 좌표 → tiles.json bbox_mm 로 층 전체 mm
```

```python
# 타일마다 _invoke_tile 후 한 파일로 저장
review = {"demote_bboxes": demote, "promote_bboxes": promote}
(review_dir / "review.json").write_text(json.dumps(review, ensure_ascii=False, indent=2))
```

`prompt`는 `SKILL.md`의 「Vision 판정 기준」에 타일 파일명을 붙인 문자열입니다. 모델에게는 설명 없이 JSON 하나만 답하라고 합니다. 좌표는 그 이미지 기준 0~1(왼쪽 위가 원점, y는 아래)이고, `_to_mm`이 층 좌표로 바꿉니다. 타일이 여러 장이면 최대 4개(`--workers`)를 동시에 호출합니다.

### 스크립트


| 스크립트                             | 역할                                                                                                                                     |
| -------------------------------- | -------------------------------------------------------------------------------------------------------------------------------------- |
| `scripts/prepare_review.py`      | `floor_wall_original.png` → `llm_review/R*C*.png` + `tiles.json`. 한 변 5000px 이하면 타일 1장. 넘으면 겹침 12% 격자. `floor_wall_full.png`는 만들지 않습니다 |
| `scripts/view_image.py`          | 타일마다 Vision → `review.json` (층 mm bbox)                                                                                                |
| `scripts/correct_walls_floor.py` | `apply_corrections` → `floor_wall_validated.*` + `corrections.json`                                                                    |
| `scripts/render_wall_diff.py`    | `diff_original_vs_validated.png` / `.json`. 어두운 빨강=kept, 초록=promote, 파랑=demote, 회색=BASE                                                |
| `scripts/lib_llm_correct.py`     | demote/promote·레이어 분리                                                                                                                  |


```bash
SCRIPTS="$SKILLS/drawing-llmvalidator/scripts"
python3 "$SCRIPTS/prepare_review.py" --artifacts "$ART" --floor 5F
# bash 300초 제한 때문에 Vision은 nohup. 로그에 review.json 경로가 나온 뒤에 correct
python3.13 "$SCRIPTS/view_image.py" --artifacts "$ART" --floor 5F
python3 "$SCRIPTS/correct_walls_floor.py" --artifacts "$ART" --floor 5F
python3 "$SCRIPTS/render_wall_diff.py" --artifacts "$ART" --floor 5F
```

`prepare_review`와 `correct_walls_floor`를 한 bash에 넣지 않습니다.

`review.json`의 좌표는 층 전체 mm입니다. 타일 범위는 `tiles.json`의 `bbox_mm`에 있습니다. `x0/y0/x1/y1`, 중첩 `bbox_mm`, `[xmin,ymin,xmax,ymax]`도 받습니다. 키가 없으면 그 항목만 skip합니다. `--short-demote`는 문 사이 복도 조각을 지울 수 있어 기본 OFF입니다.

### 처리 순서

```text
floor_wall_original
  → WINDOW/COLUMN 을 잠시 WALL 로 접기
  → promote 후보 확정
       갭, 복도, 복도 문 옆, 연속방, 맞댄 칸막이, 실 모서리,
       계단실, 엘리베이터, review promote_bboxes
       ※ 오픈홀 내부·엘리베이터 문/후면·운동기구·회의실 내부·조경·배식대는 후보에서 제외
       ※ 무대 외벽은 오픈홀 필터 뒤에 추가
  → protect (복도, 계단실, 엘리베이터, H-Beam)
  → demote (short는 기본 OFF, 가구 박스, 운동기구, 회의실 내부, 배식대, 조경)
       ※ protect 와 겹치면 protect 가 이김
       ※ review demote_bboxes, 오픈홀 중앙, 계단 디딤, 픽토그램,
         엘리베이터 문/후면, 가구·조경은 protect 보다 우선
  → demote 삭제 후 promote LINE 반영 (BASE LINE 은 레이어 변경, 그 외는 LINE 추가)
  → 사후: 오픈홀·엘리베이터 문·가구가 다시 올라온 것 제거
  → H-Beam·슬리브 승격, 픽토그램 재제거
  → 문 개구 끊기·문 양옆 승격 (correct_walls_around_doors 및 이후 규칙)
  → mark_doors_and_columns
       문짝·스윙 = DOOR, 창 = WINDOW, 기둥 = COLUMN, 벽 = WALL
  → floor_wall_validated
```

면적 단계의 경계는 `WALL`·`WINDOW`·`COLUMN`·`DOOR` 선입니다. 문 스윙 호는 경계가 아닙니다. `_meta.json`의 `doors`·`windows`·`columns`에 범위가 있습니다.

### 산출물

```text
$ARTIFACTS_DIR/<drawing_id>/floors/<F>/
  floor_wall_original.*                 # 불변
  floor_wall_validated.dxf / .png / _meta.json
  diff_original_vs_validated.png / .json
  llm_review/
    R0C0.png …
    tiles.json
    review.json
    corrections.json
    view_image.log
```



### 예외와 대응 (지원동에서 나온 실패)


| 증상                 | 원인                                                                               | 대응                                                                                                                |
| ------------------ | -------------------------------------------------------------------------------- | ----------------------------------------------------------------------------------------------------------------- |
| 복도 양측이 회색·끊김       | `--short-demote`가 문 개구로 잘린 WALL을 삭제. Vision이 복도를 demote에 넣음                      | `protect_corridor_wall_entities`, `promote_corridor_walls`. `--short-demote` 기본 OFF                               |
| 같은 줄 방 한 칸만 회색     | 폴리라인 `entity_wall_ratio < 0.75`로 통째 BASE. 또는 demote bbox가 층 절반이라 이웃 WALL이 먼저 사라짐 | promote 후보를 demote **전에** 확정. `promote_collinear_room_walls`. 과대 bbox는 load 시 skip                                |
| `KeyError: xmin`   | Vision bbox 키가 `x0`·`bbox_mm` 등                                                  | `normalize_bbox` / `normalize_bbox_list`. 키 없으면 그 항목만 skip                                                        |
| 강당 한가운데 벽          | 객석·통로 장축이 길이 ≥ 6 m라 corridor protect에 걸림                                         | `demote_open_hall_center_walls`가 protect보다 우선. `filter_promote_away_from_open_halls`                              |
| 계단실 외곽이 회색         | 트레드 다발 demote, 외곽이 복도 min_len(6 m)에 못 미침                                         | `promote_stair_enclosure_walls`, `protect_stair_enclosure_entities`. 중심 난간·트레드는 비승격                               |
| 엘리베이터 잼·어깨가 빠짐     | 문 방향 오인, 500 mm 필터가 짧은 리턴을 자름                                                    | 로비 간격으로 문 방향. `apply_corrections`의 `min_len_mm=70`. 전고 문·후면·중앙은 demote                                            |
| 기둥이 회색이거나 설비 격자가 벽 | `_` 없는 사각 오인, 밀집 격자, 외부 연결선까지 승격                                                 | 정사각+`_`만 `promote_hbeam_columns` 후 `COLUMN`. 밀집·휠체어는 demote. `demote_fitness_equipment`, `demote_landscape_walls` |


---



## 4. 실 면적 — `drawing-totalroom` / `drawing-roomevaluator`

둘 다 `floor_wall_validated.dxf`를 읽기만 합니다. 검증 파일은 수정하지 않습니다. 면적 구현은 `drawing-roomevaluator/scripts/evaluate_room.py`이고, totalroom이 그 모듈을 불러 층 전체 라벨에 적용합니다.

### 경계

- 라벨이 있는 쪽의 **벽 안쪽 면**.
- 경계 레이어: `WALL`, `WINDOW`, `COLUMN`, `DOOR`. 문 스윙 호는 경계가 아닙니다.
- 같은 벽선의 문 개구(2.4 m 이하)는 그 벽선으로 이어 실에 포함합니다. 여닫이 문이 있는 개구는 그 문선에서 멈춥니다.
- `--door` 기본은 `close`. 먼저 문을 모두 닫아 테두리를 잡고, 그 테두리 위 `DOOR` 직선과 테두리 450 mm 안의 여닫이만 그 실의 문입니다. `open`은 그 문만 엽니다. 테두리 밖 `DOOR`는 `open`이어도 닫힌 경계입니다.
- H-Beam이 벽 안쪽보다 실 안으로 들어온 면적은 `area_m2`에서 뺍니다 (`column_protrusion_m2`). 벽 두께 안에만 있는 부분은 빼지 않습니다.



### 라벨 (`detect_labels.py`)

모델스페이스 `TEXT`/`MTEXT`만. 블록 안 글자는 펼치지 않습니다. 공백 제거 후 24자 이하이고 `면적`·`천장`·`:`·`=`이 없으며, 한글이 있거나 영문 2자 이상에 숫자가 붙은 이름만 실명입니다. `UP`/`DN`, 치수, 축선, `옷장`·`화분` 같은 집기 표기는 제외합니다. `옷방`처럼 실명인 것은 남깁니다. 위·아래 줄 간격이 글자 높이의 1.8배 안이고 가로로 겹치면 한 실명으로 잇습니다 (`투시영상` + `검사실7`).

### 호출

층 전체:

```bash
python3.13 "$SKILLS/drawing-totalroom/scripts/detect_labels.py" \
  --dxf "$ART/floors/5F/floor_wall_validated.dxf" \
  --meta "$ART/floors/5F/floor_wall_validated_meta.json" \
  --door close
```

산출: `floor_label_detected.dxf` / `.png` / `.json`. DXF는 검증 도면 사본에 라벨별 레이어(HATCH + 실명·면적 문자)를 더한 것입니다. PNG는 그 DXF를 `render_wall_dxf_png`로 그립니다. `floor_wall_validated.png`를 배경으로 덧그리지 않습니다.

- `labels[].area_m2` — 그 라벨 HATCH 합 (m²).
- `unique_face_area_m2` — 서로 다른 면을 한 번씩만 더한 값. `shared_with`가 있으면 합계에 두 번 넣지 않습니다.
- `skipped` — 실명으로 봤으나 벽이 닫히지 않은 글자.
- 도면 문구 `면적 : N㎡`(`drawing_area_m2`)와 다르면 계산값을 기준으로 말합니다.

실 하나 (`drawing-roomevaluator`):

```bash
python3.13 "$SKILLS/drawing-roomevaluator/scripts/evaluate_room.py" \
  --dxf "$ART/floors/5F/floor_wall_validated.dxf" \
  --meta "$ART/floors/5F/floor_wall_validated_meta.json" \
  --png "$ART/floors/5F/floor_wall_validated.png" \
  --room "회의실#1" \
  --door close
```

결과는 DXF 옆 `room_eval/<실명>.json`과 `<실명>_overlay.png`만 덮어씁니다. 같은 실명이 여러 곳이면 좌표를 알리고 멈추며, `--x` `--y`로 다시 호출합니다. `room_eval_1`처럼 번호 폴더를 만들지 않습니다.

---



## 5. 한 번에 실행 — `drawing-areasizing`

경로: `application/skills/drawing-areasizing/`. 자체 스크립트는 없습니다.

```text
① drawing-devider       범위 안 전 층 추출 → analyze_drawing 1회
② 층마다:
     walldetector → llmvalidator (review.json 필수) → totalroom
③ $ARTIFACTS_DIR/<drawing_id>/area_sizing.md
```

- 각 단계 직전에 그 스킬 `SKILL.md`를 따릅니다. 이 문서의 명령과 자식 스킬이 다르면 **자식 스킬**이 맞습니다.
- 전 층이 끝난 뒤 `area_sizing.md`와 요약을 전달합니다.
- 기존 산출이 있어도 단계를 건너뛰지 않고 덮어씁니다.
- 실패한 층은 그 단계에서 멈추고, 나머지 층은 이어서 처리한 뒤 보고에 적습니다.
- `sheet_XX`는 층 이름이 미확정인 도곽입니다. `1F`로 바꾸지 않습니다.

`area_sizing.md`의 면적은 `floor_label_detected.json`의 `labels[].area_m2`, `unique_face_area_m2`를 그대로 씁니다.

---



## 6. 주제 객체 표시 — `drawing-llmextractor`

면적 파이프라인과 별개입니다. 이미지와 주제(기둥, 문, 창 등)를 받아 UI에서 고른 모델(`UI_MODEL_NAME`)로 객체를 찾고, **원본은 그대로 둔 채** `{원본이름}.{주제}.png` / `.json`에 박스를 그립니다.

기본 `--image`는 `drawing_list`가 가리키는 `floor_original.png`입니다. 벽 도면을 말하면 `floor_wall_validated.png`, 없으면 `floor_wall_original.png`. `--model`은 사용자가 모델을 명시할 때만 붙입니다. 층 도면은 Vision이 5분을 넘기므로 `python3.13`과 `nohup`으로 `extract_objects.py`를 돌립니다. 조각은 한 변 5000px 이하입니다.

---



## 가구·물체 블록 (지원동)

원본에서 가구·의자·설비는 **레이어가 아니라 블록(INSERT)** 입니다. 층 평면 블록(`XA-S-5F 평면` 등) 안에 `$0$` 접미사로 중첩되고, 기하는 거의 `0arch`에 있습니다.


| 항목                | 값                                                                        |
| ----------------- | ------------------------------------------------------------------------ |
| 표현                | `INSERT` → 블록 정의 (중첩)                                                    |
| 식별                | 블록명 키워드 (`지원동_가구(...)`, `chair_...`, `화)...`)                            |
| 필터 위치             | `extract_2d.py` / `lib_render.py` / `lib_split.py`의 `FURNITURE_KEYWORDS` |
| 평면 블록 안 중첩 INSERT | 가구 키워드 매칭 약 6,200+ (고유 short name ~84)                                   |


block 방식 기본값(`--variant original`)은 이 키워드로 가구를 **빼지 않습니다**. 키워드는 `--variant clean`(레거시)과, 도곽 추출에서 `skip_furniture`일 때 씁니다. 벽에서 가구를 걷는 일은 `drawing-walldetector`의 닫힌 박스·짧은 선 규칙과 `drawing-llmvalidator`의 demote가 합니다.

### `FURNITURE_KEYWORDS`

가구만이 아니라 문·입면·RAIN·업다운 등 비구조도 포함합니다.

```text
가구, chair, 피트니스, 화), DOOR, DOR_, 도어, 자동문,
락커, 라커, 샤워, 신발, 러닝, 파우더, 큐비클, 대변기,
소변, 객석, 좌석, 모바일, 회의, 업다운, 절취, RAIN, rain,
입면, 슬라이딩, 접견
```



### 블록명 (short name = `$0$` 뒤)



#### 사무·좌석


| 블록명                                       | 비고         |
| ----------------------------------------- | ---------- |
| `지원동_가구(업무좌석)` / `(업무좌석END)` / `(업무좌석TL)` | 업무용 책상+의자  |
| `지원동_가구(모바일)`                             | 모바일/가변 좌석  |
| `지원동_가구(리더1)` / `(리더2)`                   | 리더 가구      |
| `지원동_가구(임원1)` / `(임원2)`                   | 임원 가구      |
| `chair_190820`                            | 의자 (5F·6F) |




#### 회의·접견


| 블록명                                                       | 비고        |
| --------------------------------------------------------- | --------- |
| `지원동_가구(6인회의)` / `(8인회의)` / `(중회의)` / `(대회의)` / `(리더대회의)` | 회의 테이블·의자 |
| `접견가구 TYPE2` / `접견실 가구 TYPE3` / `접견홀 가구`                  | 접견실 (5F)  |




#### 소파·OA·기타


| 블록명               | 비고              |
| ----------------- | --------------- |
| `지원동_가구(소파1)`     | 소파              |
| `지원동_가구(OA1)`     | OA 가구           |
| `지원동_가구(칠판)`      | 칠판              |
| `지원동_설비통합(가구1~4)` | 설비 통합형 가구 (11F) |




#### 피트니스·샤워·락커 (주로 5F)


| 블록명                                                    | 비고         |
| ------------------------------------------------------ | ---------- |
| `지원동_피트니스(의자)` / `(러닝머신)` / `(라커)` / `(신발장)` / `(파우더)` | 피트니스 기구·부속 |
| `지원동_피트니스(가구2/4/5/6/7)`                                | 기타 피트니스 가구 |
| `지원동_피트니스(5F)`                                         | 피트니스 영역 묶음 |
| `지원동_가구(기준층락커)`                                        | 락커         |
| `지원동_5F(샤워부스)` / `지원동_남자샤워실(5F)`                       | 샤워         |




#### 위생기구


| 블록명                                     | 비고     |
| --------------------------------------- | ------ |
| `화)대변기` / `화)소변기` / `화)세면기`             | 위생기구   |
| `화)큐비클01` / `화)큐비클1(왼쪽 끝)` / `(오른쪽끝)`   | 대변 큐비클 |
| `화)소변큐비클` / `…900` / `(왼쪽끝)` / `(오른쪽끝)` | 소변 큐비클 |
| `대변기_B`                                 | 5F     |




#### 객석·식당


| 블록명                                        | 비고       |
| ------------------------------------------ | -------- |
| `소강당 객석_21석` / `_21석(장애인추가)` / `_23석`      | 소강당 (6F) |
| `장애인좌석`                                    | 5F·6F    |
| `지원동_좌석배치(6F식당)`                           | 식당       |
| `지원동_7층 좌측/우측좌석배치(억조)` / `지원동_7층 좌석배치(억조)` | 7F·8F    |




#### 문·도어 (키워드 매칭, 가구 아님)

`DOOR_*`, `DOR_*`, `자동문`, `지원동_자동문(...)`, `지원동_방화자동문`, `슬라이딩 도어`, `편개형 도어 1290` 등.

#### 기타 (키워드 매칭, 가구 아님)

`입면라인`, `지원동_입면(돌출바)`, `RAINPLUS` / `rainplus`, `업다운 시작/종점`, `절취선 (에스컬레이터 …)`.

#### 키워드 밖이지만 물체성 있는 블록


| 블록명                                                         | 비고    |
| ----------------------------------------------------------- | ----- |
| `지원동_주방(6F식당)` / `지원동_주방장비배치(20F)` / `지원동_주방용 외조기#2(7F-8F)` | 주방    |
| `지원동_7층 …장비배치(억조)` / `지원동_8층 중앙배치(억조)`                      | 장비 배치 |
| `원형벤치` (조경 블록 안)                                            | 조경 벤치 |


순수 가구 목록이 필요하면 `가구`·`chair`·`좌석`·`회의`·`접견`·`피트니스`·`화)`만 쓰고, 문·입면·RAIN·업다운·절취는 빼면 됩니다.